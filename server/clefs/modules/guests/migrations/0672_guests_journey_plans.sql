-- 0672_guests_journey_plans: one row per planned journey step, and the
-- marketing ledger every cap reads (04 spec 3.2, 3.3 and 3.5).
--
-- Plans are cancelled and re-created, never moved, so (hotel, reservation,
-- step, seq) stays unique among live rows. The dispatcher is the only writer
-- of `claimed`. reservation_id has no FK (core/stays lands in parallel, 0650).
--
-- gst_marketing_sends counts every marketing message per guest identity,
-- including Demand campaign exports (counted at export time) and upsell, so
-- "at most 2 post-stay per 30 days" holds across every sender.

CREATE TABLE IF NOT EXISTS gst_journey_plans (
    id                     BIGSERIAL PRIMARY KEY,
    hotel_slug             TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id         BIGINT NOT NULL,
    step                   TEXT NOT NULL CHECK (step IN ('j1', 'j2', 'j3', 'j4', 'j5', 'j6_pre', 'j6_post', 'j7', 'j8')),
    seq                    SMALLINT NOT NULL DEFAULT 1,
    due_at                 TIMESTAMPTZ NOT NULL,
    valid_until            TIMESTAMPTZ NOT NULL,
    status                 TEXT NOT NULL DEFAULT 'planned' CHECK (status IN (
                               'planned', 'deferred', 'claimed', 'proposed', 'observed', 'sent',
                               'delivered', 'read', 'replied', 'skipped', 'cancelled', 'failed', 'done')),
    skip_reason            TEXT,
    channel                TEXT,
    contact_point_id       BIGINT REFERENCES gst_contact_points (id),
    template_id            BIGINT REFERENCES gst_journey_templates (id),
    offer_id               BIGINT REFERENCES gst_journey_offers (id),
    direct_code            TEXT,
    action_id              BIGINT,
    outbound_message_id    BIGINT,
    wamid                  TEXT,
    attempt                SMALLINT NOT NULL DEFAULT 0,
    error_code             TEXT,
    category_at_send       TEXT,
    plan_version           INT NOT NULL DEFAULT 1,
    checkin_time_inferred  BOOLEAN NOT NULL DEFAULT FALSE,
    claimed_at             TIMESTAMPTZ,
    last_attempt_at        TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS gst_journey_plans_live_idx
    ON gst_journey_plans (hotel_slug, reservation_id, step, seq) WHERE status <> 'cancelled';
CREATE INDEX IF NOT EXISTS gst_journey_plans_due_idx
    ON gst_journey_plans (status, due_at) WHERE status IN ('planned', 'deferred', 'claimed');
CREATE INDEX IF NOT EXISTS gst_journey_plans_outbound_idx ON gst_journey_plans (outbound_message_id) WHERE outbound_message_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS gst_journey_plans_action_idx ON gst_journey_plans (action_id) WHERE action_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS gst_journey_plans_reservation_idx ON gst_journey_plans (hotel_slug, reservation_id);
CREATE INDEX IF NOT EXISTS gst_journey_plans_contact_idx ON gst_journey_plans (contact_point_id, last_attempt_at DESC) WHERE contact_point_id IS NOT NULL;

-- 03's direct codes, amended for J8: no profile needed, one code per plan.
ALTER TABLE gst_direct_codes ADD COLUMN IF NOT EXISTS plan_id BIGINT REFERENCES gst_journey_plans (id);
ALTER TABLE gst_direct_codes ADD COLUMN IF NOT EXISTS offer_id BIGINT REFERENCES gst_journey_offers (id);
CREATE UNIQUE INDEX IF NOT EXISTS gst_direct_codes_plan_idx ON gst_direct_codes (plan_id) WHERE plan_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS gst_marketing_sends (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    identity_hmac   BYTEA NOT NULL,
    channel         TEXT NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('journey_j3', 'journey_j8', 'demand_export', 'upsell', 'other')),
    phase           TEXT NOT NULL CHECK (phase IN ('pre_stay', 'in_stay', 'post_stay')),
    reservation_id  BIGINT,
    ref             TEXT,
    sent_at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS gst_marketing_sends_identity_idx ON gst_marketing_sends (hotel_slug, identity_hmac, sent_at);
CREATE INDEX IF NOT EXISTS gst_marketing_sends_reservation_idx ON gst_marketing_sends (hotel_slug, reservation_id, phase) WHERE reservation_id IS NOT NULL;
