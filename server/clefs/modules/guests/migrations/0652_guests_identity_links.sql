-- 0652_guests_identity_links: what the stay linker needs beyond 0650
-- (03 spec 2.7: the hourly guests.link_stays job and the returning badge).
--
--   gst_stays.contact_hmacs    per-hotel contact HMACs of a roster stay (the
--                              GuestModal phone/email, hashed then scrubbed
--                              from guest_rooms). PMS stays keep theirs in
--                              core reservation_contacts, so this stays empty
--                              for them. Only written with a recorded
--                              transfer_abroad consent. Lets
--                              is_known_guest_sender see an in-house roster
--                              guest without a profile (no profile_memory).
--   gst_stays.link_checked_at  when the linker last looked at the stay, so
--                              the hourly run skips stays it already settled.
--
-- Deliberately NO foreign key from gst_stays.reservation_id to core
-- reservations: roster and manual stays have none, and the stays port lets
-- tests and the demo run on an in-memory core/stays whose ids exist nowhere
-- in SQL (stays_port.set_backend).

ALTER TABLE gst_stays ADD COLUMN IF NOT EXISTS contact_hmacs BYTEA[] NOT NULL DEFAULT '{}';
ALTER TABLE gst_stays ADD COLUMN IF NOT EXISTS link_checked_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS gst_stays_contact_hmacs_idx ON gst_stays USING GIN (contact_hmacs);
CREATE INDEX IF NOT EXISTS gst_stays_unchecked_idx ON gst_stays (hotel_slug, id) WHERE link_checked_at IS NULL;

CREATE INDEX IF NOT EXISTS gst_profiles_last_seen_idx ON gst_profiles (hotel_slug, last_seen);
