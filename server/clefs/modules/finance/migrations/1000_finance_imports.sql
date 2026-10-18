-- Finance: monthly statement imports and the hotel's chart of accounts (04 spec 6.2, 6.3).
--
-- One fin_import_batches row per hotel, month and statement. An upload goes
-- through core ingest (import_batches, kind PL_STATEMENT / TRIAL_BALANCE) and
-- the finance commit handler writes one row here per month column; a
-- connector sync (Xero, QuickBooks) writes one directly, hashing the
-- canonical report JSON so a re-sync of unchanged books is a no-op.

CREATE TABLE IF NOT EXISTS fin_import_batches (
    id                        BIGSERIAL PRIMARY KEY,
    hotel_slug                TEXT NOT NULL REFERENCES hotels (slug),
    period_month              DATE NOT NULL CHECK (EXTRACT(DAY FROM period_month) = 1),
    statement_kind            TEXT NOT NULL CHECK (statement_kind IN ('pl', 'trial_balance')),
    source                    TEXT NOT NULL CHECK (source IN ('upload_xlsx', 'upload_csv', 'upload_pdf', 'xero', 'qbo',
                                                               'zoho', 'tally_upload')),
    core_import_batch_id      BIGINT REFERENCES import_batches (id) ON DELETE SET NULL,
    file_id                   UUID,
    -- file hash, or the hash of the canonical report JSON for connector batches
    sha256                    TEXT NOT NULL,
    currency                  CHAR(3) NOT NULL,
    basis                     TEXT CHECK (basis IN ('accrual', 'cash', 'unknown')),
    status                    TEXT NOT NULL CHECK (status IN ('uploaded', 'mapping_required', 'review', 'checks_failed',
                                                              'published', 'superseded', 'failed')),
    source_net_profit_minor   BIGINT,
    mapped_net_profit_minor   BIGINT,
    checks                    JSONB NOT NULL DEFAULT '{}',
    -- {account_id: version} snapshotted at publish: the month is read with
    -- exactly the mapping that was confirmed when it went out
    map_versions              JSONB,
    connector_meta            JSONB,
    published_by              BIGINT,
    published_at              TIMESTAMPTZ,
    superseded_by             BIGINT,
    created_by                BIGINT,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (hotel_slug, period_month, statement_kind, sha256)
);

CREATE INDEX IF NOT EXISTS fin_import_batches_month_idx
    ON fin_import_batches (hotel_slug, period_month DESC, created_at DESC);

-- One row per account as the source system names it. NULLS NOT DISTINCT: a
-- Tally ledger or an uploaded sheet often has no account code, and two
-- uploads of "Room Revenue" with no code must stay one account.
CREATE TABLE IF NOT EXISTS fin_coa_accounts (
    id                BIGSERIAL PRIMARY KEY,
    hotel_slug        TEXT NOT NULL REFERENCES hotels (slug),
    source_system     TEXT NOT NULL,
    code              TEXT,
    name_raw          TEXT NOT NULL,
    name_norm         TEXT NOT NULL,
    language          TEXT,
    parent_group_raw  TEXT,
    type_raw          TEXT,
    first_batch_id    BIGINT,
    last_batch_id     BIGINT,
    UNIQUE NULLS NOT DISTINCT (hotel_slug, source_system, code, name_norm)
);
