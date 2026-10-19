-- 0131_service_dispatch: smart dispatch with an ETA, the guest's mood, how
-- a job closes, and the staff app's "Go off duty" (05 spec, decisions 5-8).
--
-- Instants are epoch-ms BIGINT like the rest of staff_requests (0100).

-- ─── staff_requests ───

ALTER TABLE staff_requests
    -- decision 6: minutes promised to the guest when the job was (re)assigned,
    -- and the instant they run out
    ADD COLUMN IF NOT EXISTS eta_minutes INT,
    ADD COLUMN IF NOT EXISTS eta_at BIGINT,
    -- the guest's mood and frustration (0..1) when the concierge filed the job
    ADD COLUMN IF NOT EXISTS mood TEXT,
    ADD COLUMN IF NOT EXISTS frustration REAL,
    -- the concierge session that filed it (recovery cases and gestures find
    -- the guest's conversation through it)
    ADD COLUMN IF NOT EXISTS session_id TEXT,
    -- decision 8: the job type needs a completion photo (set at intake from
    -- the request type, so a later config change never rewrites history)
    ADD COLUMN IF NOT EXISTS needs_proof BOOLEAN NOT NULL DEFAULT FALSE,
    -- verified | rejected | unverified (the photo could not be checked)
    ADD COLUMN IF NOT EXISTS proof_status TEXT,
    ADD COLUMN IF NOT EXISTS proof_reason TEXT,
    ADD COLUMN IF NOT EXISTS proof_id BIGINT,
    -- closure is not a state: a completed job is closed when the guest
    -- confirms, or 30 minutes after completion with no answer; a cancelled
    -- one when it is cancelled. A reopen clears both.
    ADD COLUMN IF NOT EXISTS closed_at BIGINT,
    ADD COLUMN IF NOT EXISTS closure_reason TEXT;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_proof_status_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_proof_status_check
            CHECK (proof_status IS NULL OR proof_status IN ('verified', 'rejected', 'unverified'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_closure_reason_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_closure_reason_check
            CHECK (closure_reason IS NULL OR closure_reason IN ('guest_confirmed', 'no_reply', 'cancelled'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'staff_requests_frustration_check') THEN
        ALTER TABLE staff_requests ADD CONSTRAINT staff_requests_frustration_check
            CHECK (frustration IS NULL OR (frustration >= 0 AND frustration <= 1));
    END IF;
END $$;

-- Backfill: the photo rule for existing open work, and closure for work that
-- finished before this migration (the minute sweep closes only new
-- completions, so it never walks the whole history at once).
UPDATE staff_requests SET needs_proof = TRUE
WHERE NOT needs_proof AND request_type_key IN ('towels', 'linen', 'pillows', 'extra_bed', 'amenities', 'water',
    'make_up_room', 'turndown', 'maintenance', 'ac', 'plumbing', 'electrical', 'minibar', 'laundry');

UPDATE staff_requests
SET closed_at = CASE WHEN guest_confirmation = 'yes' AND guest_confirmed_at IS NOT NULL THEN guest_confirmed_at
                     ELSE resolved_at + 30 * 60000 END,
    closure_reason = CASE WHEN guest_confirmation = 'yes' THEN 'guest_confirmed' ELSE 'no_reply' END
WHERE state = 'completed' AND closed_at IS NULL AND resolved_at IS NOT NULL
  AND (guest_confirmation = 'yes' OR resolved_at < (EXTRACT(EPOCH FROM NOW()) * 1000)::BIGINT - 30 * 60000);

UPDATE staff_requests SET closed_at = COALESCE(cancelled_at, "timestamp"), closure_reason = 'cancelled'
WHERE state = 'cancelled' AND closed_at IS NULL;

-- The no-reply closure in the minute sweep reads only unclosed completions.
CREATE INDEX IF NOT EXISTS staff_requests_unclosed_idx
    ON staff_requests (hotel_slug, resolved_at) WHERE state = 'completed' AND closed_at IS NULL;
CREATE INDEX IF NOT EXISTS staff_requests_session_idx
    ON staff_requests (hotel_slug, session_id) WHERE session_id IS NOT NULL;

-- ─── staff_members: "Go off duty" on the staff app ───
-- Set when the person goes off duty on their device, cleared when they go
-- back on. A PIN shift started after it counts as back on duty, so a worker
-- who forgot to switch back is never lost to routing for good.
ALTER TABLE staff_members ADD COLUMN IF NOT EXISTS off_duty_at TIMESTAMPTZ;

-- ─── svc_request_types: a hotel may change which types need a photo ───
-- NULL keeps the default (constants.PROOF_TYPES).
ALTER TABLE svc_request_types ADD COLUMN IF NOT EXISTS needs_proof BOOLEAN;
