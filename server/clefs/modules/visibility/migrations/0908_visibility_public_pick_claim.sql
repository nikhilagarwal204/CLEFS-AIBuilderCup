-- Public Google check: an atomic claim for the pick step (review finding
-- security-privacy-6). pick() used to check status = 'needs_pick' under a
-- row lock, commit, then spend 3 paid SerpApi searches, so N parallel picks
-- on one token all passed the check and each spent 3 searches. A pick now
-- moves the row to 'picking' (stamping picking_at) in the same transaction
-- as the check, so only one request ever reaches SerpApi. picking_at is a
-- lease: a claim left behind by a crashed worker can be taken again after
-- public_check.PICK_LEASE, so the check is never stuck for its prospect.

ALTER TABLE vis_public_checks DROP CONSTRAINT IF EXISTS vis_public_checks_status_check;
ALTER TABLE vis_public_checks ADD CONSTRAINT vis_public_checks_status_check
    CHECK (status IN ('needs_pick', 'picking', 'ready', 'no_match'));
ALTER TABLE vis_public_checks ADD COLUMN IF NOT EXISTS picking_at TIMESTAMPTZ;
