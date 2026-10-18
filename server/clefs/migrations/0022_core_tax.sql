-- 0022_core_tax: date-effective fee rules, per-hotel overrides and tax
-- profiles, and FX rates (Canon: core/tax; 02 spec 2.3; 04 spec D3).
--
-- Rules are date-effective because the Dubai 2026 relief dates are
-- unverified: an ANNOUNCED_UNVERIFIED rule applies to a hotel only while it has
-- an override row with apply = TRUE (the owner toggle), and every stay that an
-- unverified rule intersects carries a notice (the banner).
--
-- td_ledger and the Tourism Dirham detectors belong to Profit Guard (Canon).

CREATE TABLE IF NOT EXISTS fee_rules (
    id                      BIGSERIAL PRIMARY KEY,
    -- Stable key for seeded rows (idempotent seeding, tests, overrides by key).
    -- NULL for rules an owner configures.
    rule_key                TEXT UNIQUE,
    -- NULL: an emirate-wide rule seeded with its source. Set: an
    -- OWNER_CONFIGURED rule of that one hotel (other emirates, Abu Dhabi SC).
    hotel_slug              TEXT REFERENCES hotels (slug),
    emirate                 TEXT NOT NULL CHECK (emirate IN ('DXB', 'AUH', 'SHJ', 'AJM', 'RAK', 'FUJ', 'UAQ')),
    property_category       TEXT NOT NULL DEFAULT '*',
    fee_code                TEXT NOT NULL CHECK (fee_code IN ('SC', 'MF', 'TF', 'TD', 'VAT', 'DEST')),
    calc_mode               TEXT NOT NULL CHECK (calc_mode IN ('PERCENT', 'PER_ROOM_NIGHT', 'REMIT_DEFERRAL')),
    rate                    NUMERIC(18, 8) NOT NULL DEFAULT 0,   -- a ratio (0.07) or major units per unit-night (10)
    currency                CHAR(3) NOT NULL DEFAULT 'AED',      -- of a PER_ROOM_NIGHT rate
    base                    TEXT[] NOT NULL DEFAULT '{ROOM}',
    vat_applies             BOOLEAN NOT NULL DEFAULT TRUE,
    cap_consecutive_nights  INT,
    per_bedroom             BOOLEAN NOT NULL DEFAULT FALSE,
    effective_from          DATE NOT NULL,
    effective_to            DATE,
    rule_status             TEXT NOT NULL CHECK (rule_status IN ('VERIFIED', 'ANNOUNCED_UNVERIFIED', 'OWNER_CONFIGURED')),
    label                   TEXT NOT NULL,
    params                  JSONB NOT NULL DEFAULT '{}',        -- e.g. {"remit_deferral_months": 3, "group": "..."}
    source_url              TEXT,
    verified_by             TEXT,
    verified_at             TIMESTAMPTZ,
    created_by              BIGINT REFERENCES users (id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CHECK (rule_status <> 'OWNER_CONFIGURED' OR verified_by IS NOT NULL),
    -- Canon (UAE defaults): every seeded row carries its source.
    CHECK (hotel_slug IS NOT NULL OR source_url IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS fee_rules_lookup_idx ON fee_rules (emirate, fee_code, effective_from);
CREATE INDEX IF NOT EXISTS fee_rules_hotel_idx ON fee_rules (hotel_slug) WHERE hotel_slug IS NOT NULL;

CREATE TABLE IF NOT EXISTS hotel_tax_overrides (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    fee_rule_id      BIGINT NOT NULL REFERENCES fee_rules (id),
    apply            BOOLEAN NOT NULL,
    -- NULL = the rule's own dates. An owner narrows an announced window to the
    -- dates the authority finally confirms.
    effective_from   DATE,
    effective_to     DATE,
    note             TEXT,
    acknowledged_by  BIGINT REFERENCES users (id),
    acknowledged_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, fee_rule_id),
    CHECK (effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from)
);

-- The hotel's category and stacking convention, date-effective (02 spec
-- 3.13: a hotel can change convention mid-period).
CREATE TABLE IF NOT EXISTS hotel_tax_profiles (
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    effective_from     DATE NOT NULL,
    property_category  TEXT NOT NULL,
    convention         CHAR(1) NOT NULL DEFAULT 'A' CHECK (convention IN ('A', 'B', 'C')),
    trn                TEXT,
    td_per_bedroom     BOOLEAN,                               -- NULL: follow the fee rule
    updated_by         BIGINT REFERENCES users (id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, effective_from)
);

-- Units of `quote` per 1 `base` on `date`. clefs.core.money reads the latest
-- row on or before a date, in either direction, and falls back to the peg.
CREATE TABLE IF NOT EXISTS fx_rates (
    date        DATE NOT NULL,
    base        CHAR(3) NOT NULL,
    quote       CHAR(3) NOT NULL,
    rate        NUMERIC(18, 8) NOT NULL CHECK (rate > 0),
    source      TEXT NOT NULL CHECK (source IN ('PEG', 'ECB', 'MANUAL')),
    source_url  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (date, base, quote)
);
