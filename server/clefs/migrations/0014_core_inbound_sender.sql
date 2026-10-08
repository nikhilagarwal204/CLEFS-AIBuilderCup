-- 0014_core_inbound_sender: the hashed sender on inbound_messages (04 spec F4, F8).
--
-- Why: journeys ("the guest wrote first today", 131047 handling) and inquiries
-- (wa_window_expires_at, "same sender in the last 30 days") need to find a
-- sender's recent inbound messages OUTSIDE the webhook handler, by the one
-- joinable identity every module shares: core.privacy.contact_hmac. The raw
-- sender column is plaintext and is not what modules store, so without this
-- column each module would keep its own copy of every inbound sender.
--
-- The webhook fills sender_hmac for every inbound WhatsApp message and email
-- routed to a hotel (the HMAC is per hotel, so rows with no hotel have none).
-- Rows written before this migration stay NULL: the HMAC needs the pepper,
-- which SQL does not have, and every lookup only reaches back hours or days.
--
-- Idempotent; no percent signs, no transaction statements.

ALTER TABLE inbound_messages ADD COLUMN IF NOT EXISTS sender_hmac BYTEA;

-- core.notify.wa_window_open_until and the module lookups:
--   WHERE hotel_slug = $1 AND sender_hmac = $2 ORDER BY received_at DESC LIMIT n
-- Partial: status rows and unrouted mail never carry a hash.
CREATE INDEX IF NOT EXISTS inbound_messages_sender_hmac_idx
    ON inbound_messages (hotel_slug, sender_hmac, received_at DESC)
    WHERE sender_hmac IS NOT NULL;
