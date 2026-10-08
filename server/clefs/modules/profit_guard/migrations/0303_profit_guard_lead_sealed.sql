-- Profit Guard final fixes: the free OTA audit's lead is handled like the
-- other two lead magnets (vis_public_checks, dr_leads).

-- ─── Sealed contacts (QA PUB-8) ───

-- The lead's email and WhatsApp number are sealed with core.privacy.seal
-- (AES-GCM), never stored in plaintext. The plaintext columns stay for
-- rows written before this migration: the purge job seals and clears them
-- (a migration cannot call privacy.seal), and new writes leave them NULL.
ALTER TABLE pfg_lead_audits ADD COLUMN IF NOT EXISTS email_enc BYTEA;
ALTER TABLE pfg_lead_audits ADD COLUMN IF NOT EXISTS whatsapp_enc BYTEA;

-- ─── Lead transfer abroad (review compliance-ota-audit-no-transfer-consent) ───

-- The unlock form stores the lead's name and contacts on the database in
-- Singapore and emails them to the founder through a US email provider:
-- a required tick of its own, with its wording and version in
-- report.consents.lead_transfer_abroad.
ALTER TABLE pfg_lead_audits ADD COLUMN IF NOT EXISTS consent_lead_transfer_abroad BOOLEAN NOT NULL DEFAULT FALSE;
