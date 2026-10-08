-- 0102_service_roster: shift templates, the roster, labour rules and
-- handover notes (03 spec 1.1 "0004" and 1.6).
--
-- Canon 5: devices, pairing, PIN login and device_sessions belong to the
-- foundation (0004_core_auth). Service owns what is planned around them: who
-- works when, where and in which shift role, and what the last shift left.

-- ─── staff_members: v2 columns (PIN and identity columns are the foundation's) ───

ALTER TABLE staff_members
    ADD COLUMN IF NOT EXISTS department_key TEXT,
    -- the job title key used by the authority matrix (staff | supervisor | duty_manager | gm)
    ADD COLUMN IF NOT EXISTS role_key TEXT,
    -- outdoor roles are blocked 12:30-15:00 from 15 Jun to 15 Sep (the midday ban)
    ADD COLUMN IF NOT EXISTS is_outdoor_role BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS languages TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS max_open INT NOT NULL DEFAULT 4,
    -- business-initiated WhatsApp (escalations) needs the person's opt-in
    ADD COLUMN IF NOT EXISTS whatsapp_opt_in BOOLEAN NOT NULL DEFAULT FALSE;

-- The legacy shift log gains the staff id and the PIN session it came from.
ALTER TABLE staff_shifts
    ADD COLUMN IF NOT EXISTS staff_id INT,
    ADD COLUMN IF NOT EXISTS device_session_id BIGINT;

-- ─── Shift templates ───
-- Seeded per hotel on first use (morning 07-15, evening 15-23, night 23-07,
-- hk_day 08-17, hk_eve 15-24), then owned by the hotel.

CREATE TABLE IF NOT EXISTS svc_shift_templates (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    key               TEXT NOT NULL,
    name              TEXT NOT NULL,
    department_key    TEXT,
    start_time        TIME NOT NULL,
    end_time          TIME NOT NULL,
    crosses_midnight  BOOLEAN NOT NULL DEFAULT FALSE,
    break_min         INT NOT NULL DEFAULT 0 CHECK (break_min >= 0),
    active            BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (hotel_slug, key)
);

-- ─── Roster ───

CREATE TABLE IF NOT EXISTS svc_shift_assignments (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    staff_member_id   INT NOT NULL REFERENCES staff_members (id) ON DELETE CASCADE,
    template_id       BIGINT REFERENCES svc_shift_templates (id) ON DELETE SET NULL,
    business_date     DATE NOT NULL,
    start_at          TIMESTAMPTZ NOT NULL,
    end_at            TIMESTAMPTZ NOT NULL,
    department_key    TEXT,
    floors            TEXT[] NOT NULL DEFAULT '{}',
    shift_role        TEXT NOT NULL DEFAULT 'attendant' CHECK (shift_role IN ('attendant', 'supervisor', 'duty_manager')),
    status            TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'cancelled')),
    labour_flags      JSONB NOT NULL DEFAULT '[]',
    -- a blocked labour rule may be overridden only with a reason (audited)
    override_reason   TEXT,
    created_by        BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (end_at > start_at)
);
CREATE INDEX IF NOT EXISTS svc_shift_assignments_when_idx
    ON svc_shift_assignments (hotel_slug, start_at, end_at) WHERE status = 'planned';
CREATE INDEX IF NOT EXISTS svc_shift_assignments_staff_idx ON svc_shift_assignments (staff_member_id, business_date);

-- ─── Labour guardrails (guidance, not legal advice) ───

CREATE TABLE IF NOT EXISTS svc_labour_rules (
    hotel_slug                TEXT PRIMARY KEY REFERENCES hotels (slug),
    weekly_max_h              INT NOT NULL DEFAULT 48,
    daily_max_h               INT NOT NULL DEFAULT 8,
    max_consecutive_days      INT NOT NULL DEFAULT 6,
    ramadan_start             DATE,
    ramadan_end               DATE,
    ramadan_reduction_h       INT NOT NULL DEFAULT 2,
    -- MM-DD dates and HH:MM local times of the summer midday outdoor work ban
    midday_ban                JSONB NOT NULL DEFAULT '{"from": "06-15", "to": "09-15", "start": "12:30", "end": "15:00"}',
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ─── Handover notes ───

CREATE TABLE IF NOT EXISTS svc_handover_notes (
    id                   BIGSERIAL PRIMARY KEY,
    hotel_slug           TEXT NOT NULL REFERENCES hotels (slug),
    department_key       TEXT,
    floor                TEXT,
    shift_assignment_id  BIGINT,
    author_staff_id      INT,
    author_user_id       BIGINT,
    author_label         TEXT,
    body                 TEXT NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- [{"staff_id": 3, "at": "..."}]
    acks                 JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS svc_handover_notes_hotel_idx ON svc_handover_notes (hotel_slug, created_at DESC);
