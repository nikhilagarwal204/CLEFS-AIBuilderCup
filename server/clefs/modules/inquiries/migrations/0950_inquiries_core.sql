-- ─── Inquiry Guard core (04 spec 4.1) ───
-- One row per direct inquiry, whatever the channel. Contacts are never stored
-- in clear: *_enc is privacy.seal, *_hmac is privacy.contact_hmac (F8), which
-- is how dedupe, reopen and leak matching join without plaintext.

CREATE TABLE IF NOT EXISTS inq_inquiries (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  ref TEXT NOT NULL,
  source TEXT NOT NULL CHECK (source IN ('whatsapp','email','web_form','meta_leadgen','google_leadform','phone_desk','concierge')),
  channel_ref TEXT,
  external_lead_id TEXT,
  ad_referral JSONB,
  ctwa_clid TEXT,
  gclid TEXT,
  utm JSONB,
  page_url TEXT,
  is_test BOOL NOT NULL DEFAULT FALSE,
  contact_name TEXT,
  contact_phone_enc BYTEA,
  contact_phone_hmac BYTEA,
  contact_email_enc BYTEA,
  contact_email_hmac BYTEA,
  company TEXT,
  consent_basis TEXT NOT NULL CHECK (consent_basis IN ('express_web_form','pre_contract_notice','staff_logged')),
  consent_ids BIGINT[] NOT NULL DEFAULT '{}',
  notice_sent_at TIMESTAMPTZ,
  language TEXT,
  segment TEXT NOT NULL DEFAULT 'unknown' CHECK (segment IN ('fit','corporate','group','long_stay','event','unknown')),
  is_rfp BOOL NOT NULL DEFAULT FALSE,
  intent TEXT,
  check_in DATE,
  check_out DATE,
  nights INT,
  rooms INT,
  adults INT,
  children INT,
  room_type_code TEXT,
  budget_minor BIGINT,
  budget_currency CHAR(3),
  dates_flexible BOOL,
  extraction JSONB,
  extraction_confidence NUMERIC(3,2),
  stage TEXT NOT NULL DEFAULT 'new' CHECK (stage IN ('new','replied','quoted','payment_pending','confirmed_verified','lost',
    'turned_down','future','spam','rerouted')),
  guest_engaged BOOL NOT NULL DEFAULT FALSE,
  stage_changed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  first_response_due_at TIMESTAMPTZ,
  first_response_at TIMESTAMPTZ,
  first_response_by TEXT,
  first_response_business_min INT,
  sla_breached BOOL NOT NULL DEFAULT FALSE,
  wa_window_expires_at TIMESTAMPTZ,
  ctwa_free_window_expires_at TIMESTAMPTZ,
  expected_adr_minor BIGINT,
  room_nights_est INT,
  room_nights_estimated BOOL,
  stage_probability NUMERIC(4,3),
  money_at_risk_minor BIGINT,
  currency CHAR(3) NOT NULL,
  -- lost codes plus turned_down codes (4.10)
  lost_reason TEXT CHECK (lost_reason IN ('PRICE_VS_COMP','NO_AVAILABILITY','SLOW_RESPONSE','BOOKED_VIA_OTA','BOOKED_COMPETITOR',
    'GUEST_UNRESPONSIVE','PAYMENT_LINK_EXPIRED','POLICY_TERMS','DATES_CHANGED','PRODUCT_LOCATION_MISMATCH','TRIP_CANCELLED',
    'SPAM_DUPLICATE','DISPLACEMENT','POLICY','BELOW_FLOOR')),
  lost_note TEXT,
  future_remind_on DATE,
  duplicate_of BIGINT REFERENCES inq_inquiries(id),
  assignee_user_id BIGINT,
  last_inbound_at TIMESTAMPTZ,
  last_outbound_at TIMESTAMPTZ,
  last_activity_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, ref)
);
-- A lead id is unique per source: Google retries and Meta re-deliveries land
-- on the same row (4.3).
CREATE UNIQUE INDEX IF NOT EXISTS inq_inquiries_external_lead_uidx
  ON inq_inquiries (hotel_slug, source, external_lead_id) WHERE external_lead_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS inq_inquiries_stage_due_idx ON inq_inquiries (hotel_slug, stage, first_response_due_at);
CREATE INDEX IF NOT EXISTS inq_inquiries_phone_hmac_idx ON inq_inquiries (hotel_slug, contact_phone_hmac);
CREATE INDEX IF NOT EXISTS inq_inquiries_email_hmac_idx ON inq_inquiries (hotel_slug, contact_email_hmac);

CREATE TABLE IF NOT EXISTS inq_messages (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id),
  direction TEXT NOT NULL CHECK (direction IN ('in','out')),
  channel TEXT NOT NULL,
  provider_message_id TEXT,
  body TEXT,
  attachments JSONB NOT NULL DEFAULT '[]',
  author_type TEXT NOT NULL CHECK (author_type IN ('guest','staff','ai','system')),
  author_user_id BIGINT,
  outbound_message_id BIGINT,
  template_name TEXT,
  action_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (hotel_slug, channel, provider_message_id)
);
CREATE INDEX IF NOT EXISTS inq_messages_inquiry_idx ON inq_messages (inquiry_id, created_at);

CREATE TABLE IF NOT EXISTS inq_stage_events (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL REFERENCES inq_inquiries(id),
  from_stage TEXT,
  to_stage TEXT NOT NULL,
  at TIMESTAMPTZ NOT NULL DEFAULT now(),
  actor_type TEXT,
  actor_id TEXT,
  reason TEXT,
  data JSONB
);
CREATE INDEX IF NOT EXISTS inq_stage_events_inquiry_idx ON inq_stage_events (inquiry_id, at);

-- Recalibrated values (4.5); absent rows use the assumed priors.
CREATE TABLE IF NOT EXISTS inq_stage_probabilities (
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  stage TEXT NOT NULL,
  p NUMERIC(4,3) NOT NULL,
  n INT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (hotel_slug, stage)
);

-- Seeded per hotel from 4.6 and 4.10.
CREATE TABLE IF NOT EXISTS inq_templates (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  name TEXT NOT NULL CHECK (name IN ('inq_quote_ready_v1','inq_quote_followup_v1')),
  language TEXT NOT NULL CHECK (language IN ('en','ar')),
  body TEXT NOT NULL,
  buttons JSONB NOT NULL DEFAULT '[]',
  version INT NOT NULL,
  meta_status TEXT,
  category_meta TEXT,
  approved_by BIGINT,
  approved_at TIMESTAMPTZ,
  submitted_at TIMESTAMPTZ,
  synced_at TIMESTAMPTZ,
  UNIQUE (hotel_slug, name, language, version)
);
