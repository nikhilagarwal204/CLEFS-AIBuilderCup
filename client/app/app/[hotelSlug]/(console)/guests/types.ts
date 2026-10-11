/* ═══════════════════════════════════════════════════════════════════════
   Guests console response shapes, mirrored from the guests README (the
   identity part) and journeys/router.py. Guest data arrives already masked
   for the viewer's role by core.pii, so a masked field is a string like
   "A***" or null; the client never un-masks anything.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Identity part ───────────────────────────────────────────────────────

export type ContactKind = "phone" | "email";
export type Lang = "en" | "ar";
/** The languages the consent wording (gst.v3) is written in: what a guest read. */
export type ConsentLang = "en" | "ar" | "ja" | "ko" | "th";

export interface Health {
  allergies: string[];
  notes: string | null;
}

export interface Preferences {
  tags: string[];
  notes: string | null;
  /** null when the viewer's role may not see health data. */
  health: Health | null;
}

export interface Profile {
  id: number;
  first_name: string | null;
  language: Lang | string | null;
  preferences: Preferences;
  stays_count: number;
  first_seen: string | null;
  last_seen: string | null;
  vip: boolean;
  returning: boolean;
  has_phone: boolean;
  has_email: boolean;
  contacts: { kind: ContactKind; value: string | null }[];
  created_at: string;
  updated_at: string;
}

export interface ConsentDecision {
  granted: boolean;
  at: string | null;
  text_version: string | null;
  source: string | null;
}

export interface ConsentView {
  ref: string;
  purposes: Record<string, ConsentDecision>;
  suppressed: boolean;
  marketing_suppressed: boolean;
}

export interface StayRow {
  id: number;
  reservation_id: number | null;
  origin: string | null;
  room: string | null;
  check_in: string | null;
  check_out: string | null;
  source: string | null;
  status: string | null;
}

export interface DirectCode {
  id: number;
  code: string;
  hotel_slug: string;
  guest_profile_id: number | null;
  discount_pct: string | number | null;
  valid_to: string;
  redeemed_at: string | null;
  redeemed_reservation_id: number | null;
  plan_id: number | null;
  offer_id: number | null;
  created_at: string;
}

export interface ProfileDetail extends Profile {
  stays: StayRow[];
  consents: ConsentView[];
  direct_codes: DirectCode[];
}

export interface Badge {
  stay_id: number;
  reservation_id: number | null;
  room: string | null;
  check_in: string | null;
  check_out: string | null;
  profile_id: number | null;
  stay_number: number;
  vip: boolean;
  first_name: string | null;
  tags: string[];
  health: Health | null;
  label: string;
}

export interface Overview {
  profiles: number;
  returning_profiles: number;
  vip_profiles: number;
  returning_arrivals_today: number;
  consents: Record<string, number>;
  suppressed_contacts: number;
  direct_codes: { issued: number; redeemed: number };
  returning_badge: boolean;
  day: string;
}

export interface ConsentPoint {
  id: number;
  provenance: string;
  status: string;
  capture_source: string | null;
  reservation_id: number | null;
  usable_until: string | null;
  created_at: string;
}

export interface ConsentLookup extends ConsentView {
  points: ConsentPoint[];
  profile_id: number | null;
}

export interface J8Step {
  seq: number;
  status: string;
  due_at: string | null;
  skip_reason: string | null;
}

export interface RebookRow {
  stay_id: number;
  reservation_id: number | null;
  room: string | null;
  check_out: string;
  days_since: number;
  source: string | null;
  ota: boolean;
  profile: Profile | null;
  marketing: { whatsapp: boolean; email: boolean };
  suppressed: boolean;
  eligible: boolean;
  j8: J8Step[];
}

export interface ConsentFlags {
  transfer_abroad: boolean;
  profile_memory: boolean;
  whatsapp_updates: boolean;
  marketing_whatsapp: boolean;
  marketing_email: boolean;
}

/** One row of the legacy guest_rooms roster (the GuestModal shape). */
export interface RosterGuest {
  name: string;
  type: string;
  age?: number;
}

export interface RosterRoom {
  roomNumber: string;
  guests?: RosterGuest[];
  stayType?: string;
  checkIn?: string;
  checkOut?: string;
  preferences?: { tags?: string[]; language?: string };
  loyaltyTier?: string;
  occasions?: string[];
  /** Masked copies left by the hourly scrub (raw values never stay). */
  phone?: string;
  email?: string;
  [key: string]: unknown;
}

export type Roster = Record<string, RosterRoom>;

// ─── Journeys part ───────────────────────────────────────────────────────

export type StepKey = "j1" | "j2" | "j3" | "j4" | "j5" | "j6_pre" | "j6_post" | "j7" | "j8";

export interface FunnelRow {
  planned: number;
  sent: number;
  delivered: number;
  replied: number;
  observed: number;
  proposed: number;
  skipped: Record<string, number>;
  other: number;
}

export interface DialChip {
  action_type: string;
  level: string | null;
  effective_level: string | null;
  max_level: string | null;
}

export interface JourneysOverview {
  days: number;
  funnel: Record<StepKey, FunnelRow>;
  dial: Partial<Record<StepKey, DialChip>>;
  capture: { ota_stays: number; captured: number; rate: number | null };
  optouts: Record<string, number>;
  cost: { currency: string; amount: string };
  templates_approved: number;
  all_observing: boolean;
}

export interface TemplateButton {
  type?: string;
  text?: string;
  url?: string;
  [key: string]: unknown;
}

export interface Template {
  id: number;
  step: StepKey;
  channel: "whatsapp" | "email";
  language: Lang;
  meta_name: string | null;
  version: number;
  subject: string | null;
  body: string;
  buttons: TemplateButton[] | null;
  variables: { name?: string; example?: string; source?: string; [k: string]: unknown }[] | null;
  lint: { ok: boolean; errors: string[] } | null;
  category_expected: string;
  category_meta: string | null;
  meta_status: string | null;
  quality: string | null;
  approved_at: string | null;
  submitted_at: string | null;
  sendable: boolean;
  dir: "rtl" | "ltr";
  category_mismatch: boolean;
}

export interface Offer {
  id: number;
  hotel_slug: string;
  step: "j3" | "j8";
  title_en: string;
  title_ar: string | null;
  price_minor: number | null;
  price: string | null;
  discount_pct: string | null;
  currency: string;
  code_prefix: string | null;
  valid_from: string;
  valid_to: string;
  approved_by: number | null;
  approved_at: string | null;
  active: boolean;
  created_at: string;
}

export interface Window {
  start: string;
  end: string;
}

export interface JourneySettings {
  enabled_steps: StepKey[];
  utility_window: Window;
  marketing_window: Window;
  j2_min_lead_days: number;
  j5_offset_h: number;
  j7_offset_days: number;
  j8_offsets: number[];
  review_links: { google_url: string | null; tripadvisor_url: string | null };
  j7_for_ota_provided_contacts: boolean;
  standard_checkin_time: string;
  standard_checkout_time: string;
  ai_on_unconsented_inbound: boolean;
  wa_billing_attested_at: string | null;
  booking_engine_url: string | null;
  planning_info: string | null;
  checkin_bring: string | null;
  directions: string | null;
}

export interface TimelinePlan {
  id: number;
  step: StepKey;
  seq: number;
  status: string;
  skip_reason: string | null;
  channel: string | null;
  due_at: string | null;
  valid_until: string | null;
  last_attempt_at: string | null;
  action_id: number | null;
  attempt: number | null;
  error_code: string | null;
  category_at_send: string | null;
  direct_code: string | null;
  plan_version: number | null;
  checkin_time_inferred: boolean | null;
}

export interface Timeline {
  reservation_id: number;
  plans: TimelinePlan[];
  replies: { id: number; plan_id: number | null; outcome: string; deterministic_hit: boolean | null; llm_used: boolean | null;
    summary_en: string | null; service_ref: string | null; action_id: number | null; created_at: string }[];
  messages: { id: number; plan_id: number | null; kind: string; channel: string; sent_at: string }[];
  prefs: { arrival_time: string | null; requests: string | null; occasion: string | null; language: string | null;
    submitted_at: string | null } | null;
  feedback: { overall: number | null; went_well: string | null; improve: string | null; contact_ok: boolean | null;
    language: string | null; followup_ref: string | null; submitted_at: string | null } | null;
}
