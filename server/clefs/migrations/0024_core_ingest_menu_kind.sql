-- 0024_core_ingest_menu_kind: the MENU import kind (core/ingest).
--
-- Why: Upsell's menu CSV import used its own parser because import_batches
-- had no MENU kind (its CHECK in 0020). With it, a menu file gets the shared
-- pipeline like every other import: batch history, saved mapping templates,
-- the AI column-mapping proposal, staged rows a person can review, and the
-- commit handler Upsell registers for MENU (clefs.core.ingest.kinds).
--
-- The CHECK is replaced, never edited in 0020 (applied files are
-- checksummed). The inline CHECK of 0020 got Postgres's default name
-- import_batches_kind_check; any CHECK on import_batches that tests `kind`
-- is dropped by definition rather than by that name, so a database where
-- the constraint was renamed still converges. Re-running is a no-op.

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT conname FROM pg_constraint
        WHERE conrelid = 'import_batches'::regclass AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%kind%'
          AND pg_get_constraintdef(oid) NOT ILIKE '%''MENU''%'
    LOOP
        EXECUTE format('ALTER TABLE import_batches DROP CONSTRAINT %I', c.conname);
    END LOOP;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'import_batches'::regclass AND conname = 'import_batches_kind_check'
    ) THEN
        ALTER TABLE import_batches ADD CONSTRAINT import_batches_kind_check CHECK (kind IN (
            'OTA_STATEMENT', 'OTA_PAYOUT', 'VCC_REPORT', 'CREDIT_NOTE', 'PMS_RESERVATIONS',
            'PMS_FOLIO', 'BANK', 'CONTRACT_PDF', 'COMP_RATES', 'ISSUANCE', 'OCCUPANCY', 'ICS',
            'SUPPLIER_INVOICE', 'PL_STATEMENT', 'TRIAL_BALANCE', 'BOOKING_ENGINE_ATTRIBUTION', 'MENU'));
    END IF;
END
$$;
