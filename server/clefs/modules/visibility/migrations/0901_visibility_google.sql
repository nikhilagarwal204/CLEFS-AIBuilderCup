-- Google Hotels observations (04 spec 5.2): organic rank, official price
-- presence and parity for one (query, date pair) per row. Money in minor
-- units of `currency` (the hotel's, which google_hotels_area_params asks for).

CREATE TABLE IF NOT EXISTS vis_google_hotels_obs (
    id                     BIGSERIAL PRIMARY KEY,
    hotel_slug             TEXT NOT NULL REFERENCES hotels (slug),
    observed_at            TIMESTAMPTZ NOT NULL,
    query                  TEXT,
    check_in               DATE,
    check_out              DATE,
    pages_fetched          SMALLINT,
    organic_rank           SMALLINT,                  -- NULL = not found in the fetched pages
    organic_total          SMALLINT,                  -- non-sponsored properties across the fetched pages
    sponsored_count        SMALLINT,
    official_present       BOOLEAN,                   -- NULL = property details not fetched
    official_price_minor   BIGINT,
    min_other_price_minor  BIGINT,
    min_other_source       TEXT,
    min_other_channel      TEXT,                      -- channels.code when the source maps to one
    price_basis            TEXT,                      -- total_rate | rate_per_night (like for like)
    parity_gap_minor       BIGINT,
    parity_gap_pct         NUMERIC(7, 3),
    prices                 JSONB,                     -- the price list, kept as evidence
    currency               CHAR(3) NOT NULL,
    serp_cache_key         TEXT
);

CREATE INDEX IF NOT EXISTS vis_gh_obs_hotel_idx ON vis_google_hotels_obs (hotel_slug, observed_at DESC);
CREATE INDEX IF NOT EXISTS vis_gh_obs_stay_idx ON vis_google_hotels_obs (hotel_slug, check_in, observed_at DESC);
