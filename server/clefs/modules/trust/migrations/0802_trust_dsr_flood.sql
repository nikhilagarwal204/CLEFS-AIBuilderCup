-- 0802_trust_dsr_flood: a flag on privacy requests filed while the hotel's
-- public form was taking an unusual number of submissions (security finding
-- security-privacy-8). WHY a flag and not a 429: the form is a legally
-- mandated channel (PDPL DSR), so a flood must never lock a genuine data
-- subject out. Past the hotel's daily threshold every request is still filed,
-- flagged for triage, and gets no acknowledgement email (so the hotel's
-- sending identity cannot be used to mail addresses of an attacker's choosing).

-- ─── Privacy requests ───
ALTER TABLE trs_dsr_requests ADD COLUMN IF NOT EXISTS suspected_flood BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS trs_dsr_requests_flood_idx
    ON trs_dsr_requests (hotel_slug, due_at) WHERE suspected_flood;
