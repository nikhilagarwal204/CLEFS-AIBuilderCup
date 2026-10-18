-- 0130_service_gaps: the Wave 1 service gaps (device Web Push subscriptions,
-- keyed create_request replays after a merge).

-- ─── Web Push subscriptions ───
-- One row per browser push endpoint (03 spec 1.6: floor devices, VAPID).
-- WHY a Service table and not devices.push_sub: `devices` is a foundation
-- table, and one paired device can hold several subscriptions over its life
-- (a reinstalled PWA, a second browser profile), each tied to the person
-- who enabled it. `endpoint` is the push service URL and is globally
-- unique: a re-subscribe from the same browser updates its row (and may
-- move it to another device or person).
CREATE TABLE IF NOT EXISTS svc_push_subscriptions (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    device_id        BIGINT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    staff_member_id  INT,
    endpoint         TEXT NOT NULL UNIQUE,
    p256dh           TEXT NOT NULL,
    auth             TEXT NOT NULL,
    expiration_at    TIMESTAMPTZ,
    user_agent       TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_sent_at     TIMESTAMPTZ,
    failure_count    INT NOT NULL DEFAULT 0 CHECK (failure_count >= 0),
    -- Set by DELETE, by a 404/410 from the push service, or after repeated
    -- failures. Revoked rows are kept (never re-used by a send) until the
    -- same endpoint subscribes again.
    revoked_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS svc_push_subscriptions_device_idx
    ON svc_push_subscriptions (hotel_slug, device_id) WHERE revoked_at IS NULL;

-- ─── Keyed create_request replays after a merge ───
-- A keyed request that merged into the room's open one leaves its key on
-- the "merged" feed event (intake._by_merged_key). Partial and expression
-- indexed, so the replay lookup stays a point read on a feed table that
-- grows with every transition. The predicate is only `kind = 'merged'`:
-- the lookup's WHERE must imply it for the planner to use the index.
CREATE INDEX IF NOT EXISTS svc_request_events_merged_key_idx
    ON svc_request_events (hotel_slug, (payload->>'idempotency_key'))
    WHERE kind = 'merged';
