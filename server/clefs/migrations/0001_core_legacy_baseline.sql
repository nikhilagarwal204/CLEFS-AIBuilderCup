-- 0001_core_legacy_baseline: the pre-refactor init_db() DDL, verbatim and in order.
--
-- Every statement is IF [NOT] EXISTS, so on the production database (which
-- init_db already built on each cold start) this file is a no-op. On a fresh
-- database it recreates exactly the legacy schema. The HOTELS_DATA upsert that
-- init_db ran between the hotels DDL and guest_rooms moved to 0002 (Python).
-- Legacy tables keep epoch-ms BIGINT timestamps (Canon 3); do not "fix" them here.

CREATE TABLE IF NOT EXISTS hotels (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    star_rating INTEGER,
    rooms_count INTEGER,
    category TEXT,
    website TEXT,
    city TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Drop notes column if it exists (requested change)
ALTER TABLE hotels DROP COLUMN IF EXISTS notes;
-- Add knowledge_base column for storing hotel-specific content (menus, info booklets, etc.)
ALTER TABLE hotels ADD COLUMN IF NOT EXISTS knowledge_base TEXT;
-- Per-hotel name for the guest-facing voice assistant. NULL means "use the
-- platform default" (DEFAULT_ASSISTANT_NAME on the client). Set directly in the
-- DB; the HOTELS_DATA upsert deliberately leaves it untouched.
ALTER TABLE hotels ADD COLUMN IF NOT EXISTS assistant_name TEXT;

CREATE TABLE IF NOT EXISTS guest_rooms (
    id SERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    room_number TEXT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (hotel_slug, room_number)
);

CREATE TABLE IF NOT EXISTS staff_requests (
    id BIGSERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    room TEXT NOT NULL,
    type TEXT NOT NULL,
    subtype TEXT,
    description TEXT,
    category TEXT DEFAULT 'General',
    priority TEXT DEFAULT 'medium',
    status TEXT DEFAULT 'new',
    timestamp BIGINT NOT NULL,
    assigned_to TEXT,
    accepted_at BIGINT,
    resolved_at BIGINT
);
ALTER TABLE staff_requests ADD COLUMN IF NOT EXISTS assigned_to TEXT;
ALTER TABLE staff_requests ADD COLUMN IF NOT EXISTS accepted_at BIGINT;
ALTER TABLE staff_requests ADD COLUMN IF NOT EXISTS resolved_at BIGINT;

CREATE TABLE IF NOT EXISTS pending_requests (
    hotel_slug TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS staff_members (
    id SERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    department TEXT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    is_online BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (hotel_slug, name)
);
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS phone_number TEXT;
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS is_online BOOLEAN DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS staff_shifts (
    id SERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    staff_name TEXT NOT NULL,
    check_in TIMESTAMPTZ NOT NULL,
    check_out TIMESTAMPTZ,
    duration_minutes INTEGER
);

CREATE TABLE IF NOT EXISTS conversation_logs (
    id BIGSERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    room_number TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_logs_hotel_room ON conversation_logs (hotel_slug, room_number);
CREATE INDEX IF NOT EXISTS idx_conv_logs_session ON conversation_logs (session_id);

CREATE TABLE IF NOT EXISTS hotel_locations (
    id SERIAL PRIMARY KEY,
    hotel_slug TEXT NOT NULL,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'public',
    custom_instructions TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (hotel_slug, slug)
);
