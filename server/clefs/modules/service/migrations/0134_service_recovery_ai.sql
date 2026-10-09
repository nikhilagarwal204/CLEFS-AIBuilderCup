-- 0134_service_recovery_ai: recovery before the review (05 spec, sections 1
-- and 3). A "not fixed", a frustrated guest or a job that never arrived opens
-- (or joins) a recovery case with a gesture Gemini suggests; the duty manager
-- approves it (or edits or dismisses it) on the service Dashboard, and an
-- approved gesture reaches the guest in their language.
--
-- reason (0103, free text checked in Python) gains frustration and
-- never_arrived beside complaint, not_resolved, post_checkout and manual.

ALTER TABLE svc_recovery_cases
    ADD COLUMN IF NOT EXISTS summary TEXT,
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    ADD COLUMN IF NOT EXISTS guest_language TEXT,
    -- the gesture as the guest would read it (English), its kind and value
    ADD COLUMN IF NOT EXISTS suggested_gesture TEXT,
    ADD COLUMN IF NOT EXISTS suggested_kind TEXT,
    ADD COLUMN IF NOT EXISTS suggested_value_minor BIGINT CHECK (suggested_value_minor IS NULL OR suggested_value_minor >= 0),
    -- ai | default (AI off or too slow)
    ADD COLUMN IF NOT EXISTS suggestion_source TEXT,
    -- NULL: nothing to approve (a LEARN case opened by hand or for a complaint)
    ADD COLUMN IF NOT EXISTS approval_status TEXT
        CHECK (approval_status IS NULL OR approval_status IN ('pending', 'approved', 'dismissed')),
    ADD COLUMN IF NOT EXISTS approved_gesture TEXT,
    ADD COLUMN IF NOT EXISTS decided_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS decided_by JSONB,
    -- the svc_recovery_actions row an approval recorded
    ADD COLUMN IF NOT EXISTS gesture_id BIGINT;

-- One open case per conversation (a frustrated guest is flagged once).
CREATE INDEX IF NOT EXISTS svc_recovery_cases_session_idx
    ON svc_recovery_cases (hotel_slug, session_id) WHERE session_id IS NOT NULL AND status = 'open';
CREATE INDEX IF NOT EXISTS svc_recovery_cases_pending_idx
    ON svc_recovery_cases (hotel_slug, opened_at DESC) WHERE approval_status = 'pending';
