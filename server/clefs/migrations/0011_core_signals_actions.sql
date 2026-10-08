-- 0011_core_signals_actions: the detect -> explain -> approve -> execute -> verify loop.
--
--   signals                 candidate brief items any module emits (Canon 6), upserted by dedupe_key
--   actions                 the ledger, with the Canon 7 state machine and the autonomy-dial
--                           ledger columns (Canon 17, 04 spec 1.3)
--   action_events           every transition, decision and execution, append-only
--   action_autonomy         the dial per (hotel, action type)
--   action_autonomy_events  seeds, changes, promotion suggestions and demotions
--
-- Money is BIGINT minor units + CHAR(3) currency (Canon 2); APIs expose Decimal strings.

-- ─── Signals ───

CREATE TABLE IF NOT EXISTS signals (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    module              TEXT NOT NULL,
    kind                TEXT NOT NULL,                     -- "<module>.<what>", registered with register_signal_kind
    dedupe_key          TEXT NOT NULL,
    section             TEXT NOT NULL CHECK (section IN (
                            'money_lost', 'money_available', 'service_risk', 'reviews', 'verified', 'compliance')),
    cadence             TEXT NOT NULL DEFAULT 'daily' CHECK (cadence IN ('daily', 'weekly', 'monthly')),
    title               TEXT NOT NULL,
    summary             TEXT,
    severity            SMALLINT NOT NULL DEFAULT 2 CHECK (severity BETWEEN 1 AND 4),
    impact_minor        BIGINT,
    currency            CHAR(3),
    impact_kind         TEXT,                              -- recoverable | revenue_upside | cost_leak | risk | service
    confidence          NUMERIC(3, 2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    evidence            JSONB NOT NULL DEFAULT '[]',
    recommended_action  JSONB,
    owner_role          TEXT,
    owner_user_id       BIGINT REFERENCES users (id),
    due_at              TIMESTAMPTZ,
    subject_type        TEXT,
    subject_id          TEXT,
    status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN (
                            'open', 'surfaced', 'decided', 'snoozed', 'resolved', 'expired',
                            'ignored', 'superseded', 'suppressed')),
    snoozed_until       TIMESTAMPTZ,
    -- Impact when a person closed it (ignored / decided / snoozed): the detector
    -- reopens it only if the impact later grows past reopen_if_impact_grows_pct.
    closed_impact_minor BIGINT,
    occurrences         INT NOT NULL DEFAULT 1,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    surfaced_at         TIMESTAMPTZ,
    resolved_at         TIMESTAMPTZ,
    resolution          TEXT,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, dedupe_key),
    CHECK (impact_minor IS NULL OR currency IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS signals_hotel_status_idx ON signals (hotel_slug, status, due_at);
CREATE INDEX IF NOT EXISTS signals_kind_idx ON signals (hotel_slug, module, kind, status);
CREATE INDEX IF NOT EXISTS signals_snoozed_idx ON signals (snoozed_until) WHERE status = 'snoozed';

-- ─── Actions ───

CREATE TABLE IF NOT EXISTS actions (
    id                       BIGSERIAL PRIMARY KEY,
    hotel_slug               TEXT NOT NULL REFERENCES hotels (slug),
    signal_id                BIGINT REFERENCES signals (id),
    module                   TEXT NOT NULL,
    type                     TEXT NOT NULL,                -- a registered ActionType key
    subtype                  TEXT,                         -- 'dispute' once the dispute path starts
    title                    TEXT NOT NULL,
    params                   JSONB NOT NULL DEFAULT '{}',
    status                   TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
                                 -- main path
                                 'proposed', 'approved', 'assigned', 'in_progress', 'done', 'verified', 'not_verified',
                                 -- side states (observed: the dial logged it and nothing else, 04 spec 1.1)
                                 'snoozed', 'rejected', 'cancelled', 'observed',
                                 -- dispute path
                                 'submitted', 'awaiting_counterparty', 'rejected_by_counterparty', 'unresolved')),
    snoozed_from             TEXT,
    snoozed_until            TIMESTAMPTZ,
    surface                  TEXT CHECK (surface IN ('brief', 'console', 'none')),
    dedupe_key               TEXT,
    decided_by               BIGINT REFERENCES users (id),
    decided_at               TIMESTAMPTZ,
    decided_via              TEXT CHECK (decided_via IN ('console', 'whatsapp', 'email', 'api', 'system', 'dial')),
    human_decided            BOOLEAN NOT NULL DEFAULT FALSE,
    edited                   BOOLEAN NOT NULL DEFAULT FALSE, -- approved with a params_patch
    assignee_user_id         BIGINT REFERENCES users (id),
    assignee_staff_id        BIGINT,                         -- staff_members.id (service-owned table: no FK)
    assignee_role            TEXT,
    owner_role               TEXT,
    due_at                   TIMESTAMPTZ,
    executed_at              TIMESTAMPTZ,
    execution_result         JSONB,
    execution_attempts       INT NOT NULL DEFAULT 0,
    execution_error          TEXT,
    verify_after             TIMESTAMPTZ,
    next_verify_at           TIMESTAMPTZ,
    verify_until             TIMESTAMPTZ,
    verified_at              TIMESTAMPTZ,
    verification             JSONB,
    expected_minor           BIGINT,
    outcome_minor            BIGINT,
    currency                 CHAR(3),
    outcome_confidence       NUMERIC(3, 2),
    counts_toward_verified_profit BOOLEAN NOT NULL DEFAULT FALSE,
    attribution              TEXT CHECK (attribution IN ('caused', 'influenced', 'observed')),
    reversed_at              TIMESTAMPTZ,
    reversal_reason          TEXT,
    -- Autonomy-dial ledger (Canon: Autonomy dial; 04 spec 1.3)
    dial_level_at_creation   TEXT,
    dial_level_at_execution  TEXT,
    policy_version           INT,
    limit_reasons            TEXT[] NOT NULL DEFAULT '{}',
    template_id              TEXT,                         -- "meta_name@version"
    template_category        TEXT,
    consent_basis            TEXT,
    reversal_handle          JSONB,
    -- Brief owns reminders and escalation; the counters live here.
    reminders_sent           INT NOT NULL DEFAULT 0,
    last_reminded_at         TIMESTAMPTZ,
    escalation_level         SMALLINT NOT NULL DEFAULT 0,
    escalated_at             TIMESTAMPTZ,
    created_by               BIGINT REFERENCES users (id),
    created_via              TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (expected_minor IS NULL OR currency IS NOT NULL),
    CHECK (outcome_minor IS NULL OR currency IS NOT NULL)
);
CREATE UNIQUE INDEX IF NOT EXISTS actions_dedupe_idx ON actions (hotel_slug, dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS actions_hotel_status_idx ON actions (hotel_slug, status, due_at);
CREATE INDEX IF NOT EXISTS actions_signal_idx ON actions (signal_id) WHERE signal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS actions_type_idx ON actions (hotel_slug, type, created_at DESC);
CREATE INDEX IF NOT EXISTS actions_verify_idx ON actions (next_verify_at)
    WHERE status IN ('done', 'submitted', 'awaiting_counterparty');
CREATE INDEX IF NOT EXISTS actions_snoozed_idx ON actions (snoozed_until) WHERE status = 'snoozed';

CREATE TABLE IF NOT EXISTS action_events (
    id           BIGSERIAL PRIMARY KEY,
    action_id    BIGINT NOT NULL REFERENCES actions (id),
    hotel_slug   TEXT NOT NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actor_type   TEXT NOT NULL,                            -- user | device | service | system | dial
    actor_id     TEXT,
    actor_label  TEXT,
    event        TEXT NOT NULL,                            -- created | approve | adjust | assign | ... | executed | verified
    from_status  TEXT,
    to_status    TEXT,
    via          TEXT,
    -- WhatsApp button nonces are single-use for state-changing verbs.
    nonce        TEXT,
    data         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS action_events_action_idx ON action_events (action_id, id);
CREATE INDEX IF NOT EXISTS action_events_stats_idx ON action_events (hotel_slug, event, at);
CREATE UNIQUE INDEX IF NOT EXISTS action_events_nonce_idx ON action_events (nonce) WHERE nonce IS NOT NULL;

-- ─── Autonomy dial (Canon 17) ───

CREATE TABLE IF NOT EXISTS action_autonomy (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    action_type     TEXT NOT NULL,
    level           TEXT NOT NULL CHECK (level IN ('off', 'observe', 'suggest', 'approve', 'auto_within_limits', 'auto')),
    limits          JSONB NOT NULL DEFAULT '{}',
    policy_version  INT NOT NULL DEFAULT 1,
    reason          TEXT,
    updated_by      BIGINT REFERENCES users (id),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, action_type)
);

CREATE TABLE IF NOT EXISTS action_autonomy_events (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    action_type     TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN (
                        'seed', 'set', 'promotion_suggested', 'promotion_accepted', 'promotion_dismissed', 'demoted')),
    from_level      TEXT,
    to_level        TEXT,
    trigger         TEXT CHECK (trigger IN ('guest_complaint', 'optout_spike', 'meta_quality', 'eval_regression', 'manual')),
    policy_version  INT,
    evidence        JSONB NOT NULL DEFAULT '[]',
    stats           JSONB,
    actor_type      TEXT,
    actor_id        TEXT,
    at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS action_autonomy_events_type_idx ON action_autonomy_events (hotel_slug, action_type, at DESC);
