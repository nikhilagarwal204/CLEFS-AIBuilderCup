-- Site health (04 spec 5.6) and the module's vendor-cost ledger (5.10).
-- 0904 is the AI radar's migration (built by the AI part of this lane).

CREATE TABLE IF NOT EXISTS vis_site_health (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    checked_at     TIMESTAMPTZ,
    url            TEXT,
    crux           JSONB,                             -- {status, metrics:{lcp_ms, inp_ms, cls}, outside_good[]}
    psi            JSONB,
    jsonld_found   BOOLEAN,
    jsonld_types   TEXT[],
    jsonld_fields  JSONB,
    jsonld_issues  JSONB,
    facts_vs_kb    JSONB
);

CREATE INDEX IF NOT EXISTS vis_site_health_hotel_idx ON vis_site_health (hotel_slug, checked_at DESC);

-- Costs are micro-units (Canon K3). SerpApi spend is in core's serpapi_calls
-- ledger; this table holds every other paid call (Gemini, Perplexity) plus
-- the global public-check and snapshot spend, which has no hotel.
CREATE TABLE IF NOT EXISTS vis_api_costs (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT REFERENCES hotels (slug),
    provider       TEXT NOT NULL,
    purpose        TEXT NOT NULL,
    cost_micros    BIGINT NOT NULL,
    cost_currency  CHAR(3) NOT NULL DEFAULT 'USD',
    at             TIMESTAMPTZ NOT NULL,
    CHECK (hotel_slug IS NOT NULL OR purpose IN ('public_check', 'snapshot'))
);

CREATE INDEX IF NOT EXISTS vis_api_costs_hotel_idx ON vis_api_costs (hotel_slug, at);
