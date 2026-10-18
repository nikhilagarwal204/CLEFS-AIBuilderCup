-- 0800_trust_privacy: audit-chain verification state, DSR queue, retention
-- policies, the deletion log, subject restriction flags and tenant exports
-- (03 spec 5.1 and 5.3). audit_log, consents and consent_texts are foundation
-- tables (Canon 4); nothing here alters them.

-- ─── Audit chain verification ───
-- One row per hotel chain. The verifier walks audit_log incrementally from
-- (last_id, last_hash); the retention purge moves the anchor forward when it
-- removes the oldest rows, so the first surviving row's prev_hash still has
-- something to be checked against.
CREATE TABLE IF NOT EXISTS trs_audit_chain_state (
    hotel_slug      TEXT PRIMARY KEY REFERENCES hotels (slug),
    last_id         BIGINT,                      -- newest row verified good
    last_hash       BYTEA,
    anchor_id       BIGINT,                      -- newest row the retention purge removed
    anchor_hash     BYTEA,
    status          TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('unverified', 'ok', 'broken')),
    broken_at_id    BIGINT,
    broken_reason   TEXT,
    rows_checked    BIGINT NOT NULL DEFAULT 0,
    verified_at     TIMESTAMPTZ,
    -- The daily run is incremental; once a week it re-walks the whole chain, so an
    -- edit to a row that was already verified is still caught.
    last_full_at    TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Data subject requests ───
-- identifiers_enc holds the requester's contact details sealed
-- (privacy.seal); identifier_hmacs holds contact_hmac hex values so the
-- executors and duplicate checks never need the raw values in SQL.
CREATE TABLE IF NOT EXISTS trs_dsr_requests (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    type              TEXT NOT NULL CHECK (type IN ('access', 'rectify', 'erase', 'restrict', 'object', 'portability')),
    status            TEXT NOT NULL DEFAULT 'received'
                      CHECK (status IN ('received', 'verified', 'in_progress', 'completed', 'rejected', 'cancelled')),
    channel           TEXT NOT NULL DEFAULT 'public_form'
                      CHECK (channel IN ('public_form', 'console', 'email', 'whatsapp', 'phone', 'in_person')),
    identifiers_enc   BYTEA,
    identifier_hmacs  TEXT[] NOT NULL DEFAULT '{}',
    identifiers_hint  JSONB NOT NULL DEFAULT '{}',  -- masked display values only
    details_enc       BYTEA,                        -- the requester's own words, sealed
    language          TEXT NOT NULL DEFAULT 'en',
    verification      JSONB NOT NULL DEFAULT '{}',  -- {method, note, verified_by, verified_at}
    received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    due_at            TIMESTAMPTZ NOT NULL,
    handled_by        BIGINT REFERENCES users (id),
    completed_at      TIMESTAMPTZ,
    result_upload_id  UUID REFERENCES files (id),
    result            JSONB NOT NULL DEFAULT '{}',  -- counts per table, never contents
    note              TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS trs_dsr_requests_queue_idx ON trs_dsr_requests (hotel_slug, status, due_at);

-- ─── Restriction flags (DSR restrict / object) ───
CREATE TABLE IF NOT EXISTS trs_subject_flags (
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    subject_ref   TEXT NOT NULL,                   -- contact_ref hex, as in consents
    restricted    BOOLEAN NOT NULL DEFAULT TRUE,
    scopes        TEXT[] NOT NULL DEFAULT '{marketing,ai_analytics}',
    dsr_id        BIGINT REFERENCES trs_dsr_requests (id) ON DELETE SET NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    lifted_at     TIMESTAMPTZ,
    PRIMARY KEY (hotel_slug, subject_ref)
);

-- ─── Retention ───
CREATE TABLE IF NOT EXISTS trs_retention_policies (
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    data_class   TEXT NOT NULL,
    keep_days    INT NOT NULL CHECK (keep_days > 0),
    action       TEXT NOT NULL CHECK (action IN ('delete', 'anonymize')),
    updated_by   BIGINT REFERENCES users (id),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, data_class)
);

-- Counts only, never contents (03 spec 5.3). One row per run, class and
-- table; each purge batch adds its count in the same transaction as its
-- DELETE/UPDATE, so the log always equals what committed.
CREATE TABLE IF NOT EXISTS trs_deletion_log (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT REFERENCES hotels (slug),  -- NULL: platform rows (no hotel), e.g. unrouted webhooks
    run_id       TEXT NOT NULL,
    data_class   TEXT NOT NULL,
    table_name   TEXT NOT NULL,
    action       TEXT NOT NULL,
    row_count    BIGINT NOT NULL DEFAULT 0,
    criteria     JSONB NOT NULL DEFAULT '{}',
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, data_class, table_name)
);
CREATE INDEX IF NOT EXISTS trs_deletion_log_hotel_idx ON trs_deletion_log (hotel_slug, at DESC);

-- ─── Tenant exports ───
CREATE TABLE IF NOT EXISTS trs_exports (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'ready', 'failed', 'expired')),
    requested_by  BIGINT REFERENCES users (id),
    file_id       UUID REFERENCES files (id),
    tables        JSONB NOT NULL DEFAULT '{}',     -- {table: row_count}
    error         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS trs_exports_hotel_idx ON trs_exports (hotel_slug, created_at DESC);
