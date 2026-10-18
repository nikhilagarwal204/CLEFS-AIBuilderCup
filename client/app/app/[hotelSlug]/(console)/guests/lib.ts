/* ═══════════════════════════════════════════════════════════════════════
   Guests console helpers: paths, labels and small pure functions shared by
   every guests page. No React state, so any page imports it cheaply.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, hotelPath } from "../../../../lib/api";
import type { Tone } from "../../../../ui";
import type { StepKey } from "./types";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/guests. */
export const gstApi = (slug: string, p = "") => hotelPath(slug, `/guests${p}`);

/** API path under /hotels/{slug}/guests/journeys. */
export const jrnApi = (slug: string, p = "") => hotelPath(slug, `/guests/journeys${p}`);

/** Console route under /app/{slug}/guests. */
export const gstHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/guests${p}`;

// ─── Permissions ─────────────────────────────────────────────────────────

export const PERM = {
  view: "guests.view",
  manage: "guests.manage",
  capture: "guests.contacts.capture",
  jView: "guests.journeys.view",
  jManage: "guests.journeys.manage",
} as const;

// ─── Labels ──────────────────────────────────────────────────────────────

export const PURPOSE_LABEL: Record<string, string> = {
  transfer_abroad: "Data transfer (required)",
  profile_memory: "Remember preferences",
  whatsapp_updates: "Stay updates on WhatsApp",
  marketing_whatsapp: "Offers on WhatsApp",
  marketing_email: "Offers by email",
};

export const PURPOSES = ["transfer_abroad", "profile_memory", "whatsapp_updates", "marketing_whatsapp", "marketing_email"] as const;

export const PROVENANCE_LABEL: Record<string, string> = {
  guest_direct: "Guest gave it directly",
  guest_stay: "This stay only",
  pms_direct: "PMS (direct booking)",
  pms_ota: "PMS (OTA booking)",
  ota_alias: "OTA relay address",
};

export const STEP_LABEL: Record<StepKey, string> = {
  j1: "Booking confirmation",
  j2: "Planning info",
  j3: "Pre-arrival offer",
  j4: "Check-in instructions",
  j5: "Day-1 check",
  j6_pre: "Check-out gate",
  j6_post: "Post-stay gate",
  j7: "Stay feedback",
  j8: "Rebooking offer",
};

export const STEPS: readonly StepKey[] = ["j1", "j2", "j3", "j4", "j5", "j6_pre", "j6_post", "j7", "j8"];
export const SEND_STEPS: readonly StepKey[] = ["j1", "j2", "j3", "j4", "j5", "j7", "j8"];
export const MARKETING_STEPS: readonly StepKey[] = ["j3", "j8"];

export const SKIP_LABEL: Record<string, string> = {
  dial_off: "Dial off",
  window_expired: "Window expired",
  cap_daily: "Daily cap",
  no_eligible_contact: "No eligible contact",
  template_missing: "No approved template",
  complaint_open: "Open complaint",
  meta_131049: "Meta marketing cap",
  consent: "No consent",
  suppressed: "Opted out",
  unknown: "Other",
};

export function stepLabel(step: string): string {
  return STEP_LABEL[step as StepKey] ?? step.toUpperCase();
}

export function humanize(key: string | null | undefined): string {
  if (!key) return "-";
  return SKIP_LABEL[key] ?? key.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
}

/** Tone for a plan or template status chip. */
export function statusTone(status: string | null | undefined): Tone {
  switch ((status || "").toLowerCase()) {
    case "sent": case "delivered": case "read": case "replied": case "done": case "approved": case "redeemed":
      return "ok";
    case "planned": case "deferred": case "claimed": case "pending": case "in_appeal": case "observed": case "proposed":
      return "info";
    case "skipped": case "cancelled": case "paused":
      return "neutral";
    case "failed": case "rejected": case "disabled":
      return "bad";
    default:
      return "neutral";
  }
}

export function dialTone(level: string | null | undefined): Tone {
  if (level === "off") return "neutral";
  if (level === "observe") return "info";
  if (level === "propose") return "warn";
  return "ok";
}

/** "3rd", "21st": the returning-guest badge counts stays. */
export function ordinal(n: number): string {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return `${n}${s[(v - 20) % 10] || s[v] || s[0]}`;
}

/** Message for a failed request; the server's `detail` is already human. */
export function errText(err: unknown): string {
  if (err instanceof ApiError) return err.message || `Request failed (${err.status})`;
  return err instanceof Error ? err.message : String(err);
}

export function splitList(v: string): string[] {
  return v.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
}
