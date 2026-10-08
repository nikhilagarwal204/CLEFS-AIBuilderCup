-- ─── Guest Concierge v2: turns, text chat and WhatsApp sessions ───
-- 03 spec 2.4 (POST .../turns stores each turn with its prompt version,
-- language and consent) and the text surfaces (HTTP chat fallback, the
-- WhatsApp chat handler in GUEST_PRIORITY slot 30).
--
-- chat_history is the working memory of a text session (the last few
-- messages the model needs to follow the conversation). It is not the
-- transcript: it is kept whatever the recording consent says, and the
-- session GC clears it once the session has ended, so it never outlives the
-- conversation. The 12-month transcript lives in conversation_logs and only
-- with the recording consent (cc_sessions.store_transcripts).

ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS chat_history JSONB;
-- A WhatsApp session belongs to one sender: the hex of the sender's
-- privacy.contact_hmac (never the phone number itself).
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS sender_ref TEXT;
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS last_safety_level TEXT;
CREATE INDEX IF NOT EXISTS cc_sessions_sender_idx
    ON cc_sessions (hotel_slug, sender_ref, created_at DESC) WHERE sender_ref IS NOT NULL;

-- The consent a stored turn was logged under: the subject ref and the flags
-- in force at that moment (a later withdrawal does not rewrite history, the
-- retention purge does).
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS consent_ref TEXT;
ALTER TABLE conversation_logs ADD COLUMN IF NOT EXISTS consents JSONB;
