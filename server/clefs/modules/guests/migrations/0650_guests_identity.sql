-- 0650_guests_identity: repeat-guest recognition (03 spec 2.7, Canon 10).
--
--   gst_profiles      a remembered guest, keyed by per-hotel contact HMACs
--                     (core.privacy.contact_hmac). Created ONLY with a
--                     recorded profile_memory consent: the PDPL has no
--                     legitimate-interest basis, so no consent means no row.
--   gst_stays         03's `stays` link table, widened into the journeys'
--                     reservation snapshot (dates, status, channel, chain).
--                     One row per core reservation, or per roster stay from
--                     the legacy guest_rooms GuestModal.
--   gst_direct_codes  03's direct booking codes. 0672 adds the J8 plan and
--                     offer links once those tables exist (04 spec 3.2).
--
-- reservation_id columns carry core reservations.id WITHOUT a foreign key:
-- core/stays (0020-0099) is built in parallel with this lane, and a database
-- that applied this file first must still migrate. The stay-event consumer
-- keeps the ids honest.
--
-- Contact values never land here: only HMACs, and the first name sealed with
-- core.privacy.seal. Allergies live in preferences.health (PDPL health data,
-- masked by core.pii for every role but F&B and front desk).

CREATE TABLE IF NOT EXISTS gst_profiles (
    id                    BIGSERIAL PRIMARY KEY,
    hotel_slug            TEXT NOT NULL REFERENCES hotels (slug),
    phone_hmac            BYTEA,
    email_hmac            BYTEA,
    first_name_enc        BYTEA,                           -- core.privacy.seal
    language              TEXT,
    -- {tags: [...], notes, health: {allergies: [...], notes}}; health is masked
    preferences           JSONB NOT NULL DEFAULT '{}',
    stays_count           INT NOT NULL DEFAULT 0,
    first_seen            DATE,
    last_seen             DATE,
    vip                   BOOLEAN NOT NULL DEFAULT FALSE,
    marketing_consent_id  BIGINT REFERENCES consents (id),
    profile_consent_id    BIGINT REFERENCES consents (id),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (phone_hmac IS NOT NULL OR email_hmac IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS gst_profiles_phone_idx ON gst_profiles (hotel_slug, phone_hmac) WHERE phone_hmac IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS gst_profiles_email_idx ON gst_profiles (hotel_slug, email_hmac) WHERE email_hmac IS NOT NULL;

CREATE TABLE IF NOT EXISTS gst_stays (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    reservation_id    BIGINT,                              -- core reservations.id; NULL for roster stays
    guest_profile_id  BIGINT REFERENCES gst_profiles (id) ON DELETE SET NULL,
    origin            TEXT NOT NULL DEFAULT 'pms' CHECK (origin IN ('pms', 'roster', 'manual')),
    room              TEXT,
    check_in          DATE,
    check_out         DATE,
    -- channel code (BOOKING, AGODA, EXPEDIA, DIRECT_WEB, DIRECT_PHONE, WALKIN, CORPORATE, GDS, OTHER) or ROSTER
    source            TEXT,
    status            TEXT CHECK (status IN ('CONFIRMED', 'IN_HOUSE', 'CHECKED_OUT', 'CANCELLED', 'NO_SHOW')),
    segment           TEXT,
    booked_at         TIMESTAMPTZ,
    checked_in_at     TIMESTAMPTZ,
    checked_out_at    TIMESTAMPTZ,
    -- TRUE when a PMS never reported checked_in_at and the nightly repair used
    -- check_in + standard_checkin_time instead (04 spec 3.3 step 8)
    checkin_time_inferred BOOLEAN NOT NULL DEFAULT FALSE,
    language          TEXT,
    stay_chain_id     BIGINT,
    booking_ref       TEXT,
    rooms_count       INT,
    promo_code        TEXT,
    utm               JSONB NOT NULL DEFAULT '{}',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS gst_stays_reservation_idx ON gst_stays (hotel_slug, reservation_id) WHERE reservation_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS gst_stays_roster_idx ON gst_stays (hotel_slug, room, check_in) WHERE origin = 'roster';
CREATE INDEX IF NOT EXISTS gst_stays_dates_idx ON gst_stays (hotel_slug, check_in, check_out);
CREATE INDEX IF NOT EXISTS gst_stays_profile_idx ON gst_stays (guest_profile_id) WHERE guest_profile_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS gst_stays_chain_idx ON gst_stays (hotel_slug, stay_chain_id) WHERE stay_chain_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS gst_direct_codes (
    id                       BIGSERIAL PRIMARY KEY,
    code                     TEXT NOT NULL,
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    -- NULL for J8 codes: a J8 recipient may have no profile_memory consent
    guest_profile_id         BIGINT REFERENCES gst_profiles (id) ON DELETE SET NULL,
    discount_pct             NUMERIC(5, 2),
    valid_to                 DATE,
    redeemed_at              TIMESTAMPTZ,
    redeemed_reservation_id  BIGINT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, code)
);
