-- ─── In-room phone AI (03 spec 3.1, Canon 11) ───
-- Tenant key hotel_slug (Canon 1), TIMESTAMPTZ (Canon 3), vc_ prefix.
-- A line is one SIP entry point (one SIP trunk, one PBX trunk) of a
-- hotel. Its header secret is stored only as a sha256 hex digest, so a
-- database leak does not let anyone impersonate the PBX.

CREATE TABLE IF NOT EXISTS vc_lines (
    id                  SERIAL PRIMARY KEY,
    hotel_slug          TEXT NOT NULL REFERENCES hotels (slug),
    label               TEXT NOT NULL,
    trunk_id   TEXT,
    line_secret_hash    TEXT UNIQUE,
    line_secret_hint    TEXT,
    -- From domains, Via/Contact hosts and IPs or CIDRs that identify this
    -- line when the PBX cannot inject X-Clefs-Line (a Grandstream UCM).
    allowed_sources     JSONB NOT NULL DEFAULT '[]'::jsonb,
    pbx_kind            TEXT NOT NULL DEFAULT 'other'
                        CHECK (pbx_kind IN ('grandstream_ucm', '3cx', 'asterisk', 'freeswitch', 'fxo_gateway', 'bridge', 'other')),
    caller_id_mode      TEXT NOT NULL DEFAULT 'extension' CHECK (caller_id_mode IN ('extension', 'none')),
    -- REFER target. sip: or sips: only, never tel: (PSTN breakout through
    -- the telephony bridge is a UAE regulatory grey area, 03 spec 3.3).
    front_desk_uri      TEXT CHECK (front_desk_uri IS NULL OR front_desk_uri ~* '^sips?:[^[:space:]]+$'),
    outside_line_prefix TEXT CHECK (outside_line_prefix IS NULL OR outside_line_prefix ~ '^[0-9*#]{1,4}$'),
    default_language    TEXT NOT NULL DEFAULT 'en',
    model               TEXT,
    voice               TEXT,
    max_call_s          INT NOT NULL DEFAULT 1800 CHECK (max_call_s BETWEEN 60 AND 7200),
    max_concurrent      INT NOT NULL DEFAULT 4 CHECK (max_concurrent BETWEEN 1 AND 50),
    tool_transport      TEXT NOT NULL DEFAULT 'mcp' CHECK (tool_transport IN ('mcp', 'sideband')),
    enabled             BOOLEAN NOT NULL DEFAULT FALSE,
    -- {"enabled": bool, "start": "23:00", "end": "06:00"} in hotel time:
    -- inside the window calls get 486 so the PBX rings the night operator.
    after_hours         JSONB NOT NULL DEFAULT '{}'::jsonb,
    extension_rule      JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vc_lines_hotel_idx ON vc_lines (hotel_slug);

-- The extension allowlist. The SIP From user part is attacker-controlled
-- text; only an extension listed here ever becomes a room.
CREATE TABLE IF NOT EXISTS vc_extensions (
    id            SERIAL PRIMARY KEY,
    hotel_slug    TEXT NOT NULL REFERENCES hotels (slug),
    voice_line_id INT NOT NULL REFERENCES vc_lines (id) ON DELETE CASCADE,
    extension     TEXT NOT NULL,
    room          TEXT,
    location_slug TEXT,
    kind          TEXT NOT NULL DEFAULT 'room' CHECK (kind IN ('room', 'location', 'staff', 'other')),
    source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'csv', 'rule')),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (voice_line_id, extension),
    CHECK (kind <> 'room' OR room IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS vc_extensions_hotel_room_idx ON vc_extensions (hotel_slug, room);

CREATE TABLE IF NOT EXISTS vc_calls (
    id                   BIGSERIAL PRIMARY KEY,
    hotel_slug           TEXT REFERENCES hotels (slug),
    voice_line_id        INT REFERENCES vc_lines (id) ON DELETE SET NULL,
    provider_call_id       TEXT NOT NULL UNIQUE,
    webhook_id           TEXT,
    status               TEXT NOT NULL DEFAULT 'incoming'
                         CHECK (status IN ('incoming', 'rejected', 'accepting', 'accepted', 'active', 'ended', 'failed', 'stale')),
    reject_code          INT,
    reject_reason        TEXT,
    from_user            TEXT,
    extension_matched    BOOLEAN NOT NULL DEFAULT FALSE,
    room                 TEXT,
    location_slug        TEXT,
    room_confirmed_by    TEXT NOT NULL DEFAULT 'none' CHECK (room_confirmed_by IN ('extension', 'spoken', 'none')),
    tool_transport       TEXT,
    session_id           TEXT,
    language             TEXT,
    prompt_version       TEXT,
    model                TEXT,
    session_config       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- the Gemini Live config the worker opens the session with
    started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    accepted_at          TIMESTAMPTZ,
    ended_at             TIMESTAMPTZ,
    duration_s           INT,
    end_reason           TEXT,
    transferred          BOOLEAN NOT NULL DEFAULT FALSE,
    transfer_target      TEXT,
    requests_created     INT NOT NULL DEFAULT 0,
    emergencies          INT NOT NULL DEFAULT 0,
    worker_id            TEXT,
    worker_lease_until   TIMESTAMPTZ,
    sip_headers_redacted JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS vc_calls_hotel_started_idx ON vc_calls (hotel_slug, started_at DESC);
CREATE INDEX IF NOT EXISTS vc_calls_live_idx ON vc_calls (voice_line_id) WHERE status IN ('accepting', 'accepted', 'active');
CREATE INDEX IF NOT EXISTS vc_calls_lease_idx ON vc_calls (worker_lease_until) WHERE status IN ('accepted', 'active');

-- Dedupe of the telephony bridge webhook deliveries (webhook-id), and the console's
-- "last 20 webhook events" panel. 30-day retention (voice.receipts_purge).
-- hotel_slug is NULL for a call no line claimed; project_id (from the To
-- header) still lets that hotel's console see its own misrouted calls.
CREATE TABLE IF NOT EXISTS vc_webhook_receipts (
    webhook_id    TEXT PRIMARY KEY,
    source        TEXT NOT NULL DEFAULT 'bridge',
    event_type    TEXT,
    call_id       TEXT,
    hotel_slug    TEXT REFERENCES hotels (slug),
    voice_line_id INT,
    project_id    TEXT,
    outcome       TEXT NOT NULL DEFAULT 'received',
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    received_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS vc_webhook_receipts_hotel_idx ON vc_webhook_receipts (hotel_slug, received_at DESC);
CREATE INDEX IF NOT EXISTS vc_webhook_receipts_project_idx ON vc_webhook_receipts (project_id, received_at DESC);
CREATE INDEX IF NOT EXISTS vc_webhook_receipts_age_idx ON vc_webhook_receipts (received_at);

-- Tool calls made on a phone call (MCP or sideband). The concierge executor
-- keeps its own cc_tool_calls; this table is the voice-side ledger: the
-- idempotency key for retried calls, and what the console shows per call.
CREATE TABLE IF NOT EXISTS vc_tool_calls (
    id           BIGSERIAL PRIMARY KEY,
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    call_row_id  BIGINT NOT NULL REFERENCES vc_calls (id) ON DELETE CASCADE,
    tool_call_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    transport    TEXT NOT NULL DEFAULT 'mcp',
    arguments    JSONB NOT NULL DEFAULT '{}'::jsonb,
    result       JSONB,
    status       TEXT NOT NULL DEFAULT 'running',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (call_row_id, tool_call_id)
);
