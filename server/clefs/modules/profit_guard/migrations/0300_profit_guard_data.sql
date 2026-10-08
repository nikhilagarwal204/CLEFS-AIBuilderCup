-- Profit Guard data layer (02 spec 3.1, Canon 2 and K2): contracts, programs,
-- channel configuration, owner policies, OTA statements and their lines,
-- payouts, bank credits, virtual cards and rate evidence.
--
-- PMS data is truth and OTA data is a claim (02 spec 0.2): nothing here is
-- merged into core/stays; a line meets its reservation through
-- core reservation_matches. Money is BIGINT minor units + CHAR(3) currency,
-- rates NUMERIC(18,8), instants TIMESTAMPTZ (Canon 2, 3).

-- ─── Contracts (AI extraction, then a person confirms) ───

CREATE TABLE IF NOT EXISTS pfg_ota_contracts (
    id                    BIGSERIAL PRIMARY KEY,
    hotel_slug            TEXT NOT NULL REFERENCES hotels (slug),
    ota_code              TEXT NOT NULL,
    entity_name           TEXT,
    entity_country        CHAR(2),
    effective_from        DATE NOT NULL,
    effective_to          DATE,
    -- Percent (15.000 = 15%), the contract's own wording.
    base_commission_pct   NUMERIC(6, 3) NOT NULL CHECK (base_commission_pct >= 0 AND base_commission_pct <= 100),
    commission_base_rule  TEXT NOT NULL
                          CHECK (commission_base_rule IN ('ROOM_INCL_VAT', 'RSR_TAX_INCL', 'NET_RATE', 'ROOM_EXCL_TAX')),
    excluded_charge_types TEXT[] NOT NULL DEFAULT '{}',   -- MUNICIPALITYFEE, TOURISMFEE (Booking charge types)
    collect_models        TEXT[] NOT NULL DEFAULT '{}',   -- OTA_COLLECT, HOTEL_COLLECT
    dispute_rules         JSONB NOT NULL DEFAULT '[]',
    vcc_rules             JSONB NOT NULL DEFAULT '{}',
    clauses               JSONB NOT NULL DEFAULT '[]',    -- [{ref, text, page, field}]
    source_file_id        UUID REFERENCES files (id) ON DELETE SET NULL,
    import_batch_id       BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    extracted_by          TEXT NOT NULL DEFAULT 'MANUAL' CHECK (extracted_by IN ('LLM', 'MANUAL')),
    -- The model's raw proposal (with its per-field citations and confidence),
    -- kept beside the confirmed values so a reviewer sees what changed.
    extraction            JSONB,
    status                TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'CONFIRMED', 'RETIRED')),
    note                  TEXT,
    created_by            BIGINT REFERENCES users (id) ON DELETE SET NULL,
    confirmed_by          BIGINT REFERENCES users (id) ON DELETE SET NULL,
    confirmed_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (effective_to IS NULL OR effective_to >= effective_from),
    -- Confirmed terms are the only ones any calculation reads.
    CHECK (status <> 'CONFIRMED' OR confirmed_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS pfg_ota_contracts_lookup_idx
    ON pfg_ota_contracts (hotel_slug, ota_code, effective_from DESC) WHERE status = 'CONFIRMED';

-- ─── Programs (Genius, Preferred, Visibility Booster, AGX, ...) ───

CREATE TABLE IF NOT EXISTS pfg_ota_programs (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    ota_code          TEXT NOT NULL,
    program           TEXT NOT NULL CHECK (program IN ('GENIUS', 'MOBILE', 'COUNTRY', 'EARLY_BOOKER', 'PREFERRED',
                                                       'PREFERRED_PLUS', 'VISIBILITY_BOOSTER', 'AGODA_VIP', 'AGX',
                                                       'OTHER')),
    kind              TEXT NOT NULL CHECK (kind IN ('DISCOUNT', 'COMMISSION_UPLIFT', 'PER_BOOKING_COST')),
    pct               NUMERIC(7, 3) CHECK (pct IS NULL OR (pct >= 0 AND pct <= 100)),
    fixed_minor       BIGINT CHECK (fixed_minor IS NULL OR fixed_minor >= 0),
    currency          CHAR(3),
    booker_countries  TEXT[] NOT NULL DEFAULT '{}',   -- empty = every booker country
    stay_from         DATE,
    stay_to           DATE,
    rate_plans        TEXT[] NOT NULL DEFAULT '{}',   -- empty = every rate plan
    stackable         BOOLEAN NOT NULL DEFAULT TRUE,
    source            TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'EXTRANET_EXPORT')),
    label             TEXT,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    created_by        BIGINT REFERENCES users (id) ON DELETE SET NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (stay_to IS NULL OR stay_from IS NULL OR stay_to >= stay_from),
    CHECK (pct IS NOT NULL OR fixed_minor IS NOT NULL),
    CHECK (fixed_minor IS NULL OR currency IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS pfg_ota_programs_hotel_idx ON pfg_ota_programs (hotel_slug, ota_code) WHERE is_active;

-- ─── Channel configuration (date-effective) ───

-- Whether the property configured the municipality fee and Tourism Dirham as
-- charges in the OTA extranet decides Booking's commission base (Canon K2),
-- so each change is a new dated row rather than an edit.
CREATE TABLE IF NOT EXISTS pfg_channel_config (
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    ota_code                 TEXT NOT NULL,
    configured_from          DATE NOT NULL,
    mf_configured_as_charge  BOOLEAN,
    td_configured_as_charge  BOOLEAN,
    card_fee_pct             NUMERIC(7, 4) CHECK (card_fee_pct IS NULL OR (card_fee_pct >= 0 AND card_fee_pct <= 100)),
    payments_fee_pct         NUMERIC(7, 4) CHECK (payments_fee_pct IS NULL OR (payments_fee_pct >= 0 AND payments_fee_pct <= 100)),
    booking_engine_fee_pct   NUMERIC(7, 4) CHECK (booking_engine_fee_pct IS NULL OR (booking_engine_fee_pct >= 0 AND booking_engine_fee_pct <= 100)),
    note                     TEXT,
    updated_by               BIGINT REFERENCES users (id) ON DELETE SET NULL,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, ota_code, configured_from)
);

-- ─── Owner policies (one row per hotel; absent = the defaults) ───

CREATE TABLE IF NOT EXISTS pfg_owner_policies (
    hotel_slug              TEXT PRIMARY KEY REFERENCES hotels (slug),
    max_total_discount_pct  NUMERIC(6, 3) NOT NULL DEFAULT 25 CHECK (max_total_discount_pct BETWEEN 0 AND 100),
    parity_tolerance_pct    NUMERIC(6, 3) NOT NULL DEFAULT 1.0 CHECK (parity_tolerance_pct BETWEEN 0 AND 100),
    min_finding_minor       BIGINT NOT NULL DEFAULT 2500 CHECK (min_finding_minor >= 0),
    confidence_floor        NUMERIC(4, 3) NOT NULL DEFAULT 0.5 CHECK (confidence_floor BETWEEN 0 AND 1),
    -- 04 spec 6.4: the single source of the service-charge treatment.
    treat_sc_as_revenue     BOOLEAN NOT NULL DEFAULT TRUE,
    -- NULL: the OTA with the most room nights over the trailing 90 days.
    reference_ota           TEXT,
    updated_by              BIGINT REFERENCES users (id) ON DELETE SET NULL,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── OTA statements (commission invoices, reservation statements, credit notes) ───

CREATE TABLE IF NOT EXISTS pfg_ota_statements (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    ota_code        TEXT NOT NULL,
    doc_kind        TEXT NOT NULL CHECK (doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT', 'PAYOUT_STATEMENT',
                                                      'CREDIT_NOTE', 'DEBIT_NOTE')),
    -- The printed document number, else one derived from source and period:
    -- a corrected re-upload of the same document upserts this row.
    doc_no          TEXT NOT NULL,
    doc_date        DATE,
    related_doc_no  TEXT,
    period_start    DATE,
    period_end      DATE,
    currency        CHAR(3) NOT NULL,
    total_minor     BIGINT NOT NULL DEFAULT 0,        -- sum of line commission (credit note: credited amount)
    line_count      INT NOT NULL DEFAULT 0,
    batch_id        BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    source_code     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, ota_code, doc_no)
);

CREATE TABLE IF NOT EXISTS pfg_ota_statement_lines (
    id                   BIGSERIAL PRIMARY KEY,
    statement_id         BIGINT NOT NULL REFERENCES pfg_ota_statements (id) ON DELETE CASCADE,
    hotel_slug           TEXT NOT NULL REFERENCES hotels (slug),
    ota_code             TEXT NOT NULL,
    -- Natural key inside the document: normalised id + occurrence, so a
    -- modified booking's second line is its own row and a re-import upserts.
    line_key             TEXT NOT NULL,
    ota_res_id           TEXT,
    ota_res_id_norm      TEXT,
    guest_name           TEXT,
    check_in             DATE,
    check_out            DATE,
    room_nights          INT,
    rooms                INT,
    line_status          TEXT NOT NULL DEFAULT 'OK'
                         CHECK (line_status IN ('OK', 'NO_SHOW', 'CANCELLED', 'MODIFIED', 'ADJUSTMENT')),
    collect_model        TEXT,
    gross_minor          BIGINT,
    commissionable_minor BIGINT,
    commission_rate      NUMERIC(18, 8),              -- a ratio (0.15), as core/ingest reads a pct column
    commission_minor     BIGINT,
    charges              JSONB NOT NULL DEFAULT '[]', -- [{type, amount_minor, is_already_collected_from_guest, is_included_total_partner_payout}]
    booker_country       TEXT,
    programs             TEXT[] NOT NULL DEFAULT '{}',
    rate_plan            TEXT,
    room_type            TEXT,
    booked_at            DATE,
    cancelled_at         DATE,
    remarks              TEXT,
    currency             CHAR(3) NOT NULL,
    fx_rate              NUMERIC(18, 8),              -- units of the hotel currency per 1 line currency
    batch_id             BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    import_row_id        BIGINT,
    page                 INT,
    -- Denormalised from core reservation_matches for the detectors' joins.
    reservation_id       BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    match_status         TEXT,
    match_score          NUMERIC(4, 3),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (statement_id, line_key)
);
CREATE INDEX IF NOT EXISTS pfg_ota_statement_lines_res_idx ON pfg_ota_statement_lines (hotel_slug, ota_code, ota_res_id_norm);
CREATE INDEX IF NOT EXISTS pfg_ota_statement_lines_stay_idx ON pfg_ota_statement_lines (hotel_slug, check_in);

-- ─── Payouts and bank credits ───

CREATE TABLE IF NOT EXISTS pfg_ota_payouts (
    id                       BIGSERIAL PRIMARY KEY,
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    ota_code                 TEXT NOT NULL,
    payout_ref               TEXT NOT NULL,
    payout_date              DATE,
    payout_type              TEXT CHECK (payout_type IS NULL OR payout_type IN ('GROSS', 'NET')),
    status                   TEXT,
    currency                 CHAR(3) NOT NULL,
    amount_minor             BIGINT NOT NULL DEFAULT 0,   -- sum of partner_payout over the lines
    line_count               INT NOT NULL DEFAULT 0,
    bank_transfer_reference  TEXT,
    bank_credit_id           BIGINT,
    batch_id                 BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, ota_code, payout_ref)
);

CREATE TABLE IF NOT EXISTS pfg_ota_payout_lines (
    id                       BIGSERIAL PRIMARY KEY,
    payout_id                BIGINT NOT NULL REFERENCES pfg_ota_payouts (id) ON DELETE CASCADE,
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    ota_code                 TEXT NOT NULL,
    line_key                 TEXT NOT NULL,
    ota_res_id               TEXT,
    ota_res_id_norm          TEXT,
    guest_name               TEXT,
    check_in                 DATE,
    check_out                DATE,
    total_amount_paid_minor  BIGINT,
    commissionable_minor     BIGINT,
    commission_minor         BIGINT,
    charges_minor            BIGINT,
    partner_payout_minor     BIGINT NOT NULL,
    line_kind                TEXT NOT NULL DEFAULT 'BOOKING'
                             CHECK (line_kind IN ('BOOKING', 'REFUND', 'CHARGEBACK', 'ADJUSTMENT')),
    event_date               DATE,
    currency                 CHAR(3) NOT NULL,
    batch_id                 BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    import_row_id            BIGINT,
    reservation_id           BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    match_status             TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (payout_id, line_key)
);
CREATE INDEX IF NOT EXISTS pfg_ota_payout_lines_res_idx ON pfg_ota_payout_lines (hotel_slug, ota_code, ota_res_id_norm);

CREATE TABLE IF NOT EXISTS pfg_bank_credits (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    -- value_date|amount|reference digest: the same statement row imported
    -- twice (overlapping bank exports) stays one credit.
    line_key            TEXT NOT NULL,
    value_date          DATE NOT NULL,
    currency            CHAR(3) NOT NULL,
    amount_minor        BIGINT NOT NULL,
    hotel_currency      CHAR(3) NOT NULL,
    hotel_amount_minor  BIGINT,                      -- NULL when no rate was available
    reference           TEXT,
    counterparty        TEXT,
    description         TEXT,
    matched_payout_id   BIGINT REFERENCES pfg_ota_payouts (id) ON DELETE SET NULL,
    batch_id            BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    import_row_id       BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, line_key)
);
CREATE INDEX IF NOT EXISTS pfg_bank_credits_date_idx ON pfg_bank_credits (hotel_slug, value_date);

-- ─── Virtual cards (VCC / EVC / UPC) ───

CREATE TABLE IF NOT EXISTS pfg_virtual_cards (
    id                     BIGSERIAL PRIMARY KEY,
    hotel_slug             TEXT NOT NULL REFERENCES hotels (slug),
    ota_code               TEXT NOT NULL,
    vcc_id                 TEXT NOT NULL,             -- the OTA's card reference, never a card number
    ota_res_id             TEXT,
    ota_res_id_norm        TEXT,
    reservation_id         BIGINT REFERENCES reservations (id) ON DELETE SET NULL,
    activation_date        DATE,
    expiration_date        DATE NOT NULL,
    -- min(expiry, contract rule from the stay's check-out); the L8/L9 clock.
    charge_deadline        DATE NOT NULL,
    deadline_basis         TEXT,
    initial_minor          BIGINT,
    current_balance_minor  BIGINT NOT NULL,
    currency               CHAR(3) NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'AVAILABLE'
                           CHECK (status IN ('AVAILABLE', 'FUNDED', 'NOT_LOADED', 'PARTIALLY_CHARGED', 'FULLY_CHARGED',
                                             'CANCELLED', 'EXPIRED')),
    charged_at             TIMESTAMPTZ,
    charged_by             BIGINT REFERENCES users (id) ON DELETE SET NULL,
    last_seen_batch_id     BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    import_row_id          BIGINT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, ota_code, vcc_id)
);
CREATE INDEX IF NOT EXISTS pfg_virtual_cards_deadline_idx ON pfg_virtual_cards (hotel_slug, charge_deadline)
    WHERE current_balance_minor > 0;

-- ─── Rate evidence (parity observations) ───

CREATE TABLE IF NOT EXISTS pfg_rate_evidence (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    captured_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    channel             TEXT NOT NULL,              -- BOOKING, AGODA, EXPEDIA, DIRECT_WEB, GOOGLE_HOTELS, ...
    stay_date           DATE NOT NULL,
    los                 INT NOT NULL DEFAULT 1 CHECK (los >= 1),
    adults              INT NOT NULL DEFAULT 2 CHECK (adults >= 1),
    room_label          TEXT,
    plan_label          TEXT,
    price_minor         BIGINT NOT NULL CHECK (price_minor >= 0),
    currency            CHAR(3) NOT NULL,
    taxes_included      BOOLEAN,
    device              TEXT NOT NULL DEFAULT 'DESKTOP' CHECK (device IN ('DESKTOP', 'MOBILE')),
    login_state         TEXT NOT NULL DEFAULT 'LOGGED_OUT' CHECK (login_state IN ('LOGGED_OUT', 'LOGGED_IN', 'MEMBER')),
    booker_country      TEXT,
    search_url          TEXT,
    screenshot_file_id  UUID REFERENCES files (id) ON DELETE SET NULL,
    source              TEXT NOT NULL DEFAULT 'MANUAL' CHECK (source IN ('MANUAL', 'SERPAPI', 'FEED')),
    source_module       TEXT,                        -- 'visibility' for Google Hotels parity rows
    -- date|los|adults|room|device|login: observations that may be compared.
    comparable_key      TEXT NOT NULL,
    -- 04 spec 7.2: Visibility's rows go in with FALSE so L7 never signals a gap twice.
    emit_signal         BOOLEAN NOT NULL DEFAULT TRUE,
    created_by          BIGINT REFERENCES users (id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS pfg_rate_evidence_key_idx ON pfg_rate_evidence (hotel_slug, stay_date, comparable_key);
