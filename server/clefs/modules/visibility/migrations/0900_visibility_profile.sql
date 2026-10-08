-- Visibility profile and competitor mirror (04 spec 5.1).
--
-- vis_profile is the module's per-hotel configuration: identifiers Google
-- knows the hotel by (Places id, GBP location, Google Hotels property token),
-- the inputs the rank keywords and AI prompts are templated from, and the
-- monthly vendor budget. It is a table rather than hotel_module_settings
-- because other jobs join on it and the AI lane adds engine columns here.
-- Google Hotels area queries are NOT stored: they come from
-- demand.api.area_queries so Demand and Visibility share one billed call.

CREATE TABLE IF NOT EXISTS vis_profile (
    hotel_slug            TEXT PRIMARY KEY REFERENCES hotels (slug),
    place_id              TEXT,
    gbp_location          TEXT,                       -- "locations/123..." picked after the OAuth connect
    gh_property_token     TEXT,                       -- SerpApi google_hotels property_token
    website_url           TEXT,
    booking_engine_host   TEXT,
    brand_aliases         TEXT[],
    rank_keywords         TEXT[],                     -- NULL = generated from the templates (5.3)
    area                  TEXT,
    landmarks             TEXT[],
    room_page_urls        TEXT[],                     -- extra pages the JSON-LD check reads (5.6)
    free_booking_link_pattern TEXT,                   -- utm_campaign LIKE pattern counted as Google (5.8)
    grid_size             SMALLINT NOT NULL DEFAULT 7 CHECK (grid_size BETWEEN 3 AND 9),
    grid_spacing_m        INT NOT NULL DEFAULT 800 CHECK (grid_spacing_m BETWEEN 100 AND 5000),
    ai_markets            JSONB NOT NULL DEFAULT '[]',
    monthly_budget_minor  BIGINT NOT NULL DEFAULT 3000 CHECK (monthly_budget_minor >= 0),
    budget_currency       CHAR(3) NOT NULL DEFAULT 'USD',
    engines_enabled       TEXT[] NOT NULL DEFAULT '{gemini_web_search,serpapi_ai_mode}',
    serpapi_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    updated_by            BIGINT,
    updated_at            TIMESTAMPTZ
);

-- Synced daily from demand.api.comp_set() (visibility.competitors_sync), so
-- rank and SoV jobs can join competitors without calling Demand per row.
CREATE TABLE IF NOT EXISTS vis_competitors (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    comp_id         TEXT NOT NULL,
    name            TEXT,
    place_id        TEXT,
    property_token  TEXT,
    aliases         TEXT[],
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, comp_id)
);
