-- Demand Radar, engine side (02 spec 4.3-4.4): per-hotel event relevance
-- and its learned class weights, calibration from the hotel's own pickup,
-- the daily demand index, rate recommendations (never pushed to a channel
-- manager), graph guidance (staffing, KB, pre-arrival offer), export-only
-- campaign drafts, the Google Hotel Center checklist and the public
-- demand-preview leads.
--
-- Money is BIGINT minor units plus CHAR(3) currency (Canon 2). Actions live
-- in core `actions`; these tables only keep their id (no FK: actions is core
-- and may be archived independently).

-- ─── Engine settings (one row per hotel; defaults live in code) ───
CREATE TABLE IF NOT EXISTS dr_engine_settings (
    hotel_slug         TEXT PRIMARY KEY REFERENCES hotels (slug),
    corridor_tags      TEXT[] NOT NULL DEFAULT '{}',
    -- {venue_code: minutes}: a measured travel time overrides the km decay
    travel_minutes     JSONB NOT NULL DEFAULT '{}',
    stayover_ratio     NUMERIC(4, 3),
    credits_per_attendant NUMERIC(5, 2),
    commission_share   NUMERIC(4, 3),
    updated_by         BIGINT REFERENCES users (id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Relevance of an event to a hotel ───
CREATE TABLE IF NOT EXISTS dr_event_relevance (
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    event_id            UUID NOT NULL REFERENCES dr_events (id),
    class_key           TEXT NOT NULL,
    distance_km         NUMERIC(8, 2),
    travel_band         TEXT,
    corridor_match      BOOLEAN NOT NULL DEFAULT FALSE,
    score               NUMERIC(6, 4) NOT NULL DEFAULT 0,
    expected_rn_uplift  NUMERIC(10, 2),
    nights              JSONB NOT NULL DEFAULT '{}',    -- {iso date: weight}
    dismissed           BOOLEAN NOT NULL DEFAULT FALSE,
    dismiss_reason      TEXT,
    features            JSONB NOT NULL DEFAULT '{}',
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, event_id)
);
CREATE INDEX IF NOT EXISTS dr_event_relevance_score_idx ON dr_event_relevance (hotel_slug, score DESC);

-- class_key is CATEGORY or CATEGORY:VENUE (w_class = (useful+2)/(useful+dismissed+3)).
CREATE TABLE IF NOT EXISTS dr_event_class_prefs (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    class_key   TEXT NOT NULL,
    useful      INT NOT NULL DEFAULT 0 CHECK (useful >= 0),
    dismissed   INT NOT NULL DEFAULT 0 CHECK (dismissed >= 0),
    muted       BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, class_key)
);

-- k = sum(lift) / sum(score) per event class from the hotel's own pickup.
CREATE TABLE IF NOT EXISTS dr_calibration (
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    category     TEXT NOT NULL,
    k            NUMERIC(8, 4) NOT NULL,
    n_events     INT NOT NULL DEFAULT 0,
    fit          NUMERIC(4, 3) NOT NULL DEFAULT 0,
    source       TEXT NOT NULL CHECK (source IN ('own', 'prior')),
    detail       JSONB NOT NULL DEFAULT '[]',
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, category)
);

-- ─── Demand index per stay night ───
CREATE TABLE IF NOT EXISTS dr_demand_days (
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    stay_date      DATE NOT NULL,
    demand_index   SMALLINT NOT NULL CHECK (demand_index BETWEEN 0 AND 100),
    components     JSONB NOT NULL DEFAULT '{}',
    coverage       NUMERIC(4, 3) NOT NULL DEFAULT 0,
    top_event_ids  UUID[] NOT NULL DEFAULT '{}',
    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, stay_date)
);

-- ─── Rate recommendations ───
CREATE TABLE IF NOT EXISTS dr_rate_recommendations (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    stay_date          DATE NOT NULL,
    room_type_code     TEXT NOT NULL,
    currency           CHAR(3) NOT NULL,
    current_bar_minor  BIGINT NOT NULL,
    recommended_minor  BIGINT NOT NULL,
    floor_minor        BIGINT,
    ceiling_minor      BIGINT,
    decided_minor      BIGINT,
    change_pct         NUMERIC(6, 2) NOT NULL,
    levers             JSONB NOT NULL DEFAULT '[]',
    rationale          TEXT NOT NULL DEFAULT '',
    why                TEXT NOT NULL DEFAULT '',
    why_source         TEXT NOT NULL DEFAULT 'template' CHECK (why_source IN ('llm', 'template')),
    facts              JSONB NOT NULL DEFAULT '{}',
    driver_event_ids   UUID[] NOT NULL DEFAULT '{}',
    demand_index       SMALLINT,
    confidence         NUMERIC(4, 3) NOT NULL DEFAULT 0,
    impact_minor       BIGINT NOT NULL DEFAULT 0,
    status             TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN (
                           'PROPOSED', 'APPROVED', 'ADJUSTED', 'REJECTED', 'SUPERSEDED', 'EXPIRED', 'APPLIED')),
    status_reason      TEXT,
    action_id          BIGINT,
    baseline           JSONB NOT NULL DEFAULT '{}',
    outcome            JSONB,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at         TIMESTAMPTZ,
    decided_by         BIGINT REFERENCES users (id),
    applied_at         TIMESTAMPTZ,
    measured_at        TIMESTAMPTZ,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- One open recommendation per night and room type; a newer one supersedes it.
CREATE UNIQUE INDEX IF NOT EXISTS dr_rate_recommendations_open_uq
    ON dr_rate_recommendations (hotel_slug, stay_date, room_type_code) WHERE status = 'PROPOSED';
CREATE INDEX IF NOT EXISTS dr_rate_recommendations_hotel_idx ON dr_rate_recommendations (hotel_slug, stay_date);
CREATE INDEX IF NOT EXISTS dr_rate_recommendations_action_idx ON dr_rate_recommendations (action_id);

-- ─── Graph guidance from one event (staffing, KB update, pre-arrival offer) ───
CREATE TABLE IF NOT EXISTS dr_guidance (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    event_id    UUID REFERENCES dr_events (id),
    kind        TEXT NOT NULL CHECK (kind IN ('STAFFING', 'KB_UPDATE', 'PRE_ARRIVAL_OFFER')),
    stay_date   DATE,
    dedupe_key  TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'PROPOSED' CHECK (status IN ('PROPOSED', 'APPROVED', 'DONE', 'DISMISSED', 'EXPIRED')),
    action_id   BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (hotel_slug, dedupe_key)
);
CREATE INDEX IF NOT EXISTS dr_guidance_event_idx ON dr_guidance (event_id);

-- ─── Campaign drafts (export-only: CSV of audience refs plus the copy) ───
CREATE TABLE IF NOT EXISTS dr_campaign_drafts (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    event_id           UUID REFERENCES dr_events (id),
    channel            TEXT NOT NULL CHECK (channel IN ('WHATSAPP', 'INSTAGRAM', 'EMAIL')),
    audience           JSONB NOT NULL DEFAULT '{}',
    copy               JSONB NOT NULL DEFAULT '{}',
    copy_source        TEXT NOT NULL DEFAULT 'template' CHECK (copy_source IN ('llm', 'template')),
    suggested_send_at  TIMESTAMPTZ,
    status             TEXT NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT', 'APPROVED', 'EXPORTED', 'ARCHIVED')),
    action_id          BIGINT,
    created_by         BIGINT REFERENCES users (id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    exported_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS dr_campaign_drafts_hotel_idx ON dr_campaign_drafts (hotel_slug, created_at DESC);

-- ─── Google Hotel Center checklist ───
CREATE TABLE IF NOT EXISTS dr_ghc_checklist (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    item_code   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'TODO' CHECK (status IN ('TODO', 'DONE', 'NA')),
    note        TEXT,
    updated_by  BIGINT REFERENCES users (id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, item_code)
);

-- ─── Public demand-preview leads (no hotel yet; contacts sealed) ───
CREATE TABLE IF NOT EXISTS dr_leads (
    id                        UUID PRIMARY KEY,
    hotel_name                TEXT NOT NULL,
    city                      TEXT NOT NULL,
    area                      TEXT,
    rooms                     INT,
    contact_name              TEXT,
    contact_role              TEXT,
    email_enc                 BYTEA,
    whatsapp_enc              BYTEA,
    consent_processing        BOOLEAN NOT NULL,
    consent_transfer_abroad   BOOLEAN NOT NULL,
    consent_contact_email     BOOLEAN NOT NULL DEFAULT FALSE,
    consent_contact_whatsapp  BOOLEAN NOT NULL DEFAULT FALSE,
    ip_hash                   BYTEA,
    preview                   JSONB NOT NULL DEFAULT '{}',
    status                    TEXT NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW', 'CONTACTED', 'CLOSED')),
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at                TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS dr_leads_expiry_idx ON dr_leads (expires_at);
