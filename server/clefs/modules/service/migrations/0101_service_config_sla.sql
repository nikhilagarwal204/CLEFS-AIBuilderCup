-- 0101_service_config_sla: per-hotel overrides of departments, request types
-- and SLA policies, the escalation log, and the recovery configuration
-- (03 spec 1.1 "0001", "0003", "0005").
--
-- Global defaults (the UAE seeds: departments, request types, the 3/15 towel
-- SLA, the 0/3/5/10 ladder, the authority matrix) live in Python
-- (service/constants.py), not in NULL-hotel rows: a default that changes after
-- a review reaches every hotel that never overrode it, and it is testable
-- without a database. These tables hold only what a hotel changed.

CREATE TABLE IF NOT EXISTS svc_departments (
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    key              TEXT NOT NULL,
    name             TEXT NOT NULL,
    name_ar          TEXT,
    -- the value still written to staff_requests.category for old clients
    legacy_category  TEXT NOT NULL,
    color            TEXT,
    active           BOOLEAN NOT NULL DEFAULT TRUE,
    sort             INT NOT NULL DEFAULT 0,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, key)
);

CREATE TABLE IF NOT EXISTS svc_request_types (
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    key               TEXT NOT NULL,
    label_en          TEXT NOT NULL,
    label_ar          TEXT,
    department_key    TEXT NOT NULL,
    default_severity  TEXT NOT NULL DEFAULT 'L3' CHECK (default_severity IN ('L1', 'L2', 'L3')),
    is_amenity        BOOLEAN NOT NULL DEFAULT FALSE,
    sensitive         BOOLEAN NOT NULL DEFAULT FALSE,
    legacy_type       TEXT,
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, key)
);

-- scope_type: request_type | department_severity | department | severity | default.
-- The most specific match wins (request_type, then department+severity, then
-- department, then severity, then the hotel default), a hotel row before the
-- global seed at the same scope.
CREATE TABLE IF NOT EXISTS svc_sla_policies (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    scope_type    TEXT NOT NULL CHECK (scope_type IN ('request_type', 'department_severity', 'department', 'severity', 'default')),
    scope_key     TEXT NOT NULL DEFAULT '',
    ack_min       INT NOT NULL CHECK (ack_min >= 0),
    resolve_min   INT NOT NULL CHECK (resolve_min > 0),
    -- window-based policies (turndown): due at this hotel-local time
    due_local     TIME,
    -- [{"after_min": 0, "to": "assignee"}, {"after_min": 3, "to": "supervisor"}, ...]
    ladder        JSONB,
    -- {"outdoor_temp_c_gt": 40, "then_severity": "L1"}
    conditions    JSONB NOT NULL DEFAULT '{}',
    active        BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (hotel_slug, scope_type, scope_key)
);

-- One row per ladder step crossed (per reopen cycle). shadow rows were computed
-- and shown but sent nothing external (the 7-day calibration period).
CREATE TABLE IF NOT EXISTS svc_escalations (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    request_id   BIGINT NOT NULL,
    cycle        INT NOT NULL DEFAULT 0,
    level        SMALLINT NOT NULL,
    target_role  TEXT NOT NULL,
    recipients   JSONB NOT NULL DEFAULT '[]',
    channels     TEXT[] NOT NULL DEFAULT '{}',
    shadow       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    acked_at     TIMESTAMPTZ,
    notify_ids   BIGINT[] NOT NULL DEFAULT '{}',
    UNIQUE (request_id, cycle, level)
);
CREATE INDEX IF NOT EXISTS svc_escalations_hotel_idx ON svc_escalations (hotel_slug, created_at DESC);

-- ─── Recovery configuration (03 spec 1.1 "0005") ───

CREATE TABLE IF NOT EXISTS svc_complaint_categories (
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    key             TEXT NOT NULL,
    label_en        TEXT NOT NULL,
    label_ar        TEXT,
    department_key  TEXT,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (hotel_slug, key)
);

-- Who may give what without asking (Canon 2: minor units + currency; NULL
-- max_minor = unlimited, the GM's row).
CREATE TABLE IF NOT EXISTS svc_compensation_authority (
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    role_key       TEXT NOT NULL CHECK (role_key IN ('staff', 'supervisor', 'duty_manager', 'gm')),
    max_minor      BIGINT CHECK (max_minor IS NULL OR max_minor >= 0),
    currency       CHAR(3) NOT NULL,
    allowed_kinds  TEXT[] NOT NULL DEFAULT '{}',
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, role_key)
);
