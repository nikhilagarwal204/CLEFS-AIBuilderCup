-- ─── Quotes (04 spec 4.1, 4.6) ───
-- Created with the core tables so every Inquiry Guard part builds on one
-- schema; the quote builder fills it.

CREATE TABLE IF NOT EXISTS inq_quotes (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id),
  version INT NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','awaiting_approval','approved','sent','accepted','expired','superseded','withdrawn')),
  -- [{stay_date, room_type_code, rooms, rate_minor, bar_minor, floor_minor, ceiling_minor, discount_pct, source}]
  lines JSONB NOT NULL,
  room_minor BIGINT NOT NULL,
  sc_minor BIGINT NOT NULL,
  mf_minor BIGINT NOT NULL,
  vat_minor BIGINT NOT NULL,
  td_minor BIGINT NOT NULL,
  td_nights_charged INT NOT NULL,
  td_units INT NOT NULL,
  total_minor BIGINT NOT NULL,
  currency CHAR(3) NOT NULL,
  tax_convention CHAR(1),
  tax_rule_ids BIGINT[],
  fee_notices JSONB NOT NULL DEFAULT '[]',
  below_bar BOOL NOT NULL,
  max_discount_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
  valid_until TIMESTAMPTZ NOT NULL,
  deposit_schedule JSONB NOT NULL,
  cancellation_terms JSONB NOT NULL,
  group_terms JSONB,
  inclusions JSONB,
  language TEXT NOT NULL,
  pricing_trace JSONB NOT NULL,
  guardrails_snapshot JSONB NOT NULL,
  comp_snapshot JSONB,
  auto_send_eligible BOOL NOT NULL,
  auto_send_reasons TEXT[] NOT NULL DEFAULT '{}',
  public_token_hash BYTEA UNIQUE,
  action_id BIGINT,
  created_by BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at TIMESTAMPTZ,
  UNIQUE (hotel_slug, inquiry_id, version)
);
