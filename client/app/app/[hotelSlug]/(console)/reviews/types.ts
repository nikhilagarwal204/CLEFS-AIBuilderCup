/* Reviews console types: mirror the reviews module README (HTTP API) exactly,
   so a server shape change shows up as a type error here and nowhere else. */

// ─── Reviews ─────────────────────────────────────────────────────────────

export type Platform = "google" | "tripadvisor" | "booking" | "agoda" | "expedia" | "email" | string;
export type Severity = "none" | "low" | "medium" | "high" | string;
export type ReplyStatus = "draft" | "approved" | "copied" | "posted" | "discarded";

export interface ReviewTheme {
  key: string;
  label: string;
  department: string | null;
  polarity: "negative" | "positive" | "neutral" | string;
  summary_en: string | null;
}

export interface Review {
  id: number;
  platform: Platform;
  source_id: number | null;
  external_id: string;
  author_display: string | null;
  title: string | null;
  text: string | null;
  purged: boolean;
  rating: number | null;
  rating_scale: number | null;
  rating_norm_5: number | null;
  language: string | null;
  posted_at: string | null;
  has_owner_response: boolean;
  owner_response_at: string | null;
  room_hint: string | null;
  stay_date_guess: string | null;
  ingested_at: string;
  analyzed_at: string | null;
  sentiment: number | null;
  themes: ReviewTheme[];
  summary_en: string | null;
  severity: Severity | null;
  negative: boolean | null;
  linked_request_ids: number[];
  reply_id: number | null;
  reply_status: ReplyStatus | null;
  reply_needs_approval: boolean | null;
}

export interface LinkedRequest {
  id: number;
  room: string | null;
  request_type: string | null;
  department: string | null;
  priority: string | null;
  status: string;
  created_at: string;
  resolved_at: string | null;
}

export interface ReviewDetail extends Review {
  model: string | null;
  prompt_version: string | null;
  source_kind: string | null;
  source_label: string | null;
  linked_requests: LinkedRequest[];
  reply: Reply | null;
  postable: boolean;
}

// ─── Replies ─────────────────────────────────────────────────────────────

export interface ReplyChecks {
  ok: boolean;
  words: number;
  em_dash: boolean;
  liability: string[];
  compensation: string[];
  language_ok: boolean;
  has_contact: boolean;
  model_draft_rejected?: boolean;
}

export interface Reply {
  id: number;
  hotel_slug: string;
  review_id: number;
  draft_text: string;
  language: string | null;
  status: ReplyStatus;
  needs_approval: boolean;
  edited: boolean;
  checks: ReplyChecks | null;
  action_id: number | null;
  approved_by: number | null;
  approved_at: string | null;
  posted_at: string | null;
  posted_via: "gbp" | "copy" | "dial" | null;
  platform_reply_id: string | null;
  model: "llm" | "template" | string | null;
  created_at: string;
  updated_at: string;
}

// ─── Aggregates ──────────────────────────────────────────────────────────

export interface Summary {
  days: number;
  count: number;
  avg_rating: number | null;
  response_rate: number | null;
  negative_share: number | null;
  unanswered: number;
  drafts_waiting: number;
  by_platform: Record<string, { count: number; avg_rating: number | null }>;
}

export interface ThemeRow {
  key: string;
  label: string;
  department: string | null;
  negative: number;
  positive: number;
  neutral: number;
  reviews: number;
}

export interface Trends {
  weeks: string[];
  themes: Record<string, { label: string; negative: number[]; positive: number[] }>;
  rating: { reviews: number; avg_rating: number | null }[];
}

// ─── Sources and competitors ─────────────────────────────────────────────

export type SourceKind = "serpapi_google" | "gbp" | "tripadvisor" | "csv" | "email";

export interface Source {
  id: number;
  hotel_slug: string;
  kind: SourceKind;
  platform: Platform;
  label: string | null;
  config: Record<string, unknown>;
  status: "active" | "paused" | "error" | "disconnected";
  auto_reply_consent: boolean;
  auto_reply_consent_by: number | null;
  auto_reply_consent_at: string | null;
  last_synced_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
  google?: { status: string; account_ref: string | null };
}

export interface SourcesPayload {
  sources: Source[];
  inbound_address: string | null;
  serpapi: { enabled: boolean; disclosure: string };
  auto_reply_consent: boolean;
}

export interface CompetitorPlace {
  place_id: string;
  name: string | null;
  rating: number | null;
  user_rating_count: number | null;
  maps_url: string | null;
  own: boolean;
  error?: string;
}

export interface Competitors {
  place_ids: string[];
  own_place_id: string | null;
  updated_at: string | null;
  places: CompetitorPlace[];
  attribution: string;
  stored: false;
}

export interface ImportReport {
  preset: string;
  platform: string;
  rows: number;
  created: number;
  updated: number;
  skipped: number;
  errors: { row: number; error: string }[];
  columns: Record<string, string>;
  dry_run: boolean;
  source_id: number | null;
}
