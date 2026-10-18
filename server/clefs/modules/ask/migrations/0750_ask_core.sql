-- Ask Clefs (02 spec 7): the question log with feedback, saved views that
-- re-run without the LLM, and the WhatsApp Q&A opt-in. The metric registry
-- itself is foundation code (clefs.core.metrics); Ask stores no numbers of
-- its own, only what was asked, which governed query answered it, and what
-- the owner thought of the answer.

-- ─── Question log ───

CREATE TABLE IF NOT EXISTS ask_logs (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    user_id         BIGINT REFERENCES users (id),
    channel         TEXT NOT NULL DEFAULT 'WEB' CHECK (channel IN ('WEB', 'WHATSAPP', 'VIEW')),
    question        TEXT NOT NULL,
    -- [{name, arguments, ok, error?}]: the model's calls as validated (never SQL)
    tool_calls      JSONB NOT NULL DEFAULT '[]',
    -- {answer_text, table, chart_spec, trace, suggestions}: exactly what the caller saw
    answer          JSONB NOT NULL DEFAULT '{}',
    answer_text     TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'answered'
                    CHECK (status IN ('answered', 'table_only', 'unsupported', 'refused', 'error')),
    unsupported     BOOLEAN NOT NULL DEFAULT FALSE,
    reason          TEXT,                             -- refusal / unsupported / error code
    grounded        BOOLEAN,                          -- NULL when no numbers were written
    regenerated     BOOLEAN NOT NULL DEFAULT FALSE,
    saved_view_id   BIGINT,
    latency_ms      INTEGER,
    tokens          INTEGER,
    feedback        SMALLINT CHECK (feedback IN (-1, 1)),
    feedback_note   TEXT,
    feedback_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ask_logs_hotel_idx ON ask_logs (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS ask_logs_user_idx ON ask_logs (hotel_slug, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ask_logs_feedback_idx ON ask_logs (hotel_slug, feedback) WHERE feedback IS NOT NULL;

-- ─── Saved views: the validated tool arguments, re-run without the LLM ───

CREATE TABLE IF NOT EXISTS ask_saved_views (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    user_id         BIGINT REFERENCES users (id),
    title           TEXT NOT NULL CHECK (length(title) BETWEEN 1 AND 120),
    query           JSONB NOT NULL,                   -- query_metric arguments (keys only, no SQL)
    question        TEXT,                             -- the question that produced it, if any
    source_log_id   BIGINT REFERENCES ask_logs (id) ON DELETE SET NULL,
    pinned          BOOLEAN NOT NULL DEFAULT FALSE,
    last_run_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ask_saved_views_user_idx ON ask_saved_views (hotel_slug, user_id, pinned DESC, created_at DESC);

-- ─── WhatsApp Q&A opt-in (owner, gm and finance members only) ───

CREATE TABLE IF NOT EXISTS ask_wa_optins (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    user_id         BIGINT NOT NULL REFERENCES users (id),
    opted_in        BOOLEAN NOT NULL,
    method          TEXT NOT NULL DEFAULT 'console',  -- console | admin
    last_asked_at   TIMESTAMPTZ,                      -- picks the hotel for a multi-hotel member
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, user_id)
);

CREATE INDEX IF NOT EXISTS ask_wa_optins_user_idx ON ask_wa_optins (user_id) WHERE opted_in;
