-- Public Google check, the secondary lead magnet (04 spec 5.9, Canon 23).
-- Intentionally global (a prospect's hotel has no hotels row). Lead contacts
-- are sealed with core.privacy.seal and the lead columns are deleted at 30 d
-- (register_pii in jobs.py); the whole row goes at expires_at
-- (visibility.public_check_purge).

CREATE TABLE IF NOT EXISTS vis_public_checks (
    id                        UUID PRIMARY KEY,
    token_hash                BYTEA UNIQUE NOT NULL,
    ip_hash                   BYTEA,
    hotel_query               TEXT,
    city                      TEXT,
    property_token            TEXT,
    candidates                JSONB,
    result                    JSONB,
    lead_name                 TEXT,
    lead_email_enc            BYTEA,
    lead_whatsapp_enc         BYTEA,
    consent_processing        BOOLEAN NOT NULL,
    consent_transfer_abroad   BOOLEAN NOT NULL,
    consent_contact_email     BOOLEAN,
    consent_contact_whatsapp  BOOLEAN,
    status                    TEXT NOT NULL CHECK (status IN ('needs_pick', 'ready', 'no_match')),
    lead_at                   TIMESTAMPTZ,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at                TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS vis_public_checks_created_idx ON vis_public_checks (created_at);
