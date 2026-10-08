-- WhatsApp number confirmation for the brief. users.phone_e164 is a GLOBAL
-- identity: magic links are minted by it and inbound WhatsApp senders are
-- mapped to a user by it, so Brief never stores a number nobody proved. A
-- member who adds their own number gets a one-time code on screen and sends
-- "CLEFS <code>" from that WhatsApp to the platform number; Meta signs the
-- sender, so a matching message proves they control the number. Managers
-- can never add a number for someone else.

CREATE TABLE IF NOT EXISTS brf_phone_verifications (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    user_id       BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    phone_e164    TEXT NOT NULL,
    code_hash     TEXT NOT NULL,                             -- sha256 hex of the 6-digit code (never stored plain)
    attempts      SMALLINT NOT NULL DEFAULT 0,               -- wrong codes from that number; 5 ends it
    expires_at    TIMESTAMPTZ NOT NULL,
    verified_at   TIMESTAMPTZ,
    cancelled_at  TIMESTAMPTZ,                               -- replaced by a newer request, or too many attempts
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- One open request per member (a new request cancels the old one first).
CREATE UNIQUE INDEX IF NOT EXISTS brf_phone_verifications_open_user
    ON brf_phone_verifications (user_id) WHERE verified_at IS NULL AND cancelled_at IS NULL;
-- The inbound lookup: open requests for the sender's number.
CREATE INDEX IF NOT EXISTS brf_phone_verifications_open_phone
    ON brf_phone_verifications (phone_e164) WHERE verified_at IS NULL AND cancelled_at IS NULL;
