-- Finance: admin-entered market benchmarks and the immutable pilot baseline (04 spec 6.7).

-- Intentionally global. Every figure carries its public source_url.
CREATE TABLE IF NOT EXISTS fin_market_benchmarks (
    id           BIGSERIAL PRIMARY KEY,
    region       TEXT NOT NULL,
    metric       TEXT NOT NULL,
    period       TEXT NOT NULL,
    period_kind  TEXT NOT NULL CHECK (period_kind IN ('month', 'ytd')),
    value        NUMERIC NOT NULL,
    source_url   TEXT NOT NULL,
    publisher    TEXT,
    entered_by   BIGINT,
    entered_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS fin_market_benchmarks_lookup_idx ON fin_market_benchmarks (region, metric, period);

-- 12 trailing months of P&L with the confirmed mapping, stored at pilot
-- start. Immutable: the trigger below refuses UPDATE and DELETE, so the
-- before-picture a pilot is judged against can never be edited afterwards.
CREATE TABLE IF NOT EXISTS fin_baselines (
    hotel_slug            TEXT NOT NULL REFERENCES hotels (slug),
    registered_at         TIMESTAMPTZ NOT NULL,
    period_start          DATE NOT NULL,
    period_end            DATE NOT NULL,
    map_version_snapshot  JSONB NOT NULL,
    metrics               JSONB NOT NULL,
    currency              CHAR(3) NOT NULL,
    registered_by         BIGINT,
    PRIMARY KEY (hotel_slug, registered_at)
);

CREATE OR REPLACE FUNCTION fin_baselines_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'fin_baselines is immutable' USING ERRCODE = 'insufficient_privilege';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS fin_baselines_no_change ON fin_baselines;
CREATE TRIGGER fin_baselines_no_change BEFORE UPDATE OR DELETE ON fin_baselines
    FOR EACH ROW EXECUTE FUNCTION fin_baselines_immutable();
