-- Map rank grid (04 spec 5.3): one run per keyword per week over a 7x7 grid
-- of Places Text Search (IDs only) calls, and the monthly SerpApi Maps
-- calibration of that ordering.

CREATE TABLE IF NOT EXISTS vis_rank_runs (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    run_at        TIMESTAMPTZ NOT NULL,
    keyword       TEXT NOT NULL,
    source        TEXT NOT NULL CHECK (source IN ('places_ids', 'serpapi_maps')),
    points        SMALLINT,
    found         SMALLINT,
    arp           NUMERIC(5, 2),                      -- mean rank where found
    atrp          NUMERIC(5, 2),                      -- mean rank, not found = 21
    solv          NUMERIC(5, 4),                      -- share of points ranked 1-3
    comp_metrics  JSONB,                              -- {comp_id: {arp, atrp, solv, found}}
    calls         INT
);

CREATE INDEX IF NOT EXISTS vis_rank_runs_hotel_idx ON vis_rank_runs (hotel_slug, keyword, run_at DESC);

CREATE TABLE IF NOT EXISTS vis_rank_points (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    run_id      BIGINT NOT NULL REFERENCES vis_rank_runs (id) ON DELETE CASCADE,
    idx         SMALLINT NOT NULL,
    lat         NUMERIC(9, 6),
    lng         NUMERIC(9, 6),
    rank        SMALLINT,                             -- 1-20, 21 = not found
    comp_ranks  JSONB,
    PRIMARY KEY (run_id, idx)
);

CREATE TABLE IF NOT EXISTS vis_rank_calibration (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    month           DATE NOT NULL,
    keyword         TEXT NOT NULL,
    spearman        NUMERIC(4, 3),
    top3_agreement  NUMERIC(4, 3),
    status          TEXT CHECK (status IN ('ok', 'drift')),
    PRIMARY KEY (hotel_slug, month, keyword)
);
