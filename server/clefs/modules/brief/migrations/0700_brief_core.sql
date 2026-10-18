-- Owner Brief core tables (02 spec 6.1, 6.4; Canon 18 cadence tiers).
-- The action state machine, the autonomy dial and the signals live in the
-- foundation (actions, action_events, signals); Brief only records what it
-- put in front of whom, the escalation ladder it walked, and its reports.

-- ─── Runs: one assembled brief per hotel, cadence and local date ───

CREATE TABLE IF NOT EXISTS brf_runs (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    cadence         TEXT NOT NULL CHECK (cadence IN ('daily', 'weekly', 'monthly')),
    brief_date      DATE NOT NULL,                    -- hotel-local date the brief is for
    period_start    DATE NOT NULL,                    -- the window its blocks summarise
    period_end      DATE NOT NULL,
    scheduled_local TIMESTAMPTZ,                      -- the slot that produced it (NULL: send-now)
    generated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger         TEXT NOT NULL DEFAULT 'schedule' CHECK (trigger IN ('schedule', 'send_now', 'api')),
    status          TEXT NOT NULL DEFAULT 'generated'
                    CHECK (status IN ('generated', 'sent', 'partial', 'failed', 'empty')),
    headline        TEXT NOT NULL DEFAULT '',
    currency        CHAR(3) NOT NULL,
    at_stake_minor  BIGINT NOT NULL DEFAULT 0,        -- sum of the ranked items' impact
    payload         JSONB NOT NULL DEFAULT '{}',      -- rendered blocks (verified line, autonomy, finance, ...)
    delivery        JSONB NOT NULL DEFAULT '[]',      -- [{user_id, channel, ok, status, outbox_id, error}]
    sent_at         TIMESTAMPTZ,
    created_by      BIGINT REFERENCES users (id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- A scheduled brief exists once per day; send-now runs stack beside it.
CREATE UNIQUE INDEX IF NOT EXISTS brf_runs_scheduled_uniq
    ON brf_runs (hotel_slug, cadence, brief_date) WHERE trigger = 'schedule';
CREATE INDEX IF NOT EXISTS brf_runs_hotel_idx ON brf_runs (hotel_slug, cadence, brief_date DESC, id DESC);

-- ─── Items: the ranked top 5 (plus pinned overdue items) of one run ───

CREATE TABLE IF NOT EXISTS brf_items (
    id                   BIGSERIAL PRIMARY KEY,
    run_id               BIGINT NOT NULL REFERENCES brf_runs (id) ON DELETE CASCADE,
    hotel_slug           TEXT NOT NULL REFERENCES hotels (slug),
    item_no              SMALLINT NOT NULL,             -- 1-based position shown to the owner
    kind                 TEXT NOT NULL CHECK (kind IN ('ranked', 'overdue', 'digest')),
    section              TEXT NOT NULL,
    detector             TEXT,                          -- the signal kind (detector cap key)
    signal_id            BIGINT REFERENCES signals (id),
    action_id            BIGINT REFERENCES actions (id),
    title                TEXT NOT NULL,
    impact_minor         BIGINT,
    currency             CHAR(3),
    confidence           NUMERIC(4, 3),
    importance           NUMERIC(6, 4),
    urgency              NUMERIC(6, 4),
    score                NUMERIC(8, 6),
    deadline_at          TIMESTAMPTZ,
    owner_role           TEXT,
    responsible_user_id  BIGINT REFERENCES users (id),
    responsible_staff_id BIGINT,                        -- staff_members.id (service-owned: no FK)
    responsible_label    TEXT,
    evidence_url         TEXT,
    recommended_action   JSONB,
    UNIQUE (run_id, item_no)
);
CREATE INDEX IF NOT EXISTS brf_items_signal_idx ON brf_items (signal_id);
CREATE INDEX IF NOT EXISTS brf_items_action_idx ON brf_items (action_id);

-- ─── Links: the magic links a run minted (the token lives in magic_links) ───

CREATE TABLE IF NOT EXISTS brf_links (
    id           BIGSERIAL PRIMARY KEY,
    token_hash   BYTEA NOT NULL UNIQUE,                 -- hash_token(raw), same as magic_links.token_hash
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    user_id      BIGINT NOT NULL REFERENCES users (id),
    run_id       BIGINT NOT NULL REFERENCES brf_runs (id) ON DELETE CASCADE,
    scope        JSONB NOT NULL DEFAULT '{}',           -- {"action_ids": [...]} (CONTRACTS Part 2, magic sessions)
    expires_at   TIMESTAMPTZ NOT NULL,                  -- 72 h (02 spec 6.1)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS brf_links_run_idx ON brf_links (run_id);

-- ─── Escalations: one row per (action, level), so every level fires once ───

CREATE TABLE IF NOT EXISTS brf_escalations (
    action_id    BIGINT NOT NULL REFERENCES actions (id) ON DELETE CASCADE,
    level        SMALLINT NOT NULL CHECK (level BETWEEN 1 AND 3),
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    due_at       TIMESTAMPTZ NOT NULL,                  -- the action deadline the ladder counts from
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    notified     JSONB NOT NULL DEFAULT '[]',           -- [{user_id|staff_id, channel, ok, status}]
    PRIMARY KEY (action_id, level)
);
CREATE INDEX IF NOT EXISTS brf_escalations_hotel_idx ON brf_escalations (hotel_slug, at DESC);

-- ─── Reports: the weekly verified-value report (HTML + PDF) ───

CREATE TABLE IF NOT EXISTS brf_reports (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    kind          TEXT NOT NULL CHECK (kind IN ('weekly')),
    period_key    TEXT NOT NULL,                        -- ISO week "2026-W40"
    period_start  DATE NOT NULL,
    period_end    DATE NOT NULL,
    currency      CHAR(3) NOT NULL,
    payload       JSONB NOT NULL DEFAULT '{}',
    html          TEXT NOT NULL DEFAULT '',
    pdf_file_id   UUID REFERENCES files (id) ON DELETE SET NULL,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, kind, period_key)
);
