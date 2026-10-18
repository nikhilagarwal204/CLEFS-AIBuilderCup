-- ─── Guest Concierge: console transcripts (Wave 1 gaps) ───
-- GET /hotels/{slug}/concierge/transcripts groups a hotel's
-- conversation_logs by session_id and pages by each conversation's last
-- turn; the detail view reads one session's turns in order. The legacy
-- indexes cover (hotel_slug, room_number) and (session_id) alone, so a
-- hotel-wide list would scan every hotel's rows for the session grouping.
-- One composite index serves both the grouped list (hotel, then session)
-- and the ordered detail read (hotel, session, time).
CREATE INDEX IF NOT EXISTS idx_conv_logs_hotel_session_ts
    ON conversation_logs (hotel_slug, session_id, timestamp);
