-- 0004_core_auth: users, hotel memberships, sessions, devices, magic links, staff PINs.
--
-- Tokens (sessions, devices, pairing codes, magic links) are opaque random
-- strings; only their SHA-256 is stored, so a database leak does not leak
-- live credentials. Opaque tokens instead of JWTs because staff turnover, lost
-- floor tablets and owner phone changes all need instant revocation.

CREATE TABLE IF NOT EXISTS users (
    id                  BIGSERIAL PRIMARY KEY,
    email               TEXT UNIQUE,
    phone_e164          TEXT UNIQUE,
    name                TEXT NOT NULL,
    password_hash       TEXT,
    locale              TEXT NOT NULL DEFAULT 'en',
    is_clefs_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    whatsapp_opt_in_at  TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at       TIMESTAMPTZ,
    -- Emails are stored lowercased so the UNIQUE constraint is case-insensitive.
    CONSTRAINT users_email_lower CHECK (email IS NULL OR email = lower(email)),
    CONSTRAINT users_phone_e164 CHECK (phone_e164 IS NULL OR phone_e164 ~ '^\+[1-9][0-9]{6,14}$')
);

CREATE TABLE IF NOT EXISTS hotel_memberships (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    role         TEXT NOT NULL CHECK (role IN ('owner', 'gm', 'finance', 'revenue', 'dept_head', 'front_desk', 'staff')),
    -- dept_head / staff scoping; empty means "all".
    departments  TEXT[] NOT NULL DEFAULT '{}',
    floors       TEXT[] NOT NULL DEFAULT '{}',
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, hotel_slug)
);
CREATE INDEX IF NOT EXISTS hotel_memberships_hotel_idx ON hotel_memberships (hotel_slug) WHERE is_active;

CREATE TABLE IF NOT EXISTS auth_sessions (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    BYTEA NOT NULL UNIQUE,
    user_id       BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    -- web: password login; magic: WhatsApp/email link (scoped, 12 h); api: scripts
    kind          TEXT NOT NULL DEFAULT 'web' CHECK (kind IN ('web', 'magic', 'api')),
    scope         JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL,
    last_seen_at  TIMESTAMPTZ,
    ip            TEXT,
    user_agent    TEXT,
    revoked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS auth_sessions_user_idx ON auth_sessions (user_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS devices (
    id            BIGSERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    name          TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('floor', 'department', 'desk', 'phone_console', 'personal')),
    floors        TEXT[] NOT NULL DEFAULT '{}',
    departments   TEXT[] NOT NULL DEFAULT '{}',
    token_hash    BYTEA UNIQUE,
    paired_by     BIGINT REFERENCES users (id) ON DELETE SET NULL,
    paired_at     TIMESTAMPTZ,
    last_seen_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS devices_hotel_idx ON devices (hotel_slug) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS device_pairing_codes (
    code_hash   BYTEA PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    -- {name, kind, floors[], departments[]} copied onto the device at pairing
    spec        JSONB NOT NULL,
    created_by  BIGINT REFERENCES users (id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at  TIMESTAMPTZ NOT NULL,
    used_at     TIMESTAMPTZ,
    device_id   BIGINT REFERENCES devices (id) ON DELETE SET NULL
);

-- Additive PIN and identity columns on the legacy staff table (Service owns the
-- rest of staff_members; these belong to the foundation per Canon 5).
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS pin_hash TEXT;
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS user_id BIGINT REFERENCES users (id) ON DELETE SET NULL;
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS floors TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS whatsapp_e164 TEXT;
-- Cross-device backstop; the 5-strike lock is per device (device_pin_failures).
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS pin_failed_count INT NOT NULL DEFAULT 0;
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS pin_locked_until TIMESTAMPTZ;

-- One row per PIN shift on a shared device (Canon 5: the name is
-- device_sessions). Service's shift templates, roster and handover build on it.
CREATE TABLE IF NOT EXISTS device_sessions (
    id                BIGSERIAL PRIMARY KEY,
    device_id         BIGINT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    staff_member_id   INT NOT NULL REFERENCES staff_members (id) ON DELETE CASCADE,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    planned_end_at    TIMESTAMPTZ,
    last_activity_at  TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    end_reason        TEXT,
    handover_note     TEXT,
    handover_ack_by   INT REFERENCES staff_members (id) ON DELETE SET NULL,
    handover_ack_at   TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS device_sessions_active_idx ON device_sessions (device_id) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS device_sessions_staff_idx ON device_sessions (staff_member_id, started_at DESC);

-- "5 failures lock that staff member on that device for 5 minutes."
CREATE TABLE IF NOT EXISTS device_pin_failures (
    device_id        BIGINT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    staff_member_id  INT NOT NULL REFERENCES staff_members (id) ON DELETE CASCADE,
    failed_count     INT NOT NULL DEFAULT 0,
    locked_until     TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (device_id, staff_member_id)
);

CREATE TABLE IF NOT EXISTS magic_links (
    id            BIGSERIAL PRIMARY KEY,
    token_hash    BYTEA NOT NULL UNIQUE,
    user_id       BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    purpose       TEXT NOT NULL CHECK (purpose IN ('brief', 'login', 'action')),
    next_path     TEXT,
    scope         JSONB NOT NULL DEFAULT '{}',
    expires_at    TIMESTAMPTZ NOT NULL,
    max_uses      INT NOT NULL DEFAULT 3,
    uses          INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at  TIMESTAMPTZ
);
