-- Demand Radar, rates side (02 spec 4.1-4.2, Canon 2, 12, K3): the hotel's
-- comp set and its indicative competitor rates (SerpApi Google Hotels,
-- Lighthouse/RateGain CSV, Xotelo demo, MakCorps stub), the hotel's own BAR
-- calendar with its change history, the owner's rate guardrails, and the
-- Open-Meteo weather cache behind demand.api.outdoor_temp_c.
--
-- Money is BIGINT minor units plus CHAR(3) currency (Canon 2). Every comp
-- rate is indicative: it is a public shopping price, never a contract rate,
-- and the UI labels it so (there is no Booking Demand API adapter because
-- its terms forbid price comparison).

-- ─── Rate settings (one row per hotel) ───

CREATE TABLE IF NOT EXISTS dr_rate_settings (
    hotel_slug           TEXT PRIMARY KEY REFERENCES hotels (slug),
    -- Google Hotels `q` strings (demand.api.area_queries); empty = the
    -- generic "hotels in <city>" query.
    area_queries         TEXT[] NOT NULL DEFAULT '{}',
    -- The hotel's own Google Hotels token, so an area page never lists the
    -- hotel as its own competitor.
    self_property_token  TEXT,
    -- The room type a NULL room_type_code means in price_guardrails.
    reference_room_type  TEXT,
    comp_fetch_enabled   BOOLEAN NOT NULL DEFAULT TRUE,
    -- SerpApi per-property calls per run for comps missing from the area page
    -- (nights 0-14 only; 0 = area pages only).
    fallback_calls_max   SMALLINT NOT NULL DEFAULT 5 CHECK (fallback_calls_max BETWEEN 0 AND 20),
    updated_by           BIGINT REFERENCES users (id),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Comp set ───

CREATE TABLE IF NOT EXISTS dr_comp_set (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    comp_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    property_token  TEXT,
    place_id        TEXT,
    xotelo_key      TEXT,
    makcorps_id     TEXT,
    lat             NUMERIC(9, 6),
    lon             NUMERIC(9, 6),
    stars           NUMERIC(2, 1),
    rooms           INT,
    aliases         TEXT[] NOT NULL DEFAULT '{}',
    is_demo         BOOLEAN NOT NULL DEFAULT FALSE,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    origin          TEXT NOT NULL DEFAULT 'manual' CHECK (origin IN ('manual', 'discovered', 'csv', 'demo')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, comp_id)
);

-- One comp per Google token per hotel, so an area page maps each property
-- to at most one comp.
CREATE UNIQUE INDEX IF NOT EXISTS dr_comp_set_token_uq
    ON dr_comp_set (hotel_slug, property_token) WHERE property_token IS NOT NULL;

-- ─── Comp rates (indicative) ───

CREATE TABLE IF NOT EXISTS dr_comp_rates (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL,
    comp_id            TEXT NOT NULL,
    stay_date          DATE NOT NULL,
    los                SMALLINT NOT NULL DEFAULT 1 CHECK (los BETWEEN 1 AND 30),
    adults             SMALLINT NOT NULL DEFAULT 2 CHECK (adults BETWEEN 1 AND 8),
    captured_at        TIMESTAMPTZ NOT NULL,
    -- Hotel-local capture day: one row per comp, night, source and day, so
    -- the history shows how a comp moved its price as the night approached.
    captured_on        DATE NOT NULL,
    source             TEXT NOT NULL CHECK (source IN (
                           'SERPAPI', 'MAKCORPS', 'XOTELO', 'CSV_LIGHTHOUSE', 'CSV_RATEGAIN')),
    -- Per night, in `currency`. NULL only for a sold-out comp: sold out is a
    -- strong demand signal, never a zero price (02 spec 4.4 edge cases).
    lowest_minor       BIGINT CHECK (lowest_minor IS NULL OR lowest_minor > 0),
    currency           CHAR(3) NOT NULL,
    sold_out           BOOLEAN NOT NULL DEFAULT FALSE,
    -- [{source, minor, official?}] per booking site where the feed has it.
    by_source          JSONB NOT NULL DEFAULT '[]',
    indicative         BOOLEAN NOT NULL DEFAULT TRUE,
    -- Xotelo quotes USD only: the row keeps the vendor amount and is flagged.
    fx_converted       BOOLEAN NOT NULL DEFAULT FALSE,
    original_minor     BIGINT,
    original_currency  CHAR(3),
    query              TEXT,
    import_batch_id    BIGINT,
    CHECK (sold_out OR lowest_minor IS NOT NULL),
    FOREIGN KEY (hotel_slug, comp_id) REFERENCES dr_comp_set (hotel_slug, comp_id),
    UNIQUE (hotel_slug, comp_id, stay_date, los, adults, source, captured_on)
);

CREATE INDEX IF NOT EXISTS dr_comp_rates_night_idx
    ON dr_comp_rates (hotel_slug, stay_date, captured_at DESC);

-- ─── BAR calendar (the hotel's own best available rate per night) ───

CREATE TABLE IF NOT EXISTS dr_bar_calendar (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    stay_date       DATE NOT NULL,
    room_type_code  TEXT NOT NULL,
    bar_minor       BIGINT NOT NULL CHECK (bar_minor > 0),
    currency        CHAR(3) NOT NULL,
    source          TEXT NOT NULL CHECK (source IN ('MANUAL', 'CSV', 'RATE_EVIDENCE')),
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      BIGINT REFERENCES users (id),
    note            TEXT,
    PRIMARY KEY (hotel_slug, stay_date, room_type_code)
);

-- Every BAR move, so an APPLIED recommendation can be verified against a
-- later import (02 spec 4.3 measurement: new BAR within +-2%).
CREATE TABLE IF NOT EXISTS dr_bar_changes (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    stay_date       DATE NOT NULL,
    room_type_code  TEXT NOT NULL,
    old_minor       BIGINT,
    new_minor       BIGINT,
    currency        CHAR(3) NOT NULL,
    source          TEXT NOT NULL,
    actor           BIGINT REFERENCES users (id),
    at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS dr_bar_changes_night_idx
    ON dr_bar_changes (hotel_slug, stay_date, room_type_code, at DESC);

-- ─── Rate guardrails (owner floors and ceilings) ───

-- room_type_code '*' = every room type; month and dow NULL = any. The most
-- specific row wins (room type, then month, then weekday).
CREATE TABLE IF NOT EXISTS dr_rate_guardrails (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    room_type_code  TEXT NOT NULL DEFAULT '*',
    month           SMALLINT CHECK (month BETWEEN 1 AND 12),
    dow             SMALLINT CHECK (dow BETWEEN 1 AND 7),
    floor_minor     BIGINT NOT NULL CHECK (floor_minor > 0),
    ceiling_minor   BIGINT NOT NULL,
    currency        CHAR(3) NOT NULL,
    max_step_pct    NUMERIC(5, 2) NOT NULL DEFAULT 15 CHECK (max_step_pct > 0 AND max_step_pct <= 100),
    note            TEXT,
    updated_by      BIGINT REFERENCES users (id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (ceiling_minor >= floor_minor)
);

-- COALESCE rather than NULLS NOT DISTINCT: works on every Postgres we deploy.
CREATE UNIQUE INDEX IF NOT EXISTS dr_rate_guardrails_scope_uq
    ON dr_rate_guardrails (hotel_slug, room_type_code, (COALESCE(month, 0)), (COALESCE(dow, 0)));

-- ─── Weather (Open-Meteo hourly, no key; Canon 12) ───

CREATE TABLE IF NOT EXISTS dr_weather (
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    at_hour       TIMESTAMPTZ NOT NULL,
    temp_c        NUMERIC(4, 1),
    apparent_c    NUMERIC(4, 1),
    humidity_pct  SMALLINT,
    precip_mm     NUMERIC(6, 2),
    weather_code  SMALLINT,
    wind_kmh      NUMERIC(5, 1),
    is_forecast   BOOLEAN NOT NULL DEFAULT TRUE,
    fetched_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (hotel_slug, at_hour)
);
