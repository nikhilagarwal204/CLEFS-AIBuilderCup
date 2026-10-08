-- Profit Guard review fixes: one credit accounting for the recovery ledger,
-- and the two consent ticks of the anonymous statement audit.

-- ─── Findings: the credit already netted into the claim ───

-- The commission detectors subtract what the OTA already credited back for
-- a booking when they size a finding. That credit is stored here so the
-- ledger's auto-verification only counts credit ABOVE it as recovery: one
-- credit is never both netted off the claim and counted as recovered.
ALTER TABLE pfg_findings ADD COLUMN IF NOT EXISTS credit_baseline_minor BIGINT NOT NULL DEFAULT 0;

-- ─── Public audit: consents on record ───

-- consent_processing: Clefs may process the uploaded statement.
-- consent_transfer_abroad: a PDF statement's text (guest names, booking
-- numbers) is read by an AI provider outside the UAE. A PDF is refused
-- without it; a CSV never leaves Clefs unmasked.
ALTER TABLE pfg_lead_audits ADD COLUMN IF NOT EXISTS consent_processing BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE pfg_lead_audits ADD COLUMN IF NOT EXISTS consent_transfer_abroad BOOLEAN NOT NULL DEFAULT FALSE;
