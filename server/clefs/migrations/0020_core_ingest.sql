-- 0020_core_ingest: import batches, staged rows and confirmed column mappings (core/ingest).
--
-- Canon: core owns ingestion. A file (already stored through the Files
-- contract) becomes one import_batches row; its parsed rows are staged in
-- import_rows with raw values next to the canonical ones, so a person can see
-- exactly what a mapping did before anything reaches the domain tables. The
-- commit step hands the canonical rows to the handler registered for the
-- batch kind (core/stays for PMS files, Profit Guard for OTA files, Finance
-- for P&Ls; 04 spec D4).
--
-- hotel_slug is NULL only for the public "audit one OTA statement" sandbox
-- (02 spec 3.11), which has no hotel yet.

CREATE TABLE IF NOT EXISTS mapping_templates (
    id                  BIGSERIAL PRIMARY KEY,
    -- NULL: a global template every hotel may use (seeded by Clefs staff)
    hotel_slug          TEXT REFERENCES hotels (slug),
    kind                TEXT NOT NULL,
    source_code         TEXT NOT NULL,
    -- sha1 of the sorted normalized headers: one export layout = one template
    header_fingerprint  TEXT NOT NULL,
    headers             JSONB NOT NULL DEFAULT '[]',
    mapping             JSONB NOT NULL,                     -- {canonical field: source header}
    transforms          JSONB NOT NULL DEFAULT '{}',        -- {canonical field: {date_fmt, decimal_sep, sign_flip, pct_to_ratio}}
    origin              TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('ai', 'manual', 'alias')),
    confirmed_by        BIGINT REFERENCES users (id),
    confirmed_at        TIMESTAMPTZ,
    version             INT NOT NULL DEFAULT 1,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    uses                INT NOT NULL DEFAULT 0,
    last_used_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS mapping_templates_version_idx
    ON mapping_templates ((COALESCE(hotel_slug, '')), source_code, header_fingerprint, version);
CREATE INDEX IF NOT EXISTS mapping_templates_lookup_idx
    ON mapping_templates (source_code, header_fingerprint) WHERE active;

CREATE TABLE IF NOT EXISTS import_batches (
    id                    BIGSERIAL PRIMARY KEY,
    hotel_slug            TEXT REFERENCES hotels (slug),
    kind                  TEXT NOT NULL CHECK (kind IN (
                              'OTA_STATEMENT', 'OTA_PAYOUT', 'VCC_REPORT', 'CREDIT_NOTE', 'PMS_RESERVATIONS',
                              'PMS_FOLIO', 'BANK', 'CONTRACT_PDF', 'COMP_RATES', 'ISSUANCE', 'OCCUPANCY', 'ICS',
                              'SUPPLIER_INVOICE', 'PL_STATEMENT', 'TRIAL_BALANCE', 'BOOKING_ENGINE_ATTRIBUTION')),
    source_code           TEXT,                              -- an alias map key (booking_res_statement, pms_folio, ...)
    ota_code              TEXT,                              -- BOOKING | AGODA | EXPEDIA when the source is an OTA
    file_id               UUID REFERENCES files (id),
    filename              TEXT,
    sha256                TEXT NOT NULL,
    file_format           TEXT CHECK (file_format IN ('csv', 'xlsx', 'xls', 'pdf', 'ics', 'xml', 'json', 'other')),
    encoding              TEXT,
    status                TEXT NOT NULL DEFAULT 'UPLOADED' CHECK (status IN (
                              'UPLOADED', 'MAPPING_REQUIRED', 'EXTRACTING', 'REVIEW', 'COMMITTED', 'SUPERSEDED', 'FAILED')),
    mapping_template_id   BIGINT REFERENCES mapping_templates (id),
    mapping               JSONB NOT NULL DEFAULT '{}',       -- the mapping in use: {canonical: header}
    transforms            JSONB NOT NULL DEFAULT '{}',
    mapping_source        TEXT CHECK (mapping_source IN ('template', 'alias', 'ai', 'manual')),
    mapping_suggestion    JSONB,                             -- an AI proposal waiting for a person to confirm it
    headers               JSONB NOT NULL DEFAULT '[]',
    header_row            INT,
    header_fingerprint    TEXT,
    sheet_name            TEXT,
    period_start          DATE,
    period_end            DATE,
    period_month          DATE,                              -- TRIAL_BALANCE: the month given at upload
    doc_no                TEXT,
    currency              CHAR(3),
    row_count             INT NOT NULL DEFAULT 0,
    stats                 JSONB NOT NULL DEFAULT '{}',
    -- PDF (and CSV "Total" row) gate: the lines must add up to the printed total
    -- within AED 1 before a commit, unless a person overrides it (audited).
    printed_total_minor   BIGINT,
    parsed_total_minor    BIGINT,
    total_field           TEXT,
    gate_status           TEXT CHECK (gate_status IN ('ok', 'mismatch', 'missing', 'n/a')),
    gate_override_by      BIGINT REFERENCES users (id),
    gate_override_reason  TEXT,
    gate_override_at      TIMESTAMPTZ,
    via                   TEXT NOT NULL DEFAULT 'WEB' CHECK (via IN ('WEB', 'EMAIL', 'API', 'DEMO', 'PUBLIC_AUDIT')),
    auto_commit           BOOLEAN NOT NULL DEFAULT FALSE,
    options               JSONB NOT NULL DEFAULT '{}',
    uploaded_by           BIGINT REFERENCES users (id),
    inbound_message_id    BIGINT,
    supersedes_batch_id   BIGINT REFERENCES import_batches (id),
    superseded_by         BIGINT REFERENCES import_batches (id),
    committed_by          BIGINT REFERENCES users (id),
    committed_at          TIMESTAMPTZ,
    commit_result         JSONB,
    errors                JSONB NOT NULL DEFAULT '[]',
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- The same bytes uploaded twice to one hotel are one batch (a forwarded email
-- and a manual upload of the same statement never double-count).
CREATE UNIQUE INDEX IF NOT EXISTS import_batches_sha_idx ON import_batches ((COALESCE(hotel_slug, '')), sha256);
CREATE INDEX IF NOT EXISTS import_batches_hotel_idx ON import_batches (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS import_batches_open_idx ON import_batches (hotel_slug, status)
    WHERE status IN ('UPLOADED', 'MAPPING_REQUIRED', 'EXTRACTING', 'REVIEW');

CREATE TABLE IF NOT EXISTS import_rows (
    id          BIGSERIAL PRIMARY KEY,
    batch_id    BIGINT NOT NULL REFERENCES import_batches (id) ON DELETE CASCADE,
    row_no      INT NOT NULL,
    -- raw: {header: value} as read. Contact columns are redacted once the batch
    -- commits (04 spec D1): the sealed copy in reservation_contacts is the only one.
    raw         JSONB NOT NULL DEFAULT '{}',
    canonical   JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'OK' CHECK (status IN ('OK', 'WARN', 'ERROR', 'DUP', 'SKIP')),
    messages    TEXT[] NOT NULL DEFAULT '{}',
    page        INT,                                          -- PDF extraction: page reference
    line_ref    TEXT,                                         -- PDF extraction: line reference on that page
    edited_by   BIGINT REFERENCES users (id),
    edited_at   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (batch_id, row_no)
);
