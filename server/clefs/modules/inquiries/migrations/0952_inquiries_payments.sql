-- ─── Channel accounts and payments (04 spec 4.1, 4.3, 4.8) ───
-- inq_channel_accounts holds the per-hotel intake connections (Meta page,
-- Google lead form, web widget) and payment providers. Secrets are sealed
-- (secret_enc); a webhook finds its hotel by webhook_key_hash (sha256 of the
-- key in its URL) or, for Meta, by the page_id index below.

CREATE TABLE IF NOT EXISTS inq_channel_accounts (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  kind TEXT NOT NULL CHECK (kind IN ('meta_page','google_leadform','web_widget','pay_ngenius','pay_telr','pay_stripe','pay_tap')),
  -- pay_*: {fee_pct, fee_fixed_minor, currency}; web_widget: {allowed_origins[], widget_key}
  config JSONB NOT NULL DEFAULT '{}',
  secret_enc BYTEA,
  webhook_key_hash BYTEA UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  connected_by BIGINT,
  connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, kind)
);
-- leadgen page_id -> hotel
CREATE UNIQUE INDEX IF NOT EXISTS inq_channel_accounts_meta_page_uidx
  ON inq_channel_accounts ((config->>'page_id')) WHERE kind = 'meta_page';

CREATE TABLE IF NOT EXISTS inq_payment_links (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id),
  quote_id BIGINT NOT NULL REFERENCES inq_quotes(id),
  provider TEXT NOT NULL,
  provider_ref TEXT,
  url TEXT,
  amount_minor BIGINT NOT NULL,
  currency CHAR(3) NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('deposit','full','balance')),
  includes_td_minor BIGINT NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK (status IN ('created','sent','paid','partially_paid','expired','cancelled','failed','refunded')),
  expires_at TIMESTAMPTZ,
  paid_at TIMESTAMPTZ,
  paid_amount_minor BIGINT,
  fee_minor BIGINT,
  fee_source TEXT CHECK (fee_source IN ('provider','settings')),
  idempotency_key TEXT NOT NULL,
  provider_payload JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, idempotency_key),
  UNIQUE (provider, provider_ref)
);

-- Verified signatures only, so a forged event cannot pre-empt a real one.
CREATE TABLE IF NOT EXISTS inq_payment_events (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  account_id BIGINT NOT NULL REFERENCES inq_channel_accounts(id),
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  payment_link_id BIGINT,
  payload JSONB NOT NULL,
  processed_at TIMESTAMPTZ,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (provider, provider_event_id)
);

CREATE TABLE IF NOT EXISTS inq_payment_events_rejected (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  account_id BIGINT NOT NULL,
  provider TEXT NOT NULL,
  reason TEXT NOT NULL CHECK (reason IN ('bad_signature','unparseable')),
  headers_redacted JSONB,
  payload_sha256 TEXT NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
