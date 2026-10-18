/* ═══════════════════════════════════════════════════════════════════════
   Demand Radar console model: labels and pure functions shared by every
   demand page. No React and no runtime imports (only types), so the node
   test runner loads it directly (demand/__tests__/model.test.mjs).
   ═══════════════════════════════════════════════════════════════════════ */

import type {
  CalendarCell, Campaign, EventCategory, EventChange, EventStatus, Lever, NightBand, PacePoint, Recommendation, RecStatus,
} from "./types";

type Tone = "neutral" | "gold" | "ok" | "warn" | "bad" | "info";

// ─── Labels ──────────────────────────────────────────────────────────────

export const CATEGORY_LABEL: Record<EventCategory, string> = {
  CONFERENCE: "Conference",
  EXPO: "Expo",
  SPORTS: "Sports",
  CONCERT: "Concert",
  FESTIVAL: "Festival",
  PUBLIC_HOLIDAY: "Public holiday",
  SCHOOL_HOLIDAY: "School holiday",
  RELIGIOUS: "Religious",
  SOURCE_MARKET_HOLIDAY: "Source-market holiday",
  COMMUNITY: "Community",
  CITYWIDE: "Citywide",
};

export const CATEGORIES = Object.keys(CATEGORY_LABEL) as EventCategory[];

/** Two-letter badge text for a heatmap cell (the full name is in the title). */
export const CATEGORY_SHORT: Record<EventCategory, string> = {
  CONFERENCE: "CF", EXPO: "EX", SPORTS: "SP", CONCERT: "CO", FESTIVAL: "FE", PUBLIC_HOLIDAY: "PH",
  SCHOOL_HOLIDAY: "SH", RELIGIOUS: "RE", SOURCE_MARKET_HOLIDAY: "SM", COMMUNITY: "CM", CITYWIDE: "CW",
};

/** Categories the owner can type in by hand (holidays come from the calendar). */
export const MANUAL_CATEGORIES: EventCategory[] = ["CONFERENCE", "EXPO", "SPORTS", "CONCERT", "FESTIVAL", "COMMUNITY", "CITYWIDE"];

export const SOURCE_LABEL: Record<string, string> = {
  CURATED: "Clefs venue calendar",
  PREDICTHQ: "PredictHQ",
  TICKETMASTER: "Ticketmaster",
  AI_WEB: "AI web search",
  MANUAL: "Added by your team",
  ICS: "Calendar import",
};

export const STATUS_TONE: Record<EventStatus, Tone> = {
  SCHEDULED: "neutral", RESCHEDULED: "warn", POSTPONED: "warn", CANCELLED: "bad",
};

export const REC_STATUS_LABEL: Record<RecStatus, string> = {
  PROPOSED: "Proposed", APPROVED: "Approved", ADJUSTED: "Adjusted", REJECTED: "Rejected",
  SUPERSEDED: "Superseded", EXPIRED: "Expired", APPLIED: "Applied",
};

export const REC_STATUS_TONE: Record<RecStatus, Tone> = {
  PROPOSED: "gold", APPROVED: "ok", ADJUSTED: "ok", REJECTED: "neutral", SUPERSEDED: "neutral", EXPIRED: "neutral",
  APPLIED: "info",
};

export const CHANGE_FIELD_LABEL: Record<string, string> = {
  dates: "Dates", status: "Status", venue_codes: "Venue", verification: "Verification", merge: "Merged",
};

export const CHANGE_REASON_LABEL: Record<string, string> = {
  rescheduled: "rescheduled by the organiser", correction: "corrected by a better source", status: "status change",
  update: "update", merge: "duplicate folded in",
};

// ─── Numbers ─────────────────────────────────────────────────────────────

/** Server Decimal strings and numbers to a finite number, else null (never NaN). */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** "12.5" -> "+12.5%"; null-safe. */
export function signedPct(v: unknown, digits = 1): string {
  const n = num(v);
  if (n === null) return "-";
  const r = Math.round(n * 10 ** digits) / 10 ** digits;
  return `${r > 0 ? "+" : ""}${r}%`;
}

// ─── Dates (hotel-local YYYY-MM-DD keys, calendar arithmetic in UTC) ─────

export function addDays(key: string, n: number): string {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** ISO weekday, 1 = Monday ... 7 = Sunday. */
export function isoWeekday(key: string): number {
  const d = new Date(`${key}T00:00:00Z`).getUTCDay();
  return d === 0 ? 7 : d;
}

export function isDateKey(v: unknown): v is string {
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v) && !Number.isNaN(Date.parse(`${v}T00:00:00Z`));
}

/** Whole days from a to b (b - a). */
export function daysBetween(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000);
}

// ─── Heatmap ─────────────────────────────────────────────────────────────

/** Fixed DI bands so a colour means the same night to night and month to
    month (the kit heatmap scales to the data max, which would paint a quiet
    month as busy). 0 = quiet ... 4 = peak; null = no input had data. */
export function heatLevel(di: number | null | undefined): number | null {
  if (di === null || di === undefined || !Number.isFinite(di)) return null;
  if (di >= 80) return 4;
  if (di >= 65) return 3;
  if (di >= 50) return 2;
  if (di >= 35) return 1;
  return 0;
}

export const HEAT_LEVEL_LABEL = ["Quiet", "Soft", "Normal", "Busy", "Peak"] as const;

/** Leading blank cells so the first night sits under its weekday (Mon first). */
export function leadPad(firstKey: string): number {
  return isoWeekday(firstKey) - 1;
}

/** One screen-reader sentence per night: the whole cell, not only the number. */
export function cellLabel(c: CalendarCell, dateText: string): string {
  const parts = [dateText];
  parts.push(c.demand_index === null ? "no demand data" : `demand index ${c.demand_index}, ${HEAT_LEVEL_LABEL[heatLevel(c.demand_index) ?? 0].toLowerCase()}`);
  if (c.events.length) parts.push(`events: ${c.events.map(e => e.title).join(", ")}`);
  if (c.recommendation) parts.push(`rate recommendation ${REC_STATUS_LABEL[c.recommendation.status].toLowerCase()}`);
  if (c.comp?.sold_out) parts.push(`${c.comp.sold_out} competitors sold out`);
  return parts.join("; ");
}

// ─── Recommendation slider and decision ──────────────────────────────────

/** Rates move in steps of 5 major units, the same round5 the server uses. */
export const RATE_STEP = 5;

export interface SliderBounds { min: number; max: number; step: number; value: number }

/** The AdjustSlider never leaves [floor, ceiling]: the server answers 400
    rate_out_of_bounds otherwise, so the control makes that state unreachable. */
export function sliderBounds(rec: Pick<Recommendation, "floor" | "ceiling" | "recommended" | "decided">): SliderBounds | null {
  const floor = num(rec.floor);
  const ceiling = num(rec.ceiling);
  if (floor === null || ceiling === null || ceiling < floor) return null;
  const start = num(rec.decided) ?? num(rec.recommended) ?? floor;
  return { min: floor, max: ceiling, step: RATE_STEP, value: clampRate(start, floor, ceiling) };
}

/** Round to the nearest 5 then clamp; the floor and ceiling always win. */
export function clampRate(v: number, floor: number, ceiling: number): number {
  const r = Math.round(v / RATE_STEP) * RATE_STEP;
  return Math.min(ceiling, Math.max(floor, r));
}

/** Change against the current BAR in percent, one decimal. */
export function changeFromBar(value: number, bar: unknown): number | null {
  const b = num(bar);
  if (b === null || b === 0) return null;
  return Math.round(((value - b) / b) * 1000) / 10;
}

export const NOTE_MAX = 300;

/** Levers are advice the owner carries out in the channel manager; the
    decide route has no lever field, so the ticked ones travel in the audited
    note (trimmed to the route's 300-character limit). */
export function leverNote(levers: readonly Lever[], picked: ReadonlySet<string>, note = ""): string | undefined {
  const chosen = levers.filter(l => picked.has(l.code)).map(l => l.label);
  const head = chosen.length ? `Levers: ${chosen.join("; ")}` : "";
  const text = [head, note.trim()].filter(Boolean).join(". ");
  if (!text) return undefined;
  return text.length > NOTE_MAX ? `${text.slice(0, NOTE_MAX - 3)}...` : text;
}

export type DecideVerb = "approve" | "adjust" | "reject";

export interface DecideBody { verb: DecideVerb; recommended?: string; note?: string }

/** Approve keeps the proposed rate; a moved slider is an adjust with the
    chosen rate as a Decimal string (major units). */
export function decideBody(
  rec: Pick<Recommendation, "recommended">, verb: "approve" | "reject", value: number | null, note?: string,
): DecideBody {
  if (verb === "reject") return { verb, ...(note ? { note } : {}) };
  const proposed = num(rec.recommended);
  if (value !== null && proposed !== null && value !== proposed) {
    return { verb: "adjust", recommended: value.toFixed(2), ...(note ? { note } : {}) };
  }
  return { verb: "approve", ...(note ? { note } : {}) };
}

/** Every rate the slider can land on: the floor, each multiple of 5 inside,
    the ceiling. The range input walks this list by index, so arrow keys
    always move one step and a floor such as 312 stays reachable. */
export function ratePositions(floor: number, ceiling: number): number[] {
  if (!(ceiling >= floor)) return [];
  const out = [floor];
  for (let v = Math.ceil(floor / RATE_STEP) * RATE_STEP; v < ceiling; v += RATE_STEP) {
    if (v > floor) out.push(v);
    if (out.length > 5000) break;
  }
  if (ceiling > floor) out.push(ceiling);
  return out;
}

/** Index of the position nearest to a rate. */
export function nearestIndex(positions: readonly number[], v: number): number {
  let best = 0;
  for (let i = 1; i < positions.length; i++) {
    if (Math.abs(positions[i] - v) < Math.abs(positions[best] - v)) best = i;
  }
  return best;
}

// ─── CompRateStrip geometry ──────────────────────────────────────────────

export interface StripGeometry {
  lo: number;
  hi: number;
  /** Positions in percent of the strip width (null when not drawn). */
  min: number | null;
  median: number | null;
  max: number | null;
  bar: number | null;
}

/** Scale the indicative comp band and the own BAR onto one strip with 8%
    headroom either side, so a BAR outside the band still shows. */
export function stripGeometry(band: Pick<NightBand, "min" | "median" | "max"> | null, bar: unknown): StripGeometry | null {
  const vals = [num(band?.min), num(band?.median), num(band?.max), num(bar)].filter((v): v is number => v !== null);
  if (!vals.length) return null;
  const rawLo = Math.min(...vals);
  const rawHi = Math.max(...vals);
  const pad = Math.max((rawHi - rawLo) * 0.08, rawHi * 0.02, 1);
  const lo = rawLo - pad;
  const hi = rawHi + pad;
  const at = (v: unknown) => {
    const n = num(v);
    return n === null ? null : Math.round(((n - lo) / (hi - lo)) * 1000) / 10;
  };
  return { lo, hi, min: at(band?.min), median: at(band?.median), max: at(band?.max), bar: at(bar) };
}

// ─── Pace ────────────────────────────────────────────────────────────────

/** Own OTB and the comparable-night baseline on one lead-time axis, far
    lead first so the line reads left to right towards the stay date. */
export function paceSeries(own: readonly PacePoint[], baseline: readonly PacePoint[]): {
  leads: number[]; own: (number | null)[]; baseline: (number | null)[];
} {
  const leads = [...new Set([...own, ...baseline].map(p => p.days_before))].sort((a, b) => b - a);
  const pick = (rows: readonly PacePoint[]) => {
    const by = new Map(rows.map(p => [p.days_before, num(p.rooms_otb)]));
    return leads.map(l => by.get(l) ?? null);
  };
  return { leads, own: pick(own), baseline: pick(baseline) };
}

/** Latest own OTB minus the baseline at the same lead: the pace gap in rooms. */
export function paceGap(own: readonly PacePoint[], baseline: readonly PacePoint[]): number | null {
  const s = paceSeries(own, baseline);
  for (let i = s.leads.length - 1; i >= 0; i--) {
    const o = s.own[i];
    const b = s.baseline[i];
    if (o !== null && b !== null) return Math.round((o - b) * 10) / 10;
  }
  return null;
}

// ─── Event change history ────────────────────────────────────────────────

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "none";
  if (Array.isArray(v)) return v.map(show).join(" to ");
  if (typeof v === "object") {
    const o = v as Record<string, unknown>;
    if ("start" in o || "end" in o) return o.end && o.end !== o.start ? `${show(o.start)} to ${show(o.end)}` : show(o.start);
    return Object.entries(o).map(([k, x]) => `${k} ${show(x)}`).join(", ");
  }
  return String(v);
}

/** "Dates: 2027-05-04 to 2027-05-07 -> 2027-04-27 to 2027-04-30 (rescheduled by the organiser)". */
export function changeText(c: Pick<EventChange, "field" | "old" | "new" | "reason">): string {
  const field = CHANGE_FIELD_LABEL[c.field] ?? c.field;
  const why = c.reason ? ` (${CHANGE_REASON_LABEL[c.reason] ?? c.reason})` : "";
  return `${field}: ${show(c.old)} -> ${show(c.new)}${why}`;
}

// ─── Dismissals ──────────────────────────────────────────────────────────

export type DismissScope = "event" | "class" | "venue";

/** What a dismissal teaches the relevance model, in the owner's words. */
export function dismissScopeText(scope: DismissScope, category: EventCategory, venue: string | null): string {
  if (scope === "class") return `Every ${CATEGORY_LABEL[category].toLowerCase()} counts for less`;
  if (scope === "venue") return venue ? `${CATEGORY_LABEL[category]} events at ${venue} count for less` : "No venue on this event";
  return "Only this event";
}

// ─── Manual event form ───────────────────────────────────────────────────

export interface ManualEventForm {
  title: string;
  category: EventCategory;
  start: string;
  end: string;
  venue_text: string;
  attendance: string;
  url: string;
  description: string;
}

/** Client-side checks that mirror POST /events (400 invalid_event), so the
    common mistakes are caught before the round trip. */
export function validateManualEvent(f: ManualEventForm): Record<string, string> {
  const err: Record<string, string> = {};
  if (f.title.trim().length < 2) err.title = "Give the event a name.";
  if (!isDateKey(f.start)) err.start = "Pick the first day.";
  if (f.end && !isDateKey(f.end)) err.end = "Pick a valid last day.";
  if (isDateKey(f.start) && isDateKey(f.end) && f.end < f.start) err.end = "The last day is before the first day.";
  const people = f.attendance.trim();
  if (people && (!/^\d{1,7}$/.test(people) || Number(people) > 5_000_000)) err.attendance = "Whole number of people (up to 5 million), or leave blank.";
  if (f.url.trim() && !/^https?:\/\/\S+$/i.test(f.url.trim())) err.url = "Link must start with http:// or https://";
  return err;
}

export function manualEventBody(f: ManualEventForm): Record<string, unknown> {
  const body: Record<string, unknown> = { title: f.title.trim(), category: f.category, start: f.start };
  if (f.end && f.end !== f.start) body.end = f.end;
  if (f.venue_text.trim()) body.venue_text = f.venue_text.trim();
  if (f.attendance.trim()) body.attendance = Number(f.attendance.trim());
  if (f.url.trim()) body.urls = [f.url.trim()];
  if (f.description.trim()) body.description = f.description.trim();
  return body;
}

// ─── Campaign copy ───────────────────────────────────────────────────────

export const CHANNEL_LABEL: Record<Campaign["channel"], string> = {
  WHATSAPP: "WhatsApp", INSTAGRAM: "Instagram", EMAIL: "Email",
};

/** The copy as one block for the clipboard (email keeps its subject line). */
export function campaignText(c: Pick<Campaign, "copy">): string {
  const k = c.copy ?? {};
  if (k.subject || k.body) return [k.subject ? `Subject: ${k.subject}` : "", k.body ?? ""].filter(Boolean).join("\n\n");
  return k.text ?? k.caption ?? "";
}

/** The editable fields of a draft, in display order. */
export function copyFields(c: Pick<Campaign, "channel" | "copy">): string[] {
  if (c.channel === "EMAIL") return ["subject", "body"];
  if (c.channel === "INSTAGRAM") return ["caption"];
  return ["text"];
}

// ─── Source markets ──────────────────────────────────────────────────────

/** Sum of shares; the server rejects more than 100 (invalid_share). */
export function sharesTotal(rows: readonly { share_pct: string | number }[]): number {
  const t = rows.reduce((s, r) => s + (num(r.share_pct) ?? 0), 0);
  return Math.round(t * 10) / 10;
}

// ─── Top 5 ───────────────────────────────────────────────────────────────

/** impact x confidence, the server's rank key, for display. */
export function rankValue(r: Pick<Recommendation, "impact" | "confidence">): number | null {
  const i = num(r.impact);
  const c = num(r.confidence);
  return i === null || c === null ? null : Math.round(i * c);
}

// ─── Guardrails ──────────────────────────────────────────────────────────

export interface GuardrailForm { room_type_code: string; month: string; dow: string; floor: string; ceiling: string; step: string }

export const MONTH_LABEL = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
export const DOW_LABEL = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** Mirrors PUT /guardrails (400 invalid_guardrail): a positive floor, a
    ceiling at or above it, a step in (0, 100]. */
export function validateGuardrail(f: GuardrailForm): Record<string, string> {
  const err: Record<string, string> = {};
  const floor = num(f.floor);
  const ceiling = num(f.ceiling);
  if (floor === null || floor <= 0) err.floor = "A positive amount.";
  if (ceiling === null || ceiling <= 0) err.ceiling = "A positive amount.";
  else if (floor !== null && ceiling < floor) err.ceiling = "The ceiling is below the floor.";
  const step = f.step.trim() ? num(f.step) : 15;
  if (step === null || step <= 0 || step > 100) err.step = "Between 1 and 100.";
  if (f.room_type_code.trim().length > 32) err.room_type_code = "32 characters at most.";
  return err;
}

export function guardrailBody(f: GuardrailForm): Record<string, unknown> {
  return {
    room_type_code: f.room_type_code.trim() || "*",
    month: f.month ? Number(f.month) : null,
    dow: f.dow ? Number(f.dow) : null,
    floor: f.floor.trim(),
    ceiling: f.ceiling.trim(),
    max_step_pct: f.step.trim() ? Number(f.step) : 15,
  };
}

/** "All rooms, Dec, Fri" style scope label for a guardrail row. */
export function guardrailScope(g: { room_type_code: string; month: number | null; dow: number | null }): string {
  return [
    g.room_type_code === "*" ? "All rooms" : g.room_type_code,
    g.month ? MONTH_LABEL[g.month - 1] : "any month",
    g.dow ? DOW_LABEL[g.dow - 1] : "any day",
  ].join(", ");
}
