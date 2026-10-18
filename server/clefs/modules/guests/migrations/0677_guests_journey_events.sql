-- 0677_guests_journey_events: the stay-event consumer's idempotency log
-- (04 spec 3.3 step 1: `guests.journeys` is idempotent on stay_events.id).
--
-- core/stays delivers at least once. Planning itself is idempotent (the live
-- unique index on plans), but a dates_changed or cancelled event re-applied
-- after later events would move rows twice, so each event id is recorded
-- once and a redelivery is a no-op.

CREATE TABLE IF NOT EXISTS gst_journey_events (
    event_id        BIGINT PRIMARY KEY,                    -- stay_events.id
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL,
    event           TEXT NOT NULL,
    result          JSONB NOT NULL DEFAULT '{}',
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS gst_journey_events_reservation_idx ON gst_journey_events (hotel_slug, reservation_id);

-- Opt-outs attributed to a journey step (3.10 optout_monitor): which step's
-- message a STOP answered (context.id), else the latest journey send to the
-- contact within 72 h. Unattributed opt-outs are not stored here.
CREATE TABLE IF NOT EXISTS gst_journey_optouts (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    value_hmac    BYTEA NOT NULL,
    step          TEXT NOT NULL,
    plan_id       BIGINT REFERENCES gst_journey_plans (id),
    basis         TEXT NOT NULL CHECK (basis IN ('context', 'recent_send')),
    source        TEXT NOT NULL,
    opted_out_at  TIMESTAMPTZ NOT NULL,
    UNIQUE (hotel_slug, value_hmac, opted_out_at)
);
CREATE INDEX IF NOT EXISTS gst_journey_optouts_step_idx ON gst_journey_optouts (hotel_slug, step, opted_out_at);
