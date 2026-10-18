-- 0821_trust_pilots: pilot scorecards, daily checkpoints and anonymised
-- benchmark cohorts (03 spec 5.5). Money is BIGINT minor units + CHAR(3)
-- (Canon 2): the spec's conversion_price_aed becomes conversion_price_minor.

-- ─── Pilots ───
CREATE TABLE IF NOT EXISTS trs_pilots (
    id                      BIGSERIAL PRIMARY KEY,
    hotel_slug              TEXT NOT NULL REFERENCES hotels (slug),
    name                    TEXT NOT NULL DEFAULT 'Pilot',
    modules                 TEXT[] NOT NULL DEFAULT '{}',
    start_date              DATE NOT NULL,
    end_date                DATE NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'active', 'completed', 'converted', 'cancelled')),
    -- [{metric_key, comparator, target, weight, label}]; comparator is one of
    -- gte, lte, gt, lt, improve_pct, reduce_pct (the last two against the baseline).
    success_criteria        JSONB NOT NULL DEFAULT '[]'::jsonb,
    baseline_start          DATE,
    baseline_end            DATE,
    baseline_metrics        JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {metric_key: value}
    conversion_price_minor  BIGINT,
    currency                CHAR(3) NOT NULL DEFAULT 'AED',
    terms                   JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {notes, billing_period, on_track_threshold}
    outcome                 JSONB NOT NULL DEFAULT '{}'::jsonb,   -- the last evaluation (report header)
    created_by              BIGINT REFERENCES users (id),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_date >= start_date),
    CHECK (baseline_end IS NULL OR baseline_start IS NULL OR baseline_end >= baseline_start)
);
CREATE INDEX IF NOT EXISTS trs_pilots_hotel_idx ON trs_pilots (hotel_slug, status);

CREATE TABLE IF NOT EXISTS trs_pilot_checkpoints (
    pilot_id    BIGINT NOT NULL REFERENCES trs_pilots (id) ON DELETE CASCADE,
    date        DATE NOT NULL,                             -- hotel-local day the checkpoint covers up to
    "values"    JSONB NOT NULL DEFAULT '{}'::jsonb,        -- {metric_key: {value, target, met, status}}
    score       NUMERIC(6, 4),
    on_track    BOOLEAN NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (pilot_id, date)
);

-- ─── Benchmarks ───
-- Opt-in is per hotel (03 spec 5.5: only opted-in hotels are ever counted).
CREATE TABLE IF NOT EXISTS trs_benchmark_optins (
    hotel_slug  TEXT PRIMARY KEY REFERENCES hotels (slug),
    opted_in    BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by  BIGINT REFERENCES users (id),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- criteria: {country, emirate, category, star_min, star_max, rooms_min, rooms_max}
CREATE TABLE IF NOT EXISTS trs_benchmark_cohorts (
    id          SERIAL PRIMARY KEY,
    key         TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL,
    criteria    JSONB NOT NULL DEFAULT '{}'::jsonb,
    metrics     TEXT[] NOT NULL DEFAULT '{}',
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- p50 and band cut-offs are stored only when n_hotels >= 5: with n = 3 a
-- median is another hotel's exact value. Each hotel's own third is kept in
-- trs_benchmark_positions and is the only per-hotel figure ever served.
CREATE TABLE IF NOT EXISTS trs_benchmark_snapshots (
    id           BIGSERIAL PRIMARY KEY,
    cohort_id    INT NOT NULL REFERENCES trs_benchmark_cohorts (id) ON DELETE CASCADE,
    metric_key   TEXT NOT NULL,
    period       TEXT NOT NULL,                            -- YYYY-MM
    n_hotels     INT NOT NULL,
    p50          NUMERIC(20, 6),
    bands        JSONB NOT NULL DEFAULT '{}'::jsonb,       -- {cutoffs: [p33, p67]} only when n >= 5
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (n_hotels >= 3),
    CHECK (n_hotels >= 5 OR p50 IS NULL),
    UNIQUE (cohort_id, metric_key, period)
);

CREATE TABLE IF NOT EXISTS trs_benchmark_positions (
    snapshot_id  BIGINT NOT NULL REFERENCES trs_benchmark_snapshots (id) ON DELETE CASCADE,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    band         TEXT NOT NULL CHECK (band IN ('top', 'middle', 'bottom')),
    PRIMARY KEY (snapshot_id, hotel_slug)
);
