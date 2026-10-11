/* ═══════════════════════════════════════════════════════════════════════
   Amenities pure helpers. No imports on purpose: node --test loads this file
   straight from source (Node strips the types), so the stepper maths, the
   shift rule and the quick-entry payload builder are tested without a DOM.
   ═══════════════════════════════════════════════════════════════════════ */

// ─── Chart colours ───────────────────────────────────────────────────────

/** The "Expected" benchmark every variance is measured against. The kit's
 *  CHART_QUIET (0.17 alpha, about 1.6:1 on the #0D141D card) is for folded
 *  "other" buckets and nearly vanishes on a phone in daylight; the reference
 *  series needs 3:1 (WCAG 1.4.11), so it is a legible neutral at 0.5 alpha,
 *  about 4.7:1, still clearly apart from the blue and gold series. */
export const CHART_EXPECTED = "rgba(242,238,230,0.5)";

// ─── Numbers ─────────────────────────────────────────────────────────────

/** Parse a server decimal string ("2", "-0.5", null) into a finite number.
 *  Anything unparseable reads as 0 so one odd row never blanks a chart. */
export function num(v: string | number | null | undefined): number {
  if (v === null || v === undefined || v === "") return 0;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

/** Round for display: whole units stay whole, fractions keep one digit. */
export function qtyText(v: string | number | null | undefined): string {
  const n = num(v);
  // Grouped like money ("1,158", QA OWN-13 read "1158"); one decimal only
  // when the quantity has a fraction. toFixed first keeps 2.25 -> "2.3".
  const whole = Number.isInteger(n);
  const r = whole ? n : Number(n.toFixed(1));
  return r.toLocaleString("en-US", { minimumFractionDigits: whole ? 0 : 1, maximumFractionDigits: whole ? 0 : 1 });
}

/** Clamp a stepper value into [min, max] as a whole number. Steppers count
 *  pieces handed out, never fractions, and a slip of the thumb must not
 *  send 10,000 kits; the server caps a line at 9,999 anyway. */
export function clampQty(v: number, min = 0, max = 999): number {
  if (!Number.isFinite(v)) return min;
  return Math.min(max, Math.max(min, Math.round(v)));
}

/** Parse a typed quantity: empty or junk is 0, never NaN. */
export function parseQtyInput(raw: string, max = 999): number {
  const n = Number(raw.replace(/[^0-9]/g, ""));
  return clampQty(Number.isFinite(n) ? n : 0, 0, max);
}

// ─── Shifts ──────────────────────────────────────────────────────────────

export const SHIFTS = ["MORNING", "EVENING", "NIGHT"] as const;
export type ShiftKey = (typeof SHIFTS)[number];

/** Same rule as the server's infer_shift (issuance.py): 07-15 morning,
 *  15-23 evening, else night, on the hotel-local hour. */
export function shiftForHour(hour: number): ShiftKey {
  if (hour >= 7 && hour < 15) return "MORNING";
  if (hour >= 15 && hour < 23) return "EVENING";
  return "NIGHT";
}

/** The hotel-local hour of `now` in `tz` (falls back to the device clock
 *  when the zone is unknown to this browser). */
export function localHour(tz: string, now: Date = new Date()): number {
  try {
    const h = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", hourCycle: "h23", timeZone: tz }).format(now);
    const n = Number(h);
    return Number.isFinite(n) ? n : now.getHours();
  } catch {
    return now.getHours();
  }
}

export function shiftLabel(s: string | null | undefined): string {
  if (!s || s === "UNKNOWN") return "Not recorded";
  return s.charAt(0) + s.slice(1).toLowerCase();
}

// ─── Labels ──────────────────────────────────────────────────────────────

export const CATEGORY_LABELS: Record<string, string> = {
  BATH: "Bath", DENTAL: "Dental", SHAVING: "Shaving", SLIPPERS: "Slippers", WATER: "Water",
  TEA_COFFEE: "Tea and coffee", MINIBAR: "Minibar", OTHER: "Other",
};

export const BASIS_LABELS: Record<string, string> = {
  PER_OCCUPIED_ROOM_NIGHT: "Per occupied room-night",
  PER_ARRIVAL: "Per arrival",
  PER_GUEST_NIGHT: "Per guest-night",
  ON_REQUEST: "On request",
};

export const REASON_LABELS: Record<string, string> = {
  REPLENISH: "Room restock", GUEST_REQUEST: "Guest request", VIP: "VIP set-up", DAMAGE: "Damaged", RETURN: "Returned",
};

export const LOCATION_KIND_LABELS: Record<string, string> = {
  STORE: "Main store", FLOOR_CLOSET: "Floor closet", CART: "Trolley", DEPARTMENT: "Department", MINIBAR: "Minibar",
};

export function categoryLabel(c: string | null | undefined): string {
  return (c && CATEGORY_LABELS[c]) || "Other";
}

export function floorLabel(f: string | null | undefined): string {
  return f ? `Floor ${f}` : "No floor tagged";
}

// ─── Quick entry payload ─────────────────────────────────────────────────

export interface QuickEntryContext {
  shift: ShiftKey;
  floor: string;
  staffName: string;
  staffId: number | null;
  reason: string;
}

export interface QuickLine {
  item_id: number;
  qty: number;
  shift: ShiftKey;
  reason: string;
  to_floor?: string;
  staff_name?: string;
  staff_member_id?: number;
}

/** Turn the stepper counts into POST /issuance lines. Zero counts are
 *  dropped (the server 422s qty 0); a RETURN is sent positive and the
 *  server stores it negative, so the sign rule lives in one place. Blank
 *  floor and staff are left out so a paired device's on-shift person
 *  (X-Clefs-Staff) still becomes the issuer server side. */
export function buildIssueLines(counts: Record<number, number>, ctx: QuickEntryContext): QuickLine[] {
  const floor = ctx.floor.trim();
  const staff = ctx.staffName.trim();
  const out: QuickLine[] = [];
  for (const [id, raw] of Object.entries(counts)) {
    const qty = clampQty(raw);
    if (qty <= 0) continue;
    const line: QuickLine = { item_id: Number(id), qty, shift: ctx.shift, reason: ctx.reason };
    if (floor) line.to_floor = floor.slice(0, 20);
    if (ctx.staffId) line.staff_member_id = ctx.staffId;
    else if (staff) line.staff_name = staff.slice(0, 80);
    out.push(line);
  }
  return out.sort((a, b) => a.item_id - b.item_id);
}

export function totalPieces(counts: Record<number, number>): number {
  return Object.values(counts).reduce((s, v) => s + clampQty(v), 0);
}

// ─── Variance wording ────────────────────────────────────────────────────

/** Variance tone: above expected (issued more than standards plus logged
 *  requests allow) is the leak, so positive money is bad news here. A
 *  small band either side reads as on plan to avoid alarming on noise. */
export function varianceTone(pct: string | number | null | undefined): "bad" | "warn" | "ok" | "neutral" {
  if (pct === null || pct === undefined || pct === "") return "neutral";
  const p = num(pct);
  if (p > 15) return "bad";
  if (p > 5) return "warn";
  return "ok";
}

/** Coverage sentence for a window where some days had no occupancy row.
 *  Those days are excluded, never zero-filled, and the owner should know. */
export function coverageNote(covered: number, missing: number): string | null {
  if (missing <= 0) return null;
  const d = (n: number) => `${n} day${n === 1 ? "" : "s"}`;
  if (covered <= 0) return `No occupancy data for this window yet, so expected use cannot be worked out.`;
  return `${d(missing)} without occupancy data are left out; figures cover ${d(covered)}.`;
}

// ─── Date windows ────────────────────────────────────────────────────────

/** Add days to a YYYY-MM-DD key (calendar maths in UTC, no DST drift). */
export function addDays(key: string, days: number): string {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The `days` business dates ending yesterday. The nightly rollup settles a
 *  day at 03:30, so today is never complete and would read as a day with
 *  no occupancy; the server's default window ends yesterday for the same
 *  reason. `todayKey` is the hotel-local date. */
export function windowEndingYesterday(days: number, todayKey: string): { from: string; to: string } {
  const to = addDays(todayKey, -1);
  return { from: addDays(to, -(Math.max(1, days) - 1)), to };
}

// ─── Cohort label ────────────────────────────────────────────────────────

const CITY_NAMES: Record<string, string> = {
  DXB: "Dubai", AUH: "Abu Dhabi", SHJ: "Sharjah", AJM: "Ajman", RAK: "Ras Al Khaimah", FUJ: "Fujairah", UAQ: "Umm Al Quwain",
};

/** "Japan" for JP; null when Intl has no name for the code. */
function regionName(code: string | undefined): string | null {
  if (!code || !/^[A-Z]{2}$/.test(code)) return null;
  try {
    const name = new Intl.DisplayNames(["en"], { type: "region" }).of(code);
    return name && name !== code ? name : null;
  } catch {
    return null;
  }
}

/** "AE-DXB-HOTEL_4" -> "Dubai, 4-star hotels"; a hotel outside the UAE has
 *  no emirate, so "JP-ALL-HOTEL_3" -> "Japan, 3-star hotels" (server
 *  benchmarks.cohort_key). Unknown shapes fall back to a readable form of
 *  the key rather than a blank. */
export function cohortLabel(key: string | null | undefined): string {
  if (!key) return "hotels of the same city and class";
  const [country, city, ...rest] = key.split("-");
  const cls = rest.join("-");
  const where = (city && CITY_NAMES[city]) || (city === "ALL" ? regionName(country) : city) || "";
  const star = /^HOTEL_(\d)$/.exec(cls);
  const what = star ? `${star[1]}-star hotels` : cls ? cls.replace(/_/g, " ").toLowerCase() : "hotels";
  return where ? `${where}, ${what}` : what;
}
