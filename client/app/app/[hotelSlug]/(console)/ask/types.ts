/* ═══════════════════════════════════════════════════════════════════════
   Ask Clefs wire types, mirroring server/clefs/modules/ask/README.md
   ("HTTP API"). Table cells arrive as raw numbers in MAJOR units (money in
   the hotel's currency, percent as 0-100); the console formats them, the
   server never pre-formats, so a saved view re-run reads the same.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Enums ───────────────────────────────────────────────────────────────

export type AskStatus = "answered" | "table_only" | "unsupported" | "refused" | "error";

/** clefs.core.metrics Unit. Ask's derived ratios reuse the same set. */
export type Unit = "count" | "money" | "percent" | "minutes" | "ratio" | "nights" | "rate";

export type ColumnKind = "time" | "dimension" | "value" | "compare" | "change";

export type Grain = "total" | "day" | "week" | "month";

export type ChartType = "kpi" | "column" | "hbar" | "line";

export type AskReason =
  | "guest_pii" | "forecast_others" | "too_long" | "empty" | "model_unsupported" | "no_metrics"
  | "ungrounded" | "ai_disabled" | "ai_error" | "no_tool_call" | "tool_call_budget";

export type Channel = "WEB" | "WHATSAPP" | "VIEW";

// ─── Answer ──────────────────────────────────────────────────────────────

export interface TableColumn {
  key: string;
  label: string;
  kind: ColumnKind;
  unit?: Unit | string | null;
  currency?: string | null;
}

export type Cell = string | number | null;

export interface AnswerTable {
  columns: TableColumn[];
  rows: Cell[][];
  total: number | null;
  compare_total: number | null;
  row_count: number;
  limited: boolean;
  truncated: boolean;
}

/** One chart datum. kpi: {value, compare_value, change_pct}; column/hbar and
    single-line: {x, value, compare_value}; grouped line: {x, s0, s1, ...}. */
export type ChartDatum = { x?: string | null } & Record<string, string | number | null | undefined>;

export interface ChartSpec {
  type: ChartType;
  /** "bucket" for a time axis, a dimension key for a breakdown, null for kpi. */
  x: string | null;
  series: { key: string; label: string | null }[];
  unit: Unit | string | null;
  currency: string | null;
  label: string | null;
  data: ChartDatum[];
}

export interface TracePeriod {
  preset: string | null;
  from: string;
  to: string;
  label: string;
}

export interface Trace {
  metric: string;
  metric_label: string;
  description: string | null;
  dims: string[];
  filters: { field: string; values: string[] }[];
  period: TracePeriod;
  grain: Grain;
  compare: "prev_period" | "prev_year" | null;
  sort: string;
  limit: number;
  unit: Unit | string | null;
  currency: string | null;
  row_count: number | null;
  truncated: boolean;
  derived: { numerator: string; denominator: string; scale: number } | null;
  source: string;
}

/** The validated query_metric arguments; what a saved view stores. */
export type QueryArgs = Record<string, unknown>;

export interface ResultBlock {
  table: AnswerTable | null;
  chart_spec: ChartSpec | null;
  trace: Trace | null;
  query: QueryArgs | null;
}

export interface Answer extends ResultBlock {
  log_id: number | null;
  question: string;
  status: AskStatus;
  answer_text: string | null;
  results: ResultBlock[];
  unsupported: boolean;
  reason: AskReason | string | null;
  suggestions: string[];
  grounded: boolean | null;
  regenerated: boolean;
  ungrounded?: string[];
  tool_calls?: number;
  latency_ms?: number;
  tokens?: number | null;
  /** Only on POST /views/{id}/run. */
  view?: SavedView;
}

// ─── History, views, catalog ─────────────────────────────────────────────

export interface LogRow {
  id: number;
  channel: Channel;
  question: string;
  status: AskStatus;
  unsupported: boolean;
  reason: string | null;
  grounded: boolean | null;
  regenerated: boolean;
  answer: Partial<Answer> | null;
  tool_calls: { name: string; arguments: unknown; ok: boolean; error?: string }[];
  saved_view_id: number | null;
  latency_ms: number | null;
  tokens: number | null;
  feedback: -1 | 1 | null;
  feedback_note: string | null;
  created_at: string;
}

export interface SavedView {
  id: number;
  title: string;
  query: QueryArgs;
  question: string | null;
  source_log_id: number | null;
  pinned: boolean;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CatalogMetric {
  key: string;
  label: string;
  description: string | null;
  unit: Unit | string;
  currency: string | null;
  dimensions: { key: string; label: string }[];
  higher_is_better: boolean | null;
  synonyms: string[];
  derived: boolean;
}

export interface WhatsAppOptIn {
  eligible: boolean;
  role: string | null;
  has_phone: boolean;
  opted_in: boolean;
  updated_at: string | null;
  last_asked_at: string | null;
}
