-- 0010_core_platform: the platform plumbing every module shares (F-contracts).
--
--   files              bytea file store (FileStore interface; an S3 adapter slot)
--   job_runs           one row per (job, hotel, scheduled slot): the claim that makes
--                      scheduling idempotent across the worker, cron ticks and restarts
--   job_queue          ad-hoc tasks and timers, claimed with FOR UPDATE SKIP LOCKED
--   outbound_messages  the notification outbox (idempotency_key UNIQUE)
--   inbound_messages   webhook dedupe log (provider_id UNIQUE: messages[].id, statuses, email ids)
--   whatsapp_windows   the 24 h customer-service window per (our number, their number)
--   notify_holds       account-wide sending holds (Meta 130429 throughput backoff)
--   ai_calls           one row per model call; content only when AI_LOG_CONTENT
--   consent_texts      versioned consent wording shown to guests
--   consents           append-only consent ledger (PDPL: consent is the only basis)
--   worker_heartbeats  liveness of workers and cron ticks (/internal/status)
--
-- Every statement is idempotent. Money is not stored here.

-- ─── Files ───

CREATE TABLE IF NOT EXISTS files (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- NULL: platform files (the public OTA-audit sandbox has no hotel yet)
    hotel_slug          TEXT REFERENCES hotels (slug),
    sha256              TEXT NOT NULL,                     -- lowercase hex of the content
    filename            TEXT NOT NULL,
    content_type        TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes          BIGINT NOT NULL CHECK (size_bytes >= 0),
    storage             TEXT NOT NULL DEFAULT 'db' CHECK (storage IN ('db', 's3')),
    data                BYTEA,                             -- NULL once purged, or when storage = 's3'
    s3_key              TEXT,
    source              TEXT NOT NULL DEFAULT 'upload'
                        CHECK (source IN ('upload', 'email', 'whatsapp', 'generated', 'import', 'api')),
    purpose             TEXT NOT NULL,
    uploaded_by         BIGINT REFERENCES users (id),
    uploaded_by_device  BIGINT REFERENCES devices (id),
    retention_until     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    deleted_at          TIMESTAMPTZ
);
-- Same bytes uploaded twice to one hotel resolve to one row (sha256 dedupe).
CREATE UNIQUE INDEX IF NOT EXISTS files_dedupe_idx ON files ((COALESCE(hotel_slug, '')), sha256) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS files_hotel_idx ON files (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS files_retention_idx ON files (retention_until) WHERE deleted_at IS NULL AND retention_until IS NOT NULL;

-- ─── Jobs ───

CREATE TABLE IF NOT EXISTS job_runs (
    id             BIGSERIAL PRIMARY KEY,
    job            TEXT NOT NULL,
    hotel_slug     TEXT REFERENCES hotels (slug),          -- NULL: a platform-wide job
    scheduled_for  TIMESTAMPTZ NOT NULL,                   -- the slot, not the wall-clock start
    status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'done', 'failed', 'skipped')),
    attempts       INT NOT NULL DEFAULT 1,
    worker         TEXT,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    duration_ms    INT,
    error          TEXT,
    result         JSONB
);
-- The claim: INSERT ... ON CONFLICT DO NOTHING on this index. COALESCE because
-- a UNIQUE over a NULL hotel_slug would never conflict.
CREATE UNIQUE INDEX IF NOT EXISTS job_runs_slot_idx ON job_runs (job, (COALESCE(hotel_slug, '')), scheduled_for);
CREATE INDEX IF NOT EXISTS job_runs_recent_idx ON job_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS job_runs_failed_idx ON job_runs (finished_at) WHERE status = 'failed';

CREATE TABLE IF NOT EXISTS job_queue (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    hotel_slug    TEXT REFERENCES hotels (slug),
    payload       JSONB NOT NULL DEFAULT '{}',
    run_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dedupe_key    TEXT UNIQUE,                             -- NULLs never conflict
    status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'dead', 'cancelled')),
    attempts      INT NOT NULL DEFAULT 0,
    max_attempts  INT NOT NULL DEFAULT 5,
    locked_by     TEXT,
    locked_until  TIMESTAMPTZ,
    last_error    TEXT,
    result        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS job_queue_ready_idx ON job_queue (run_at, id) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS job_queue_running_idx ON job_queue (locked_until) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS job_queue_finished_idx ON job_queue (finished_at) WHERE status IN ('done', 'dead', 'cancelled');

-- ─── Notifications ───

CREATE TABLE IF NOT EXISTS outbound_messages (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT REFERENCES hotels (slug),
    channel           TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email', 'sms')),
    kind              TEXT NOT NULL CHECK (kind IN ('template', 'text', 'interactive', 'document', 'email', 'sms')),
    purpose           TEXT NOT NULL,                       -- "<module>.<what>", e.g. brief.daily
    -- Meta pricing category; marketing sends are held to 09:00-18:00 Asia/Dubai.
    category          TEXT CHECK (category IN ('utility', 'marketing', 'authentication', 'service')),
    sender            TEXT NOT NULL DEFAULT 'platform' CHECK (sender IN ('platform', 'hotel')),
    sender_id         TEXT,                                -- WhatsApp phone_number_id, email From, SMS sender id
    recipient         TEXT NOT NULL,                       -- E.164 or email address
    payload           JSONB NOT NULL,                      -- the provider request body we send
    status            TEXT NOT NULL DEFAULT 'queued' CHECK (status IN (
                          'queued', 'sending', 'sent', 'delivered', 'read', 'failed',
                          'skipped_unconfigured', 'outside_window', 'cancelled')),
    provider          TEXT,                                -- meta | postmark | msg91 | twilio | capture | log
    provider_id       TEXT,                                -- wamid, Postmark MessageID, Twilio SID
    error_code        TEXT,
    error             TEXT,
    attempts          INT NOT NULL DEFAULT 0,
    not_before        TIMESTAMPTZ,
    idempotency_key   TEXT UNIQUE,
    template_name     TEXT,
    template_lang     TEXT,
    fallback_of       BIGINT REFERENCES outbound_messages (id),
    action_id         BIGINT,
    billable          BOOLEAN,
    pricing_category  TEXT,
    meta              JSONB NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at           TIMESTAMPTZ,
    delivered_at      TIMESTAMPTZ,
    read_at           TIMESTAMPTZ,
    failed_at         TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS outbound_messages_due_idx ON outbound_messages (not_before) WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS outbound_messages_recipient_idx ON outbound_messages (channel, recipient, sent_at DESC);
CREATE INDEX IF NOT EXISTS outbound_messages_provider_idx ON outbound_messages (provider_id) WHERE provider_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS outbound_messages_hotel_idx ON outbound_messages (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS outbound_messages_failed_idx ON outbound_messages (failed_at) WHERE status = 'failed';

CREATE TABLE IF NOT EXISTS inbound_messages (
    id           BIGSERIAL PRIMARY KEY,
    channel      TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email', 'sms')),
    -- messages[].id | "status:<wamid>:<status>" | "email:<MessageID>": Meta and
    -- Postmark both redeliver, so this UNIQUE is the dedupe.
    provider_id  TEXT NOT NULL UNIQUE,
    kind         TEXT NOT NULL,
    hotel_slug   TEXT REFERENCES hotels (slug),
    sender       TEXT,
    recipient    TEXT,
    audience     TEXT,
    payload      JSONB NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'received' CHECK (status IN ('received', 'handled', 'unhandled', 'error', 'ignored')),
    handled_by   TEXT,
    error        TEXT,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    handled_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS inbound_messages_hotel_idx ON inbound_messages (hotel_slug, received_at DESC);
CREATE INDEX IF NOT EXISTS inbound_messages_sender_idx ON inbound_messages (channel, sender, received_at DESC);

CREATE TABLE IF NOT EXISTS whatsapp_windows (
    sender_id        TEXT NOT NULL,                        -- our phone_number_id
    recipient        TEXT NOT NULL,                        -- their E.164
    hotel_slug       TEXT REFERENCES hotels (slug),
    last_inbound_at  TIMESTAMPTZ NOT NULL,
    csw_expires_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (sender_id, recipient)
);

CREATE TABLE IF NOT EXISTS notify_holds (
    key         TEXT PRIMARY KEY,                          -- e.g. "wa:<phone_number_id>"
    until       TIMESTAMPTZ NOT NULL,
    reason      TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── AI calls ───

CREATE TABLE IF NOT EXISTS ai_calls (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT REFERENCES hotels (slug),
    task           TEXT NOT NULL,
    kind           TEXT NOT NULL,                          -- structured | text | tool_call | web_search | raw
    model          TEXT NOT NULL,
    backend        TEXT NOT NULL,                          -- gemini | fake
    status         TEXT NOT NULL CHECK (status IN ('ok', 'error', 'disabled', 'invalid_output')),
    tokens_in      INT,
    tokens_out     INT,
    latency_ms     INT,
    -- Vendor costs are sub-cent, so micro-units (04 spec 0.2 item 10).
    cost_micros    BIGINT,
    cost_currency  CHAR(3) DEFAULT 'USD',
    response_id    TEXT,
    error          TEXT,
    input          JSONB,                                  -- only when AI_LOG_CONTENT=true
    output         JSONB,                                  -- only when AI_LOG_CONTENT=true
    at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ai_calls_hotel_idx ON ai_calls (hotel_slug, at DESC);
CREATE INDEX IF NOT EXISTS ai_calls_task_idx ON ai_calls (task, at DESC);

-- ─── Consent (Canon 4: foundation-owned; Trust adds DSR and retention) ───

CREATE TABLE IF NOT EXISTS consent_texts (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT REFERENCES hotels (slug),             -- NULL: the platform default wording
    purpose     TEXT NOT NULL,
    version     TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'en',
    body        TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retired_at  TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS consent_texts_version_idx ON consent_texts ((COALESCE(hotel_slug, '')), purpose, version, language);

CREATE TABLE IF NOT EXISTS consents (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    subject_type  TEXT NOT NULL DEFAULT 'guest',           -- guest | contact | lead | user | staff
    subject_ref   TEXT NOT NULL,                           -- hashed identity (hex contact_hmac) or an internal id
    -- Validated in core/consent.py (not a CHECK) so a new purpose needs no migration.
    purpose       TEXT NOT NULL,
    granted       BOOLEAN NOT NULL,
    source        TEXT NOT NULL,                           -- concierge | registration_card | web_form | whatsapp | staff | import
    text_id       BIGINT REFERENCES consent_texts (id),
    text_version  TEXT,
    evidence      JSONB NOT NULL DEFAULT '{}',
    actor_type    TEXT,
    actor_id      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ
);
-- Append-only: the latest row per (hotel, subject, purpose) is the current state.
CREATE INDEX IF NOT EXISTS consents_subject_idx ON consents (hotel_slug, subject_ref, purpose, created_at DESC, id DESC);

-- ─── Worker liveness ───

CREATE TABLE IF NOT EXISTS worker_heartbeats (
    worker_id     TEXT PRIMARY KEY,                        -- "<host>:<pid>" or "cron"
    kind          TEXT NOT NULL DEFAULT 'worker' CHECK (kind IN ('worker', 'cron')),
    host          TEXT,
    pid           INT,
    is_scheduler  BOOLEAN NOT NULL DEFAULT FALSE,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_beat_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_tick_at  TIMESTAMPTZ,
    info          JSONB NOT NULL DEFAULT '{}'
);
