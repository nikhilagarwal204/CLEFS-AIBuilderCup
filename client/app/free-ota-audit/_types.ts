/**
 * Shapes of the public OTA audit API (profit_guard README, "Public OTA audit
 * and Leakage Index"). Types only, no runtime code: _view.ts imports them and
 * is transpiled on its own by __tests__/view.test.mjs.
 *
 *   POST /public/audits               multipart {file, consent_processing, consent_transfer_abroad, ota_code?} -> 201 AuditView + token
 *   GET  /public/audits/{token}       -> AuditView (404 not_found once expired)
 *   POST /public/audits/{token}/lead  {name, hotel_name, email?, whatsapp?, consent_contact, consent_transfer_abroad, consent_index?, ...}
 *
 * Money is `{minor, amount}` in the statement's currency; `amount` is a
 * Decimal string, `minor` an int. Every field the server may omit is optional
 * here so an older or partial report degrades instead of throwing.
 */

// ─── Primitives ──────────────────────────────────────────────────────────────

export type OtaCode = "BOOKING" | "AGODA" | "EXPEDIA";

export type Money = { minor: number; amount: string };

/** Public classes (server public_audit.py): only RECOVERABLE reaches an AED total. */
export type FindingClass =
  | "RECOVERABLE" | "TO_CONFIRM" | "CHECK_CONTRACT" | "STRUCTURAL" | "PREVENTABLE" | "COMPLIANCE" | string;

export type CalcStep = {
  label: string;
  formula: string;
  inputs?: Record<string, unknown>;
  result_minor?: number | null;
  result?: string;
};

// ─── Teaser (always shown) ───────────────────────────────────────────────────

export type Teaser = {
  ota_code: OtaCode | null;
  currency: string;
  lines: number;
  period: { from: string; to: string } | null;
  commission: Money;
  /** RECOVERABLE findings only; low == high since L1 is to confirm. */
  found: { low: Money; high: Money };
  /** Count of RECOVERABLE findings. */
  findings: number;
  by_detector: Record<string, number>;
  /** Statement lines with an L1, L1b or L2 to confirm: a count, never an amount. */
  to_confirm?: number;
  open_deadlines: number;
};

// ─── Full report (after the lead unlock) ─────────────────────────────────────

export type Finding = {
  detector: string;
  class: FindingClass;
  scenario: string | null;
  title: string;
  amount_minor: number;
  amount: string;
  currency: string;
  confidence: string;
  missing: string[];
  row: number | null;
  page: number | null;
  reservation: string | null;
  /** Initials only ("A. M."): the server never sends a full name. */
  guest: string | null;
  check_in: string | null;
  check_out: string | null;
  status: string | null;
  deadline_at: string | null;
  window_open: boolean | null;
  calc: CalcStep[];
};

export type Deadline = {
  detector: string;
  reservation: string | null;
  row: number | null;
  deadline_at: string;
  amount_minor: number;
  amount: string;
};

export type Assumptions = {
  emirate?: string;
  city?: string;
  star?: number;
  convention?: string;
  commission_pct?: Record<string, string>;
  fees?: string;
};

export type FullReport = {
  findings: Finding[];
  /** Agoda's informational sum: kept by the API, never shown as a figure. */
  structural: Money;
  deadlines: Deadline[];
  status_counts: Record<string, number>;
  skipped: { other_currency_lines: number; ota_unknown: boolean };
  assumptions: Assumptions;
  not_checked: { detector: string; reason: string }[];
  notes: string[];
};

// ─── AI snapshot (visibility.api.get_ai_snapshot, or unavailable) ────────────

export type EngineRow = {
  engine: string;
  label?: string;
  mentioned: number;
  runs: number;
  ci?: [string, string] | null;
  display?: string;
};

export type AboutAnswer = {
  prompt: string;
  engine: string;
  label?: string;
  answer: string;
  run_at?: string;
  citations?: { url: string; title?: string | null }[];
};

export type AiSnapshot = {
  status: "running" | "ready" | "failed" | "unavailable" | string;
  per_engine?: EngineRow[];
  top_cited_domains?: string[];
  answers_about_you?: AboutAnswer[];
  label?: string;
};

// ─── The audit ───────────────────────────────────────────────────────────────

export type AuditView = {
  status: string;
  created_at: string;
  expires_at: string;
  unlocked: boolean;
  teaser: Teaser | null;
  full: FullReport | null;
  ai_snapshot: AiSnapshot | null;
  lead: { name: string; hotel_name: string } | null;
  /** Only in the 201 answer to the upload. */
  token?: string;
};

export type LeadBody = {
  name: string;
  hotel_name: string;
  email?: string | null;
  whatsapp?: string | null;
  consent_contact: true;
  /** Required: the contacts are processed outside the UAE (Singapore database, US email provider). */
  consent_transfer_abroad: true;
  consent_index?: boolean;
  city?: string | null;
  area?: string | null;
  website?: string | null;
  star?: number | null;
};

/** What the page shows when a call fails (codes from the README). */
export type Failure =
  | "unavailable" | "rate_limited" | "not_found" | "too_large" | "too_many_rows" | "too_many_pages"
  | "unsupported" | "unreadable" | "consent" | "transfer" | "invalid" | "network";

// ─── UAE OTA Leakage Index (GET /public/leakage-index) ───────────────────────

/** Rates are decimal strings of a ratio ("0.0421" = 4.21%). */
export type IndexStats = {
  n: number;
  median_leak_rate: string;
  p25_leak_rate: string;
  p75_leak_rate: string;
  share_with_leak: string;
};

export type LeakageIndex = {
  title: string;
  claim_id: string;
  min_n: number;
  n: number;
  methodology: Record<string, string>;
  /** False below min_n: no figure at all, and the page renders nothing. */
  published: boolean;
  needed?: number;
  as_of?: string;
  period?: { from: string; to: string } | null;
  detector_share?: Record<string, string>;
  by_ota?: (IndexStats & { ota_code: string })[];
  suppressed_otas?: string[];
} & Partial<IndexStats>;
