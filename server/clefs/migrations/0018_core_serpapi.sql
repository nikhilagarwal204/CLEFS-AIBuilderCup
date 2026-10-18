-- 0018_core_serpapi: the shared SerpApi response cache and its cost ledger
-- (04 spec F5, Canon amendment K3).
--
-- Every SerpApi call in the product goes through clefs.core.serpapi.search, so
-- Demand and Visibility building identical Google Hotels params share one
-- billed call, and SERPAPI_MONTHLY_BUDGET_USD is a real global budget.
--
-- Both tables are intentionally global: a cached response is keyed by its
-- request, not by hotel (two hotels in Deira asking the same area query share
-- it), and the ledger carries hotel_slug only for attribution (NULL for the
-- public lead magnet, which has no hotel).
--
-- Vendor costs are sub-cent, so they are held in micro-units (K3):
-- 1 USD = 1,000,000 micros, 1 minor unit of USD = 10,000 micros.
--
-- Nothing here uses a percent sign, so the file is safe whatever way the
-- runner hands it to the driver.

-- ─── Cache ───

CREATE TABLE IF NOT EXISTS serpapi_cache (
    cache_key   TEXT PRIMARY KEY,                -- sha1 hex of engine + sorted normalised params
    engine      TEXT NOT NULL,
    params      JSONB NOT NULL,                  -- the normalised params; never api_key or no_cache
    response    JSONB NOT NULL,
    -- ok: results; empty: SerpApi answered "no results" (billed, so cached too)
    status      TEXT NOT NULL CHECK (status IN ('ok', 'empty')),
    fetched_at  TIMESTAMPTZ NOT NULL,
    -- When core.serpapi.purge may drop the row. Freshness is decided per call
    -- by its max_age against fetched_at, not by this column.
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS serpapi_cache_expires_idx ON serpapi_cache (expires_at);

-- ─── Ledger ───
-- One row per search() call: cache hits (free) and upstream calls. A call is
-- written as 'reserved' (billed, at its expected cost) before the HTTP request
-- so concurrent callers cannot overshoot the budget, then settled to 'ok' or
-- to 'error' (billed = false, cost 0; SerpApi does not charge failed searches).

CREATE TABLE IF NOT EXISTS serpapi_calls (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT REFERENCES hotels (slug),
    purpose        TEXT NOT NULL,                -- <module>.<what> or 'public_check'
    engine         TEXT NOT NULL,
    cache_key      TEXT NOT NULL,
    cache_hit      BOOLEAN NOT NULL,
    billed         BOOLEAN NOT NULL,
    cost_micros    BIGINT NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
    cost_currency  CHAR(3) NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL CHECK (status IN ('cache_hit', 'reserved', 'ok', 'error')),
    error          TEXT,
    latency_ms     INT,
    at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Month-to-date spend (the global budget) and the public_check cap.
CREATE INDEX IF NOT EXISTS serpapi_calls_billed_at_idx ON serpapi_calls (at) WHERE billed;
CREATE INDEX IF NOT EXISTS serpapi_calls_purpose_at_idx ON serpapi_calls (purpose, at) WHERE billed;
-- Per-hotel spend (Visibility's monthly budget, 04 spec 5.10).
CREATE INDEX IF NOT EXISTS serpapi_calls_hotel_at_idx ON serpapi_calls (hotel_slug, at);
