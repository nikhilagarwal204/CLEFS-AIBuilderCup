-- 0501_amenities_ledger: issuance log, request lines, the daily variance
-- rollup, learned on-request rates and cohort benchmarks (02 spec 5.1).
--
-- Why the variance table has no shift column (the 02 sketch had one in its
-- PK): expected use is a per-day, per-floor quantity; splitting it by shift
-- would invent numbers. By-shift and by-staff views read the issuance log
-- directly and compare issuers on "issued per occupied room served".

-- ─── Issuance log ───
-- qty is signed: a RETURN is negative. VIP and DAMAGE lines are explained
-- use and stay out of variance (02 spec 5.3 edge cases).

CREATE TABLE IF NOT EXISTS amn_issuance (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    issued_at         TIMESTAMPTZ NOT NULL,
    business_date     DATE NOT NULL,                       -- hotel business date of issued_at (core.time)
    shift             TEXT CHECK (shift IN ('MORNING', 'EVENING', 'NIGHT')),
    item_id           BIGINT NOT NULL REFERENCES amn_items (id),
    qty               NUMERIC(10, 1) NOT NULL CHECK (qty <> 0),
    from_location_id  BIGINT REFERENCES amn_locations (id) ON DELETE SET NULL,
    to_location_id    BIGINT REFERENCES amn_locations (id) ON DELETE SET NULL,
    to_floor          TEXT,
    staff_member_id   BIGINT,                               -- staff_members.id (legacy table, no FK)
    staff_name        TEXT,
    reason            TEXT NOT NULL DEFAULT 'REPLENISH' CHECK (reason IN (
                          'REPLENISH', 'GUEST_REQUEST', 'VIP', 'DAMAGE', 'RETURN')),
    request_id        BIGINT,                               -- staff_requests.id (legacy table, no FK)
    source            TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'CSV', 'API', 'DEMO')),
    import_batch_id   BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    -- CSV re-imports upsert on (batch, row) so a corrected file never doubles.
    import_row_no     INT,
    unit_cost_minor   BIGINT,                               -- cost on business_date, incl. excise (snapshot)
    currency          CHAR(3) NOT NULL,
    note              TEXT,
    created_by        BIGINT REFERENCES users (id) ON DELETE SET NULL,
    voided_at         TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS amn_issuance_day_idx ON amn_issuance (hotel_slug, business_date, item_id);
CREATE INDEX IF NOT EXISTS amn_issuance_staff_idx ON amn_issuance (hotel_slug, staff_name, business_date);
CREATE UNIQUE INDEX IF NOT EXISTS amn_issuance_import_row_idx
    ON amn_issuance (hotel_slug, import_batch_id, import_row_no) WHERE import_batch_id IS NOT NULL;

-- ─── Request lines ───
-- Which catalog items a service request consumed. Resolution order: the
-- concierge tool value (TOOL), an alias match on the request text (ALIAS), a
-- nightly LLM batch for unmatched housekeeping requests (LLM), a person
-- (MANUAL). A better method replaces a weaker one, never the reverse.

CREATE TABLE IF NOT EXISTS amn_request_lines (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    request_id    BIGINT NOT NULL,                          -- staff_requests.id
    item_id       BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    qty           INT NOT NULL CHECK (qty > 0),
    match_method  TEXT NOT NULL CHECK (match_method IN ('TOOL', 'ALIAS', 'LLM', 'MANUAL')),
    confidence    NUMERIC(4, 3) NOT NULL DEFAULT 1 CHECK (confidence >= 0 AND confidence <= 1),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, request_id, item_id)
);

-- Requests the matcher already looked at and could not link, so the nightly
-- LLM batch never pays twice for the same request.
CREATE TABLE IF NOT EXISTS amn_request_scans (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    request_id  BIGINT NOT NULL,
    method      TEXT NOT NULL CHECK (method IN ('ALIAS', 'LLM')),
    outcome     TEXT NOT NULL CHECK (outcome IN ('linked', 'no_match', 'error')),
    scanned_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, request_id, method)
);

-- ─── Daily variance rollup ───
-- One row per business date, item and floor ('' = not floor-tagged). A day
-- without an occupancy_daily row is not written at all: a missing day is
-- excluded, never zero-filled (02 spec 5.3 tests).

CREATE TABLE IF NOT EXISTS amn_variance_daily (
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    business_date    DATE NOT NULL,
    item_id          BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    floor            TEXT NOT NULL DEFAULT '',
    basis_count      NUMERIC(10, 1) NOT NULL DEFAULT 0,     -- occupied rooms, arrivals or guests
    expected_std     NUMERIC(12, 3) NOT NULL DEFAULT 0,     -- basis_count x std_qty
    requested        NUMERIC(10, 1) NOT NULL DEFAULT 0,     -- logged request units
    expected_total   NUMERIC(12, 3) NOT NULL DEFAULT 0,
    issued           NUMERIC(10, 1) NOT NULL DEFAULT 0,     -- net of returns, without VIP and DAMAGE
    issued_excluded  NUMERIC(10, 1) NOT NULL DEFAULT 0,     -- VIP and DAMAGE lines
    variance_units   NUMERIC(12, 3) NOT NULL DEFAULT 0,     -- issued - expected_total
    unit_cost_minor  BIGINT NOT NULL DEFAULT 0,
    issued_minor     BIGINT NOT NULL DEFAULT 0,
    expected_minor   BIGINT NOT NULL DEFAULT 0,
    variance_minor   BIGINT NOT NULL DEFAULT 0,
    currency         CHAR(3) NOT NULL,
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, business_date, item_id, floor)
);

-- Hotel-level day totals: occupied room-nights and the money side, so CPOR
-- is one division and never double counts ORN across items.
CREATE TABLE IF NOT EXISTS amn_daily_totals (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    business_date   DATE NOT NULL,
    orn             INT NOT NULL DEFAULT 0,
    arrivals        INT NOT NULL DEFAULT 0,
    issued_units    NUMERIC(12, 1) NOT NULL DEFAULT 0,     -- net, without VIP and DAMAGE
    issued_minor    BIGINT NOT NULL DEFAULT 0,
    expected_minor  BIGINT NOT NULL DEFAULT 0,
    requests        INT NOT NULL DEFAULT 0,                -- requested units linked to requests
    currency        CHAR(3) NOT NULL,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, business_date)
);

-- ─── Learned on-request rates ───
-- r = (req + prior x n0/100) / (ORN + n0) x 100: the hotel's own rate per
-- 100 occupied room-nights, shrunk toward the category prior while ORN is small.

CREATE TABLE IF NOT EXISTS amn_item_rates (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    item_id         BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    window_days     INT NOT NULL,
    requests        NUMERIC(10, 1) NOT NULL DEFAULT 0,
    orn             INT NOT NULL DEFAULT 0,
    prior_per_100   NUMERIC(8, 3) NOT NULL,
    n0              INT NOT NULL,
    rate_per_100    NUMERIC(8, 3) NOT NULL,
    raw_per_100     NUMERIC(8, 3),
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, item_id)
);

-- ─── Cohort benchmarks (system scope) ───
-- Units issued per 100 ORN by item category, per cohort and month. Published
-- only with hotels_count >= 3 and no hotel above 50% of the volume; below 5
-- hotels only the band is stored (p25/p50/p75 stay NULL) so no hotel's own
-- figure can be read back out.

CREATE TABLE IF NOT EXISTS amn_benchmarks (
    cohort_key     TEXT NOT NULL,                           -- AE-DXB-HOTEL_4
    item_category  TEXT NOT NULL,
    month          DATE NOT NULL,                           -- first of the month
    hotels_count   INT NOT NULL,
    max_share      NUMERIC(5, 4) NOT NULL,
    published      BOOLEAN NOT NULL,
    suppressed_reason TEXT,
    p25            NUMERIC(10, 2),
    p50            NUMERIC(10, 2),
    p75            NUMERIC(10, 2),
    band_low       NUMERIC(10, 2),
    band_high      NUMERIC(10, 2),
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (cohort_key, item_category, month)
);
