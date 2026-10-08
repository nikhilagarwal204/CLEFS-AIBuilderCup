-- 0103_service_incidents_recovery: incidents (emergencies, sensitive
-- pathways, staff SOS), lost and found, and service recovery cases with the
-- gestures given (03 spec 1.1 "0004", "0005" and 1.5).
--
-- Money (Canon 2): gesture values are BIGINT minor units + CHAR(3) currency.

CREATE TABLE IF NOT EXISTS svc_incidents (
    id               BIGSERIAL PRIMARY KEY,
    hotel_slug       TEXT NOT NULL REFERENCES hotels (slug),
    -- a safety kind (medical, fire, harassment, ...) or staff_sos
    kind             TEXT NOT NULL,
    level            TEXT NOT NULL DEFAULT 'emergency' CHECK (level IN ('emergency', 'sensitive', 'handoff')),
    severity         TEXT NOT NULL DEFAULT 'L1' CHECK (severity IN ('L1', 'L2', 'L3')),
    room             TEXT,
    floor            TEXT,
    request_id       BIGINT,
    summary          TEXT,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
    opened_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at        TIMESTAMPTZ,
    -- {"type": "device|user|guest|system", "staff_id", "user_id", "device_id", "label"}
    opened_by        JSONB NOT NULL DEFAULT '{}',
    closed_by        JSONB,
    close_note       TEXT,
    device_id        BIGINT,
    staff_member_id  INT
);
CREATE INDEX IF NOT EXISTS svc_incidents_hotel_idx ON svc_incidents (hotel_slug, opened_at DESC);
CREATE INDEX IF NOT EXISTS svc_incidents_open_idx ON svc_incidents (hotel_slug) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS svc_lost_found (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    found_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    location     TEXT,
    room         TEXT,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'stored' CHECK (status IN ('stored', 'claimed', 'returned', 'disposed')),
    file_id      TEXT,
    claimed_by   TEXT,
    created_by   JSONB NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS svc_lost_found_hotel_idx ON svc_lost_found (hotel_slug, found_at DESC);

-- ─── Service recovery ───

CREATE TABLE IF NOT EXISTS svc_recovery_cases (
    id                  BIGSERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    request_id          BIGINT,
    room                TEXT,
    guest_ref           TEXT,
    category_key        TEXT,
    severity            TEXT NOT NULL DEFAULT 'L2' CHECK (severity IN ('L1', 'L2', 'L3')),
    status              TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'closed')),
    -- why it opened: complaint | not_resolved | post_checkout | manual
    reason              TEXT NOT NULL DEFAULT 'manual',
    -- LEARN checkboxes: {"listen": true, "empathize": true, "apologize": false, "resolve": false, "notify": false}
    learn               JSONB NOT NULL DEFAULT '{}',
    notes               TEXT,
    opened_by_staff_id  INT,
    opened_by_user_id   BIGINT,
    opened_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at           TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS svc_recovery_cases_hotel_idx ON svc_recovery_cases (hotel_slug, opened_at DESC);
CREATE INDEX IF NOT EXISTS svc_recovery_cases_open_idx ON svc_recovery_cases (hotel_slug, room) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS svc_recovery_actions (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    case_id           BIGINT NOT NULL REFERENCES svc_recovery_cases (id) ON DELETE CASCADE,
    -- amenity | fnb_credit | late_checkout | room_night | upgrade | discount | other
    kind              TEXT NOT NULL,
    description       TEXT,
    value_minor       BIGINT NOT NULL DEFAULT 0 CHECK (value_minor >= 0),
    currency          CHAR(3) NOT NULL,
    -- given: done (within authority, or approved); pending_approval: above the
    -- giver's authority, waiting on the next role up; rejected; cancelled
    status            TEXT NOT NULL DEFAULT 'given' CHECK (status IN ('given', 'pending_approval', 'rejected', 'cancelled')),
    within_authority  BOOLEAN NOT NULL,
    -- {"staff_id"|"user_id", "role", "label", "via"}
    authorized_by     JSONB NOT NULL DEFAULT '{}',
    requested_by      JSONB NOT NULL DEFAULT '{}',
    approver_role     TEXT,
    action_id         BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS svc_recovery_actions_case_idx ON svc_recovery_actions (case_id);
CREATE INDEX IF NOT EXISTS svc_recovery_actions_hotel_idx ON svc_recovery_actions (hotel_slug, created_at);
