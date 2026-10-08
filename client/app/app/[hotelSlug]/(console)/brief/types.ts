/* ═══════════════════════════════════════════════════════════════════════
   Owner Brief wire types: exactly what server/clefs/modules/brief/router.py
   and whatsapp.py answer (see the brief README, "HTTP API"). Money is
   *_minor + currency next to a Decimal string (*_value); render the minor
   ints through MoneyText, never float arithmetic on the strings.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Action, ActionStatus, Role } from "../../../../lib/types";

export type Cadence = "daily" | "weekly" | "monthly";
export type RunStatus = "generated" | "sent" | "partial" | "failed" | "empty";
export type ItemKind = "ranked" | "overdue";

// ─── Runs and items ──────────────────────────────────────────────────────

export interface RecommendedAction {
  type?: string;
  label?: string;
  [key: string]: unknown;
}

export interface BriefItem {
  item_no: number;
  kind: ItemKind;
  section: string;
  /** "Finance to-do" when a desk module files a to-do under service_risk; else the section's own label. */
  section_label?: string | null;
  detector: string | null;
  signal_id: number | null;
  action_id: number | null;
  action_status: ActionStatus | null;
  title: string;
  impact_minor: number | null;
  impact_value: string | null;
  /** The amount is an estimate (inquiry money at risk, Demand opportunity): shown with "est.". */
  impact_estimated?: boolean;
  currency: string | null;
  confidence: number | null;
  importance: number | null;
  urgency: number | null;
  score: number | null;
  deadline_at: string | null;
  owner_role: string | null;
  responsible_user_id: number | null;
  responsible_staff_id: number | null;
  responsible_label: string | null;
  evidence_url: string | null;
  recommended_action: RecommendedAction | null;
}

export interface VerifiedLine {
  currency: string;
  last_24h_minor: number;
  last_24h_value: string;
  last_24h_actions?: number;
  mtd_minor: number;
  mtd_value: string;
  mtd_actions?: number;
  text: string;
}

export interface DeliveryAttempt {
  user_id?: number | null;
  name?: string | null;
  channel?: string | null;
  ok?: boolean;
  status?: string | null;
  error?: string | null;
  fallback_of?: string | null;
  [key: string]: unknown;
}

export interface AutonomyRow {
  event_id: number;
  action_type: string;
  label: string;
  from_level: string | null;
  to_level: string | null;
  at: string | null;
  text: string;
  stats?: Record<string, unknown>;
  trigger?: string | null;
}

export interface AutonomyBlock {
  promotions: AutonomyRow[];
  demotions: AutonomyRow[];
}

export interface ComplianceRow {
  signal_id: number;
  kind: string;
  title: string;
  due_at: string | null;
  impact_value: string | null;
  currency: string | null;
}

export interface InquiriesBlock {
  unanswered?: number;
  unanswered_over_2h?: number;
  money_at_risk_minor?: number | null;
  money_at_risk_value?: string | null;
  currency?: string | null;
  estimated?: boolean;
  line?: string;
  items?: { id: number; ref?: string; source?: string; segment?: string; due_at?: string | null; money_at_risk_minor?: number | null }[];
}

export interface FinanceBlock {
  kind: "weekly_pulse" | "pl_pulse";
  week_start?: string;
  month?: string;
  available?: boolean;
  data: Record<string, unknown> | null;
}

export interface ReportPointer {
  period_key?: string;
  iso_week?: string;
  [key: string]: unknown;
}

export interface RunBlocks {
  inquiries?: InquiriesBlock;
  autonomy?: AutonomyBlock;
  compliance?: ComplianceRow[];
  finance?: FinanceBlock;
  report?: ReportPointer;
}

export interface Run {
  id: number | null;
  preview?: boolean;
  hotel_slug: string;
  cadence: Cadence;
  brief_date: string;
  period_start?: string;
  period_end?: string;
  scheduled_local?: string | null;
  generated_at: string;
  trigger?: "schedule" | "send_now" | "api";
  status?: RunStatus;
  headline: string;
  currency: string;
  at_stake_minor: number;
  at_stake_value: string;
  delivery?: DeliveryAttempt[];
  sent_at?: string | null;
  created_by?: number | null;
  verified: VerifiedLine | null;
  blocks: RunBlocks;
  counts?: { candidates?: number; ranked?: number; overdue?: number };
  items?: BriefItem[];
  overdue?: BriefItem[];
}

export interface SendNowResult {
  ok: boolean;
  brief_id: number | null;
  sent_to: { user_id: number; name: string; channel: string }[];
  status: "sent" | "partial" | "failed";
  reason?: "no_recipients" | "not_delivered" | "invalid_cadence" | string;
}

// ─── Settings and WhatsApp contacts ──────────────────────────────────────

export type BriefChannel = "email" | "whatsapp";

export interface BriefSettingsView {
  daily_enabled: boolean;
  weekly_enabled: boolean;
  monthly_enabled: boolean;
  /** Empty = every active owner and GM. */
  recipient_user_ids: number[];
  /** The order channels are tried in. */
  channels: BriefChannel[];
  send_when_empty: boolean;
  top_n: number;
  /** "HH:MM" in the hotel's timezone. */
  brief_time_local: string;
  /** Per-person quiet hours, hotel-local (brief quiet.py): scheduled briefs
      and non-critical escalations wait until the window ends. */
  quiet_hours: QuietWindow[];
  timezone: string;
  /** Who the brief goes to right now (resolved server-side). */
  recipients: { user_id: number; name: string; role: Role | string; has_email: boolean; has_whatsapp: boolean }[];
}

export interface QuietWindow {
  user_id: number;
  /** "HH:MM"; overnight when start is later than end (22:00 to 07:00). */
  start: string;
  end: string;
}

export type SettingsPatch = Partial<Omit<BriefSettingsView, "timezone" | "recipients">>;

export type ContactState = "implicit" | "opted_in" | "opted_out" | "no_phone";

export interface WaContact {
  user_id: number;
  name: string;
  role: Role | string;
  phone_masked: string | null;
  has_phone: boolean;
  state: ContactState;
  opted_in_at: string | null;
  opt_in_method: "console" | "whatsapp" | "whatsapp_verify" | null;
  opted_out_at: string | null;
  opt_out_method: "console" | "whatsapp_stop" | null;
}

/** POST|GET /brief/wa/contacts/verify: a member proves their own number by
    sending "CLEFS <code>" from it. The code comes back once, on POST. */
export interface PhoneVerification {
  status: "none" | "pending" | "verified" | "expired" | "cancelled";
  phone_masked?: string | null;
  expires_at?: string;
  code?: string;
  message?: string;
  send_to?: string | null;
  wa_link?: string | null;
}

// ─── Actions and timeline ────────────────────────────────────────────────

export type BriefAction = Action & { overdue?: boolean };

export type TimelineEntry =
  | { at: string; kind: "event"; event: string; from_status: ActionStatus | null; to_status: ActionStatus | null;
      via: string | null; actor_type: string | null; actor_label: string | null }
  | { at: string; kind: "surfaced"; run_id: number; cadence: Cadence; brief_date: string; item_no: number; pinned: boolean }
  | { at: string; kind: "escalation"; level: number; notified: { channel: string | null; ok: boolean | null; status: string | null }[] };

export interface ActionTimeline {
  action: Action;
  timeline: TimelineEntry[];
}

// ─── Weekly verified-value report ────────────────────────────────────────

export interface WeeklyReportRow {
  period_key: string;
  period_start: string;
  period_end: string;
  currency: string;
  verified_minor: number | null;
  identified_minor: number | null;
  generated_at: string;
}

export interface WeeklyReportPayload {
  period_key: string;
  period_start: string;
  period_end: string;
  currency: string;
  verified_minor: number;
  verified_value: string;
  by_type: { type: string; label: string; actions: number; verified_minor: number; observed_minor: number }[];
  identified_minor: number;
  identified_signals: number;
  recovered_minor: number;
  decisions: {
    count: number;
    by_event: Record<string, number>;
    median_hours_to_decision: number | null;
    approval_rate: number | null;
  };
  overdue: { action_id: number; title: string; status: ActionStatus; due_at: string | null; escalation_level: number }[];
  top_open: { signal_id: number; title: string; section: string; impact_value: string | null; currency: string | null }[];
  generated_at: string;
}

export interface WeeklyReport {
  id: number;
  period_key: string;
  period_start: string;
  period_end: string;
  currency: string;
  payload: WeeklyReportPayload;
  html: string | null;
  pdf_file_id: string | number | null;
  generated_at: string;
}
