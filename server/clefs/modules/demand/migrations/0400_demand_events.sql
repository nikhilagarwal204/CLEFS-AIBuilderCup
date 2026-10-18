-- Demand Radar, events side (02 spec 4.1-4.2): venues with corridor tags,
-- merged events with their per-source rows and change history, holidays
-- (UAE and source markets), per-hotel source-market shares, PredictHQ
-- Features per day, and the incremental sync cursors of the adapters.
--
-- Events are global (hotel_slug NULL: PredictHQ, Ticketmaster, the curated
-- seed) or hotel-scoped (manual, ICS, AI web discovery). A hotel-scoped
-- source never edits a global event: it only attaches as corroboration, so
-- one tenant's input cannot move another tenant's calendar.

-- ─── Venues ───

CREATE TABLE IF NOT EXISTS dr_venues (
    code              TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    lat               NUMERIC(9, 6),
    lon               NUMERIC(9, 6),
    emirate           TEXT,
    corridor_tags     TEXT[] NOT NULL DEFAULT '{}',
    aliases           TEXT[] NOT NULL DEFAULT '{}',
    capacity          INT,
    feed_available    BOOLEAN NOT NULL DEFAULT FALSE,
    coords_verified   BOOLEAN NOT NULL DEFAULT FALSE,
    last_verified_at  DATE,
    source_url        TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Events (one row per real-world event after dedup) ───

CREATE TABLE IF NOT EXISTS dr_events (
    id                         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hotel_slug                 TEXT REFERENCES hotels (slug),
    canonical_key              TEXT NOT NULL,
    title                      TEXT NOT NULL,
    category                   TEXT NOT NULL CHECK (category IN (
                                   'CONFERENCE', 'EXPO', 'SPORTS', 'CONCERT', 'FESTIVAL', 'PUBLIC_HOLIDAY',
                                   'SCHOOL_HOLIDAY', 'RELIGIOUS', 'SOURCE_MARKET_HOLIDAY', 'COMMUNITY', 'CITYWIDE')),
    venue_codes                TEXT[] NOT NULL DEFAULT '{}',
    lat                        NUMERIC(9, 6),
    lon                        NUMERIC(9, 6),
    emirate                    TEXT,
    start_date                 DATE NOT NULL,
    end_date                   DATE NOT NULL,
    status                     TEXT NOT NULL DEFAULT 'SCHEDULED'
                                   CHECK (status IN ('SCHEDULED', 'RESCHEDULED', 'POSTPONED', 'CANCELLED')),
    original_start             DATE,
    original_end               DATE,
    attendance_est             INT,
    attendance_source          TEXT,
    phq_rank                   INT,
    phq_local_rank             INT,
    -- PredictHQ's accommodation spend estimate, a vendor figure in USD
    -- minor units (Canon 2: money is BIGINT minor + currency).
    predicted_accom_spend_minor BIGINT,
    predicted_accom_spend_currency CHAR(3),
    moon_dependent             BOOLEAN NOT NULL DEFAULT FALSE,
    date_uncertainty_days      INT NOT NULL DEFAULT 0,
    verification               TEXT NOT NULL DEFAULT 'UNVERIFIED' CHECK (verification IN ('VERIFIED', 'UNVERIFIED')),
    confidence                 NUMERIC(4, 3) NOT NULL DEFAULT 0.5,
    date_source                TEXT,
    source_urls                JSONB NOT NULL DEFAULT '[]',
    description                TEXT,
    owner_confirmed_at         TIMESTAMPTZ,
    owner_confirmed_by         BIGINT REFERENCES users (id),
    merged_into                UUID REFERENCES dr_events (id),
    first_seen_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_checked_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS dr_events_dates_idx ON dr_events (start_date, end_date) WHERE merged_into IS NULL;
CREATE INDEX IF NOT EXISTS dr_events_hotel_idx ON dr_events (hotel_slug, start_date) WHERE hotel_slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS dr_events_key_idx ON dr_events (canonical_key);

-- ─── Per-source rows (what each source said, kept for precedence) ───

CREATE TABLE IF NOT EXISTS dr_event_sources (
    id            BIGSERIAL PRIMARY KEY,
    event_id      UUID NOT NULL REFERENCES dr_events (id),
    source        TEXT NOT NULL CHECK (source IN ('PREDICTHQ', 'TICKETMASTER', 'CURATED', 'AI_WEB', 'MANUAL', 'ICS')),
    external_id   TEXT NOT NULL,
    hotel_slug    TEXT REFERENCES hotels (slug),
    fields        JSONB NOT NULL DEFAULT '{}',
    payload       JSONB NOT NULL DEFAULT '{}',
    confidence    NUMERIC(4, 3),
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, external_id)
);
CREATE INDEX IF NOT EXISTS dr_event_sources_event_idx ON dr_event_sources (event_id);

-- ─── Change history (reschedules, cancellations, corrections) ───

CREATE TABLE IF NOT EXISTS dr_event_changes (
    id         BIGSERIAL PRIMARY KEY,
    event_id   UUID NOT NULL REFERENCES dr_events (id),
    at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    field      TEXT NOT NULL,
    old        JSONB,
    new        JSONB,
    source     TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT 'update' CHECK (reason IN ('rescheduled', 'correction', 'status', 'update', 'merge')),
    source_url TEXT
);
CREATE INDEX IF NOT EXISTS dr_event_changes_event_idx ON dr_event_changes (event_id, at);

-- ─── Holidays (UAE and source markets; Hijri dates are moon_dependent) ───

CREATE TABLE IF NOT EXISTS dr_holidays (
    id               BIGSERIAL PRIMARY KEY,
    country_code     TEXT NOT NULL,
    name             TEXT NOT NULL,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('PUBLIC', 'SCHOOL', 'RELIGIOUS')),
    moon_dependent   BOOLEAN NOT NULL DEFAULT FALSE,
    uncertainty_days INT NOT NULL DEFAULT 0,
    verification     TEXT NOT NULL DEFAULT 'VERIFIED' CHECK (verification IN ('VERIFIED', 'UNVERIFIED')),
    source_url       TEXT,
    seed_key         TEXT UNIQUE,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS dr_holidays_dates_idx ON dr_holidays (start_date, end_date);

-- ─── Source markets (per hotel; a market is an ISO2 country or a region) ───

CREATE TABLE IF NOT EXISTS dr_source_markets (
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    market_code  TEXT NOT NULL,
    share_pct    NUMERIC(5, 2) NOT NULL CHECK (share_pct >= 0 AND share_pct <= 100),
    source       TEXT NOT NULL DEFAULT 'owner',
    updated_by   BIGINT REFERENCES users (id),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, market_code)
);

-- ─── PredictHQ Features API, one value per hotel, day and feature ───

CREATE TABLE IF NOT EXISTS dr_demand_features_daily (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    date        DATE NOT NULL,
    feature     TEXT NOT NULL,
    value       NUMERIC(18, 4) NOT NULL,
    source      TEXT NOT NULL DEFAULT 'PREDICTHQ',
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, date, feature)
);

-- ─── Adapter cursors (PredictHQ updated.gte, weekly runs, curated hash) ───

CREATE TABLE IF NOT EXISTS dr_sync_state (
    source       TEXT NOT NULL,
    scope        TEXT NOT NULL,
    cursor       TEXT,
    last_run_at  TIMESTAMPTZ,
    last_result  JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (source, scope)
);
