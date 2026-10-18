-- 0021_core_stays: what the PMS says happened (Canon: core/stays).
--
-- PMS data is truth and OTA data is a claim (02 spec 0.2): OTA statement
-- lines live in Profit Guard's tables and are linked to these reservations
-- through reservation_matches only. room_types, hotel_rooms and channels were
-- created in 0006 (Wave 0a) and are reused as they are. occupancy_daily
-- replaces the foundation design's hotel_daily_stats.
--
-- Contacts (04 spec D1): there is no plaintext guest_contacts table. A PMS
-- phone or email is sealed (AES-256-GCM) and HMAC'd per hotel in
-- reservation_contacts; OTA-provided values lose both at purge_after, so no
-- hashed OTA identifier outlives the Booking.com 30-day window (Canon 19).

CREATE TABLE IF NOT EXISTS reservations (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    source_system       TEXT NOT NULL DEFAULT 'PMS' CHECK (source_system IN ('PMS', 'CM', 'MANUAL', 'DEMO')),
    pms_res_id          TEXT NOT NULL,
    channel_code        TEXT,
    ota_res_id          TEXT,
    ota_res_id_norm     TEXT,
    guest_name          TEXT,
    guest_name_norm     TEXT,                                -- transliterated, folded, particle-joined, token-sorted
    guest_country       TEXT,
    guest_language      TEXT,
    booked_at           TIMESTAMPTZ,
    check_in            DATE NOT NULL,
    check_out           DATE NOT NULL,
    actual_check_out    DATE,
    nights              INT NOT NULL,
    rooms_count         INT NOT NULL DEFAULT 1 CHECK (rooms_count > 0),
    room_type_code      TEXT,
    room_number         TEXT,
    rate_plan_code      TEXT,
    status              TEXT NOT NULL DEFAULT 'CONFIRMED'
                        CHECK (status IN ('CONFIRMED', 'CANCELLED', 'NO_SHOW', 'IN_HOUSE', 'CHECKED_OUT')),
    cancelled_at        TIMESTAMPTZ,
    checked_in_at       TIMESTAMPTZ,
    checked_out_at      TIMESTAMPTZ,
    cancel_fee_minor    BIGINT,
    no_show_fee_minor   BIGINT,
    collect_model       TEXT CHECK (collect_model IS NULL OR collect_model IN ('OTA_COLLECT', 'HOTEL_COLLECT')),
    payment_method      TEXT CHECK (payment_method IS NULL OR payment_method IN ('VCC', 'CARD', 'CASH', 'BANK', 'UPC', 'OTHER')),
    gross_minor         BIGINT,                              -- what the guest pays, taxes and fees included
    room_revenue_minor  BIGINT,                              -- R: room revenue net of SC, fees and VAT
    rate_basis          TEXT CHECK (rate_basis IN ('nightly', 'room_revenue', 'gross_decomposed', 'none')),
    currency            CHAR(3) NOT NULL,
    segment             TEXT CHECK (segment IS NULL OR segment IN ('LEISURE', 'CORPORATE', 'MICE', 'GROUP', 'CREW', 'LONG_STAY')),
    adults              SMALLINT,
    children            SMALLINT,
    -- 04 spec D1: the back-to-back chain for the same guest and room with no
    -- gap (the id of its first reservation). The Tourism Dirham night count and
    -- the journey planner both follow the chain.
    stay_chain_id       BIGINT,
    -- 04 spec D2: direct-booking attribution
    utm                 JSONB,
    promo_code          TEXT,
    booking_engine_ref  TEXT,
    import_batch_id     BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, source_system, pms_res_id),
    CHECK (check_out >= check_in),
    FOREIGN KEY (hotel_slug, channel_code) REFERENCES channels (hotel_slug, code)
);
CREATE INDEX IF NOT EXISTS reservations_check_in_idx ON reservations (hotel_slug, check_in);
CREATE INDEX IF NOT EXISTS reservations_check_out_idx ON reservations (hotel_slug, check_out);
CREATE INDEX IF NOT EXISTS reservations_ota_idx ON reservations (hotel_slug, ota_res_id_norm) WHERE ota_res_id_norm IS NOT NULL;
CREATE INDEX IF NOT EXISTS reservations_booked_idx ON reservations (hotel_slug, booked_at);
CREATE INDEX IF NOT EXISTS reservations_chain_idx ON reservations (hotel_slug, stay_chain_id);
CREATE INDEX IF NOT EXISTS reservations_be_ref_idx ON reservations (hotel_slug, booking_engine_ref) WHERE booking_engine_ref IS NOT NULL;

-- One row per stay date and room line. rate_minor is R per room per night;
-- the fee columns are that row's totals (all its rooms), derived from the
-- hotel's date-effective fee rules (core/tax) when the PMS gives no split.
CREATE TABLE IF NOT EXISTS reservation_nights (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL REFERENCES reservations (id) ON DELETE CASCADE,
    stay_date       DATE NOT NULL,
    room_seq        SMALLINT NOT NULL DEFAULT 1,
    room_number     TEXT,
    room_type_code  TEXT,
    rooms           SMALLINT NOT NULL DEFAULT 1 CHECK (rooms > 0),
    rate_minor      BIGINT NOT NULL DEFAULT 0,
    sc_minor        BIGINT NOT NULL DEFAULT 0,
    mf_minor        BIGINT NOT NULL DEFAULT 0,
    tf_minor        BIGINT NOT NULL DEFAULT 0,
    vat_minor       BIGINT NOT NULL DEFAULT 0,
    td_minor        BIGINT NOT NULL DEFAULT 0,
    currency        CHAR(3) NOT NULL,
    -- A night counts once it has started (checked in and not left): TD and
    -- occupancy only ever use stayed nights (a no-show never checked in).
    stayed          BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE (reservation_id, stay_date, room_seq)
);
CREATE INDEX IF NOT EXISTS reservation_nights_date_idx ON reservation_nights (hotel_slug, stay_date);

CREATE TABLE IF NOT EXISTS folio_charges (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id   BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    pms_res_id       TEXT,
    posted_date      DATE NOT NULL,
    stay_date        DATE,
    code             TEXT NOT NULL CHECK (code IN (
                         'ROOM', 'SC', 'MF', 'TF', 'VAT', 'TD', 'FNB', 'MINIBAR', 'NOSHOW_FEE', 'CXL_FEE', 'REFUND', 'OTHER')),
    description      TEXT,
    qty              NUMERIC(12, 3) NOT NULL DEFAULT 1,
    amount_minor     BIGINT NOT NULL,
    currency         CHAR(3) NOT NULL,
    is_reversal      BOOLEAN NOT NULL DEFAULT FALSE,
    pms_txn_id       TEXT NOT NULL,                          -- the PMS id, or "b<batch>:r<row>" when the export has none
    room_number      TEXT,
    import_batch_id  BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, pms_txn_id)
);
CREATE INDEX IF NOT EXISTS folio_charges_res_idx ON folio_charges (hotel_slug, reservation_id);
CREATE INDEX IF NOT EXISTS folio_charges_date_idx ON folio_charges (hotel_slug, posted_date);

CREATE TABLE IF NOT EXISTS occupancy_daily (
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    date                DATE NOT NULL,
    rooms_available     INT NOT NULL,                        -- out-of-order rooms stay in (04 spec A10: ARN)
    rooms_ooo           INT NOT NULL DEFAULT 0,
    rooms_occupied      INT NOT NULL DEFAULT 0,
    arrivals            INT NOT NULL DEFAULT 0,
    departures          INT NOT NULL DEFAULT 0,
    room_revenue_minor  BIGINT NOT NULL DEFAULT 0,
    currency            CHAR(3) NOT NULL,
    -- MANUAL wins over PMS, PMS over DERIVED (02 spec 2.1: manual overrides win).
    source              TEXT NOT NULL DEFAULT 'DERIVED' CHECK (source IN ('DERIVED', 'MANUAL', 'PMS', 'DEMO')),
    is_complete         BOOLEAN NOT NULL DEFAULT FALSE,     -- the business day has closed
    import_batch_id     BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    updated_by          BIGINT REFERENCES users (id),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, date)
);

CREATE TABLE IF NOT EXISTS occupancy_by_floor_daily (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    date            DATE NOT NULL,
    floor           TEXT NOT NULL,
    rooms_occupied  INT NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, date, floor)
);

-- reservations only hold current state, so pace needs a nightly photograph.
CREATE TABLE IF NOT EXISTS otb_snapshots (
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    snapshot_date       DATE NOT NULL,
    stay_date           DATE NOT NULL,
    rooms_otb           INT NOT NULL DEFAULT 0,
    revenue_otb_minor   BIGINT NOT NULL DEFAULT 0,
    currency            CHAR(3) NOT NULL,
    by_channel          JSONB NOT NULL DEFAULT '{}',         -- {channel_code: rooms}
    by_channel_revenue  JSONB NOT NULL DEFAULT '{}',         -- {channel_code: revenue minor}
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, snapshot_date, stay_date)
);

-- Seasonality baselines skip these ranges. hotel_slug NULL = every hotel.
CREATE TABLE IF NOT EXISTS baseline_exclusions (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT REFERENCES hotels (slug),
    from_date   DATE NOT NULL,
    to_date     DATE NOT NULL,
    reason      TEXT NOT NULL,
    created_by  BIGINT REFERENCES users (id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (to_date >= from_date)
);
CREATE UNIQUE INDEX IF NOT EXISTS baseline_exclusions_key ON baseline_exclusions ((COALESCE(hotel_slug, '')), from_date, to_date);
-- Canon: seasonality uses 2024-25 because the 2026 conflict distorted history.
INSERT INTO baseline_exclusions (hotel_slug, from_date, to_date, reason)
VALUES (NULL, '2026-03-01', '2026-07-31', 'conflict distortion')
ON CONFLICT DO NOTHING;

-- An OTA claim (statement, payout or VCC line in a module's table) linked to
-- the PMS stay it is about. line_id points into the owning module's table,
-- so there is no foreign key (core never references module tables).
CREATE TABLE IF NOT EXISTS reservation_matches (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    line_kind        TEXT NOT NULL DEFAULT 'ota_statement_line',
    statement_line_id BIGINT NOT NULL,
    ota_code         TEXT,
    reservation_id   BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    reservation_ids  BIGINT[] NOT NULL DEFAULT '{}',         -- a multi-room booking split into several PMS rows
    method           TEXT NOT NULL CHECK (method IN ('EXACT', 'FUZZY', 'MANUAL', 'NONE')),
    score            NUMERIC(4, 3),
    status           TEXT NOT NULL CHECK (status IN ('LINKED', 'REVIEW', 'UNMATCHED', 'REJECTED')),
    candidates       JSONB NOT NULL DEFAULT '[]',
    query            JSONB NOT NULL DEFAULT '{}',
    confirmed_by     BIGINT REFERENCES users (id),
    confirmed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, line_kind, statement_line_id)
);
CREATE INDEX IF NOT EXISTS reservation_matches_review_idx ON reservation_matches (hotel_slug, status) WHERE status = 'REVIEW';
CREATE INDEX IF NOT EXISTS reservation_matches_res_idx ON reservation_matches (reservation_id);

CREATE TABLE IF NOT EXISTS reservation_contacts (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id   BIGINT REFERENCES reservations (id) ON DELETE CASCADE,
    kind             TEXT NOT NULL CHECK (kind IN ('phone', 'email')),
    value_enc        BYTEA,                                  -- privacy.seal(value); NULL once purged
    value_hmac       BYTEA,                                  -- privacy.contact_hmac; NULL once an OTA-provenance row is purged
    provenance       TEXT NOT NULL CHECK (provenance IN ('pms_direct', 'pms_ota', 'ota_alias')),
    alias_domain     TEXT,
    usable_until     TIMESTAMPTZ,
    purge_after      TIMESTAMPTZ,
    import_batch_id  BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purged_at        TIMESTAMPTZ,
    CHECK (purged_at IS NOT NULL OR value_hmac IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS reservation_contacts_unique_idx
    ON reservation_contacts (reservation_id, kind, value_hmac) WHERE value_hmac IS NOT NULL;
CREATE INDEX IF NOT EXISTS reservation_contacts_hmac_idx ON reservation_contacts (hotel_slug, value_hmac) WHERE value_hmac IS NOT NULL;
CREATE INDEX IF NOT EXISTS reservation_contacts_purge_idx ON reservation_contacts (purge_after)
    WHERE purged_at IS NULL;

-- The stay-event outbox (04 spec D1; names and payload frozen in
-- clefs.core.stay_events). tx_id lets the dispatcher read only events whose
-- writing transaction has finished, so a long import that commits after a
-- short one never has its lower ids skipped by a cursor that moved past them.
CREATE TABLE IF NOT EXISTS stay_events (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL REFERENCES reservations (id) ON DELETE CASCADE,
    event           TEXT NOT NULL CHECK (event IN (
                        'created', 'dates_changed', 'cancelled', 'no_show', 'checked_in', 'checked_out', 'contact_changed')),
    occurred_at     TIMESTAMPTZ NOT NULL,
    changes         JSONB NOT NULL DEFAULT '{}',
    tx_id           XID8 NOT NULL DEFAULT pg_current_xact_id(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS stay_events_tx_idx ON stay_events (tx_id, id);
CREATE INDEX IF NOT EXISTS stay_events_res_idx ON stay_events (hotel_slug, reservation_id, id);

-- One cursor per consumer ("guests.journeys", ...): the (tx_id, id) of the
-- last event it acknowledged.
CREATE TABLE IF NOT EXISTS stay_event_cursors (
    consumer        TEXT PRIMARY KEY,
    last_tx_id      XID8 NOT NULL DEFAULT '0'::xid8,
    last_id         BIGINT NOT NULL DEFAULT 0,
    failures        INT NOT NULL DEFAULT 0,               -- consecutive failures on the event after the cursor
    last_error      TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Events a consumer failed on repeatedly and was moved past (dead letters).
CREATE TABLE IF NOT EXISTS stay_event_failures (
    id              BIGSERIAL PRIMARY KEY,
    consumer        TEXT NOT NULL,
    event_id        BIGINT NOT NULL,
    error           TEXT,
    attempts        INT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (consumer, event_id)
);
