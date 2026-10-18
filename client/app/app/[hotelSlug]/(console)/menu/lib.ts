/* ═══════════════════════════════════════════════════════════════════════
   Menu console helpers: pure functions shared by the tabs. Kept free of
   React so they are trivially testable and cheap to import.
   ═══════════════════════════════════════════════════════════════════════ */

import type { Tone } from "../../../../ui";
import type { DietaryTag, HoursWindow, OrderStatus, OutletKind, Payment } from "./types";

// ─── Labels ──────────────────────────────────────────────────────────────

export const OUTLET_KINDS: { value: OutletKind; label: string }[] = [
  { value: "in_room_dining", label: "In-room dining" },
  { value: "restaurant", label: "Restaurant" },
  { value: "bar", label: "Bar" },
  { value: "cafe", label: "Cafe" },
  { value: "pool", label: "Pool" },
  { value: "spa", label: "Spa" },
  { value: "minibar", label: "Minibar" },
  { value: "other", label: "Other" },
];

export const DIETARY: { value: DietaryTag; label: string }[] = [
  { value: "vegetarian", label: "Vegetarian" },
  { value: "vegan", label: "Vegan" },
  { value: "gluten_free", label: "Gluten free" },
  { value: "dairy_free", label: "Dairy free" },
  { value: "halal", label: "Halal" },
  { value: "spicy", label: "Spicy" },
  { value: "contains_nuts", label: "Contains nuts" },
  { value: "contains_pork", label: "Contains pork" },
  { value: "contains_shellfish", label: "Contains shellfish" },
];

export const dietaryLabel = (t: string) => DIETARY.find(d => d.value === t)?.label ?? t.replace(/_/g, " ");
export const outletKindLabel = (k: string) => OUTLET_KINDS.find(o => o.value === k)?.label ?? k;

export const PAYMENTS: { value: Payment; label: string }[] = [
  { value: "room_charge", label: "Charge to room" },
  { value: "cash", label: "Cash" },
  { value: "card", label: "Card" },
  { value: "complimentary", label: "Complimentary" },
];
export const paymentLabel = (p: string | null) => (p ? PAYMENTS.find(x => x.value === p)?.label ?? p : "Not paid");

// ─── Order lifecycle ─────────────────────────────────────────────────────
// Mirrors the server's transition table (README "Quote gate and orders") so
// the console only offers moves the server will accept; the server still
// answers 409 illegal_transition if two people race.

export const STATUS_LABEL: Record<OrderStatus, string> = {
  placed: "Placed", accepted: "Accepted", preparing: "Preparing",
  delivered: "Delivered", cancelled: "Cancelled", refunded: "Refunded",
};

export const STATUS_TONE: Record<OrderStatus, Tone> = {
  placed: "gold", accepted: "info", preparing: "info", delivered: "ok", cancelled: "neutral", refunded: "warn",
};

export const NEXT_STATUS: Record<OrderStatus, OrderStatus[]> = {
  placed: ["accepted", "preparing", "delivered", "cancelled"],
  accepted: ["preparing", "delivered", "cancelled"],
  preparing: ["delivered", "cancelled"],
  delivered: ["refunded"],
  cancelled: [],
  refunded: [],
};

export const OPEN_STATUSES: OrderStatus[] = ["placed", "accepted", "preparing"];

// ─── Days and hours ──────────────────────────────────────────────────────

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;
export const ALL_DAYS = 127;

export function maskHas(mask: number, day: number): boolean {
  return (mask & (1 << day)) !== 0;
}

export function toggleDay(mask: number, day: number): number {
  return mask ^ (1 << day);
}

/** "Every day", "Weekdays", "Fri, Sat" ... for a compact table cell. */
export function daysText(mask: number): string {
  if ((mask & ALL_DAYS) === ALL_DAYS) return "Every day";
  if (mask === 0) return "No days";
  if (mask === 0b0011111) return "Mon to Fri";
  return WEEKDAYS.filter((_, i) => maskHas(mask, i)).join(", ");
}

export function hoursText(hours: HoursWindow[] | null | undefined): string {
  if (!hours || hours.length === 0) return "Always open";
  return hours
    .map(h => `${h.from}-${h.to}${(h.days_mask & ALL_DAYS) === ALL_DAYS ? "" : ` (${daysText(h.days_mask)})`}`)
    .join("; ");
}

export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

// ─── Money input ─────────────────────────────────────────────────────────

/** Normalise a typed price ("45", "45.5", "1,250.00") to the "45.00" string the API takes.
 *  Returns null when it is not a non-negative amount with at most `digits` decimals. */
export function normalisePrice(raw: string, digits = 2): string | null {
  const s = raw.replace(/[,\s]/g, "").replace(/^[A-Za-z]{3}/, "");
  if (!/^\d+(\.\d+)?$/.test(s)) return null;
  const [int, frac = ""] = s.split(".");
  if (frac.length > digits) return null;
  return digits === 0 ? String(Number(int)) : `${Number(int)}.${frac.padEnd(digits, "0")}`;
}

/** An error thrown by apiFetch as one readable sentence. */
export function errText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
