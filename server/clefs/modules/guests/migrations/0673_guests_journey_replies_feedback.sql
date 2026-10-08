-- 0673_guests_journey_replies_feedback: what guests answer, and the public
-- pages they answer on (04 spec 3.2, 3.8 and 3.9).
--
--   gst_journey_replies     one classified inbound reply; summary_en has its
--                           own column so retention can redact it at 365 d
--   gst_journey_messages    free-form journey messages (the J5 consent ask,
--                           the fixed acknowledgement, the consent link).
--                           Not in the 04 spec DDL: YES disambiguation (3.8)
--                           and opt-out attribution (3.10) must find "the
--                           latest journey message to that contact", and a
--                           free-form message has no plan row.
--   gst_public_tokens       /g/[token] and /g/f/[token]; only the sha256 is
--                           stored. `ref` (not in the spec DDL) carries the
--                           inbound message id behind a consent token, so the
--                           /g consent tick can create the point from the
--                           number that said YES without copying it here.
--   gst_stay_prefs          J1 page answers, deleted at purge_after
--   gst_feedback            J7 page answers (one per stay)
--   gst_review_link_clicks  counts only, no identity

CREATE TABLE IF NOT EXISTS gst_journey_replies (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    plan_id             BIGINT REFERENCES gst_journey_plans (id),
    reservation_id      BIGINT,
    inbound_message_id  BIGINT,
    wamid               TEXT,
    classification      JSONB NOT NULL,                    -- without summary_en (own column, retention)
    summary_en          TEXT,
    deterministic_hit   TEXT,
    llm_used            BOOLEAN NOT NULL DEFAULT FALSE,
    outcome             TEXT NOT NULL CHECK (outcome IN
                            ('none', 'service_request', 'followup', 'opt_out', 'consent_yes', 'offer_accept', 'wrong_number')),
    service_ref         TEXT,
    action_id           BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS gst_journey_replies_reservation_idx ON gst_journey_replies (hotel_slug, reservation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS gst_journey_replies_outcome_idx ON gst_journey_replies (hotel_slug, outcome, created_at DESC);
CREATE INDEX IF NOT EXISTS gst_journey_replies_plan_idx ON gst_journey_replies (plan_id) WHERE plan_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS gst_journey_messages (
    id                   BIGSERIAL PRIMARY KEY,
    hotel_slug           TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id       BIGINT,
    plan_id              BIGINT REFERENCES gst_journey_plans (id),
    kind                 TEXT NOT NULL CHECK (kind IN ('consent_ask', 'ack', 'consent_link', 'offer_ack')),
    channel              TEXT NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    contact_point_id     BIGINT REFERENCES gst_contact_points (id),
    value_hmac           BYTEA,
    outbound_message_id  BIGINT,
    sent_at              TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS gst_journey_messages_hmac_idx ON gst_journey_messages (hotel_slug, value_hmac, sent_at DESC);
CREATE INDEX IF NOT EXISTS gst_journey_messages_reservation_idx ON gst_journey_messages (hotel_slug, reservation_id, kind);

CREATE TABLE IF NOT EXISTS gst_public_tokens (
    token_hash      BYTEA PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL,
    purpose         TEXT NOT NULL CHECK (purpose IN ('prefs', 'feedback', 'consent')),
    -- prefs = check-in day 23:59 hotel tz; feedback = the J7 plan's valid_until; consent = sent_at + 24 h (3.4)
    expires_at      TIMESTAMPTZ NOT NULL,
    used_at         TIMESTAMPTZ,
    ref             JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS gst_public_tokens_expiry_idx ON gst_public_tokens (expires_at) WHERE used_at IS NULL;

CREATE TABLE IF NOT EXISTS gst_stay_prefs (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL,
    arrival_time    TEXT,
    requests        TEXT,
    occasion        TEXT,
    language        TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purge_after     TIMESTAMPTZ NOT NULL,                  -- check_out + 30 d, re-set on dates_changed
    PRIMARY KEY (hotel_slug, reservation_id)
);

CREATE TABLE IF NOT EXISTS gst_feedback (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id  BIGINT NOT NULL,
    plan_id         BIGINT,
    overall         SMALLINT CHECK (overall BETWEEN 1 AND 5),
    went_well       TEXT,
    improve         TEXT,
    contact_ok      BOOLEAN,
    language        TEXT,
    followup_ref    TEXT,
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, reservation_id)
);

CREATE TABLE IF NOT EXISTS gst_review_link_clicks (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    day         DATE NOT NULL,
    platform    TEXT NOT NULL CHECK (platform IN ('google', 'tripadvisor')),
    clicks      INT NOT NULL DEFAULT 0,
    PRIMARY KEY (hotel_slug, day, platform)
);
