-- ─── Outcomes and match reviews (04 spec 4.1, 4.9) ───

CREATE TABLE IF NOT EXISTS inq_outcomes (
  inquiry_id BIGINT PRIMARY KEY REFERENCES inq_inquiries(id),
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  outcome TEXT NOT NULL CHECK (outcome IN ('verified_direct','leaked_to_ota','lost','turned_down','cancelled_after_confirm')),
  verification_path TEXT CHECK (verification_path IN ('payment_and_pms','stay_completed')),
  attribution TEXT CHECK (attribution IN ('caused','influenced')),
  counts_toward_verified_profit BOOL NOT NULL DEFAULT FALSE,
  verified_reservation_id BIGINT REFERENCES reservations(id),
  match_score NUMERIC(4,3),
  match_evidence JSONB,
  payment_link_id BIGINT,
  verified_revenue_minor BIGINT,
  commission_avoided_minor BIGINT,
  payment_fee_minor BIGINT,
  outcome_net_minor BIGINT,
  commission_basis JSONB,
  leaked_reservation_id BIGINT REFERENCES reservations(id),
  leaked_channel TEXT,
  leaked_commission_minor BIGINT,
  profit_guard_ref TEXT,
  currency CHAR(3) NOT NULL,
  decided_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  action_id BIGINT
);

CREATE TABLE IF NOT EXISTS inq_match_reviews (
  id BIGSERIAL PRIMARY KEY,
  hotel_slug TEXT NOT NULL REFERENCES hotels(slug),
  inquiry_id BIGINT NOT NULL,
  reservation_id BIGINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('verify','leak')),
  score NUMERIC(4,3),
  evidence JSONB,
  status TEXT NOT NULL DEFAULT 'open',
  decided_by BIGINT,
  decided_at TIMESTAMPTZ,
  UNIQUE (inquiry_id, reservation_id, kind)
);
