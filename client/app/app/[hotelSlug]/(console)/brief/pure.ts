/* ═══════════════════════════════════════════════════════════════════════
   Brief pure helpers: no React, no runtime imports (only erased type
   imports), so node --test can load this file directly (see __tests__).
   Money is integer minor units; the currency's digits are passed in by
   lib.ts (currencyDigits), never guessed here.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Decision } from "../../../../lib/types";

// ─── Decisions per state ─────────────────────────────────────────────────
/** Decisions the console offers per state, mirroring core TRANSITIONS and
    REMINDABLE (clefs.core.actions); core decide() has the last word. Ignore
    is proposed-only there; a snoozed action wakes on any decision. */
const BY_STATUS: Record<string, readonly Decision[]> = {
  proposed: ["approve", "adjust", "assign", "dispute", "snooze", "ignore"],
  approved: ["adjust", "assign", "remind", "snooze"],
  assigned: ["assign", "remind", "snooze"],
  in_progress: ["assign", "remind"],
  snoozed: ["approve", "assign", "remind"],
  submitted: ["remind"],
  awaiting_counterparty: ["remind"],
  rejected_by_counterparty: ["remind"],
};

export function decisionsFor(status: string | null | undefined, type?: string | null): readonly Decision[] {
  const base = BY_STATUS[status ?? ""] ?? [];
  // Disputes are a Profit Guard path (OTA statements); elsewhere they are noise.
  const disputable = !!type && (type.startsWith("pg.") || type.startsWith("profit"));
  return base.filter(d => d !== "dispute" || disputable);
}

// ─── Signal-only items (POST /brief/signals/{id}/decide) ─────────────────
/* An item with a signal but no action yet used to be read-only ("No action
   proposed yet"), so a Today brief could hold nothing to act on (QA
   OWN-02). The card offers the same bar: Approve proposes the recommended
   action and approves it through core decide(), Assign proposes and
   assigns, Snooze and Ignore close the signal. A verified item is banked
   money: there is nothing to decide. */

export type SignalVerb = "propose" | "assign" | "snooze" | "dismiss";

export interface SignalDecideRequest {
  decision: SignalVerb;
  assignee_user_id?: number;
  snooze_until?: string;
  note?: string;
}

/** Signal statuses that are still waiting on someone (core OPEN statuses). */
export const OPEN_SIGNAL = new Set(["open", "surfaced"]);

export function signalDecisionsFor(
  item: { section: string; recommended_action?: { type?: string | null } | null },
  signalStatus?: string | null,
): readonly Decision[] {
  if (item.section === "verified" || (signalStatus && !OPEN_SIGNAL.has(signalStatus))) return [];
  return item.recommended_action?.type ? ["approve", "assign", "snooze", "ignore"] : ["assign", "snooze", "ignore"];
}

/** The signal route's body for a bar decision (approve = propose, then core decide). */
export function signalDecideBody(
  d: Decision, extra?: { assignee_user_id?: number; snooze_until?: string; note?: string },
): SignalDecideRequest | null {
  const note = extra?.note?.trim() || undefined;
  switch (d) {
    case "approve": return { decision: "propose" };
    case "assign": return extra?.assignee_user_id ? { decision: "assign", assignee_user_id: extra.assignee_user_id, note } : null;
    case "snooze": return { decision: "snooze", snooze_until: extra?.snooze_until, note };
    case "ignore": return { decision: "dismiss", note };
    default: return null;
  }
}

/** What a run covers, for its header (QA OWN-14: a monthly brief built on
    29 Sep read "Monthly brief for Tuesday, 29 September 2026"). Weekly and
    monthly name their period (the previous ISO week or calendar month,
    brief.period_for); daily returns null and the caller shows the day. */
export function periodLabel(cadence: string, start?: string | null, end?: string | null, locale = "en-GB"): string | null {
  const d = (x: string, o: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat(locale, { timeZone: "UTC", ...o }).format(new Date(`${x}T12:00:00Z`));
  if (cadence === "monthly" && start) return d(start, { month: "long", year: "numeric" });
  if (cadence === "weekly" && start && end) {
    return `${d(start, { day: "numeric", month: "short" })} to ${d(end, { day: "numeric", month: "short", year: "numeric" })}`;
  }
  return null;
}

/** "1 action" / "2 actions". */
export const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

// ─── Adjust bounds (same convention as whatsapp.adjust_bounds) ───────────

export interface AdjustBounds {
  field: string;
  label: string;
  currency: string;
  min_minor: number;
  max_minor: number;
}

const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);

/* The editable field is the proposal, never the baseline: dr.rate_change
   carries current_bar_minor before recommended_minor, and taking the first
   key in object order made Adjust edit the current BAR (the owner's rate was
   stored where nothing reads it, QA OWN-01). Proposal-named keys win and
   baseline-named keys are never offered; whatsapp.adjust_fields matches. */
const PREFERRED = ["recommended_", "proposed_", "suggested_", "target_", "new_", "decided_"];
const BASELINE = ["current_", "old_", "previous_", "prev_", "base_", "baseline_"];
const startsAny = (f: string, pre: readonly string[]) => pre.some(x => f.startsWith(x));

/** Candidate `<f>_minor` keys in the order Adjust tries them. */
export function adjustFields(p: Record<string, unknown>): string[] {
  const keys = Object.keys(p).filter(f => f.endsWith("_minor") && !f.startsWith("min_") && !f.startsWith("max_")
    && f !== "floor_minor" && f !== "ceiling_minor" && !startsAny(f, BASELINE));
  return [...keys.filter(f => startsAny(f, PREFERRED)), ...keys.filter(f => !startsAny(f, PREFERRED))];
}

/** `params.adjust {field, min_minor, max_minor, label?}`, or `<f>_minor` with
    `min_<f>_minor` / `max_<f>_minor`, or with `floor_minor` / `ceiling_minor`
    (rate recommendations). Null = no bounded field: the sheet falls back to
    editing the scalar params directly. */
export function adjustBounds(params: Record<string, unknown> | null | undefined, currency: string): AdjustBounds | null {
  const p = params ?? {};
  const exp = p.adjust as Record<string, unknown> | undefined;
  if (exp && typeof exp === "object" && typeof exp.field === "string" && isInt(exp.min_minor) && isInt(exp.max_minor)) {
    const label = typeof exp.label === "string" && exp.label ? exp.label : exp.field.replace(/_minor$/, "").replace(/_/g, " ");
    return { field: exp.field, label, currency, min_minor: exp.min_minor, max_minor: exp.max_minor };
  }
  for (const f of adjustFields(p)) {
    const v = p[f];
    const lo = p[`min_${f}`] ?? p.floor_minor;
    const hi = p[`max_${f}`] ?? p.ceiling_minor;
    if (isInt(v) && isInt(lo) && isInt(hi)) {
      return { field: f, label: f.replace(/_minor$/, "").replace(/_/g, " "), currency, min_minor: lo, max_minor: hi };
    }
  }
  return null;
}

/** "1,020.50" / "AED 455" -> minor units, by string arithmetic (no float
    rounding on money). Null when the text is not a plain amount or carries
    more decimals than the currency has (`digits`: 2 for AED, 3 for KWD). */
export function parseMajorDigits(text: string, digits: number): number | null {
  // Only a currency code at either end is dropped; a letter inside ("1e3")
  // makes the text not an amount, rather than silently becoming "13".
  const clean = text.trim().replace(/^[A-Za-z]{3}\s*/, "").replace(/\s*[A-Za-z]{3}$/, "").replace(/[\s,]/g, "");
  const m = /^(\d+)(?:\.(\d*))?$/.exec(clean);
  if (!m) return null;
  const frac = m[2] ?? "";
  if (frac.length > digits) return null;
  const minor = Number(m[1]) * 10 ** digits + Number((frac + "0".repeat(digits)).slice(0, digits) || "0");
  return Number.isSafeInteger(minor) ? minor : null;
}


// ─── ISO weeks (the weekly report key, "2026-W40") ───────────────────────

/** ISO week key of a calendar date given as "YYYY-MM-DD" (a hotel-local day). */
export function isoWeekOf(day: string): string {
  const [y, m, d] = day.split("-").map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  const dow = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - dow); // the Thursday of that week names its year
  const yearStart = Date.UTC(t.getUTCFullYear(), 0, 1);
  const week = Math.ceil(((t.getTime() - yearStart) / 86_400_000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/** The ISO week `delta` weeks from `key` ("2026-W40", -1 -> "2026-W39"). */
export function shiftIsoWeek(key: string, delta: number): string | null {
  const m = /^(\d{4})-W(\d{2})$/.exec(key);
  if (!m) return null;
  // Monday of week 1 is the Monday on or before 4 January.
  const jan4 = new Date(Date.UTC(Number(m[1]), 0, 4));
  const monday1 = jan4.getTime() - ((jan4.getUTCDay() || 7) - 1) * 86_400_000;
  const target = new Date(monday1 + ((Number(m[2]) - 1 + delta) * 7) * 86_400_000);
  return isoWeekOf(target.toISOString().slice(0, 10));
}

/** "YYYY-MM-DD" shifted by whole days (calendar arithmetic in UTC, no DST). */
export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

