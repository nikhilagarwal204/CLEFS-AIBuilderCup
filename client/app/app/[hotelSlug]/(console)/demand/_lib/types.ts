/* ═══════════════════════════════════════════════════════════════════════
   Demand Radar console types: the response shapes documented in
   server/clefs/modules/demand/README.md ("HTTP API (console)"). Amounts
   are Decimal strings in the hotel's currency unless a field says _minor;
   dates are hotel-local YYYY-MM-DD.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Events ──────────────────────────────────────────────────────────────

export type EventCategory =
  | "CONFERENCE" | "EXPO" | "SPORTS" | "CONCERT" | "FESTIVAL" | "PUBLIC_HOLIDAY" | "SCHOOL_HOLIDAY"
  | "RELIGIOUS" | "SOURCE_MARKET_HOLIDAY" | "COMMUNITY" | "CITYWIDE";

export type EventStatus = "SCHEDULED" | "RESCHEDULED" | "POSTPONED" | "CANCELLED";
export type Verification = "VERIFIED" | "UNVERIFIED";
export type EventSource = "CURATED" | "PREDICTHQ" | "MANUAL" | "ICS" | "TICKETMASTER" | "AI_WEB";

export interface EventSourceRow {
  source: EventSource | string;
  external_id: string;
  fetched_at: string | null;
  confidence: number | null;
  urls: string[];
}

export interface DemandEvent {
  id: string;
  title: string;
  category: EventCategory;
  venue_codes: string[];
  emirate: string | null;
  lat: number | null;
  lon: number | null;
  start_date: string;
  end_date: string;
  status: EventStatus;
  original_start: string | null;
  original_end: string | null;
  attendance_est: number | null;
  attendance_source: string | null;
  phq_rank: number | null;
  moon_dependent: boolean;
  date_uncertainty_days: number | null;
  verification: Verification;
  confidence: number | null;
  date_source: string | null;
  source_urls: string[];
  description: string | null;
  scope: "global" | "hotel";
  owner_confirmed: boolean;
  first_seen_at: string | null;
  last_checked_at: string | null;
  sources: EventSourceRow[];
  corroborated: boolean;
  actionable: boolean;
}

export interface EventChange {
  at: string;
  field: string;
  old: unknown;
  new: unknown;
  source: string | null;
  reason: string | null;
  source_url: string | null;
}

export interface EventDetail extends DemandEvent {
  changes: EventChange[];
}

/** An event as the day drawer lists it: the event plus its score for the night. */
export interface DayEvent extends DemandEvent {
  night_score: number;
  relevance: number;
  distance_km: number | null;
  travel_band: string | null;
  corridor_match: boolean | null;
  features: Record<string, unknown> | null;
  dismissed: boolean;
  dismiss_reason: string | null;
}

export interface RelevanceRow {
  event_id: string;
  title: string;
  category: EventCategory;
  start: string;
  end: string;
  status: EventStatus;
  score: number;
  distance_km: number | null;
  expected_rn_uplift: number | null;
  dismissed: boolean;
  dismiss_reason: string | null;
}

export interface IcsResult {
  imported: number;
  created: number;
  matched: number;
  rescheduled: number;
  event_ids: string[];
}

export interface DismissResult {
  event_id: string;
  dismissed: boolean;
  scope: "event" | "class" | "venue";
  reason: string | null;
  class_key: string | null;
}

export interface ClassPref {
  class_key: string;
  useful: number;
  dismissed: number;
  muted: boolean;
  weight: number;
}

// ─── Comp rates (always indicative) ──────────────────────────────────────

export interface NightBand {
  comps: number;
  priced: number;
  sold_out: number;
  min: string | null;
  median: string | null;
  max: string | null;
  median_minor: number | null;
  currency: string | null;
  captured_at: string | null;
  sources: string[];
  indicative: true;
  label: string;
}

export interface CompRateRow {
  competitor: string;
  comp_id: string;
  stay_date: string;
  channel: string | null;
  rate: string | null;
  currency: string;
  sold_out: boolean;
  source: string;
  fetched_at: string | null;
  indicative: boolean;
  label: string;
  fx_converted: boolean;
  is_demo: boolean;
}

// ─── Calendar and day view ───────────────────────────────────────────────

export interface CellEvent {
  event_id: string;
  title: string;
  category: EventCategory;
  night_score: number;
  verification: Verification | null;
}

export interface CalendarCell {
  stay_date: string;
  demand_index: number | null;
  coverage: number;
  weekend: boolean;
  events: CellEvent[];
  market_holidays: unknown[];
  recommendation: { id: number; status: RecStatus; current_bar: string; recommended: string; change_pct: string } | null;
  comp: (NightBand & { stay_date: string }) | null;
  bar: string | null;
}

export interface CalendarResponse {
  from: string;
  to: string;
  days: 30 | 60 | 90;
  currency: string;
  reference_room_type: string | null;
  comp_label: string;
  cells: CalendarCell[];
}

export interface NightComponents {
  season_z: number | null;
  event_score: number | null;
  event_z: number | null;
  pace_z: number | null;
  comp_z: number | null;
  pace: { otb: number; lead: number; base: number | null; n: number; pace_gap: number | null } | null;
  comp: { median_minor: number | null; sold_out: number; comps: number; comp_move: number | null } | null;
  has: { season: boolean; pace: boolean; comp: boolean; events: boolean };
  signal_free?: boolean;
}

export interface Night {
  stay_date: string;
  demand_index: number;
  coverage: number;
  components: NightComponents;
  top_event_ids: string[];
  computed_at: string | null;
}

export interface BarRow {
  stay_date: string;
  room_type_code: string;
  bar: string;
  bar_minor: number;
  currency: string;
  source: "MANUAL" | "CSV" | "RATE_EVIDENCE";
  captured_at: string;
  note: string | null;
}

export interface PacePoint { days_before: number; rooms_otb: number | null }

export interface PriceGuardrails {
  floor: string;
  ceiling: string;
  recommended: string | null;
  currency: string;
  confidence: number;
  reasons: string[];
  source: "rate_guardrails" | "bar_only" | "fallback";
  max_step_pct: number | null;
}

export interface Guidance {
  id: number;
  event_id: string;
  kind: "STAFFING" | "KB_UPDATE" | "PRE_ARRIVAL_OFFER";
  stay_date: string;
  payload: Record<string, unknown>;
  status: "PROPOSED" | "APPROVED" | "DONE" | "DISMISSED" | "EXPIRED";
  action_id: number | null;
}

export interface DayResponse {
  stay_date: string;
  demand_index: Night;
  events: DayEvent[];
  comp: { label: string; rates: CompRateRow[]; summary: NightBand };
  bar: BarRow[];
  reference_room_type: string | null;
  pace: { own: PacePoint[]; baseline: PacePoint[]; label: string };
  recommendations: Recommendation[];
  guardrails: PriceGuardrails | null;
  guidance: Guidance[];
}

// ─── Recommendations and outcomes ────────────────────────────────────────

export type RecStatus = "PROPOSED" | "APPROVED" | "ADJUSTED" | "REJECTED" | "SUPERSEDED" | "EXPIRED" | "APPLIED";

export interface Lever { code: string; label: string; rooms?: number }

export interface Recommendation {
  id: number;
  stay_date: string;
  room_type_code: string;
  currency: string;
  current_bar: string;
  recommended: string;
  floor: string;
  ceiling: string;
  decided: string | null;
  change_pct: string;
  levers: Lever[];
  rationale: string | null;
  why: string | null;
  why_source: "llm" | "template" | null;
  driver_event_ids: string[];
  demand_index: number | null;
  confidence: number | null;
  impact: string | null;
  impact_minor: number | null;
  impact_label: string;
  status: RecStatus;
  status_reason: string | null;
  action_id: number | null;
  outcome: Outcome | null;
  outcome_label: string | null;
  created_at: string;
  decided_at: string | null;
  applied_at: string | null;
}

export interface Outcome {
  label: string;
  lead_days: number | null;
  otb_at_decision: number | null;
  rooms_sold: number | null;
  /** 0-1 */
  occupancy: number | null;
  adr: string | null;
  revpar: string | null;
  pickup_after_decision: number | null;
  baseline_pickup: number | null;
  pickup_vs_baseline: number | null;
  net_revenue: { net: string; currency: string } | null;
  hours_to_decision: number | null;
}

export interface Top5Response {
  label: string;
  items: (Recommendation & { rank_score: number; group: string | null })[];
}

export interface OutcomesResponse {
  from: string;
  to: string;
  label: string;
  actionability: {
    recommendations: number;
    decided: number;
    decided_within_48h_pct: number | null;
    approved: number;
    applied: number;
    applied_pct: number | null;
    median_hours_to_decision: number | null;
    rejected: number;
    expired: number;
  };
  outcomes: (Recommendation & { outcome: Outcome })[];
}

// ─── Settings: comp set, guardrails, source markets, GHC ─────────────────

export interface Comp {
  comp_id: string;
  name: string;
  property_token: string | null;
  place_id: string | null;
  lat: number | null;
  lng: number | null;
  stars: number | null;
  rooms: number | null;
  aliases: string[];
  is_demo: boolean;
  active: boolean;
  origin: "manual" | "discovered" | "csv" | "demo";
}

export interface DiscoverCandidate {
  name: string;
  property_token: string | null;
  stars: number | null;
  rate: string | null;
  overall_rating: number | null;
  reviews: number | null;
  is_self: boolean;
  comp_id: string | null;
}

export interface DiscoverResponse {
  query: string;
  check_in: string;
  currency: string;
  candidates: DiscoverCandidate[];
  label: string;
}

export interface Guardrail {
  id: number;
  room_type_code: string;
  month: number | null;
  dow: number | null;
  floor: string;
  ceiling: string;
  currency: string;
  max_step_pct: number | string;
  note: string | null;
  updated_at: string | null;
}

export interface SourceMarket { market_code: string; label: string; share_pct: string; countries: string[] }

export interface SourceMarkets { is_default: boolean; source_url: string | null; markets: SourceMarket[] }

export type GhcStatus = "TODO" | "DONE" | "NA";

export interface GhcItem {
  code: string;
  label: string;
  detail: string | null;
  source_url: string | null;
  status: GhcStatus;
  note: string | null;
  updated_at: string | null;
}

export interface GhcChecklist {
  deadline: string;
  days_left: number;
  passed: boolean;
  open_items: number;
  summary: string | null;
  items: GhcItem[];
}

// ─── Campaigns (export only) ─────────────────────────────────────────────

export type CampaignChannel = "WHATSAPP" | "INSTAGRAM" | "EMAIL";

export interface Campaign {
  id: number;
  event_id: string;
  channel: CampaignChannel;
  audience: { kind: "consented_contacts" | "organic_post"; count: number | null; countries: string[]; note?: string | null };
  copy: { text?: string; caption?: string; subject?: string; body?: string };
  copy_source: string | null;
  suggested_send_at: string | null;
  status: "DRAFT" | "APPROVED" | "EXPORTED" | "ARCHIVED";
  action_id: number | null;
  created_at: string;
  exported_at: string | null;
  export_only: true;
}
