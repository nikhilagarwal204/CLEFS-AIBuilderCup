/* ═══════════════════════════════════════════════════════════════════════
   Brief console helpers: paths, permissions, labels, the Adjust bounds
   convention and the decide error wording. No React state, so every brief
   page (and the public mobile brief) imports it cheaply.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, hotelPath } from "../../../../lib/api";
import { currencyDigits, minorToMajor } from "../../../../lib/format";
import { ACTION_STATUS_LABELS, type ActionStatus } from "../../../../lib/types";
import type { Tone } from "../../../../ui";
import { parseMajorDigits } from "./pure";
import type { Cadence } from "./types";

export {
  addDays, adjustBounds, decisionsFor, isoWeekOf, OPEN_SIGNAL, plural, shiftIsoWeek, signalDecideBody, signalDecisionsFor,
  type AdjustBounds, type SignalDecideRequest,
} from "./pure";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/brief. */
export const brfApi = (slug: string, p = "") => hotelPath(slug, `/brief${p}`);

/** The foundation's action routes: every decision goes through core decide(). */
export const actionApi = (slug: string, id: number, p = "") => hotelPath(slug, `/actions/${id}${p}`);

/** Console route under /app/{slug}/brief. */
export const brfHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/brief${p}`;

// ─── Permissions ─────────────────────────────────────────────────────────

export const PERM = {
  view: "brief.view",
  manage: "brief.manage",
  decide: "core.actions.decide",
  team: "core.team.manage",
  autonomy: "core.autonomy.manage",
} as const;

// ─── Labels ──────────────────────────────────────────────────────────────

export const CADENCE_LABEL: Record<Cadence, string> = { daily: "Daily", weekly: "Weekly", monthly: "Monthly" };
export const CADENCES: readonly Cadence[] = ["daily", "weekly", "monthly"];

export const isCadence = (v: unknown): v is Cadence => v === "daily" || v === "weekly" || v === "monthly";

export function statusLabel(s: string | null | undefined): string {
  if (!s) return "No action";
  return ACTION_STATUS_LABELS[s as ActionStatus] ?? s.replace(/_/g, " ");
}

/** Colour of a state chip: open work is warm, done is green, dead ends grey. */
export function statusTone(s: string | null | undefined): Tone {
  switch (s) {
    case "proposed": return "gold";
    case "approved": case "assigned": case "in_progress": case "submitted": case "awaiting_counterparty": return "info";
    case "done": case "verified": return "ok";
    case "failed": case "not_verified": case "rejected_by_counterparty": case "unresolved": return "bad";
    case "snoozed": return "warn";
    default: return "neutral";
  }
}

export const roleLabel = (r: string | null | undefined) =>
  r ? r.replace(/_/g, " ").replace(/^gm$/i, "GM").replace(/^\w/, c => c.toUpperCase()) : "";

const EVENT_LABEL: Record<string, string> = {
  created: "Created", approve: "Approved", adjust: "Adjusted and approved", assign: "Assigned", dispute: "Disputed",
  remind: "Reminder sent", ignore: "Ignored", snooze: "Snoozed", start: "Started", done: "Marked done",
  executed: "Executed", execution_failed: "Execution failed", verified: "Verified", not_verified: "Not verified",
  expired: "Expired", cancelled: "Cancelled", unsnooze: "Snooze ended", dial_revoked: "Autonomy revoked",
  verify_pending: "Verification pending", verification_reversed: "Verification reversed",
};

export const eventLabel = (e: string) => EVENT_LABEL[e] ?? e.replace(/[._]/g, " ").replace(/^\w/, c => c.toUpperCase());

export const VIA_LABEL: Record<string, string> = {
  console: "console", whatsapp: "WhatsApp", email: "email", api: "API", system: "Clefs", dial: "autonomy dial",
  magic: "brief link",
};


// ─── Decide errors, in words ─────────────────────────────────────────────

/** One readable sentence for a refused decision (core decide + magic_brief). */
export function decideError(err: unknown): string {
  if (!(err instanceof ApiError)) return "Something went wrong. Try again.";
  switch (err.code) {
    case "illegal_transition": return "Someone already moved this item on. The list is refreshed.";
    case "nonce_replayed": return "Already done.";
    case "action_expired": return "This item has expired.";
    case "not_surfaced": return "This link does not cover that item. Open Clefs to decide it.";
    case "forbidden": return "Your role cannot make this decision.";
    case "out_of_bounds": case "invalid_amount": return err.message;
    case "adjust_unavailable": return "This item is adjusted in the console.";
    case "assignee_required": return "Choose who should take it.";
    case "invalid_assignee": return "That person is not a member of this hotel.";
    case "magic_link_invalid": return "This link has expired. Open the latest brief.";
    case "rate_limited": return "Too many taps. Wait a minute, then try again.";
    case "signal_closed": return "Someone already handled this item.";
    case "action_suppressed": return "This kind of action is switched off in the autonomy settings.";
    case "invalid_params": return err.message;
  }
  if (err.status === 403) return "Your role cannot make this decision.";
  if (err.status === 0) return "Can't reach Clefs. Check your connection and try again.";
  return err.message || "Something went wrong. Try again.";
}

/** Relative in-app link? Evidence URLs are either "/app/..." or https. */
export function isInternalHref(url: string | null | undefined): url is string {
  return !!url && url.startsWith("/") && !url.startsWith("//");
}

// ─── Money input ─────────────────────────────────────────────────────────

/** "1,020.50" / "AED 455" -> minor units in that currency (string arithmetic). */
export const parseMajorToMinor = (text: string, currency: string) => parseMajorDigits(text, currencyDigits(currency));

/** Minor units -> the plain major string an input shows ("455.00"). */
export const minorToInput = (minor: number, currency: string) => minorToMajor(minor, currency);
