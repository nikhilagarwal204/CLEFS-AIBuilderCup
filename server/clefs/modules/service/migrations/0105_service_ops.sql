-- 0105_service_ops: what the ops part adds on top of 0102/0103 (03 spec
-- 1.5-1.7): idempotency keys for offline device writes, the handover
-- acknowledgement log, and tracker WhatsApp bookkeeping.

-- ─── Offline-safe device writes ───
-- A device queues SOS, recovery and lost-and-found writes in its outbox with
-- a client_event_id, exactly like request transitions (svc_request_events).
-- A replay finds the first row through these unique keys and changes nothing.

ALTER TABLE svc_incidents
    ADD COLUMN IF NOT EXISTS client_event_id UUID,
    ADD COLUMN IF NOT EXISTS client_at TIMESTAMPTZ;
CREATE UNIQUE INDEX IF NOT EXISTS svc_incidents_client_event_idx
    ON svc_incidents (client_event_id) WHERE client_event_id IS NOT NULL;

ALTER TABLE svc_recovery_cases
    ADD COLUMN IF NOT EXISTS client_event_id UUID,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE UNIQUE INDEX IF NOT EXISTS svc_recovery_cases_client_event_idx
    ON svc_recovery_cases (client_event_id) WHERE client_event_id IS NOT NULL;
-- One open case per request: a second "not resolved" joins the first.
CREATE UNIQUE INDEX IF NOT EXISTS svc_recovery_cases_open_request_idx
    ON svc_recovery_cases (request_id) WHERE request_id IS NOT NULL AND status = 'open';

ALTER TABLE svc_recovery_actions
    ADD COLUMN IF NOT EXISTS client_event_id UUID,
    ADD COLUMN IF NOT EXISTS decided_by JSONB,
    ADD COLUMN IF NOT EXISTS decision_note TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS svc_recovery_actions_client_event_idx
    ON svc_recovery_actions (client_event_id) WHERE client_event_id IS NOT NULL;

ALTER TABLE svc_lost_found
    ADD COLUMN IF NOT EXISTS client_event_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS svc_lost_found_client_event_idx
    ON svc_lost_found (client_event_id) WHERE client_event_id IS NOT NULL;

-- ─── Handover acknowledgements ───
-- The device opens its queue only after the incoming person acknowledged the
-- handover (03 spec 1.6). The summary is the computed handover as they saw it
-- (counts and ids only, never guest words), so a later "nobody told me" has
-- an answer.

CREATE TABLE IF NOT EXISTS svc_handover_acks (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    device_id          BIGINT,
    device_session_id  BIGINT,
    staff_member_id    INT,
    user_id            BIGINT,
    acked_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note_ids           BIGINT[] NOT NULL DEFAULT '{}',
    summary            JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS svc_handover_acks_hotel_idx ON svc_handover_acks (hotel_slug, acked_at DESC);
CREATE INDEX IF NOT EXISTS svc_handover_acks_session_idx ON svc_handover_acks (device_session_id);

-- ─── Tracker WhatsApp updates ───
-- last_sent_at paces the status pusher; opted_out_at records a STOP.

ALTER TABLE svc_tracker_subscriptions
    ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS opted_out_at TIMESTAMPTZ;
