-- ─── Guest Concierge v2: sessions, tool calls, quotes, turn log ───
-- 03 spec 2.2 (concierge_sessions), 2.4 (server-side tool executor with
-- dedupe on sid:call_id) and 2.5 (conversation_logs additions), under the
-- Canon keys.
--
-- A session is the security boundary: the room is bound when the session
-- is minted (from the QR key or the stay token) and every tool call reads it
-- from here, never from model arguments (03 spec 0.3). Only the sha256 of the
-- bearer token is stored. Phone calls get a session too (no token; voice
-- binds it to the call), so request status, quotes and dedupe work the same
-- on every surface.

CREATE TABLE IF NOT EXISTS cc_sessions (
    id                UUID PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    surface           TEXT NOT NULL CHECK (surface IN ('web_room', 'web_location', 'phone', 'text_fallback', 'eval', 'whatsapp')),
    channel           TEXT NOT NULL,          -- web | sip | text | whatsapp (service.api SOURCES)
    room              TEXT,
    location_slug     TEXT,
    -- True only with a valid printed-QR key (or a stay token minted from
    -- one), or a PBX extension mapped to the room. Limited mode otherwise:
    -- no personal context, and requests say "confirm the room at the door".
    verified          BOOLEAN NOT NULL DEFAULT FALSE,
    key_status        TEXT,                   -- valid | invalid | absent | stay_token | extension | spoken
    language          TEXT,
    mode              TEXT,                   -- voice | text | chip | prewarm
    transport         TEXT NOT NULL DEFAULT 'live' CHECK (transport IN ('live', 'http', 'sip', 'none')),
    token_hash        BYTEA UNIQUE,
    consent_ref       TEXT,                   -- consents.subject_ref used for this guest
    consents          JSONB NOT NULL DEFAULT '{}',
    store_transcripts BOOLEAN NOT NULL DEFAULT FALSE,
    prompt_version    TEXT,
    model             TEXT,
    call_ref          TEXT,                   -- the provider call id of a phone session
    ip_hash           BYTEA,
    turn_count        INT NOT NULL DEFAULT 0,
    tool_calls        INT NOT NULL DEFAULT 0,
    requests_created  INT NOT NULL DEFAULT 0,
    handoffs          INT NOT NULL DEFAULT 0,
    unknowns          INT NOT NULL DEFAULT 0,
    emergencies       INT NOT NULL DEFAULT 0,
    orders            INT NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at        TIMESTAMPTZ NOT NULL,
    last_seen_at      TIMESTAMPTZ,
    ended_at          TIMESTAMPTZ,
    end_reason        TEXT
);
CREATE INDEX IF NOT EXISTS cc_sessions_hotel_idx ON cc_sessions (hotel_slug, created_at DESC);
CREATE INDEX IF NOT EXISTS cc_sessions_room_idx ON cc_sessions (hotel_slug, room, created_at DESC);
CREATE INDEX IF NOT EXISTS cc_sessions_open_idx ON cc_sessions (expires_at) WHERE ended_at IS NULL;

-- What happened in a session, for the 30 s emergency de-duplication, the
-- guest's request tracker and the containment metrics.
CREATE TABLE IF NOT EXISTS cc_session_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES cc_sessions (id) ON DELETE CASCADE,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    kind        TEXT NOT NULL CHECK (kind IN ('request', 'escalation', 'emergency', 'order', 'quote', 'unknown', 'contact', 'consent_withdrawn')),
    ref         JSONB NOT NULL DEFAULT '{}',
    auto        BOOLEAN NOT NULL DEFAULT FALSE, -- raised by the server's safety check, not the model
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS cc_session_events_idx ON cc_session_events (session_id, kind, at DESC);

CREATE TABLE IF NOT EXISTS cc_tool_calls (
    id          BIGSERIAL PRIMARY KEY,
    session_id  UUID NOT NULL REFERENCES cc_sessions (id) ON DELETE CASCADE,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    call_id     TEXT NOT NULL,
    name        TEXT NOT NULL,
    arguments   JSONB NOT NULL DEFAULT '{}',
    result      JSONB,
    status      TEXT NOT NULL DEFAULT 'ok',
    latency_ms  INT,
    at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- The browser, the phone and a retrying model may send one call twice:
    -- the second answer is the stored first one (03 spec 2.4).
    UNIQUE (session_id, call_id)
);
CREATE INDEX IF NOT EXISTS cc_tool_calls_hotel_idx ON cc_tool_calls (hotel_slug, at DESC);

-- place_order quote-then-confirm: a confirm needs a quote for the same lines
-- from the same session within 10 minutes (03 spec 2.4).
CREATE TABLE IF NOT EXISTS cc_order_quotes (
    id            BIGSERIAL PRIMARY KEY,
    session_id    UUID NOT NULL REFERENCES cc_sessions (id) ON DELETE CASCADE,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    room          TEXT NOT NULL,
    lines         JSONB NOT NULL,
    lines_key     TEXT NOT NULL,
    quote         JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ NOT NULL,
    confirmed_at  TIMESTAMPTZ,
    order_ref     JSONB
);
CREATE INDEX IF NOT EXISTS cc_order_quotes_idx ON cc_order_quotes (session_id, lines_key, created_at DESC);

-- ─── conversation_logs (legacy table, extended in place: 03 spec 0.1) ───
-- The legacy GET/POST keep their exact column list, so these additions never
-- change the golden shapes. created_at gives Trust's retention purge a real
-- timestamp to compare (the legacy `timestamp` is epoch ms).
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS stay_session_id UUID;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS call_id TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS language TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS prompt_version TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS safety_level TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
-- Bounded: a pre-clamp legacy row with a garbage epoch must not abort the
-- migration with "timestamp out of range" (year 9999 is the ceiling here).
UPDATE conversation_logs SET created_at = to_timestamp(timestamp / 1000.0)
WHERE created_at IS NULL AND timestamp BETWEEN 0 AND 253402300799000;
ALTER TABLE conversation_logs ALTER COLUMN created_at SET DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_conv_logs_stay_session ON conversation_logs (stay_session_id) WHERE stay_session_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_conv_logs_created ON conversation_logs (created_at);
