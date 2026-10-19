-- ─── Guest mood, frustration and service recovery (05 spec decision 2) ───
-- Every guest turn is scored by a fast Gemini model (concierge/mood.py):
-- the session keeps the latest mood and urgency and the highest frustration
-- seen so far, and each job the session files carries them to Service
-- (service.api.create_job). Only the score is stored here, never the words:
-- the words live in the turn log, which needs the recording consent.
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS last_mood TEXT;
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS last_urgency TEXT;
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS max_frustration REAL;
-- Set once, when the guest's frustration opened a recovery case for the duty
-- manager (service.api.flag_recovery kind 'frustration'). The UPDATE that
-- sets it is the claim, so two turns scored at the same moment flag once.
ALTER TABLE cc_sessions ADD COLUMN IF NOT EXISTS frustration_flagged_at TIMESTAMPTZ;

-- 'recovery': the guest said a job never came or is still not fixed (the
-- flag_service_recovery tool), or the frustration flag above (auto).
ALTER TABLE cc_session_events DROP CONSTRAINT IF EXISTS cc_session_events_kind_check;
ALTER TABLE cc_session_events ADD CONSTRAINT cc_session_events_kind_check
    CHECK (kind IN ('request', 'escalation', 'emergency', 'order', 'quote', 'unknown', 'contact', 'consent_withdrawn',
                    'recovery'));
