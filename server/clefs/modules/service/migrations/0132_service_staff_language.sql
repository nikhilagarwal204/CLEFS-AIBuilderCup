-- 0132_service_staff_language: staff work in their own language, and what
-- they say reaches the guest in the guest's (05 spec, decision 10).
--
-- svc_translations caches every machine translation Service makes, keyed by
-- what was translated (the SHA-256 of the English source) and into which
-- language: a job's title and text once per (request, language), a status
-- line or an approved gesture once per language. A changed job text (a merge
-- appends words) has a new hash, so a stale translation is never shown.

CREATE TABLE IF NOT EXISTS svc_translations (
    hotel_slug   TEXT NOT NULL REFERENCES hotels (slug),
    -- job (title + text for the staff app) | guest (a line for the guest)
    purpose      TEXT NOT NULL CHECK (purpose IN ('job', 'guest')),
    source_hash  TEXT NOT NULL,
    lang         TEXT NOT NULL,
    request_id   BIGINT,
    title        TEXT,
    text         TEXT NOT NULL,
    model        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (hotel_slug, purpose, source_hash, lang)
);
CREATE INDEX IF NOT EXISTS svc_translations_request_idx ON svc_translations (request_id) WHERE request_id IS NOT NULL;

-- What the guest's tracker shows besides the status: staff replies, approved
-- recovery gestures and a few status lines, already in the guest's language.
-- request_id is NULL only for a gesture on a conversation that filed no job
-- (the guest's frustration in a chat); session_id then finds it.
CREATE TABLE IF NOT EXISTS svc_guest_messages (
    id                 BIGSERIAL PRIMARY KEY,
    hotel_slug         TEXT NOT NULL REFERENCES hotels (slug),
    request_id         BIGINT,
    session_id         TEXT,
    case_id            BIGINT,
    kind               TEXT NOT NULL CHECK (kind IN ('staff_reply', 'gesture', 'status')),
    -- in the guest's language
    text               TEXT NOT NULL,
    language           TEXT NOT NULL,
    -- what staff typed or said (or the English source of a gesture or status line)
    text_original      TEXT,
    language_original  TEXT,
    -- FALSE: the translation failed and the guest sees the original words
    translated         BOOLEAN,
    staff_member_id    INT,
    staff_first_name   TEXT,
    -- typed | voice | approval | system
    via                TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS svc_guest_messages_request_idx ON svc_guest_messages (request_id, id);
CREATE INDEX IF NOT EXISTS svc_guest_messages_session_idx ON svc_guest_messages (hotel_slug, session_id) WHERE session_id IS NOT NULL;
