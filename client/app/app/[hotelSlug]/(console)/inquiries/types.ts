/* Inquiry Guard console types: the shapes server/clefs/modules/inquiries/
   README.md documents. Money arrives as integer minor units plus a Decimal
   string (`*_value`) in the row's currency; money at risk is always an
   estimate and is labelled "est." wherever it shows. */

export type Stage =
  | "new" | "replied" | "quoted" | "payment_pending" | "confirmed_verified"
  | "lost" | "turned_down" | "future" | "spam" | "rerouted";

export type Segment = "fit" | "corporate" | "group" | "long_stay" | "event" | "unknown";

export type Source =
  | "whatsapp" | "email" | "web_form" | "meta_leadgen" | "google_leadform" | "phone_desk" | "concierge";

export interface FieldQuote { field: string; quote: string }

/** What extraction read out of the thread (models.InquiryExtraction). */
export interface Extraction {
  language?: string;
  intent?: string;
  segment?: Segment;
  is_rfp?: boolean;
  check_in?: string | null;
  check_out?: string | null;
  nights?: number | null;
  dates_flexible?: boolean;
  date_text?: string | null;
  rooms?: number | null;
  adults?: number | null;
  children?: number | null;
  room_type_hint?: string | null;
  budget_amount?: string | number | null;
  budget_currency?: string | null;
  budget_per?: string;
  company?: string | null;
  event_name?: string | null;
  special_requests?: string[];
  missing?: string[];
  confidence?: number;
  evidence?: FieldQuote[];
  /** "llm" or "deterministic" (the date and pax parser, no model call). */
  reader?: "llm" | "deterministic";
  /** Validation notes (a date in the past, a budget converted at the peg). */
  warnings?: string[];
  /** "staff" once a person set the segment (extraction never overwrites it). */
  segment_by?: string;
  extracted_at?: string;
}

export interface Inquiry {
  id: number;
  hotel_slug: string;
  ref: string;
  source: Source;
  channel_ref: string | null;
  is_test: boolean;
  contact_name: string | null;
  contact_phone: string | null;
  contact_email: string | null;
  has_phone: boolean;
  has_email: boolean;
  company: string | null;
  consent_basis: string;
  language: string | null;
  segment: Segment;
  is_rfp: boolean;
  intent: string | null;
  check_in: string | null;
  check_out: string | null;
  nights: number | null;
  rooms: number | null;
  adults: number | null;
  children: number | null;
  room_type_code: string | null;
  budget_minor: number | null;
  budget_currency: string | null;
  budget_value: string | null;
  dates_flexible: boolean | null;
  extraction: Extraction | null;
  extraction_confidence: string | number | null;
  stage: Stage;
  guest_engaged: boolean;
  stage_changed_at: string;
  first_response_due_at: string | null;
  first_response_at: string | null;
  first_response_by: string | null;
  first_response_business_min: number | null;
  sla_breached: boolean;
  wa_window_expires_at: string | null;
  ctwa_free_window_expires_at: string | null;
  ad_referral: Record<string, unknown> | null;
  expected_adr_minor: number | null;
  expected_adr_value: string | null;
  room_nights_est: number | null;
  room_nights_estimated: boolean | null;
  stage_probability: string | number | null;
  money_at_risk_minor: number | null;
  money_at_risk_value: string | null;
  currency: string;
  lost_reason: string | null;
  lost_note: string | null;
  future_remind_on: string | null;
  duplicate_of: number | null;
  assignee_user_id: number | null;
  last_inbound_at: string | null;
  last_outbound_at: string | null;
  last_activity_at: string;
  created_at: string;
  updated_at: string;
}

export interface Message {
  id: number;
  direction: "in" | "out";
  channel: string;
  body: string | null;
  attachments: unknown[];
  author_type: "guest" | "staff" | "ai" | "system";
  author_user_id: number | null;
  template_name: string | null;
  action_id: number | null;
  created_at: string;
}

export interface StageEvent {
  from_stage: Stage | null;
  to_stage: Stage;
  at: string;
  actor_type: string | null;
  actor_id: string | null;
  reason: string | null;
}

export interface MergeSuggestion {
  id: number;
  ref: string;
  source: Source;
  score: number;
  check_in: string | null;
  check_out: string | null;
  created_at: string;
  stage: Stage;
}

export interface InquiryDetail extends Inquiry {
  messages: Message[];
  stage_events: StageEvent[];
  merge_suggestions: MergeSuggestion[];
}

// ─── Quotes ──────────────────────────────────────────────────────────────

export type QuoteStatus =
  | "draft" | "awaiting_approval" | "approved" | "sent" | "accepted" | "expired" | "superseded" | "withdrawn";

export type LineSource = "guardrails" | "staff" | "manual";

export interface QuoteLine {
  stay_date: string;
  room_type_code: string | null;
  rooms: number;
  rate_minor: number | null;
  bar_minor: number | null;
  floor_minor: number | null;
  ceiling_minor: number | null;
  rate_value: string | null;
  bar_value: string | null;
  floor_value: string | null;
  ceiling_value: string | null;
  discount_pct: string | number | null;
  source: LineSource;
  demand_index: number | null;
  bar_captured_at: string | null;
  guardrails_source: string | null;
  below_floor_reason?: string | null;
}

export interface DepositMilestone {
  seq: number;
  purpose: "deposit" | "full" | "balance";
  label: string;
  due_on: string | null;
  amount_minor: number;
  amount_value: string;
  includes_td_minor: number;
  td_note: { en: string; ar: string } | null;
  cumulative_pct?: string;
}

export interface GroupTerms {
  cutoff_date: string;
  attrition_pct: string;
  min_rooms_after_attrition: number;
  rooming_list_due: string;
  label: string;
}

export type AutoSendReason =
  | "rates_missing" | "no_guardrails" | "fallback_guardrails" | "below_bar" | "no_bar" | "discount"
  | "bar_stale" | "segment_not_allowed" | "too_many_rooms" | "too_many_nights" | "total_over_limit"
  | "low_confidence" | "missing_fields";

export interface Quote {
  id: number;
  inquiry_id: number;
  version: number;
  status: QuoteStatus;
  lines: QuoteLine[];
  room_minor: number;
  sc_minor: number;
  mf_minor: number;
  vat_minor: number;
  td_minor: number;
  total_minor: number;
  room_value: string;
  sc_value: string;
  mf_value: string;
  vat_value: string;
  td_value: string;
  total_value: string;
  td_nights_charged: number;
  td_units: number;
  currency: string;
  fee_notices: string[];
  below_bar: boolean;
  max_discount_pct: string;
  valid_until: string;
  deposit_schedule: DepositMilestone[];
  cancellation_terms: { en?: string; ar?: string } | null;
  group_terms: GroupTerms | null;
  inclusions: { en?: string; ar?: string } | null;
  language: "en" | "ar";
  auto_send_eligible: boolean;
  auto_send_reasons: AutoSendReason[];
  has_public_link: boolean;
  action_id: number | null;
  created_by: number | null;
  created_at: string;
  sent_at: string | null;
}

export interface SendResult {
  quote: Quote;
  action: { id: number; status: string; type?: string } | null;
}

// ─── Payment links ───────────────────────────────────────────────────────

export type LinkStatus =
  | "created" | "sent" | "paid" | "partially_paid" | "expired" | "cancelled" | "failed" | "refunded";

export interface PaymentLink {
  id: number;
  inquiry_id: number;
  quote_id: number;
  provider: string;
  provider_ref: string | null;
  url: string | null;
  amount_minor: number;
  amount_value: string;
  currency: string;
  purpose: "deposit" | "full" | "balance";
  includes_td_minor: number;
  includes_td_value: string | null;
  status: LinkStatus;
  expires_at: string | null;
  paid_at: string | null;
  paid_amount_minor: number | null;
  paid_amount_value: string | null;
  fee_minor: number | null;
  fee_value: string | null;
  fee_source: "provider" | "settings" | null;
  evidence_file_id: number | null;
  created_at: string;
}

// ─── Pipeline, outcome, match reviews ────────────────────────────────────

export interface Pipeline {
  currency: string;
  estimated: true;
  label: string;
  as_of: string;
  total_minor: number;
  total_value: string;
  count: number;
  rows: { month: string; segment: Segment; count: number; weighted_minor: number; value: string }[];
  months: { month: string; weighted_minor: number; value: string }[];
}

export interface Outcome {
  inquiry_id: number;
  outcome: null | "verified_direct" | "leaked_to_ota" | "lost" | "turned_down" | "cancelled_after_confirm";
  verification_path?: "payment_and_pms" | "stay_completed" | null;
  attribution?: "caused" | "influenced" | null;
  counts_toward_verified_profit?: boolean;
  match_score?: string | number | null;
  verified_revenue_value?: string | null;
  commission_avoided_value?: string | null;
  payment_fee_value?: string | null;
  outcome_net_value?: string | null;
  leaked_channel?: string | null;
  leaked_commission_value?: string | null;
  currency?: string;
  decided_at?: string | null;
}

export interface MatchReview {
  id: number;
  inquiry_id: number;
  ref: string;
  reservation_id: number;
  kind: "verify" | "leak";
  score: number | string;
  evidence: Record<string, unknown> | null;
  status: "open" | "confirmed" | "rejected" | "superseded";
  res_check_in: string | null;
  res_check_out: string | null;
  channel_code: string | null;
  pms_res_id: string | null;
  res_guest_name: string | null;
}
