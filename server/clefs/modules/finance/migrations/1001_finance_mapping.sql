-- Finance: USALI 12th-edition mapping, versioned per account (04 spec 6.4).
--
-- A confirmed mapping is locked and reused every month; a change of name or
-- group proposes a new version (the old one gets superseded_at). The
-- usali_line CHECK is the closed key table of usali.py (45 keys); a test
-- keeps the two in step.

CREATE TABLE IF NOT EXISTS fin_usali_map (
    id                    BIGSERIAL PRIMARY KEY,
    hotel_slug            TEXT NOT NULL REFERENCES hotels (slug),
    account_id            BIGINT NOT NULL REFERENCES fin_coa_accounts (id),
    version               INT NOT NULL,
    usali_line            TEXT NOT NULL CHECK (usali_line IN (
        'rooms.revenue', 'fnb.revenue', 'ood.revenue',
        'rooms.cost_of_sales', 'fnb.cost_of_sales', 'ood.cost_of_sales',
        'rooms.payroll', 'fnb.payroll', 'ood.payroll',
        'rooms.other', 'fnb.other', 'ood.other',
        'rooms.other.commissions', 'fnb.other.commissions', 'ood.other.commissions',
        'misc.income',
        'ag.payroll', 'its.payroll', 'sm.payroll', 'pom.payroll', 'eww.payroll',
        'ag.other', 'its.other', 'sm.other', 'pom.other', 'eww.other',
        'sm.paid_search', 'sm.display', 'sm.social', 'sm.loyalty',
        'mgmt_fee.base', 'mgmt_fee.incentive',
        'non_operating.rent', 'non_operating.property_tax', 'non_operating.insurance', 'non_operating.other',
        'below_line.depreciation', 'below_line.amortization', 'below_line.interest', 'below_line.income_tax',
        'below_line.owner_drawings',
        'excluded.vat', 'excluded.municipality_fee', 'excluded.tourism_dirham', 'excluded.balance_sheet')),
    usali_schedule       SMALLINT,
    usali_dept            TEXT NOT NULL,
    category              TEXT NOT NULL CHECK (category IN ('revenue', 'cost_of_sales', 'payroll', 'other_expense',
                                                            'undistributed', 'mgmt_fee', 'non_operating', 'below_line',
                                                            'excluded')),
    is_pass_through_tax   BOOL NOT NULL DEFAULT FALSE,
    pass_through_kind     TEXT CHECK (pass_through_kind IN ('vat', 'municipality_fee', 'tourism_dirham')),
    ai_confidence         NUMERIC(3, 2),
    ai_rationale          TEXT,
    ai_model              TEXT,
    learned_from          TEXT NOT NULL CHECK (learned_from IN ('rule', 'hotel_locked', 'global_memory', 'llm', 'manual')),
    -- where the account came from (the batch source: upload_csv, xero, ...);
    -- 'xero' rows never leave this hotel (Xero ToS, 6.4)
    data_origin           TEXT NOT NULL,
    confirmed_by          BIGINT,
    confirmed_at          TIMESTAMPTZ,
    locked                BOOL NOT NULL DEFAULT FALSE,
    override_reason       TEXT,
    superseded_at         TIMESTAMPTZ,
    alternatives          JSONB,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (hotel_slug, account_id, version)
);

-- At most one live version per account.
CREATE UNIQUE INDEX IF NOT EXISTS fin_usali_map_live_idx
    ON fin_usali_map (account_id) WHERE superseded_at IS NULL;

-- Intentionally global: exact names confirmed by several hotels. Rebuilt
-- weekly from locked, non-Xero mappings only; never fed from data_origin='xero'.
CREATE TABLE IF NOT EXISTS fin_mapping_memory (
    name_norm      TEXT NOT NULL,
    language       TEXT,
    usali_line     TEXT NOT NULL,
    confirmations  INT NOT NULL DEFAULT 0,
    hotels         INT NOT NULL DEFAULT 0,
    PRIMARY KEY (name_norm, usali_line)
);
