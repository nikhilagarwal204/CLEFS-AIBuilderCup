/* ═══════════════════════════════════════════════════════════════════════
   Finance console API shapes (server/clefs/modules/finance/README.md).
   Money travels as *_minor ints next to Decimal strings in major units,
   ratios as Decimal strings ("0.3283") next to percent strings ("32.83");
   null means n/a (a zero or missing denominator), never zero.
   ═══════════════════════════════════════════════════════════════════════ */

// Decimal string ("152.50", "-0.4780") or null for n/a.
export type Dec = string | null;
export type Minor = number | null;

// ─── Weekly pulse ────────────────────────────────────────────────────────

export interface ChannelCost {
  channel_code: string;
  is_ota: boolean;
  cost_minor: Minor;
  cost: Dec;
  room_nights: number | null;
  cost_per_rn_minor: Minor;
  cost_per_rn: Dec;
}

export interface Pulse {
  week_start: string;
  week_end: string;
  currency: string;
  available_rn: number | null;
  occupied_rn: number | null;
  rooms_revenue_minor: Minor; rooms_revenue: Dec;
  distribution_cost_minor: Minor; distribution_cost: Dec;
  ota_cost_minor: Minor; ota_cost: Dec;
  direct_cost_minor: Minor; direct_cost: Dec;
  nrevpar_minor: Minor; nrevpar: Dec;
  revpar_minor: Minor; revpar: Dec;
  ota_cost_por_minor: Minor; ota_cost_por: Dec;
  cancellation_leakage_minor: Minor; cancellation_leakage: Dec;
  recovery_cost_minor: Minor; recovery_cost: Dec;
  channels: ChannelCost[];
  sc_as_revenue: boolean | null;
}

export interface WeeklyOut {
  currency: string;
  from: string;
  to: string;
  weeks: Pulse[];
  latest: Pulse | null;
}

// ─── Monthly one-pager ───────────────────────────────────────────────────

export interface MarketIndex {
  available: boolean;
  reason?: string;
  chip?: "month" | "YTD";
  period_kind?: "month" | "ytd";
  period?: string;
  region?: string;
  hotel_yoy?: Dec;
  market_yoy?: Dec;
  relative?: Dec;
  source_url?: string;
  publisher?: string | null;
  caveat?: string;
}

export interface Anomaly {
  id: number;
  month: string;
  kind: string;
  usali_line: string | null;
  value: Dec;
  expected: Dec;
  delta_minor: Minor;
  delta: Dec;
  currency: string;
  zscore: Dec;
  confidence: Dec;
  evidence: Record<string, unknown>;
  status: string;
  signal_id: number | null;
  action_id: number | null;
}

export interface UsaliRow {
  usali_line: string;
  label: string;
  schedule: number | null;
  dept: string;
  category: string;
  amount_minor: number;
  amount: Dec;
}

export type CheckStatus = "pass" | "warn" | "fail" | "n/a";

export interface Check {
  key: string;
  label: string;
  status: CheckStatus;
  blocking: boolean;
  detail: string;
  data?: Record<string, unknown>;
}

export interface Monthly {
  month: string;
  currency: string;
  batch_id: number | null;
  normalized: boolean;
  booked_net: boolean;
  available_rn: number | null;
  flow_through_basis: "yoy" | "mom" | null;
  published_at: string | null;
  total_revenue_minor: Minor; total_revenue: Dec;
  rooms_revenue_minor: Minor; rooms_revenue: Dec;
  dept_profit_minor: Minor; dept_profit: Dec;
  undistributed_minor: Minor; undistributed: Dec;
  gop_minor: Minor; gop: Dec;
  goppar_minor: Minor; goppar: Dec;
  trevpar_minor: Minor; trevpar: Dec;
  revpar_minor: Minor; revpar: Dec;
  payroll_minor: Minor; payroll: Dec;
  ebitda_minor: Minor; ebitda: Dec;
  net_profit_minor: Minor; net_profit: Dec;
  verified_profit_minor: Minor; verified_profit: Dec;
  verified_profit_par_minor: Minor; verified_profit_par: Dec;
  gop_margin: Dec; gop_margin_pct: Dec;
  labour_pct_ratio: Dec; labour_pct: Dec;
  flow_through: Dec; flow_through_pct: Dec;
  eww_pct_ratio: Dec; eww_pct: Dec;
  // Detail (monthly/{month} only; the trend omits these).
  market_index?: MarketIndex;
  anomalies?: Anomaly[];
  usali?: UsaliRow[];
  checks?: Check[];
  source?: string | null;
  statement_kind?: string | null;
}

export interface VerifiedProfit {
  start: string;
  end: string;
  currency: string;
  verified_minor: number;
  verified_value: string;
  actions: number;
  by_module: Record<string, number>;
  available_rn: number | null;
  verified_par_minor: Minor;
  verified_par: Dec;
}

// ─── Imports ─────────────────────────────────────────────────────────────

export type BatchStatus = "mapping_required" | "checks_failed" | "review" | "published" | "superseded";
export type StatementKind = "pl" | "trial_balance";

export interface Batch {
  id: number;
  period_month: string;
  statement_kind: StatementKind;
  source: string;
  core_import_batch_id: number | null;
  file_id: string | null;
  currency: string;
  basis: string | null;
  status: BatchStatus;
  superseded_by: number | null;
  published_at: string | null;
  published_by: number | null;
  created_at: string;
  created_by: number | null;
  source_net_profit_minor: Minor; source_net_profit: Dec;
  mapped_net_profit_minor: Minor; mapped_net_profit: Dec;
  /** excluded.* (pass-through, balance sheet) added back to reconcile a P&L; absent before checks run. */
  reconciling_minor?: Minor; reconciling?: Dec;
  checks: Check[];
  blocking: boolean;
  booked_net: boolean;
  unmapped: number;
}

export interface BatchLine {
  account_id: number;
  code: string | null;
  name_raw: string;
  parent_group_raw: string | null;
  type_raw: string | null;
  usali_line: string | null;
  locked: boolean | null;
  ai_confidence: Dec | number;
  learned_from: string | null;
  override_reason: string | null;
  amount_minor: number;
  amount: Dec;
}

export interface BatchDetail extends Batch {
  lines: BatchLine[];
  summary: Record<string, unknown>;
  summary_reported: Record<string, unknown>;
  usali: UsaliRow[];
  stat_inputs: StatInputs;
}

export interface CoreBatch {
  id: number;
  kind: string;
  status: string;
  filename: string | null;
  file_format?: string | null;
  period_month: string | null;
  row_count?: number | null;
  errors?: unknown;
  has_suggestion?: boolean;
  gate_status?: string | null;
  commit_result?: unknown;
  created_at: string;
  commit_error?: { code?: string; detail?: unknown } | null;
}

export interface ImportOut {
  core_batch: CoreBatch;
  finance_batches: Batch[];
}

export interface ImportsList {
  imports: Batch[];
  pending_uploads: CoreBatch[];
}

// ─── Mapping ─────────────────────────────────────────────────────────────

export type LearnedFrom = "rule" | "hotel_locked" | "global_memory" | "llm" | "manual";

export interface MappingAccount {
  account_id: number;
  code: string | null;
  name_raw: string;
  language: "ar" | "en" | "mixed" | null;
  parent_group_raw: string | null;
  type_raw: string | null;
  source_system: string | null;
  usali_line: string | null;
  usali_dept: string | null;
  category: string | null;
  version: number | null;
  locked: boolean | null;
  ai_confidence: Dec | number;
  ai_rationale: string | null;
  learned_from: LearnedFrom | null;
  data_origin: string | null;
  alternatives: { usali_line: string; confidence: number }[] | null;
  override_reason: string | null;
  confirmed_at: string | null;
  is_pass_through_tax: boolean | null;
  below_gop_word: string | null | boolean;
  bulk_ok: boolean;
}

export interface CatalogEntry {
  key: string;
  schedule: number | null;
  dept: string;
  category: string;
  is_payroll: boolean;
  label: string;
}

export interface MappingOut {
  accounts: MappingAccount[];
  catalog: CatalogEntry[];
  schedules: Record<string, string>;
  bulk_min_confidence: number;
}

export interface ConfirmOut {
  confirmed: number[];
  open_batches: { id: number; period_month: string; status: BatchStatus }[];
}

// ─── Connections ─────────────────────────────────────────────────────────

export interface Connection {
  provider: "xero" | "qbo" | "zoho" | "tally";
  label: string;
  configured?: boolean | null;
  status?: string | null;
  account_ref?: string | null;
  last_sync_at?: string | null;
  sync_status?: string | null;
  last_error?: string | null;
  coming_next?: boolean;
  start_path?: string;
  upload_only?: boolean;
}

export interface SyncOut {
  provider: string;
  month: string;
  lines: number;
  batch: unknown;
  status: string;
  reused: boolean;
}

// ─── Settings, stat inputs, baseline ─────────────────────────────────────

export interface FinanceSettings {
  labour_band_min_pct: number;
  labour_band_max_pct: number;
  eww_band_min_pct: number;
  eww_band_max_pct: number;
  zscore_min: number;
  delta_min_pp: number;
  trailing_months: number;
  market_region: string | null;
}

export interface SettingsOut {
  settings: FinanceSettings;
  service_charge: {
    treat_sc_as_revenue: boolean;
    source: string;
    /** True when the viewer lacks profit.manage (the card shows the value only). */
    read_only: boolean;
    can_edit: boolean;
    /** Profit Guard's policies route under /hotels/{slug}/, the one place the value is written. */
    edit_api: string;
    /** A Profit Guard settings page under /app/{slug}/, or null while none exists. */
    settings_path: string | null;
  };
}

export interface StatInputs {
  available_rn: number | null;
  rooms_sold: number | null;
  rooms_rev_pms_minor: Minor;
  ota_share: Dec | number;
  ota_commission_expected_minor: Minor;
  currency?: string;
  source?: "stored" | "live";
  overridden?: string[];
}

export interface Baseline {
  hotel_slug: string;
  registered_at: string;
  period_start: string;
  period_end: string;
  map_version_snapshot: unknown;
  metrics: unknown;
  currency: string;
  registered_by: number | null;
}
