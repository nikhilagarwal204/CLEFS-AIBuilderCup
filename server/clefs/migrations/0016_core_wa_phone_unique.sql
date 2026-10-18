-- 0016_core_wa_phone_unique: one hotel per WhatsApp phone_number_id.
-- Idempotent, and free of percent signs (the runner may pass parameters).
--
-- ─── Why ───
-- Inbound Meta webhooks route by this id (SELECT slug ... LIMIT 1) and
-- outbound sends use it as the sender with the one platform token. Two
-- hotels holding the same id meant one tenant read the other's guest and
-- staff messages and could send as its number. PATCH /settings now lets
-- only a Clefs admin set it; this index makes the id unique like
-- inbound_email_alias (0003).

-- A blank id is no id (the settings route stores NULL from now on).
UPDATE hotels SET whatsapp_phone_number_id = NULL
WHERE whatsapp_phone_number_id IS NOT NULL AND btrim(whatsapp_phone_number_id) = '';

-- Fail closed on existing duplicates: no row can prove it owns the number,
-- so every copy is cleared and a Clefs admin reconnects the right hotel.
-- (Keeping one would pick an owner at random, as the routing query did.)
DO $$
DECLARE
    n INTEGER;
BEGIN
    UPDATE hotels h SET whatsapp_phone_number_id = NULL
    WHERE h.whatsapp_phone_number_id IS NOT NULL
      AND EXISTS (
          SELECT 1 FROM hotels o
          WHERE o.whatsapp_phone_number_id = h.whatsapp_phone_number_id AND o.slug <> h.slug
      );
    GET DIAGNOSTICS n = ROW_COUNT;
    IF n > 0 THEN
        RAISE NOTICE USING MESSAGE = 'cleared a shared whatsapp_phone_number_id on ' || n
            || ' hotels: reconnect each as a Clefs admin';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS hotels_whatsapp_phone_number_id_key
    ON hotels (whatsapp_phone_number_id) WHERE whatsapp_phone_number_id IS NOT NULL;
