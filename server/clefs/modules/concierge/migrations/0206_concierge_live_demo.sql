-- ─── Guest Concierge v2: a 'demo' source for the concierge.live switch ───
-- The synthetic demo hotel (hotels.status = 'demo', clefs.seed.demo_uae)
-- is rebuilt from scratch by every seed run, which wipes its cc_live row,
-- and it has no eval run to pass Trust's readiness gate on. Without a row
-- the guest page answers every message with "call the front desk", so the
-- demo walkthrough cannot show the concierge at all (QA GS-1).
--
-- WHY a distinct source and not 'switch' with overridden = TRUE: the
-- console's go-live card and the audit trail must say plainly that the
-- gate was skipped because the hotel is a demo, never pass it off as an
-- owner override of a red gate. Only live.set_demo_live() writes it, and
-- it refuses any hotel whose status is not 'demo'.

ALTER TABLE cc_live DROP CONSTRAINT IF EXISTS cc_live_source_check;
ALTER TABLE cc_live ADD CONSTRAINT cc_live_source_check
    CHECK (source IN ('switch', 'grandfathered', 'demo'));
