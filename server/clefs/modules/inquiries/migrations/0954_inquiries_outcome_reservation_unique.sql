-- ─── One verified inquiry per reservation (04 spec 4.9) ───
-- WHY: two unlinked inquiries from the same guest (email then WhatsApp, or a
-- repeat request days later) both score the same direct booking at >= 0.75.
-- Without this, each wrote a verified_direct outcome carrying the full
-- commission avoided, so one booking counted twice in verified profit, the
-- brief and finance. verify.candidates() skips claimed reservations; this
-- index is the backstop for a race between two verify jobs. Partial, so a
-- reversed (cancelled_after_confirm) or leaked outcome never blocks a later
-- verification against the same reservation.

CREATE UNIQUE INDEX IF NOT EXISTS inq_outcomes_verified_reservation_uq
  ON inq_outcomes (verified_reservation_id)
  WHERE outcome = 'verified_direct' AND verified_reservation_id IS NOT NULL;
