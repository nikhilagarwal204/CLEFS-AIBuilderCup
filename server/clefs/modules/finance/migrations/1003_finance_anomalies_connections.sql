-- Finance: anomalies found at publish, and accounting connections (04 spec 6.2, 6.5, 6.8).

CREATE TABLE IF NOT EXISTS fin_anomalies (
    id              BIGSERIAL PRIMARY KEY,
    hotel_slug      TEXT NOT NULL REFERENCES hotels (slug),
    period_month    DATE NOT NULL,
    fingerprint     TEXT NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('labour_ratio', 'eww_ratio', 'line_spike', 'booked_net',
                                                  'rooms_rev_mismatch', 'pass_through_in_revenue', 'reconciliation')),
    usali_line      TEXT,
    value           NUMERIC,
    expected        NUMERIC,
    delta_minor     BIGINT,
    currency        CHAR(3) NOT NULL,
    zscore          NUMERIC,
    confidence      NUMERIC(3, 2),
    evidence        JSONB,
    signal_id       BIGINT,
    action_id       BIGINT,
    status          TEXT NOT NULL DEFAULT 'open',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (hotel_slug, fingerprint)
);

CREATE INDEX IF NOT EXISTS fin_anomalies_month_idx ON fin_anomalies (hotel_slug, period_month DESC);

-- Tokens live in core oauth_connections; this row is finance's view of the
-- link (tenant, last sync).
CREATE TABLE IF NOT EXISTS fin_connections (
    hotel_slug     TEXT NOT NULL REFERENCES hotels (slug),
    provider       TEXT NOT NULL CHECK (provider IN ('xero', 'qbo', 'zoho')),
    tenant_ref     TEXT,
    status         TEXT,
    last_sync_at   TIMESTAMPTZ,
    last_error     TEXT,
    PRIMARY KEY (hotel_slug, provider)
);
