-- 0500_amenities_catalog: the amenity catalog (Canon 8: amn_items), its unit
-- cost history, stock locations, par levels and stock counts (02 spec 5.1).
--
-- Why these shapes differ from the spec sketch: the tenant key is hotel_slug
-- (Canon 1), costs and prices are BIGINT minor units + CHAR(3) currency
-- (Canon 2), instants are TIMESTAMPTZ (Canon 3) and every table carries the
-- amn_ prefix. The frozen amenities.api shape (id, key, name, request_type,
-- max_qty, chargeable, price, currency) needs key, request_type_key, max_qty,
-- chargeable and price columns the 02 sketch did not have (03 spec 1.1).

-- ─── Items ───

CREATE TABLE IF NOT EXISTS amn_items (
    id                     BIGSERIAL PRIMARY KEY,
    hotel_slug             TEXT NOT NULL REFERENCES hotels (slug),
    -- Stable key tools, CSV imports and the seed name ("DENTAL_KIT").
    key                    TEXT NOT NULL CHECK (key ~ '^[A-Z0-9][A-Z0-9_]{0,47}$'),
    sku                    TEXT,
    name_en                TEXT NOT NULL,
    name_ar                TEXT,
    category               TEXT NOT NULL DEFAULT 'OTHER' CHECK (category IN (
                               'BATH', 'DENTAL', 'SHAVING', 'SLIPPERS', 'WATER', 'TEA_COFFEE', 'MINIBAR', 'OTHER')),
    uom                    TEXT NOT NULL DEFAULT 'unit',
    -- How expected use is counted: per occupied room-night, per arrival, per
    -- guest-night, or only when a guest asks (ON_REQUEST, std_qty 0).
    basis                  TEXT NOT NULL DEFAULT 'ON_REQUEST' CHECK (basis IN (
                               'PER_OCCUPIED_ROOM_NIGHT', 'PER_ARRIVAL', 'PER_GUEST_NIGHT', 'ON_REQUEST')),
    std_qty                NUMERIC(6, 3) NOT NULL DEFAULT 0 CHECK (std_qty >= 0),
    -- Current unit cost including excise; amn_item_costs keeps the history
    -- and wins for any date it covers.
    unit_cost_minor        BIGINT NOT NULL DEFAULT 0 CHECK (unit_cost_minor >= 0),
    currency               CHAR(3) NOT NULL,
    excise_category        TEXT NOT NULL DEFAULT 'NONE' CHECK (excise_category IN ('NONE', 'SWEETENED', 'ENERGY')),
    -- Dubai single-use plastic ban (research/uae/ops-benchmarks.md section 2).
    -- sup_ban_ref names the ban phase that covers the item ("DXB-2026").
    is_single_use_plastic  BOOLEAN NOT NULL DEFAULT FALSE,
    sup_ban_ref            TEXT,
    aliases                TEXT[] NOT NULL DEFAULT '{}',
    request_type_key       TEXT,
    max_qty                INT NOT NULL DEFAULT 4 CHECK (max_qty > 0),
    chargeable             BOOLEAN NOT NULL DEFAULT FALSE,
    price_minor            BIGINT CHECK (price_minor IS NULL OR price_minor >= 0),
    -- A seeded row stays flagged until the owner confirms cost and standard.
    needs_review           BOOLEAN NOT NULL DEFAULT FALSE,
    source                 TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'SEED', 'CSV', 'DEMO')),
    source_url             TEXT,
    active                 BOOLEAN NOT NULL DEFAULT TRUE,
    sort                   INT NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, key)
);
CREATE INDEX IF NOT EXISTS amn_items_hotel_idx ON amn_items (hotel_slug, active, sort);

-- ─── Unit cost history ───
-- The cost that applies on day d is the latest row with effective_from <= d.
-- unit_cost_minor already includes excise (excise_minor is the part of it
-- that is excise), so variance in money needs no second lookup.

CREATE TABLE IF NOT EXISTS amn_item_costs (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    item_id          BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    effective_from   DATE NOT NULL,
    unit_cost_minor  BIGINT NOT NULL CHECK (unit_cost_minor >= 0),
    excise_minor     BIGINT NOT NULL DEFAULT 0 CHECK (excise_minor >= 0),
    currency         CHAR(3) NOT NULL,
    source           TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('SUPPLIER_INVOICE', 'MANUAL', 'SEED', 'DEMO')),
    invoice_ref      TEXT,
    import_batch_id  BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    created_by       BIGINT REFERENCES users (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (item_id, effective_from)
);
CREATE INDEX IF NOT EXISTS amn_item_costs_lookup_idx ON amn_item_costs (hotel_slug, item_id, effective_from DESC);

-- ─── Stock locations ───

CREATE TABLE IF NOT EXISTS amn_locations (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    kind        TEXT NOT NULL CHECK (kind IN ('STORE', 'FLOOR_CLOSET', 'CART', 'DEPARTMENT', 'MINIBAR')),
    floor       TEXT,
    name        TEXT NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, name)
);

-- ─── Par levels ───
-- par_qty = daily_usage x days_of_supply + safety_stock (02 spec 5.2).
-- computed keeps the inputs of the last recompute so the console can show
-- why the number moved; a manual par_qty sticks while `pinned` is true.

CREATE TABLE IF NOT EXISTS amn_par_levels (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    location_id     BIGINT NOT NULL REFERENCES amn_locations (id) ON DELETE CASCADE,
    item_id         BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    par_qty         NUMERIC(10, 1) NOT NULL DEFAULT 0 CHECK (par_qty >= 0),
    days_of_supply  NUMERIC(5, 1) NOT NULL DEFAULT 2 CHECK (days_of_supply > 0),
    safety_stock    NUMERIC(10, 1) NOT NULL DEFAULT 0 CHECK (safety_stock >= 0),
    lead_days       NUMERIC(5, 1) NOT NULL DEFAULT 1 CHECK (lead_days > 0),
    pinned          BOOLEAN NOT NULL DEFAULT FALSE,
    computed        JSONB NOT NULL DEFAULT '{}',
    computed_at     TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (location_id, item_id)
);
CREATE INDEX IF NOT EXISTS amn_par_levels_hotel_idx ON amn_par_levels (hotel_slug);

-- ─── Stock counts ───
-- With two counts, consumption at a location is exact:
-- opening + received - sent out - closing.

CREATE TABLE IF NOT EXISTS amn_stock_counts (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    location_id  BIGINT NOT NULL REFERENCES amn_locations (id) ON DELETE CASCADE,
    item_id      BIGINT NOT NULL REFERENCES amn_items (id) ON DELETE CASCADE,
    counted_at   TIMESTAMPTZ NOT NULL,
    qty          NUMERIC(10, 1) NOT NULL CHECK (qty >= 0),
    counted_by   TEXT,
    counted_by_user_id BIGINT REFERENCES users (id) ON DELETE SET NULL,
    note         TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS amn_stock_counts_idx ON amn_stock_counts (hotel_slug, location_id, item_id, counted_at DESC);

-- Which hotels already received the UAE default catalog (seeded once, so an
-- owner who deletes a seeded item does not see it come back).
CREATE TABLE IF NOT EXISTS amn_seed_runs (
    hotel_slug  TEXT PRIMARY KEY REFERENCES hotels (slug),
    version     INT NOT NULL,
    items       INT NOT NULL DEFAULT 0,
    seeded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
