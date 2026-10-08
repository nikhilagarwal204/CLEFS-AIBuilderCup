-- AI visibility radar (04 spec 5.5, 5.9 snapshot). Idempotent.
--
-- Deviations from the 5.1 sketch, each needed by the executor:
-- * vis_ai_runs.status adds 'pending' (a planned repeat not yet run) and
--   'timeout' (retried once in a later step), plus `attempts`, so a batch is
--   planned up front and the step task picks units without a side table.
-- * vis_ai_batches adds requester_ref (Profit Guard's audit id for a
--   snapshot), plan (repeats, degradation steps taken) and finished_at.
-- * vis_ai_runs.prompt_key and facts_checked_at: the integrity rule groups
--   repeats by prompt_key, and fact extraction runs in resumable chunks.
-- * vis_ai_weekly adds sov_ci_low/high and computed_at; market, language and
--   entity are NOT NULL (primary key), entity is 'self', a comp_id or 'other'.
-- * vis_engine_versions.status: an AI Mode source shift is only 'suggested'
--   until a person accepts the baseline reset.
-- * vis_ai_runs.cited_domains (TEXT[]) denormalises the citation hosts so the
--   rollup's source mix and the fact router never re-parse JSONB.

CREATE TABLE IF NOT EXISTS vis_ai_prompts (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    prompt_key  TEXT NOT NULL,
    version     INT NOT NULL,
    text        TEXT NOT NULL,
    language    TEXT NOT NULL,
    market      CHAR(2) NOT NULL,
    city        TEXT,
    intent      TEXT NOT NULL CHECK (intent IN ('generic', 'persona', 'event', 'fact_check', 'brand')),
    fact_key    TEXT,
    event_id    UUID,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    retired_at  TIMESTAMPTZ,
    UNIQUE (hotel_slug, prompt_key, version)
);

CREATE INDEX IF NOT EXISTS vis_ai_prompts_active_idx ON vis_ai_prompts (hotel_slug) WHERE active;

CREATE TABLE IF NOT EXISTS vis_ai_batches (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT REFERENCES hotels (slug),
    kind           TEXT NOT NULL CHECK (kind IN ('weekly', 'snapshot')),
    week           DATE,
    subject        JSONB,
    requester_ref  TEXT,
    plan           JSONB,
    planned        INT,
    done           INT NOT NULL DEFAULT 0,
    cost_micros    BIGINT NOT NULL DEFAULT 0,
    cost_currency  CHAR(3) NOT NULL DEFAULT 'USD',
    status         TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ready', 'failed')),
    expires_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at    TIMESTAMPTZ,
    CHECK (kind = 'snapshot' OR hotel_slug IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS vis_ai_batches_hotel_idx ON vis_ai_batches (hotel_slug, kind, created_at DESC);
CREATE INDEX IF NOT EXISTS vis_ai_batches_snapshot_idx ON vis_ai_batches (kind, created_at) WHERE kind = 'snapshot';

CREATE TABLE IF NOT EXISTS vis_ai_runs (
    id              BIGSERIAL PRIMARY KEY,
    batch_id        BIGINT NOT NULL REFERENCES vis_ai_batches (id) ON DELETE CASCADE,
    batch_kind      TEXT NOT NULL CHECK (batch_kind IN ('weekly', 'snapshot')),
    hotel_slug      TEXT REFERENCES hotels (slug),
    prompt_id       BIGINT,
    prompt_key      TEXT,
    prompt_intent   TEXT,
    prompt_text     TEXT,
    market          CHAR(2),
    language        TEXT,
    engine          TEXT NOT NULL CHECK (engine IN ('gemini_web_search', 'perplexity_sonar', 'serpapi_ai_mode')),
    model_id        TEXT,
    baseline_epoch  INT NOT NULL DEFAULT 1,
    locale          JSONB NOT NULL DEFAULT '{}',
    repeat_idx      SMALLINT,
    run_at          TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending', 'timeout', 'ok', 'error', 'empty', 'discarded')),
    attempts        SMALLINT NOT NULL DEFAULT 0,
    searched        BOOLEAN,
    answer_text     TEXT,
    raw             JSONB,
    citations       JSONB,
    cited_domains   TEXT[],
    entities        JSONB,
    self_mentioned  BOOLEAN,
    self_position   SMALLINT,
    self_cited      BOOLEAN,
    cost_micros     BIGINT,
    cost_currency   CHAR(3) NOT NULL DEFAULT 'USD',
    parser_version  TEXT,
    facts_checked_at TIMESTAMPTZ,                     -- fact extraction ran on this answer (rollup, in chunks)
    CHECK (batch_kind = 'snapshot' OR hotel_slug IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS vis_ai_runs_batch_idx ON vis_ai_runs (batch_id, status);
CREATE INDEX IF NOT EXISTS vis_ai_runs_hotel_idx ON vis_ai_runs (hotel_slug, run_at DESC);

-- One row per cell: engine x market x language x entity ('self', a comp_id,
-- or 'other') within one baseline_epoch. Discovery prompts only (brand and
-- fact-check prompts name the hotel, so they would inflate the rate).
CREATE TABLE IF NOT EXISTS vis_ai_weekly (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    week            DATE NOT NULL,
    window_weeks    SMALLINT NOT NULL CHECK (window_weeks IN (1, 4)),
    engine          TEXT NOT NULL,
    market          CHAR(2) NOT NULL,
    language        TEXT NOT NULL,
    entity          TEXT NOT NULL,
    baseline_epoch  INT,
    runs            INT,
    mentions        INT,
    rate            NUMERIC(5, 4),
    ci_low          NUMERIC(5, 4),
    ci_high         NUMERIC(5, 4),
    avg_position    NUMERIC(4, 2),
    sov             NUMERIC(5, 4),
    sov_ci_low      NUMERIC(5, 4),
    sov_ci_high     NUMERIC(5, 4),
    sufficient      BOOLEAN,
    source_mix      JSONB,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, week, window_weeks, engine, market, language, entity)
);

CREATE TABLE IF NOT EXISTS vis_engine_versions (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    engine          TEXT NOT NULL,
    version_key     TEXT NOT NULL,
    baseline_epoch  INT NOT NULL,
    first_seen_at   TIMESTAMPTZ NOT NULL,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suggested', 'dismissed')),
    PRIMARY KEY (hotel_slug, engine, version_key)
);

CREATE TABLE IF NOT EXISTS vis_ai_facts (
    id             BIGSERIAL PRIMARY KEY,
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    fingerprint    TEXT NOT NULL,
    fact_key       TEXT NOT NULL,
    claimed_value  TEXT,
    claimed_quote  TEXT,
    truth_value    TEXT,
    truth_source   TEXT,
    error_type     TEXT CHECK (error_type IN ('wrong_value', 'false_absent', 'false_closed', 'wrong_star',
                                              'price_band_off', 'unverifiable')),
    severity       SMALLINT,
    engines        TEXT[],
    cited_domains  TEXT[],
    occurrences    INT NOT NULL DEFAULT 1,
    run_ids        BIGINT[],
    status         TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'fixing', 'fixed', 'dismissed', 'recurred')),
    first_seen     TIMESTAMPTZ,
    last_seen      TIMESTAMPTZ,
    action_id      BIGINT,
    UNIQUE (hotel_slug, fingerprint)
);

CREATE INDEX IF NOT EXISTS vis_ai_facts_open_idx ON vis_ai_facts (hotel_slug, status);

-- Wilson 95% half-width, shared by the rollup, the UI gate and the metric
-- SQL so Ask and the console hide exactly the same cells (5.5, 5.11).
CREATE OR REPLACE FUNCTION vis_wilson_halfwidth(k NUMERIC, n NUMERIC) RETURNS NUMERIC
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN n > 0 THEN
        (1.96 * sqrt((k / n) * (1 - k / n) / n + 1.96 ^ 2 / (4 * n ^ 2))) / (1 + 1.96 ^ 2 / n)
    END
$$;
