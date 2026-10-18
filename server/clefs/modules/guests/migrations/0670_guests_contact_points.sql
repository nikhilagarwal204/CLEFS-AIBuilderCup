-- 0670_guests_contact_points: where a journey may write to, and who said no
-- (04 spec 3.2 and 3.4, Canon 19).
--
-- A contact point is one phone or email of one stay, with its provenance and
-- lifetime. pms_* and ota_alias points mirror core reservation_contacts rows
-- (the sealed value lives there; this row holds the HMAC and the rules), and
-- guest_direct / guest_stay points are captured by this module, so they carry
-- their own sealed value.
--
-- Deviation from the 04 spec DDL: reservation_id has no FOREIGN KEY to core
-- reservations, because core/stays is built in parallel (see 0650).
--
-- The partial unique index dedupes a guest_direct point with no reservation
-- (COALESCE to 0) and lets purged rows (value_hmac NULL) never collide.

CREATE TABLE IF NOT EXISTS gst_contact_points (
    id                      BIGSERIAL PRIMARY KEY,
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id          BIGINT,
    kind                    TEXT NOT NULL CHECK (kind IN ('phone', 'email')),
    provenance              TEXT NOT NULL CHECK (provenance IN ('pms_direct', 'pms_ota', 'ota_alias', 'guest_direct', 'guest_stay')),
    reservation_contact_id  BIGINT,                        -- core reservation_contacts row for pms_* and ota_alias
    value_enc               BYTEA,                         -- only guest_direct and guest_stay (captured here)
    value_hmac              BYTEA,                         -- nulled at purge for pms_ota, ota_alias and guest_stay
    country_prefix          TEXT,                          -- "+971", "+1": the NANP marketing block reads it
    alias_domain            TEXT,
    capture_source          TEXT NOT NULL CHECK (capture_source IN
                                ('pms', 'j1_form', 'concierge', 'registration_card', 'j5_reply', 'inquiry', 'console')),
    evidence                JSONB NOT NULL DEFAULT '{}',
    usable_until            TIMESTAMPTZ,
    last_used_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),   -- every send to, or inbound from, the point
    status                  TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suppressed', 'purged')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purged_at               TIMESTAMPTZ,
    CHECK (status = 'purged' OR value_hmac IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS gst_contact_points_dedupe_idx
    ON gst_contact_points (hotel_slug, (COALESCE(reservation_id, 0)), kind, value_hmac, provenance)
    WHERE value_hmac IS NOT NULL;
CREATE INDEX IF NOT EXISTS gst_contact_points_hmac_idx ON gst_contact_points (hotel_slug, value_hmac);
CREATE INDEX IF NOT EXISTS gst_contact_points_reservation_idx ON gst_contact_points (hotel_slug, reservation_id);
CREATE INDEX IF NOT EXISTS gst_contact_points_usable_idx ON gst_contact_points (usable_until) WHERE status = 'active';

-- Suppressions outlive points: a STOP from a number blocks every future point
-- that hashes the same, whatever stay it arrives with.
CREATE TABLE IF NOT EXISTS gst_suppressions (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    value_hmac  BYTEA NOT NULL,
    scope       TEXT NOT NULL CHECK (scope IN ('all', 'marketing')),
    reason      TEXT NOT NULL CHECK (reason IN ('stop', 'manual', 'wrong_number', 'complaint_about_messaging', 'dncr', 'bounce')),
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, value_hmac, scope)
);
