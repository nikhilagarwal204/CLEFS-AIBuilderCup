-- Google Business Profile data (04 spec 5.4). Raw GBP API rows expire 30
-- days after fetch (privacy-reviews.md B1, uae/zz-critique.md C10) and are
-- deleted by visibility.gbp_raw_expire; the derived monthly KPIs are Clefs'
-- own numbers and are retained.

CREATE TABLE IF NOT EXISTS vis_gbp_daily (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    date        DATE NOT NULL,
    metric      TEXT NOT NULL,
    value       BIGINT,
    fetched_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (hotel_slug, date, metric)
);

-- A keyword row is either an exact value or a "< threshold" bucket, never
-- both: thresholds are never summed (5.4), the CHECK keeps that honest.
CREATE TABLE IF NOT EXISTS vis_gbp_keywords (
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    month       DATE NOT NULL,
    keyword     TEXT NOT NULL,
    value       BIGINT,
    threshold   BIGINT,
    is_branded  BOOLEAN,
    fetched_at  TIMESTAMPTZ NOT NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (hotel_slug, month, keyword),
    CHECK ((value IS NULL) <> (threshold IS NULL))
);

CREATE TABLE IF NOT EXISTS vis_gbp_kpis_monthly (
    hotel_slug                TEXT NOT NULL REFERENCES hotels (slug),
    month                     DATE NOT NULL,
    reach_user_days           BIGINT,
    reach_prior_user_days     BIGINT,
    reach_yoy_user_days       BIGINT,
    actions                   BIGINT,
    action_rate               NUMERIC(6, 4),
    website_clicks            BIGINT,
    branded_share             NUMERIC(5, 4),
    keywords_under_threshold  INT,
    computed_at               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (hotel_slug, month)
);

CREATE TABLE IF NOT EXISTS vis_lodging_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    hotel_slug  TEXT NOT NULL REFERENCES hotels (slug),
    fetched_at  TIMESTAMPTZ,
    kind        TEXT CHECK (kind IN ('lodging', 'google_updated')),
    payload     JSONB,
    diff_vs_kb  JSONB,
    expires_at  TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS vis_gbp_daily_expiry_idx ON vis_gbp_daily (expires_at);
CREATE INDEX IF NOT EXISTS vis_gbp_keywords_expiry_idx ON vis_gbp_keywords (expires_at);
CREATE INDEX IF NOT EXISTS vis_lodging_hotel_idx ON vis_lodging_snapshots (hotel_slug, kind, fetched_at DESC);
