-- 0820_trust_evals: eval scenarios, runs and results, the change watcher that
-- triggers runs, and readiness-gate overrides (03 spec 5.4).
--
-- Scenario rows with hotel_slug NULL are the standard set, synced from
-- trust/evals/standard/*.yaml on startup and before every run; a hotel's own
-- rows are its custom scenarios and its auto-KB suite (2 paraphrases per
-- approved KB entry).

-- ─── Scenarios ───
CREATE TABLE IF NOT EXISTS trs_eval_scenarios (
    id            SERIAL PRIMARY KEY,
    hotel_slug    TEXT REFERENCES hotels (slug),          -- NULL = standard set (every hotel)
    suite         TEXT NOT NULL CHECK (suite IN ('standard', 'auto_kb', 'custom')),
    key           TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'general',        -- safety | routing | unknowns | ... (the gate reads it)
    title         TEXT NOT NULL DEFAULT '',
    surface       TEXT NOT NULL DEFAULT 'web_room',
    language      TEXT NOT NULL DEFAULT 'en',
    turns         JSONB NOT NULL,                         -- [{role: user, text}]
    context       JSONB NOT NULL DEFAULT '{}'::jsonb,     -- {room, month, ...}
    expectations  JSONB NOT NULL DEFAULT '{}'::jsonb,     -- the DSL (evals/dsl.py)
    kb_entry_id   INT,                                    -- auto_kb: the concierge entry it paraphrases
    tags          TEXT[] NOT NULL DEFAULT '{}',
    flaky         BOOLEAN NOT NULL DEFAULT FALSE,         -- run 3 times, decided by majority
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    source_hash   TEXT,                                   -- standard: sha256 of the YAML item; auto_kb: of the entry text
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- One key per suite per hotel; the standard set shares the NULL hotel.
CREATE UNIQUE INDEX IF NOT EXISTS trs_eval_scenarios_key_uidx
    ON trs_eval_scenarios (COALESCE(hotel_slug, ''), suite, key);
CREATE INDEX IF NOT EXISTS trs_eval_scenarios_hotel_idx ON trs_eval_scenarios (hotel_slug, suite) WHERE active;

-- ─── Runs ───
CREATE TABLE IF NOT EXISTS trs_eval_runs (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    trigger         TEXT NOT NULL CHECK (trigger IN ('manual', 'nightly', 'kb_change', 'prompt_change', 'ci')),
    status          TEXT NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'completed', 'failed', 'skipped')),
    suites          TEXT[] NOT NULL DEFAULT '{standard,auto_kb,custom}',
    prompt_version  TEXT,
    kb_fingerprint  TEXT,
    model           TEXT,
    judge_model     TEXT,
    runner          TEXT,                                  -- concierge_text | responses | none
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    total           INT NOT NULL DEFAULT 0,
    passed          INT NOT NULL DEFAULT 0,
    pass_rate       NUMERIC(6, 4),
    -- {category: {total, passed, rate}}: the gate reads safety and routing here.
    category_rates  JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Vendor cost in micro-USD (Canon K3); cost_usd is the spec's display column.
    cost_micros     BIGINT NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(12, 6) GENERATED ALWAYS AS (cost_micros / 1000000.0) STORED,
    requested_by    BIGINT REFERENCES users (id),
    error           TEXT,
    note            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS trs_eval_runs_hotel_idx ON trs_eval_runs (hotel_slug, created_at DESC);

-- ─── Results ───
CREATE TABLE IF NOT EXISTS trs_eval_results (
    id               BIGSERIAL PRIMARY KEY,
    run_id           BIGINT NOT NULL REFERENCES trs_eval_runs (id) ON DELETE CASCADE,
    scenario_id      INT NOT NULL REFERENCES trs_eval_scenarios (id) ON DELETE CASCADE,
    scenario_key     TEXT NOT NULL,
    suite            TEXT NOT NULL,
    category         TEXT NOT NULL,
    passed           BOOLEAN NOT NULL,
    score            NUMERIC(6, 4),
    attempts         INT NOT NULL DEFAULT 1,
    transcript       JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_calls       JSONB NOT NULL DEFAULT '[]'::jsonb,
    grader_outputs   JSONB NOT NULL DEFAULT '[]'::jsonb,
    failure_reasons  TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, scenario_id)
);
CREATE INDEX IF NOT EXISTS trs_eval_results_run_idx ON trs_eval_results (run_id, passed);

-- ─── Change watcher ───
-- The trigger for "KB approve (10-minute debounce)" and "prompt_version
-- change". Trust may not hook Concierge internals, so a 5-minute job reads
-- the eval session's prompt_version and a fingerprint of the approved KB
-- through concierge.api and enqueues a run once a change has been quiet for
-- 10 minutes. changed_at is when the current values were first seen.
CREATE TABLE IF NOT EXISTS trs_eval_watch (
    hotel_slug      TEXT PRIMARY KEY REFERENCES hotels (slug),
    prompt_version  TEXT,
    kb_fingerprint  TEXT,
    changed_at      TIMESTAMPTZ,
    pending_trigger TEXT,                                  -- kb_change | prompt_change, NULL when settled
    last_run_id     BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Readiness overrides ───
-- An owner may take concierge.live on with the gate red. Each override is
-- also written to audit_log (trust.readiness.override); this table is what
-- the gate reads, and an override lapses when a newer run completes.
CREATE TABLE IF NOT EXISTS trs_readiness_overrides (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    reason       TEXT NOT NULL,
    failing      JSONB NOT NULL DEFAULT '[]'::jsonb,       -- the check keys that were red
    run_id       BIGINT REFERENCES trs_eval_runs (id) ON DELETE SET NULL,
    created_by   BIGINT REFERENCES users (id),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at   TIMESTAMPTZ NOT NULL,
    revoked_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS trs_readiness_overrides_idx ON trs_readiness_overrides (hotel_slug, created_at DESC);
