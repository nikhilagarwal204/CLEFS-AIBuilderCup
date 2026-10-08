-- Finance: statement lines, statistical inputs, the published monthly
-- summary and the weekly pulse (04 spec 6.1, 6.2, 6.6).
--
-- Amounts are signed minor units as the source states them: income positive,
-- expenses negative (core ingest applies the section sign; connectors do the
-- same). The summary reads them through the batch's mapping snapshot.

CREATE TABLE IF NOT EXISTS fin_pl_lines (
    id             BIGSERIAL PRIMARY KEY,
    batch_id       BIGINT NOT NULL REFERENCES fin_import_batches (id) ON DELETE CASCADE,
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    account_id     BIGINT NOT NULL REFERENCES fin_coa_accounts (id),
    amount_minor   BIGINT NOT NULL,
    currency       CHAR(3) NOT NULL,
    row_ref        JSONB,
    tracking       JSONB
);

CREATE INDEX IF NOT EXISTS fin_pl_lines_batch_idx ON fin_pl_lines (batch_id);
CREATE INDEX IF NOT EXISTS fin_pl_lines_account_idx ON fin_pl_lines (hotel_slug, account_id);

-- The month's statistics beside the P&L: ARN, rooms sold, PMS rooms revenue
-- and the OTA figures of check 3. `overrides` (demo data, a manual fix) win
-- over the computed values, so the goldens read this table, not live stays.
CREATE TABLE IF NOT EXISTS fin_stat_inputs (
    hotel_slug                     TEXT NOT NULL REFERENCES hotels (slug),
    period_month                   DATE NOT NULL,
    available_rn                   INT,
    rooms_sold                     INT,
    rooms_rev_pms_minor            BIGINT,
    ota_share                      NUMERIC(5, 4),
    ota_commission_expected_minor  BIGINT,
    currency                       CHAR(3) NOT NULL,
    fte_hours                      NUMERIC,
    overrides                      JSONB,
    computed_at                    TIMESTAMPTZ,
    PRIMARY KEY (hotel_slug, period_month)
);

-- No target, tier or milestone column, on purpose (04 spec 6.7).
CREATE TABLE IF NOT EXISTS fin_monthly_summary (
    hotel_slug                 TEXT NOT NULL REFERENCES hotels (slug),
    period_month               DATE NOT NULL,
    batch_id                   BIGINT,
    currency                   CHAR(3) NOT NULL,
    normalized                 BOOL,
    total_revenue_minor        BIGINT,
    rooms_revenue_minor        BIGINT,
    dept_profit_minor          BIGINT,
    undistributed_minor        BIGINT,
    gop_minor                  BIGINT,
    gop_margin                 NUMERIC(6, 4),
    goppar_minor               BIGINT,
    trevpar_minor              BIGINT,
    revpar_minor               BIGINT,
    payroll_minor              BIGINT,
    labour_pct                 NUMERIC(6, 4),
    flow_through               NUMERIC(7, 4),
    flow_through_basis         TEXT,
    ebitda_minor               BIGINT,
    net_profit_minor           BIGINT,
    market_index               JSONB,
    verified_profit_minor      BIGINT,
    verified_profit_par_minor  BIGINT,
    -- signed totals per USALI line as published (the fin.pl_line metric and
    -- the trailing-month ratios read these), plus EWW and the checks' flags
    lines                      JSONB,
    eww_pct                    NUMERIC(6, 4),
    booked_net                 BOOL NOT NULL DEFAULT FALSE,
    arn                        INT,
    published_at               TIMESTAMPTZ,
    PRIMARY KEY (hotel_slug, period_month)
);

CREATE TABLE IF NOT EXISTS fin_weekly (
    hotel_slug                   TEXT NOT NULL REFERENCES hotels (slug),
    week_start                   DATE NOT NULL,
    available_rn                 INT,
    occupied_rn                  INT,
    rooms_revenue_minor          BIGINT,
    distribution_cost_minor      BIGINT,
    nrevpar_minor                BIGINT,
    ota_cost_por_minor           BIGINT,
    cancellation_leakage_minor   BIGINT,
    recovery_cost_minor          BIGINT,
    currency                     CHAR(3) NOT NULL,
    -- the OTA part and per-channel cost per room-night (6.1 channel dimension)
    detail                       JSONB,
    computed_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (hotel_slug, week_start)
);
