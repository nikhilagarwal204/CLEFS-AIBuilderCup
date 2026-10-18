-- 0120_service_ops_roster: roster details the ops part needs on top of 0102.
--
-- break_min on the assignment: a shift typed with explicit times (not a
-- template) still has an unpaid break, and the labour check counts paid hours.
-- NULL means "the template's break".

ALTER TABLE svc_shift_assignments
    ADD COLUMN IF NOT EXISTS break_min INT CHECK (break_min IS NULL OR break_min >= 0);

-- The device lock screen and the escalation ladder ask "who is on now";
-- this keeps that lookup off a scan of the whole roster.
CREATE INDEX IF NOT EXISTS svc_shift_assignments_date_idx
    ON svc_shift_assignments (hotel_slug, business_date) WHERE status = 'planned';
