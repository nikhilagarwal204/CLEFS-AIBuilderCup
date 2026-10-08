/* ═══════════════════════════════════════════════════════════════════════
   Trust console types. Mirrors the shapes documented in
   server/clefs/modules/trust/README.md (HTTP API). Money arrives as BIGINT
   minor units (`*_minor`) next to a Decimal string; rates are Decimal
   strings 0-1, so the client never does float money maths.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Audit viewer ────────────────────────────────────────────────────────

export interface AuditRow {
  id: number;
  hotel_slug: string | null;
  at: string;
  actor_type: string;
  actor_id: string | null;
  actor_label: string | null;
  action: string;
  entity_type: string | null;
  entity_id: string | null;
  ip: string | null;
  request_id: string | null;
  meta: Record<string, unknown> | null;
  has_diff: boolean;
}

export interface AuditPage {
  items: AuditRow[];
  next_before_id: number | null;
}

export interface AuditFacets {
  actions: string[];
  entity_types: string[];
  actor_types: string[];
}

export interface DiffEntry {
  path: string;
  op: "added" | "removed" | "changed";
  before: unknown;
  after: unknown;
}

export interface AuditDetail extends AuditRow {
  before: unknown;
  after: unknown;
  prev_hash: string | null;
  row_hash: string | null;
  hash_ok: boolean | null;
  diff: DiffEntry[];
}

export interface ChainState {
  hotel_slug: string;
  status: "unverified" | "ok" | "broken";
  last_id: number | null;
  last_hash: string | null;
  anchor_id: number | null;
  anchor_hash: string | null;
  broken_at_id: number | null;
  broken_reason: string | null;
  rows_checked: number | null;
  verified_at: string | null;
  last_full_at: string | null;
}

export interface ChainVerifyResult {
  status: "ok" | "broken";
  rows_checked: number;
  last_id: number | null;
  broken_at_id: number | null;
  reason: string | null;
}

// ─── Retention, PII matrix, consent wording ──────────────────────────────

export interface RetentionPolicy {
  data_class: string;
  label: string;
  keep_days: number;
  action: "delete" | "anonymize";
  default_keep_days: number;
  min_keep_days: number;
  max_keep_days: number;
  source: "builtin" | "registry";
  tables: string[];
  overridden: boolean;
  note: string | null;
  last_run: { at: string; rows: number } | null;
}

export interface RetentionRunResult {
  run_id: string;
  counts: Record<string, number>;
  failures: { data_class: string; error: string }[];
}

export interface DeletionLogRow {
  id: number;
  run_id: string;
  data_class: string;
  table_name: string;
  action: string;
  row_count: number;
  criteria: { keep_days?: number; older_than?: string; action?: string } | null;
  at: string;
}

export interface PiiMatrix {
  roles: string[];
  classes: Record<string, Record<string, string>>;
}

export interface ConsentText {
  id: number;
  hotel_slug: string | null;
  purpose: string;
  version: string;
  language: string;
  body: string;
  created_at: string;
  retired_at: string | null;
  current: boolean;
  scope: "hotel" | "platform";
}

// ─── Privacy requests (DSR) and tenant export ────────────────────────────

export type DsrType = "access" | "rectify" | "erase" | "restrict" | "object" | "portability";
export type DsrStatus = "received" | "verified" | "in_progress" | "completed" | "rejected" | "cancelled";
export type DsrChannel = "console" | "email" | "whatsapp" | "phone" | "in_person" | "web";
export type VerifyMethod = "id_document" | "matched_reservation" | "email_reply" | "phone_callback" | "in_person";

export interface DsrIdentifiers {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  room?: string | null;
  reservation_ref?: string | null;
  stay_date?: string | null;
}

/** Masked display values stored beside the sealed identifiers at intake
 *  (trust/dsr.py _hint, JSONB column). An OBJECT, never a string: rendering
 *  it directly as a React child crashes the page (React #31), so read it
 *  only through dsrPerson() in pure.ts. */
export interface DsrIdentifiersHint {
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  room?: string | null;
  has_reservation_ref?: boolean;
}

export interface Dsr {
  id: number;
  hotel_slug: string;
  type: DsrType;
  status: DsrStatus;
  channel: string;
  identifiers: DsrIdentifiers;
  identifiers_hint: DsrIdentifiersHint | null;
  details: string | null;
  language: string | null;
  verification: { method?: string; note?: string | null; verified_by?: string | null; verified_at?: string } | null;
  received_at: string;
  due_at: string;
  days_left: number | null;
  overdue: boolean;
  /** Filed while the public form was over its daily threshold: likely junk,
   *  but still a legal request to verify or reject (never refused at intake). */
  suspected_flood?: boolean;
  handled_by: string | null;
  completed_at: string | null;
  result_upload_id: string | null;
  result: Record<string, unknown> | null;
  note: string | null;
  updated_at: string;
}

export interface SignedLink {
  url: string;
  expires_in: number;
}

export interface TenantExport {
  id: number;
  status: "queued" | "ready" | "failed" | "expired" | "running";
  tables: Record<string, number> | null;
  error: string | null;
  created_at: string;
  finished_at: string | null;
  expires_at: string | null;
  file_id: string | null;
  download_url: string | null;
}

// ─── Evals and readiness ─────────────────────────────────────────────────

export type Suite = "standard" | "auto_kb" | "custom";

export interface ReadinessCheck {
  key: string;
  label: string;
  ok: boolean;
  detail: string | null;
}

export interface ReadinessOverride {
  id: number;
  reason: string;
  failing: string[];
  run_id: number | null;
  created_by: string | null;
  created_at: string;
  expires_at: string;
}

export interface Readiness {
  ready: boolean;
  overridden: boolean;
  checks: ReadinessCheck[];
  run_id: number | null;
  prompt_version: string | null;
  kb_fingerprint: string | null;
  evaluated_at: string;
  override: ReadinessOverride | null;
}

export interface EvalScenario {
  id: number;
  hotel_slug: string | null;
  suite: Suite;
  key: string;
  category: string;
  title: string | null;
  surface: string;
  language: string;
  turns: { role: string; text: string }[];
  context: Record<string, unknown> | null;
  expectations: Record<string, unknown>;
  kb_entry_id: number | null;
  tags: string[];
  flaky: boolean;
  active: boolean;
  source_hash: string | null;
  created_at: string;
  updated_at: string;
  read_only: boolean;
}

export interface CategoryRate {
  total: number;
  passed: number;
  rate: string | null;
}

export interface EvalRun {
  id: number;
  hotel_slug: string;
  trigger: "manual" | "nightly" | "kb_change" | "prompt_change" | "ci";
  status: "queued" | "running" | "completed" | "failed" | "skipped";
  suites: Suite[];
  prompt_version: string | null;
  kb_fingerprint: string | null;
  model: string | null;
  judge_model: string | null;
  runner: string | null;
  started_at: string | null;
  finished_at: string | null;
  total: number | null;
  passed: number | null;
  pass_rate: string | null;
  category_rates: Record<string, CategoryRate> | null;
  cost_micros: number | null;
  cost_usd: string | null;
  requested_by: string | null;
  error: string | null;
  note: string | null;
  created_at: string;
  previous_run_id?: number | null;
  previous_pass_rate?: string | null;
}

export interface GraderOutput {
  grader: string;
  passed: boolean;
  detail?: string | null;
  skipped?: boolean;
  score?: number | string | null;
  criteria?: unknown;
}

export interface EvalResult {
  id: number;
  run_id: number;
  scenario_id: number;
  scenario_key: string;
  suite: Suite;
  category: string;
  passed: boolean;
  score: string | number | null;
  attempts: number;
  transcript: {
    messages?: { role: string; content: string }[];
    tool_calls?: unknown[];
    runner?: string;
    implemented?: boolean;
    handoff?: unknown;
    error?: string | null;
  } | null;
  tool_calls: { name: string; arguments: unknown; result: unknown }[];
  grader_outputs: GraderOutput[];
  failure_reasons: string[];
  title: string | null;
  language: string | null;
  turns: unknown;
}

export interface EvalBudget {
  cap_usd: string;
  spent_today_usd: string;
  remaining_usd: string;
}

export interface AutoKbSync {
  entries: number;
  created: number;
  skipped: number;
  retired: number;
  errors: string[];
}

// ─── Pilots, ROI report, benchmarks ──────────────────────────────────────

export type Comparator = "gte" | "lte" | "gt" | "lt" | "improve_pct" | "reduce_pct";
export type PilotStatus = "draft" | "active" | "completed" | "converted" | "cancelled";

export interface Criterion {
  metric_key: string;
  comparator: Comparator;
  target: string | number;
  weight?: string | number;
  label?: string | null;
}

export interface CriterionEval {
  metric_key: string;
  label: string | null;
  comparator: Comparator;
  target: string | number;
  weight: string | number;
  value: string | number | null;
  baseline: string | number | null;
  change_pct: string | number | null;
  met: boolean | null;
  status: "met" | "not_met" | "no_data";
}

export interface Evaluation {
  period_start: string;
  period_end: string;
  criteria: CriterionEval[];
  score: string | null;
  all_met: boolean;
}

export interface Pilot {
  id: number;
  hotel_slug: string;
  name: string;
  modules: string[];
  start_date: string;
  end_date: string;
  status: PilotStatus;
  success_criteria: Criterion[];
  baseline_start: string | null;
  baseline_end: string | null;
  baseline_metrics: Record<string, string | number | null> | null;
  conversion_price_minor: number | null;
  conversion_price: string | null;
  currency: string;
  terms: { billing_period?: "month" | "year"; on_track_threshold?: number | string; [k: string]: unknown } | null;
  outcome: Partial<Evaluation> | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface PilotCheckpoint {
  date: string;
  values: Record<string, unknown> | null;
  score: string | null;
  on_track: boolean | null;
}

export interface PilotDetail extends Pilot {
  scorecard: Evaluation | null;
  checkpoints: PilotCheckpoint[];
}

/** A body for POST /hotels/{slug}/metrics/query: every report figure carries one. */
export interface MetricQueryBody {
  metric: string;
  start: string;
  end: string;
  grain?: "total" | "day" | "week" | "month";
  group_by?: string[];
  filters?: Record<string, string[]>;
  compare?: "prev_period" | "prev_year" | null;
}

export interface MoneyPair {
  minor: number | null;
  value: string | null;
}

export interface PilotReport {
  pilot: Pilot;
  hotel: { slug: string; name: string; city: string | null; rooms_count: number; currency: string };
  scope: {
    modules: string[];
    start_date: string;
    end_date: string;
    reported_through: string;
    days: number;
    baseline_start: string | null;
    baseline_end: string | null;
  };
  criteria: CriterionEval[];
  score: string | null;
  all_met: boolean;
  final: boolean;
  verified: {
    label: string;
    minor: number;
    value: string | null;
    actions: number;
    by_module: Record<string, MoneyPair>;
    available_room_nights: number;
    per_available_room_night_minor: number | null;
    per_available_room_night: string | null;
    per_room_minor: number | null;
    per_room: string | null;
    query: MetricQueryBody;
  };
  revenue_through_clefs: { metric_key: string; label: string; value: string | null; query: MetricQueryBody }[];
  recovery: {
    pilot_cost_minor: number | null;
    pilot_cost: string | null;
    baseline_cost_minor: number | null;
    avoided_minor: number | null;
    avoided: string | null;
  };
  roi: {
    price_minor: number | null;
    price: string | null;
    billing_period: "month" | "year" | null;
    annual_price_minor: number | null;
    annualised_verified_minor: number | null;
    roi_multiple: string | null;
    payback_days: number | null;
  };
  caveats: string[];
  generated_at: string;
}

export interface BenchmarkItem {
  cohort_key: string;
  cohort_label: string;
  metric_key: string;
  metric_label: string;
  unit: string;
  period: string;
  n_hotels: number;
  band: "top" | "middle" | "bottom" | null;
  p50: string | number | null;
  cutoffs: unknown;
  bands_only: boolean;
  computed_at: string;
}

export interface Benchmarks {
  opted_in: boolean;
  items: BenchmarkItem[];
}

// ─── Metrics (foundation clefs.core.metrics) ─────────────────────────────

export interface MetricInfo {
  key: string;
  label: string;
  description?: string | null;
  unit: string;
  currency?: string | null;
  higher_is_better?: boolean | null;
}

export interface MetricResult {
  metric: string;
  label: string;
  unit: string;
  currency: string | null;
  grain: string;
  start: string;
  end: string;
  group_by: string[];
  filters: Record<string, string[]>;
  rows: { bucket: string | null; group: Record<string, string>; value: number | null; compare_value: number | null }[];
  total: number | null;
  compare: string | null;
  compare_total: number | null;
  truncated: boolean;
}
