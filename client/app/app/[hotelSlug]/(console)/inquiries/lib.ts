/* ═══════════════════════════════════════════════════════════════════════
   Inquiry Guard console helpers. Pure functions and vocabularies only, with
   type-only imports: `npm test` loads this file straight into node --test
   (type stripping), so a runtime import of the kit or of lib/format would
   break the test. Anything that needs the hotel formatter lives in the
   components.
   ═══════════════════════════════════════════════════════════════════════ */

import type {
  AutoSendReason, Inquiry, LinkStatus, QuoteLine, QuoteStatus, Segment, Source, Stage,
} from "./types";

// ─── Vocabularies ────────────────────────────────────────────────────────

type BadgeTone = "neutral" | "gold" | "ok" | "warn" | "bad" | "info";

export const STAGES: readonly Stage[] = [
  "new", "replied", "quoted", "payment_pending", "confirmed_verified",
  "lost", "turned_down", "future", "spam", "rerouted",
];
export const OPEN_STAGES: readonly Stage[] = ["new", "replied", "quoted", "payment_pending"];

export const STAGE_LABEL: Record<Stage, string> = {
  new: "New",
  replied: "Replied",
  quoted: "Quoted",
  payment_pending: "Payment pending",
  confirmed_verified: "Verified booking",
  lost: "Lost",
  turned_down: "Turned down",
  future: "Future",
  spam: "Spam",
  rerouted: "Rerouted",
};

export const STAGE_TONE: Record<Stage, BadgeTone> = {
  new: "warn",
  replied: "info",
  quoted: "gold",
  payment_pending: "gold",
  confirmed_verified: "ok",
  lost: "neutral",
  turned_down: "neutral",
  future: "info",
  spam: "neutral",
  rerouted: "neutral",
};

export const SEGMENTS: readonly Segment[] = ["fit", "corporate", "group", "long_stay", "event", "unknown"];
export const SEGMENT_LABEL: Record<Segment, string> = {
  fit: "Leisure (FIT)",
  corporate: "Corporate",
  group: "Group",
  long_stay: "Long stay",
  event: "Event",
  unknown: "Not sure yet",
};

export const SOURCES: readonly Source[] = [
  "whatsapp", "email", "web_form", "meta_leadgen", "google_leadform", "phone_desk", "concierge",
];
export const SOURCE_LABEL: Record<Source, string> = {
  whatsapp: "WhatsApp",
  email: "Email",
  web_form: "Website form",
  meta_leadgen: "Meta lead ad",
  google_leadform: "Google lead form",
  phone_desk: "Phone",
  concierge: "Concierge",
};

/** Lost reasons a person picks (4.10). SLOW_RESPONSE, BOOKED_VIA_OTA,
    GUEST_UNRESPONSIVE and PAYMENT_LINK_EXPIRED are set by the system, so
    they are shown on a closed inquiry but never offered; DATES_CHANGED is
    the "Future" path (it needs a remind date), not a lost reason here. */
export const LOST_PICKABLE = [
  "PRICE_VS_COMP", "NO_AVAILABILITY", "BOOKED_COMPETITOR", "POLICY_TERMS",
  "PRODUCT_LOCATION_MISMATCH", "TRIP_CANCELLED", "SPAM_DUPLICATE",
] as const;

export const TURNDOWN_REASONS = ["NO_AVAILABILITY", "DISPLACEMENT", "POLICY", "BELOW_FLOOR"] as const;

export const REASON_LABEL: Record<string, string> = {
  PRICE_VS_COMP: "Price vs competitors",
  NO_AVAILABILITY: "No availability",
  SLOW_RESPONSE: "Slow response",
  BOOKED_VIA_OTA: "Booked through an OTA",
  BOOKED_COMPETITOR: "Booked a competitor",
  GUEST_UNRESPONSIVE: "Guest stopped replying",
  PAYMENT_LINK_EXPIRED: "Payment link expired",
  POLICY_TERMS: "Policy or terms",
  DATES_CHANGED: "Dates changed",
  PRODUCT_LOCATION_MISMATCH: "Wrong product or location",
  TRIP_CANCELLED: "Trip cancelled",
  SPAM_DUPLICATE: "Spam or duplicate",
  DISPLACEMENT: "Displaces better business",
  POLICY: "Against hotel policy",
  BELOW_FLOOR: "Below our floor rate",
};

export const reasonLabel = (code: string | null | undefined): string =>
  code ? REASON_LABEL[code] ?? code.replace(/_/g, " ").toLowerCase() : "";

export const QUOTE_STATUS_LABEL: Record<QuoteStatus, string> = {
  draft: "Draft",
  awaiting_approval: "Awaiting approval",
  approved: "Approved",
  sent: "Sent",
  accepted: "Accepted",
  expired: "Expired",
  superseded: "Superseded",
  withdrawn: "Withdrawn",
};

export const LINK_STATUS_TONE: Record<LinkStatus, BadgeTone> = {
  created: "neutral",
  sent: "info",
  paid: "ok",
  partially_paid: "warn",
  expired: "neutral",
  cancelled: "neutral",
  failed: "bad",
  refunded: "warn",
};

// ─── Auto-send reasons (4.7) ─────────────────────────────────────────────

/** Why a quote needs a person before it goes. Plain words: the reader is a
    front-desk agent deciding whether to press Send. */
export const AUTO_SEND_REASON_TEXT: Record<AutoSendReason, string> = {
  rates_missing: "Some nights have no rate yet",
  no_guardrails: "No pricing guardrails for these dates, so a person must set the rate",
  fallback_guardrails: "Guardrails are a fallback estimate, not a live rate",
  below_bar: "At least one night is below the best available rate",
  no_bar: "No best available rate to compare against",
  discount: "The quote carries a discount",
  bar_stale: "The best available rate is out of date",
  segment_not_allowed: "This kind of stay always needs approval",
  too_many_rooms: "More rooms than the auto-send limit",
  too_many_nights: "More nights than the auto-send limit",
  total_over_limit: "Total is above the auto-send limit",
  low_confidence: "The request details were read with low confidence",
  missing_fields: "Dates, guests or rooms are still missing",
};

export const autoSendText = (r: string): string =>
  (AUTO_SEND_REASON_TEXT as Record<string, string>)[r] ?? r.replace(/_/g, " ");

// ─── SLA and windows ─────────────────────────────────────────────────────

export type SlaState = "answered" | "breached" | "due" | "none";

export function slaState(
  row: Pick<Inquiry, "first_response_at" | "first_response_due_at" | "sla_breached">,
  now: number = Date.now(),
): SlaState {
  if (row.first_response_at) return "answered";
  if (row.sla_breached) return "breached";
  if (!row.first_response_due_at) return "none";
  return Date.parse(row.first_response_due_at) <= now ? "breached" : "due";
}

/** A timestamp still in the future (the WhatsApp 24 h window, the CTWA 72 h
    free window). Null or unparseable counts as closed. */
export function windowOpen(until: string | null | undefined, now: number = Date.now()): boolean {
  if (!until) return false;
  const t = Date.parse(until);
  return Number.isFinite(t) && t > now;
}

// ─── Room-nights ─────────────────────────────────────────────────────────

/** Room-nights for the list: the stated stay when both are known, else the
    server's estimate (segment defaults, 4.5) flagged as such. */
export function roomNights(
  row: Pick<Inquiry, "nights" | "rooms" | "room_nights_est" | "room_nights_estimated">,
): { value: number | null; estimated: boolean } {
  if (row.nights && row.rooms) return { value: row.nights * row.rooms, estimated: false };
  if (row.room_nights_est) return { value: row.room_nights_est, estimated: row.room_nights_estimated !== false };
  return { value: null, estimated: false };
}

/** Nights between two ISO dates (YYYY-MM-DD), or null. */
export function nightsBetween(a: string | null | undefined, b: string | null | undefined): number | null {
  if (!a || !b) return null;
  const d = (Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000;
  return Number.isFinite(d) && d > 0 ? Math.round(d) : null;
}

// ─── Rate band (the quote editor's per-night hint) ───────────────────────

export type RateBand = "missing" | "below_floor" | "above_ceiling" | "below_bar" | "at_bar" | "no_guardrails";

/** Where a night's rate sits against its guardrails. The server is the
    authority (it clamps and refuses); this only colours the grid. */
export function rateBand(
  line: Pick<QuoteLine, "bar_minor" | "floor_minor" | "ceiling_minor">,
  rate: number | null,
): RateBand {
  if (rate === null || !Number.isFinite(rate) || rate <= 0) return "missing";
  if (line.floor_minor != null && rate < line.floor_minor) return "below_floor";
  if (line.ceiling_minor != null && rate > line.ceiling_minor) return "above_ceiling";
  if (line.bar_minor != null) return rate < line.bar_minor ? "below_bar" : "at_bar";
  if (line.floor_minor == null && line.ceiling_minor == null) return "no_guardrails";
  return "at_bar";
}

/** One grid row per night and room type (a group can mix room types). */
export const lineKey = (l: { stay_date: string; room_type_code: string | null }) => `${l.stay_date}|${l.room_type_code ?? ""}`;

// ─── Money input ─────────────────────────────────────────────────────────

/** "504.34" -> 50434 with 2 minor digits, exactly (string arithmetic, no
    float rounding: 0.1 + 0.2 must never become a 1-fil error on a quote).
    Returns null for anything that is not a plain positive decimal with at
    most `digits` places. */
export function majorToMinor(input: string, digits = 2): number | null {
  const s = input.trim().replace(/[,\s]/g, "");
  const m = /^(\d+)(?:\.(\d*))?$/.exec(s);
  if (!m) return null;
  const frac = m[2] ?? "";
  if (frac.length > digits) return null;
  const whole = m[1].replace(/^0+(?=\d)/, "");
  const minor = Number(whole + frac.padEnd(digits, "0"));
  return Number.isSafeInteger(minor) ? minor : null;
}

/** 50434 -> "504.34" (the editable form of a stored rate). */
export function minorToInput(minor: number | null | undefined, digits = 2): string {
  if (minor === null || minor === undefined || !Number.isFinite(minor)) return "";
  if (digits === 0) return String(Math.round(minor));
  const s = String(Math.round(Math.abs(minor))).padStart(digits + 1, "0");
  return `${minor < 0 ? "-" : ""}${s.slice(0, -digits)}.${s.slice(-digits)}`;
}

// ─── Paths and filters ───────────────────────────────────────────────────

export const inqPath = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}/inquiries${p}`;

export interface Filters {
  stage: Stage[];
  segment: Segment[];
  source: Source[];
  sla: "" | "breached" | "due" | "answered";
  check_in_from: string;
  check_in_to: string;
}

export const EMPTY_FILTERS: Filters = {
  stage: [], segment: [], source: [], sla: "", check_in_from: "", check_in_to: "",
};

/** The default view is the open pipeline: what still needs a person. */
export const DEFAULT_FILTERS: Filters = { ...EMPTY_FILTERS, stage: [...OPEN_STAGES] };

/** Filters -> the list route's query (comma lists; empty values dropped). */
export function filterQuery(f: Filters, limit = 200): Record<string, string | number> {
  const q: Record<string, string | number> = { limit };
  if (f.stage.length) q.stage = f.stage.join(",");
  if (f.segment.length) q.segment = f.segment.join(",");
  if (f.source.length) q.source = f.source.join(",");
  if (f.sla) q.sla = f.sla;
  if (f.check_in_from) q.check_in_from = f.check_in_from;
  if (f.check_in_to) q.check_in_to = f.check_in_to;
  return q;
}

export const activeFilterCount = (f: Filters): number =>
  (f.stage.length ? 1 : 0) + (f.segment.length ? 1 : 0) + (f.source.length ? 1 : 0)
  + (f.sla ? 1 : 0) + (f.check_in_from || f.check_in_to ? 1 : 0);

// ─── Errors ──────────────────────────────────────────────────────────────

/** Server error codes -> what the person should do about them. Unknown
    codes fall back to the server's own sentence. */
export const ERROR_TEXT: Record<string, string> = {
  illegal_stage: "That move is not possible from this inquiry's current stage. Refresh and try again.",
  invalid_lost_reason: "Pick a lost reason from the list.",
  remind_on_required: "Pick a remind date after today.",
  invalid_dates: "Check-out must be after check-in.",
  invalid_phone: "That phone number does not look valid. Include the country code.",
  invalid_email: "That email address does not look valid.",
  invalid_merge: "These two inquiries cannot be merged.",
  dates_required: "Add check-in and check-out dates before building a quote.",
  invalid_rooms: "Check the room types and counts.",
  unknown_line: "A night in the edit is not on this quote. Rebuild the quote.",
  invalid_rate: "Rates must be positive amounts.",
  above_ceiling: "A rate is above the ceiling for that night.",
  reason_required: "A rate below the floor needs a reason.",
  below_floor_needs_approval: "Only an approver can quote below the floor rate.",
  quote_locked: "This quote was already sent. Build a new version to change it.",
  rates_missing: "Every night needs a rate before the quote can be sent.",
  quote_expired: "This quote has expired. Build a new version.",
  dial_off: "Sending quotes is switched off in the autonomy settings.",
  quote_not_sent: "Send the quote before creating a payment link.",
  no_payment_provider: "No payment provider is connected. Connect one in Inquiry settings, or use a manual link.",
  invalid_amount: "Check the payment amount.",
  illegal_link_state: "This link can no longer be cancelled.",
  not_manual: "Only manual payment links can be marked paid by hand.",
  evidence_required: "Attach proof of payment first.",
  review_closed: "This match was already decided.",
  invalid_settings: "Some settings are not valid. Check the highlighted values.",
  not_connected: "That channel is not connected.",
  page_in_use: "That Facebook page is already connected to another hotel.",
  invalid_payment_url: "The payment link must be a full https:// address from your payment provider.",
  draft_contains_price: "The AI draft mentioned a price, so it was not used. Write the reply yourself or send a quote.",
  ai_not_allowed: "AI drafts need the guest's express consent for this inquiry. Write the reply yourself.",
  ai_disabled: "AI drafting is switched off for now. Write the reply yourself.",
};

/** Draft refusals that mean "no AI draft for this inquiry": the button hides
    rather than inviting a retry that will fail the same way. */
export const DRAFT_OFF_CODES: readonly string[] = ["ai_not_allowed", "ai_disabled", "dial_off"];

export function errorText(err: unknown): string {
  const e = err as { code?: string; message?: string; status?: number } | null;
  if (e?.code && ERROR_TEXT[e.code]) return ERROR_TEXT[e.code];
  if (e?.status === 403) return "You do not have permission to do that.";
  return e?.message || "Something went wrong. Try again.";
}

/** A route that is not deployed yet (another lane's part): 404 or 405 on a
    known path means "not available", not "this inquiry is missing". */
export const isMissingRoute = (err: unknown): boolean => {
  const e = err as { status?: number; code?: string } | null;
  return e?.status === 405 || (e?.status === 404 && !e?.code);
};

// ─── Setup checklist state ───────────────────────────────────────────────

interface Loadable { data?: unknown; error?: unknown; loading: boolean }

/** What the empty-state checklist may show. WHY: its items read "Not
    connected" from missing data, so rendering them while /channels or
    /payment-accounts is in flight, or after either failed, told a fully
    connected hotel to reconnect everything. Only settled data speaks. */
export function checklistState(channels: Loadable, payments: Loadable): "loading" | "error" | "ready" {
  if ((channels.loading && !channels.data) || (payments.loading && !payments.data)) return "loading";
  if ((channels.error && !channels.data) || (payments.error && !payments.data)) return "error";
  return "ready";
}
