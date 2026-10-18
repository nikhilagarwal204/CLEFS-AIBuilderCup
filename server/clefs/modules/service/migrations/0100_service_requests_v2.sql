-- 0100_service_requests_v2: Service Control v2 on the legacy request table
-- (03 spec 1.1 "0002_requests_v2", with the Canon overrides).
--
-- Why additive ALTERs on staff_requests rather than a new table: the legacy
-- routes, the StaffDashboard and the pilots' analytics read this table, and
-- dashboards deploy separately from the API, so its columns and meanings are a
-- contract (03 spec 0.1). Every new column has a default or is nullable, so a
-- legacy writer that names only the old columns still inserts a valid row.
--
-- Time: this is a legacy table, so the new instants are epoch-ms BIGINT like
-- timestamp / accepted_at / resolved_at (Canon 3). New tables use TIMESTAMPTZ.
-- Tenant key: hotel_slug (Canon 1); the spec's hotel_id is not added.

-- ─── staff_requests: v2 columns ───

ALTER TABLE staff_requests
    -- NULL = a row written before v2 (or by a raw INSERT): its accepted_at is
    -- the only "someone took it" instant, and ack metrics label it pre-v2.
    ADD COLUMN IF NOT EXISTS intake_version SMALLINT,
    ADD COLUMN IF NOT EXISTS state TEXT,
    ADD COLUMN IF NOT EXISTS version INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS channel_ref TEXT,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT,
    ADD COLUMN IF NOT EXISTS guest_ref TEXT,
    -- description stays English for staff; original_text is what the guest said
    ADD COLUMN IF NOT EXISTS language TEXT,
    ADD COLUMN IF NOT EXISTS original_text TEXT,
    -- NULL: no translation needed; TRUE: translated; FALSE: the translation
    -- timed out or failed and staff see the guest's own words
    ADD COLUMN IF NOT EXISTS translated BOOLEAN,
    ADD COLUMN IF NOT EXISTS floor TEXT,
    ADD COLUMN IF NOT EXISTS room_type TEXT,
    ADD COLUMN IF NOT EXISTS stay_stage TEXT,
    ADD COLUMN IF NOT EXISTS location_slug TEXT,
    ADD COLUMN IF NOT EXISTS department_key TEXT,
    ADD COLUMN IF NOT EXISTS request_type_key TEXT,
    ADD COLUMN IF NOT EXISTS amenity_item_id BIGINT,
    ADD COLUMN IF NOT EXISTS quantity INT,
    ADD COLUMN IF NOT EXISTS order_id BIGINT,
    ADD COLUMN IF NOT EXISTS severity TEXT,
    ADD COLUMN IF NOT EXISTS sensitivity TEXT NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS sensitive_kind TEXT,
    ADD COLUMN IF NOT EXISTS assigned_staff_id INT,
    ADD COLUMN IF NOT EXISTS assigned_device_id BIGINT,
    -- assigned_at is the v2 name; accepted_at keeps being written with the
    -- same value as a documented deprecated alias (03 spec 1.2)
    ADD COLUMN IF NOT EXISTS assigned_at BIGINT,
    ADD COLUMN IF NOT EXISTS acknowledged_at BIGINT,
    ADD COLUMN IF NOT EXISTS ack_implicit BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS started_at BIGINT,
    ADD COLUMN IF NOT EXISTS cancelled_at BIGINT,
    ADD COLUMN IF NOT EXISTS scheduled_for BIGINT,
    -- the escalation ladder counts from here (creation, or the last reopen)
    ADD COLUMN IF NOT EXISTS ladder_started_at BIGINT,
    ADD COLUMN IF NOT EXISTS ladder_cycle INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ack_due_at BIGINT,
    ADD COLUMN IF NOT EXISTS resolve_due_at BIGINT,
    ADD COLUMN IF NOT EXISTS sla_policy_ref TEXT,
    ADD COLUMN IF NOT EXISTS sla_ack_breached BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS sla_resolve_breached BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS escalation_level SMALLINT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS reopen_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS merged_count INT NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS guest_confirmation TEXT,
    ADD COLUMN IF NOT EXISTS guest_confirmed_at BIGINT,
    ADD COLUMN IF NOT EXISTS guest_comment TEXT,
    ADD COLUMN IF NOT EXISTS shift_key TEXT,
    ADD COLUMN IF NOT EXISTS created_by_staff_id INT,
    ADD COLUMN IF NOT EXISTS created_by_user_id BIGINT,
    ADD COLUMN IF NOT EXISTS completed_by_staff_id INT,
    ADD COLUMN IF NOT EXISTS resolution_code TEXT,
    ADD COLUMN IF NOT EXISTS verified_room BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS incident_id BIGINT,
    -- small facts that do not deserve a column: ooo_room, heat, vip, ...
    ADD COLUMN IF NOT EXISTS flags JSONB NOT NULL DEFAULT '{}';

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_state_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_state_check CHECK (state IS NULL OR state IN (
            'scheduled', 'new', 'assigned', 'acknowledged', 'in_progress', 'completed', 'cancelled', 'reopened'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_severity_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_severity_check
            CHECK (severity IS NULL OR severity IN ('L1', 'L2', 'L3'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_sensitivity_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_sensitivity_check
            CHECK (sensitivity IN ('emergency', 'sensitive', 'handoff', 'none'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_guest_confirmation_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_guest_confirmation_check
            CHECK (guest_confirmation IS NULL OR guest_confirmation IN ('yes', 'no', 'unknown'));
    END IF;
END $$;

-- ─── Backfill (pre-v2 rows) ───
-- state from the legacy status; assigned_at from accepted_at; acknowledged_at
-- stays NULL (unknown for legacy rows, so they never enter ack metrics).
-- The pilots' tables are small, so one pass is enough.

UPDATE staff_requests
SET state = CASE status WHEN 'progress' THEN 'assigned' WHEN 'completed' THEN 'completed' ELSE 'new' END,
    assigned_at = COALESCE(assigned_at, accepted_at),
    ladder_started_at = COALESCE(ladder_started_at, "timestamp")
WHERE state IS NULL;

UPDATE staff_requests r
SET assigned_staff_id = s.id
FROM staff_members s
WHERE r.assigned_staff_id IS NULL AND r.assigned_to IS NOT NULL
  AND s.hotel_slug = r.hotel_slug AND s.name = r.assigned_to;

-- ─── Keep state and the legacy status in step for any writer ───
-- Service code sets both in the same UPDATE. This trigger covers a writer
-- that only knows the legacy columns (a raw INSERT, an old script): the row
-- still gets a state, and a status-only UPDATE still moves the state.

CREATE OR REPLACE FUNCTION svc_staff_requests_state_sync() RETURNS trigger AS $fn$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.state IS NULL THEN
            NEW.state := CASE NEW.status WHEN 'progress' THEN 'assigned' WHEN 'completed' THEN 'completed' ELSE 'new' END;
        END IF;
        IF NEW.assigned_at IS NULL AND NEW.accepted_at IS NOT NULL THEN
            NEW.assigned_at := NEW.accepted_at;
        END IF;
        IF NEW.ladder_started_at IS NULL THEN
            NEW.ladder_started_at := NEW."timestamp";
        END IF;
    ELSIF NEW.status IS DISTINCT FROM OLD.status AND NEW.state IS NOT DISTINCT FROM OLD.state THEN
        NEW.state := CASE NEW.status
            WHEN 'progress' THEN CASE WHEN OLD.state IN ('assigned', 'acknowledged', 'in_progress', 'reopened')
                                      THEN OLD.state ELSE 'assigned' END
            WHEN 'completed' THEN CASE WHEN OLD.state = 'cancelled' THEN 'cancelled' ELSE 'completed' END
            ELSE 'new' END;
    END IF;
    RETURN NEW;
END
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS svc_staff_requests_state_sync ON staff_requests;
CREATE TRIGGER svc_staff_requests_state_sync
    BEFORE INSERT OR UPDATE ON staff_requests
    FOR EACH ROW EXECUTE FUNCTION svc_staff_requests_state_sync();

-- ─── Indexes ───

CREATE UNIQUE INDEX IF NOT EXISTS staff_requests_idempotency_idx
    ON staff_requests (hotel_slug, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS staff_requests_open_idx
    ON staff_requests (hotel_slug, state) WHERE state NOT IN ('completed', 'cancelled');
CREATE INDEX IF NOT EXISTS staff_requests_room_idx ON staff_requests (hotel_slug, room, "timestamp" DESC);
CREATE INDEX IF NOT EXISTS staff_requests_hotel_ts_idx ON staff_requests (hotel_slug, "timestamp" DESC);

-- ─── Request events: the cursor feed devices and dashboards poll ───
-- Polling first, SSE as an accelerator (03 spec 0.4). No foreign key to
-- staff_requests: the legacy DELETE route still removes rows, and the feed
-- keeps a "deleted" event so an offline device drops the card.

CREATE TABLE IF NOT EXISTS svc_request_events (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    request_id       BIGINT NOT NULL,
    at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- created | merged | transition | note | escalated | sla_breach | guest_confirm | updated | deleted
    kind             TEXT NOT NULL,
    action           TEXT,
    from_state       TEXT,
    to_state         TEXT,
    version          INT,
    actor_type       TEXT NOT NULL,
    actor_staff_id   INT,
    actor_user_id    BIGINT,
    device_id        BIGINT,
    actor_label      TEXT,
    -- the device outbox's idempotency key; a replayed batch hits this
    client_event_id  UUID UNIQUE,
    client_at        TIMESTAMPTZ,
    late_sync        BOOLEAN NOT NULL DEFAULT FALSE,
    -- denormalised so the device feed filters by scope without a join
    floor            TEXT,
    department_key   TEXT,
    payload          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS svc_request_events_feed_idx ON svc_request_events (hotel_slug, id);
CREATE INDEX IF NOT EXISTS svc_request_events_request_idx ON svc_request_events (request_id, id);

-- ─── Guest tracking tokens ───
-- 22 random characters; only the SHA-256 is stored (03 spec 1.3 step 8). A
-- merged request mints another token for the same row, hence a table.

CREATE TABLE IF NOT EXISTS svc_guest_tokens (
    token_hash  BYTEA PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    request_id  BIGINT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS svc_guest_tokens_request_idx ON svc_guest_tokens (request_id);

-- WhatsApp status updates a guest opted into from the tracker page. The phone
-- is sealed (core.privacy.seal); phone_hmac joins it to inbound messages.
CREATE TABLE IF NOT EXISTS svc_tracker_subscriptions (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    request_id       BIGINT NOT NULL,
    phone_hmac       BYTEA NOT NULL,
    phone_enc        BYTEA NOT NULL,
    consent_id       BIGINT,
    language         TEXT NOT NULL DEFAULT 'en',
    last_state_sent  TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (request_id, phone_hmac)
);
CREATE INDEX IF NOT EXISTS svc_tracker_subscriptions_sender_idx ON svc_tracker_subscriptions (hotel_slug, phone_hmac, created_at DESC);

-- Per-hotel sweep bookkeeping: when shadow mode started, when the SLA sweep
-- last ran (evaluate-on-read skips a sweep younger than 30 s), and how long
-- each department queue has been deep (the understaffed signal).
CREATE TABLE IF NOT EXISTS svc_sweep_state (
    hotel_slug         TEXT PRIMARY KEY REFERENCES hotels (slug),
    shadow_started_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sweep_at      TIMESTAMPTZ,
    deep_since         JSONB NOT NULL DEFAULT '{}',
    last_result        JSONB NOT NULL DEFAULT '{}'
);
