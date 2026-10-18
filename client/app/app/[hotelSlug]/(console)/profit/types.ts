/* ═══════════════════════════════════════════════════════════════════════
   Profit Guard wire types. They mirror the server views in
   server/clefs/modules/profit_guard (ledger.finding_out, ledger.summary,
   disputes.packet_out, economics.read_months, tax_checks.ledger_out,
   router_data card and rate-evidence rows). Money always arrives twice:
   `<x>_minor` (integer minor units, the one we compute with) and `<x>`
   (a Decimal string, only for display fallbacks). Percentages and
   confidences arrive as Decimal strings.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Evidence } from "../../../../lib/types";

// ─── Vocabulary ──────────────────────────────────────────────────────────

export type FindingStatus =
  | "DETECTED" | "ACCEPTED" | "DISPUTED" | "RECOVERED" | "REJECTED" | "UNRESOLVED" | "DISMISSED";

export type FindingClass = "RECOVERABLE" | "PREVENTABLE" | "STRUCTURAL" | "COMPLIANCE";

export type DismissReason =
  | "FALSE_POSITIVE_DATA" | "CONTRACT_EXCEPTION" | "ALREADY_HANDLED" | "TOO_SMALL" | "NOT_WORTH_DISPUTING" | "OTHER";

export type PacketStatus =
  | "DRAFT" | "APPROVED" | "SUBMITTED" | "ACKNOWLEDGED" | "CREDITED" | "PARTIAL" | "REJECTED";

// ─── Findings and the ledger ─────────────────────────────────────────────

/** One CalcStep (models.CalcStep): the formula trace the stepper renders. */
export interface CalcStep {
  label: string;
  formula: string;
  inputs?: Record<string, unknown>;
  result_minor?: number | null;
  result?: string | null;
}

export interface Finding {
  id: number;
  detector: string;
  fingerprint?: string;
  ota_code: string | null;
  reservation_id: number | null;
  statement_line_id: number | null;
  subject_ref: string | null;
  class: FindingClass;
  amount_minor: number;
  amount?: string | null;
  recovered_minor: number;
  recovered?: string | null;
  currency: string;
  confidence: string | number;
  calc: CalcStep[];
  evidence: Evidence[];
  deadline_at: string | null;
  status: FindingStatus;
  dismiss_reason: DismissReason | null;
  dismiss_note?: string | null;
  signal_id?: number | null;
  action_id?: number | null;
  dispute_packet_id: number | null;
  verified_by?: string | null;
  created_at: string;
  updated_at?: string;
  group: string | null;
  booking_ref: string | null;
  hours_left: number | null;
}

export interface FindingEvent {
  id: number;
  at: string;
  from_status: FindingStatus | null;
  to_status: FindingStatus;
  actor: string | null;
  channel: string | null;
  note: string | null;
  amount_minor: number | null;
  evidence?: unknown;
}

export interface FindingDetail extends Finding {
  events: FindingEvent[];
  dispute_packet: {
    id: number; status: PacketStatus; total_minor: number; currency: string; submitted_at: string | null;
  } | null;
}

export interface MoneyBucket {
  count: number;
  amount_minor: number;
}

export interface LedgerEvent extends FindingEvent {
  finding_id: number;
  detector: string;
  ota_code: string | null;
  currency: string;
}

/** GET /ledger: the Overview figures in the hotel currency. */
export interface LedgerSummary {
  currency: string;
  found_minor: number;
  recovered_minor: number;
  open_minor: number;
  due_72h_count: number;
  due_72h_minor: number;
  compliance_open: number;
  funnel: Partial<Record<FindingStatus, MoneyBucket>>;
  by_detector: ({ detector: string } & MoneyBucket)[];
  other_currency_findings: number;
  events: LedgerEvent[];
}

/** GET /deadlines: open findings with a clock plus unremitted TD months. */
export type Deadline =
  | ({ kind: "finding" } & Finding)
  | {
      kind: "td_remittance";
      month: string;
      deadline_at: string;
      amount_minor: number;
      currency: string;
      hours_left: number;
    };

export interface PilotScorecard {
  currency: string;
  valid_findings: number;
  by_status: Partial<Record<FindingStatus, number>>;
  valid_recoverable_minor: number;
  recovered_minor: number;
  min_valid: number;
  amount_threshold_minor: number | null;
  criteria: { count: boolean; amount: boolean };
  success: boolean;
  pilot_started_on: string | null;
  pilot_months: number;
  subscription_minor_month: number | null;
}

// ─── Dispute packets ─────────────────────────────────────────────────────

export interface PacketItem {
  id: number;
  detector: string;
  label: string;
  status: FindingStatus;
  booking_ref: string | null;
  pms_res_id: string | null;
  guest_name: string | null;
  check_in: string | null;
  check_out: string | null;
  stay_date?: string | null;
  doc_no: string | null;
  doc_date?: string | null;
  amount_minor: number;
  amount: string;
  finding_amount_minor: number;
  recovered_minor: number;
  currency: string;
  confidence: string;
  deadline_at: string | null;
  calc: CalcStep[];
  evidence: Evidence[];
  clause_ref: string | null;
  clause_text: string | null;
}

export interface Packet {
  id: number;
  ota_code: string;
  finding_ids: number[];
  total_minor: number;
  total?: string;
  currency: string;
  status: PacketStatus;
  extranet_text: string | null;
  email_subject: string | null;
  email_body: string | null;
  html_file_id: string | null;
  pdf_file_id: string | null;
  approved_by_name: string | null;
  approved_at: string | null;
  submitted_at: string | null;
  follow_up_at: string | null;
  outcome_note: string | null;
  can_approve: boolean;
  pdf_path: string;
  print_path: string;
  created_at: string;
  recovered_minor?: number;
  items?: PacketItem[];
}

// ─── Channel economics (stored monthly rollup) ──────────────────────────

export interface WaterfallStepWire {
  label: string;
  kind: "total" | "delta";
  value_minor: number;
}

export interface EconomicsRow {
  month: string;
  channel: string;
  currency: string;
  room_nights: number;
  gross_minor: number;
  room_rev_minor: number;
  sc_minor: number;
  pass_through_tax_minor: number;
  commission_minor: number;
  commission_on_tax_minor: number;
  commission_on_tax_kind: "structural" | "fee_config" | "other" | null;
  payment_cost_minor: number;
  fx_loss_minor: number;
  cancellation_cost_minor: number;
  program_cost_minor: number;
  net_minor: number;
  net_adr_minor: number | null;
  gross_adr_minor: number | null;
  retention: string | null;
  effective_commission_pct: string | null;
  avg_discount_depth: string | null;
  commission_basis?: string | null;
  waterfall: WaterfallStepWire[];
}

export interface EconomicsMonthly {
  currency: string;
  from: string;
  to: string;
  rows: EconomicsRow[];
  totals: Record<string, number | string | null>;
}

// ─── Virtual cards ───────────────────────────────────────────────────────

export type CardStatus =
  | "AVAILABLE" | "FUNDED" | "NOT_LOADED" | "PARTIALLY_CHARGED" | "FULLY_CHARGED" | "CANCELLED" | "EXPIRED";

export interface VirtualCard {
  id: number;
  ota_code: string;
  vcc_id: string;
  ota_res_id: string | null;
  reservation_id: number | null;
  activation_date: string | null;
  expiration_date: string;
  charge_deadline: string;
  deadline_basis: string | null;
  initial_minor: number | null;
  current_balance_minor: number;
  currency: string;
  status: CardStatus;
  charged_at: string | null;
  days_left: number;
}

// ─── Tourism Dirham ledger ───────────────────────────────────────────────

export interface TdMonth {
  month: string;
  currency: string;
  liable_room_nights: number | null;
  td_expected_minor: number | null;
  td_collected_minor: number;
  td_remitted_minor: number;
  gap_minor: number;
  due_date: string | null;
  status: "OPEN" | "REMITTED" | "MISMATCH" | string;
  remitted_on: string | null;
  remit_reference: string | null;
}

export interface TdLedger {
  currency: string;
  months: TdMonth[];
  notices: string[];
}

// ─── Contracts and parity evidence ───────────────────────────────────────

export interface Clause {
  ref: string;
  text: string;
  page?: number | null;
  field?: string | null;
}

export interface Contract {
  id: number | null;
  ota_code: string;
  entity_name: string | null;
  entity_country: string | null;
  effective_from: string | null;
  effective_to: string | null;
  base_commission_pct: string;
  commission_base_rule: "ROOM_INCL_VAT" | "RSR_TAX_INCL" | "NET_RATE" | "ROOM_EXCL_TAX";
  excluded_charge_types: string[];
  collect_models: string[];
  dispute_rules: Record<string, unknown>[];
  vcc_rules: Record<string, unknown>;
  clauses: Clause[];
  source_file_id: string | null;
  extracted_by?: "LLM" | "MANUAL" | null;
  review_flags?: string[];
  status: "DRAFT" | "CONFIRMED" | "RETIRED" | "DEFAULT";
  note?: string | null;
  confirmed_at?: string | null;
  is_default: boolean;
}

export interface ContractsResponse {
  contracts: Contract[];
  defaults: Contract[];
}

export interface RateEvidence {
  id: number;
  captured_at: string;
  channel: string;
  stay_date: string;
  los: number;
  adults: number;
  room_label: string | null;
  plan_label: string | null;
  price_minor: number;
  currency: string;
  taxes_included: boolean | null;
  device: "DESKTOP" | "MOBILE";
  login_state: "LOGGED_OUT" | "LOGGED_IN" | "MEMBER";
  booker_country: string | null;
  search_url: string | null;
  screenshot_file_id: string | null;
  source: "MANUAL" | "SERPAPI" | "FEED";
  source_module?: string | null;
  comparable_key: string;
}
