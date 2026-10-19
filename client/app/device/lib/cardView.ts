/* Card wording for the floor device: what a housekeeper needs at a glance
   (room, what, how late), never internal keys. */

import { UNACKED_STATES, type Card } from "./deviceApi";

export type ChipTone = "neutral" | "gold" | "ok" | "warn" | "bad" | "info";

const STATE_LABEL: Record<string, string> = {
  scheduled: "Scheduled",
  new: "New",
  reopened: "Reopened",
  assigned: "Assigned",
  acknowledged: "Acked",
  in_progress: "On the way",
  completed: "Done",
  cancelled: "Cancelled",
};

export function stateLabel(state: string): string {
  return STATE_LABEL[state] ?? state.replace(/_/g, " ");
}

export function stateTone(state: string): ChipTone {
  if (state === "in_progress") return "info";
  if (state === "acknowledged") return "ok";
  if (state === "reopened") return "warn";
  if (state === "new" || state === "assigned") return "gold";
  return "neutral";
}

function humanize(key: string | null | undefined): string {
  if (!key) return "";
  return key.replace(/[_-]+/g, " ").replace(/\b\w/g, m => m.toUpperCase());
}

/** The v2 request type ("Baby Cot") first: the legacy `type` column is a
    coarse bucket ("Cleaning") kept for old dashboards. */
export function cardTitle(c: Card): string {
  const typed = c.request_type_key && c.request_type_key !== "other" ? humanize(c.request_type_key) : "";
  const base = typed || c.subtype || c.type || humanize(c.category) || "Request";
  return c.quantity && c.quantity > 1 ? `${base} x${c.quantity}` : base;
}

export function roomLabel(c: Card): string {
  return c.room || "Lobby";
}

function mins(ms: number): string {
  const m = Math.max(0, Math.round(ms / 60_000));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

/** "Ack in 3m" / "Ack overdue 4m" / "Due in 25m" / "Overdue 10m". */
export function slaText(c: Card, now: number): { text: string; tone: ChipTone } | null {
  if (UNACKED_STATES.includes(c.state) && c.ack_due_at) {
    const d = c.ack_due_at - now;
    return d >= 0 ? { text: `Ack in ${mins(d)}`, tone: d < 60_000 ? "warn" : "neutral" } : { text: `Ack late ${mins(-d)}`, tone: "bad" };
  }
  if (c.resolve_due_at && ["acknowledged", "in_progress", "assigned"].includes(c.state)) {
    const d = c.resolve_due_at - now;
    return d >= 0 ? { text: `Due in ${mins(d)}`, tone: d < 5 * 60_000 ? "warn" : "neutral" } : { text: `Overdue ${mins(-d)}`, tone: "bad" };
  }
  return null;
}

export function ageText(c: Card, now: number): string {
  return `${mins(now - (c.timestamp || now))} ago`;
}

/** The staff member's own words first; the guest's original stays one tap away. */
export function descriptionOf(c: Card): string {
  return (c.description || "").trim();
}

/** The title in the worker's language when the server translated it. */
export function jobTitle(c: Card): string {
  return (c.display_title ?? "").trim() || cardTitle(c);
}

export function hasOriginal(c: Card): boolean {
  return Boolean(c.original_text && c.original_text.trim() && c.original_text.trim() !== descriptionOf(c));
}

const LANG: Record<string, string> = { ar: "Arabic", en: "English", hi: "Hindi", ur: "Urdu", ru: "Russian", zh: "Chinese", de: "German", fr: "French" };

export function languageLabel(code: string | null | undefined): string | null {
  if (!code || code === "en") return null;
  return LANG[code.slice(0, 2).toLowerCase()] ?? code.toUpperCase();
}
