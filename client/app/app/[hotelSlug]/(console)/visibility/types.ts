/* Response shapes of the visibility routes (server/clefs/modules/visibility
   README, "HTTP API"). Decimals arrive as strings (the API's number
   contract), money as integer minor units plus currency, vendor costs in
   micros. Nothing here is computed: model.ts turns these into view values. */

import type { ActionStatus } from "../../../../lib/types";

// ─── Shared ──────────────────────────────────────────────────────────────

export type Dec = string | number | null;

/** Spend and budget in USD micros; budget_micros is null when no FX rate converts a non-USD budget. */
export interface Budget { spent_micros: number; budget_micros: number | null; remaining_micros: number | null; exhausted: boolean }

// ─── GET /google ─────────────────────────────────────────────────────────

export interface PriceRow { source: string; official: boolean; price_minor: number; currency: string; channel: string | null }

export interface ParityRow {
  check_in: string; check_out: string; official_present: boolean; official_minor: number | null;
  lowest_minor: number | null; lowest_source: string | null; lowest_channel: string | null; price_basis: string | null;
  gap_minor: number | null; gap_pct: Dec; breach: boolean; prices: PriceRow[]; currency: string; observed_at: string;
}

export interface AttributionMonth {
  month: string; gbp_website_clicks: number | null; direct_bookings: number; direct_room_revenue_minor: number;
  commission_avoided_minor: number; reference_ota: string | null; currency: string; computed_at: string;
  line: string; label: string;
}

export interface GoogleView {
  serpapi_enabled: boolean;
  setup_missing: string[];
  /** rate_30d: share of stay-date checks in the last 30 days that showed the official price
   *  (the *_30d counts are optional so an older API still renders). */
  presence: { latest: boolean | null; rate_30d: Dec; shown_30d?: number; checks_30d?: number; days_30d?: number };
  parity: ParityRow[];
  tolerance_pct: Dec;
  rank_history: { day: string; organic_rank: number | null; organic_total: number | null; official_present: boolean | null }[];
  attribution: AttributionMonth | null;
  labels: { disclosure: string; parity: string; price_accuracy: string; vendor_risk_url?: string | null };
}

// ─── GET /rank ───────────────────────────────────────────────────────────

export interface RankStats { points: number; found: number; arp: Dec; atrp: Dec; solv: Dec }

export interface RankKeyword {
  keyword: string;
  latest: (RankStats & { run_at: string; competitors: (RankStats & { comp_id: string; name: string | null })[] }) | null;
  history: { run_at: string; solv: Dec; atrp: Dec }[];
  calibration: { status: "ok" | "drift" | "uncalibrated"; month?: string; spearman?: Dec; top3_agreement?: Dec; note?: string; reason?: string };
}

export interface RankView { keywords: RankKeyword[]; setup_missing: string[]; grid: { size: number; spacing_m: number }; label: string }

// ─── GET /gbp ────────────────────────────────────────────────────────────

export type GbpStatus = "project_not_approved" | "not_connected" | "pick_location" | "connected";

export interface GbpState { status: GbpStatus; qpm: number; location: string | null; message: string | null; connection?: unknown }

export interface GbpKpi {
  month: string; reach_user_days: number | null; reach_prior_user_days: number | null; reach_yoy_user_days: number | null;
  actions: number | null; action_rate: Dec; website_clicks: number | null; branded_share: Dec;
  keywords_under_threshold: number | null; computed_at: string;
}

export interface GbpKeyword { keyword: string; value: number | null; threshold: number | null; is_branded: boolean; display: string }

/** reviews.api.reply_stats: response_rate is a percent (0-100). */
export interface ReplyStats { unanswered?: number; response_rate?: Dec; median_reply_h?: Dec }

/** POST /visibility/gbp/draft-replies: drafts made through reviews.api.draft_reply (never posted). */
export interface DraftRepliesOut {
  drafted: number;
  considered: number;
  unanswered: number | null;
  drafts: { review_id: number; reply_id: number; status: string; needs_approval: boolean; language: string | null }[];
  reviews_tab: string;
}

export interface GbpView {
  state: GbpState;
  kpis: GbpKpi[];
  reach_label: string;
  keywords: { month: string | null; rows: GbpKeyword[]; summary: { total_exact: number; branded_exact: number; under_threshold: number; branded_share: Dec; display: string } | null } | null;
  review_replies: ReplyStats | null;
  utm: { action_id: number | null; status: ActionStatus | null; created_at: string | null } | null;
}

export interface GbpLocation { location: string; title: string; address: string | null; website_uri: string | null; account: string | null }

// ─── GET /ai ─────────────────────────────────────────────────────────────

export interface AiCell {
  engine: string; market: string; language: string; window_weeks: number; epoch: number; label: string;
  avg_position: Dec; runs: number; mentions: number; rate: Dec; ci_low: Dec; ci_high: Dec; sufficient: boolean; display: string;
}

export interface SovEntity { entity: string; name: string; mentions: number; sov: Dec; ci_low: Dec; ci_high: Dec }

export interface AiFact {
  id: number; fact_key: string; label: string; claimed_value: string | null; claimed_quote: string | null;
  truth_value: string | null; truth_source: string | null;
  error_type: "wrong_value" | "false_absent" | "false_closed" | "wrong_star" | "price_band_off" | "unverifiable";
  severity: number; engines: string[]; cited_domains: string[]; occurrences: number; run_ids: number[];
  status: "open" | "recurred" | "fixing" | "fixed" | "dismissed"; first_seen: string | null; last_seen: string | null;
  action_id: number | null;
}

export interface AiEngine { engine: string; label: string; status: string; disclaimer: string }

export interface AiEpoch { engine: string; epoch: number; status: "active" | "suggested" | "dismissed"; version_key: string; reason: string | null; since: string | null; note: string | null }

export interface AiView {
  enabled: boolean;
  setup_missing: string[];
  engines: AiEngine[];
  week: string | null;
  cells: AiCell[];
  sov: { engine: string; market: string; language: string; sufficient: boolean; entities: SovEntity[] }[];
  source_mix: Record<string, { self_wins?: Record<string, number>; comp_wins?: Record<string, number>; all?: Record<string, number> }>;
  facts: AiFact[];
  min_sample: { runs: number; max_halfwidth: string };
  epochs: AiEpoch[];
  suggested_resets: string[];
  latest_batch: { id: number; status: string; planned: number; done: number; cost_micros: number; week: string | null; steps: unknown[] } | null;
  budget: Budget;
  labels: { simulation: string; third_party: string };
  method_note: string;
}

export interface AiCitation { url: string; title: string | null; domain: string | null; class: string | null; kind: "citation" | "source" | null; start_index: number | null; end_index: number | null }

export interface AiRun {
  id: number; batch_id: number; engine: string; label: string; prompt_key: string; prompt: string; intent: string;
  market: string; language: string; model_id: string | null; baseline_epoch: number; repeat_idx: number;
  run_at: string | null; status: string; searched: boolean | null; answer_text: string | null; citations: AiCitation[];
  entities: { key: string; name: string; position: number; via?: string }[]; self_mentioned: boolean | null;
  self_position: number | null; self_cited: boolean | null;
}

// ─── GET /site-health ────────────────────────────────────────────────────

export interface SiteHealthCheck {
  id: number; checked_at: string; url: string;
  crux: { status: "ok" | "insufficient_data" | "error"; metrics?: { lcp_ms?: Dec; inp_ms?: Dec; cls?: Dec }; outside_good?: { metric: string; label: string; value: Dec; threshold: Dec; note: string }[] } | null;
  psi: { score: Dec; lcp_ms: Dec; cls: Dec; tbt_ms: Dec; fetched_at?: string | null } | null;
  jsonld_found: boolean | null; jsonld_types: string[] | null;
  jsonld_fields: { pages?: { url: string; status: string | number; found: boolean; types: string[] }[]; fields?: unknown } | null;
  jsonld_issues: { severity: "required" | "recommended" | "useful"; type: string; field: string; url: string; message: string }[] | null;
  facts_vs_kb: { fact_key: string; ours: string | null; theirs: string | null; status: "match" | "mismatch" | "missing"; source_url: string | null }[] | null;
}

export interface SiteHealthView { latest: SiteHealthCheck | null; setup_missing: string[]; thresholds: { lcp_ms: Dec; inp_ms: Dec; cls: Dec }; note: string }

// ─── GET/PUT /settings ───────────────────────────────────────────────────

/** A source market for the AI prompts: ISO country plus an optional city. */
export interface AiMarket { market: string; city?: string | null }

export interface VisSettings {
  place_id: string | null; gbp_location: string | null; gh_property_token: string | null; website_url: string | null;
  booking_engine_host: string | null; brand_aliases: string[]; rank_keywords: string[] | null; area: string | null;
  landmarks: string[]; room_page_urls: string[]; free_booking_link_pattern: string | null; grid_size: number;
  grid_spacing_m: number; ai_markets: AiMarket[]; monthly_budget_minor: number; budget_currency: string;
  engines_enabled: string[]; serpapi_enabled: boolean; updated_at: string | null; gbp: GbpState; budget: Budget;
}
