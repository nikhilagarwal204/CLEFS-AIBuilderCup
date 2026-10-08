/* ═══════════════════════════════════════════════════════════════════════
   Finance console helpers: paths, n/a-safe number reading, labels and the
   grouping the mapping review uses. Pure functions and type-only imports,
   so `node --test` can load this file directly (finance/__tests__).
   ═══════════════════════════════════════════════════════════════════════ */

import type { Tone } from "../../../../ui";
import type {
  Anomaly, BatchStatus, CatalogEntry, CheckStatus, Dec, LearnedFrom, MappingAccount, Pulse,
} from "./types";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/finance. */
export const finApi = (slug: string, p = "") => `/hotels/${encodeURIComponent(slug)}/finance${p}`;

/** Console route under /app/{slug}/finance. */
export const finHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/finance${p}`;

// ─── Numbers (null means n/a, never zero) ────────────────────────────────

export const NA = "n/a";

/** A Decimal string or number to a JS number; null for n/a or junk, so a
 *  missing denominator never renders as 0. */
export function num(v: Dec | number | undefined): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

/** "32.83" (a percent string) to "32.8%"; n/a when missing. */
export function pctText(pct: Dec | number | undefined, digits = 1, signed = false): string {
  const n = num(pct);
  if (n === null) return NA;
  const s = n.toFixed(digits);
  return `${signed && n > 0 ? "+" : ""}${s}%`;
}

/** "-0.478" (a ratio string) to "-47.8%"; n/a when missing. */
export function ratioText(ratio: Dec | number | undefined, digits = 1, signed = true): string {
  const n = num(ratio);
  return n === null ? NA : pctText(n * 100, digits, signed);
}

/** A major-unit string typed by a person ("1,234.5") to integer minor
 *  units; null when it is not a number. Two decimals (AED, INR, USD). */
export function majorToMinor(input: string, digits = 2): number | null {
  const clean = input.replace(/[,\s]/g, "");
  if (!clean || !/^-?\d+(\.\d+)?$/.test(clean)) return null;
  return Math.round(Number(clean) * 10 ** digits);
}

// ─── Months ──────────────────────────────────────────────────────────────

/** "2026-08-01" or "2026-08" to "2026-08". */
export function monthKey(iso: string): string {
  return iso.slice(0, 7);
}

/** "2026-08" to "August 2026" (or "Aug 2026" when short). Calendar months
 *  carry no timezone, so this formats in UTC to avoid a day shift. */
export function monthLabel(iso: string, short = false): string {
  const [y, m] = monthKey(iso).split("-").map(Number);
  if (!y || !m) return iso;
  return new Intl.DateTimeFormat("en", { month: short ? "short" : "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(Date.UTC(y, m - 1, 1)));
}

/** The month before "2026-01" is "2025-12". */
export function addMonths(key: string, delta: number): string {
  const [y, m] = monthKey(key).split("-").map(Number);
  const idx = y * 12 + (m - 1) + delta;
  return `${Math.floor(idx / 12)}-${String((idx % 12) + 1).padStart(2, "0")}`;
}

/** The weeks the NRevPAR strip plots: ARN 0 or missing weeks are skipped
 *  (6.1: those ratios are n/a, not zero). */
export function plottableWeeks(weeks: readonly Pulse[]): Pulse[] {
  return weeks.filter(w => (w.available_rn ?? 0) > 0);
}

// ─── Labels and tones ────────────────────────────────────────────────────

export const CHECK_STATUS: Record<CheckStatus, { label: string; tone: Tone }> = {
  pass: { label: "Pass", tone: "ok" },
  warn: { label: "Check", tone: "warn" },
  fail: { label: "Blocked", tone: "bad" },
  "n/a": { label: "n/a", tone: "neutral" },
};

export function checkStatus(s: string | null | undefined): { label: string; tone: Tone } {
  return CHECK_STATUS[(s ?? "n/a") as CheckStatus] ?? CHECK_STATUS["n/a"];
}

export const BATCH_STATUS: Record<BatchStatus, { label: string; tone: Tone }> = {
  mapping_required: { label: "Mapping needed", tone: "gold" },
  checks_failed: { label: "Checks failed", tone: "bad" },
  review: { label: "Ready to publish", tone: "info" },
  published: { label: "Published", tone: "ok" },
  superseded: { label: "Superseded", tone: "neutral" },
};

export function batchStatus(s: string | null | undefined): { label: string; tone: Tone } {
  return BATCH_STATUS[s as BatchStatus] ?? { label: s ?? "Unknown", tone: "neutral" };
}

/** Where a proposal came from, in words an owner reads. */
export const LEARNED_FROM: Record<LearnedFrom, string> = {
  rule: "Fixed rule",
  hotel_locked: "Your earlier mapping",
  global_memory: "Common across hotels",
  llm: "AI suggestion",
  manual: "Set by hand",
};

export function learnedFromLabel(v: string | null | undefined): string {
  return v ? LEARNED_FROM[v as LearnedFrom] ?? v : "No proposal yet";
}

const SOURCE_LABELS: Record<string, string> = {
  upload_xlsx: "Excel upload", upload_csv: "CSV upload", upload_pdf: "PDF upload",
  xero: "Xero", qbo: "QuickBooks", zoho: "Zoho Books", tally_upload: "Tally export",
};

/** PMS channel codes as the owner reads them ("BOOKING" -> "Booking.com",
    "DIRECT_WEB" -> "Direct web"); an unknown code is title-cased, never
    shown raw (QA OWN-13: the weekly pulse listed BOOKING and DIRECT_WEB). */
export const CHANNEL_LABELS: Record<string, string> = {
  BOOKING: "Booking.com", AGODA: "Agoda", EXPEDIA: "Expedia", HOTELS_COM: "Hotels.com", TRIP_COM: "Trip.com",
  AIRBNB: "Airbnb", GOOGLE_HOTELS: "Google Hotels", DIRECT: "Direct", DIRECT_WEB: "Direct web",
  DIRECT_PHONE: "Direct phone", WALK_IN: "Walk-in", CORPORATE: "Corporate", GDS: "GDS", WHOLESALE: "Wholesale",
};

export function channelLabel(code: string | null | undefined): string {
  if (!code) return "Unknown channel";
  const known = CHANNEL_LABELS[code.toUpperCase()];
  if (known) return known;
  const words = code.toLowerCase().split(/[_\s]+/).filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : code;
}

export function sourceLabel(s: string | null | undefined): string {
  return s ? SOURCE_LABELS[s] ?? s : "Unknown";
}

export const PROVIDER_LABELS: Record<string, string> = {
  xero: "Xero", qbo: "QuickBooks Online", zoho: "Zoho Books", tally: "Tally",
};

/** Departments in USALI schedule order; `usali_dept` values from 6.4. */
export const DEPT_ORDER = [
  "rooms", "fnb", "ood", "misc", "ag", "its", "sm", "pom", "eww", "mgmt_fee", "non_operating", "below_line", "excluded",
] as const;

export const DEPT_LABELS: Record<string, string> = {
  rooms: "Rooms", fnb: "Food and beverage", ood: "Other operated departments", misc: "Miscellaneous income",
  ag: "Administrative and general", its: "Information and telecoms", sm: "Sales and marketing",
  pom: "Property operations and maintenance", eww: "Energy, water and waste", mgmt_fee: "Management fees",
  non_operating: "Non-operating", below_line: "Below GOP", excluded: "Excluded (taxes and balance sheet)",
  unmapped: "Not mapped yet",
};

export const deptLabel = (d: string | null | undefined) => DEPT_LABELS[d ?? "unmapped"] ?? d ?? "Not mapped yet";

const ANOMALY_KINDS: Record<string, string> = {
  labour_ratio: "Labour cost",
  eww_ratio: "Energy, water and waste",
  line_spike: "Expense line",
  rooms_rev_mismatch: "Rooms revenue against the PMS",
  pass_through_in_revenue: "Taxes inside revenue",
  booked_net: "OTA revenue booked net",
};

export function anomalyKindLabel(kind: string): string {
  return ANOMALY_KINDS[kind] ?? kind.replace(/_/g, " ");
}

/** One line on what moved: "31.2% against 24.6% usual (z 3.1)". */
export function anomalyLine(a: Pick<Anomaly, "evidence" | "zscore">): string {
  const e = a.evidence ?? {};
  const v = typeof e.value_pct === "string" ? e.value_pct : null;
  const x = typeof e.expected_pct === "string" ? e.expected_pct : null;
  const z = num(a.zscore);
  const parts: string[] = [];
  if (v !== null) parts.push(x !== null ? `${pctText(v)} against ${pctText(x)} usual` : pctText(v));
  if (z !== null) parts.push(`z ${z.toFixed(1)}`);
  return parts.join(", ");
}

// ─── Mapping review ──────────────────────────────────────────────────────

const ARABIC = /[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]/;

/** Arabic account names render right to left (6.8: AR dir=rtl). The server
 *  says `language`; a name with Arabic letters counts too, for rows that
 *  predate the column. Mixed names stay auto so the browser decides. */
export function nameDir(language: string | null | undefined, name: string): "rtl" | "auto" | undefined {
  if (language === "ar") return "rtl";
  if (language === "mixed") return "auto";
  return ARABIC.test(name) ? "auto" : undefined;
}

export function confidenceOf(a: Pick<MappingAccount, "ai_confidence">): number | null {
  return num(a.ai_confidence);
}

/** The department an account sits under in the review: its mapped line's
 *  department, else "unmapped" (listed first, since those block publishing). */
export function accountDept(a: Pick<MappingAccount, "usali_dept" | "usali_line">,
  catalog: readonly CatalogEntry[]): string {
  if (a.usali_dept) return a.usali_dept;
  const hit = a.usali_line ? catalog.find(c => c.key === a.usali_line) : undefined;
  return hit?.dept ?? "unmapped";
}

export interface DeptGroup<T> {
  dept: string;
  label: string;
  accounts: T[];
}

/** Accounts grouped by department in schedule order, "not mapped yet" first. */
export function groupByDept<T extends Pick<MappingAccount, "usali_dept" | "usali_line">>(
  accounts: readonly T[], catalog: readonly CatalogEntry[],
): DeptGroup<T>[] {
  const order = ["unmapped", ...DEPT_ORDER] as string[];
  const map = new Map<string, T[]>();
  for (const a of accounts) {
    const d = accountDept(a, catalog);
    const list = map.get(d);
    if (list) list.push(a);
    else map.set(d, [a]);
  }
  const rank = (d: string) => (order.indexOf(d) === -1 ? order.length : order.indexOf(d));
  return Array.from(map.entries())
    .sort((x, y) => rank(x[0]) - rank(y[0]) || x[0].localeCompare(y[0]))
    .map(([dept, list]) => ({ dept, label: deptLabel(dept), accounts: list }));
}

/** Accounts "Confirm all >= threshold" would lock: an unlocked proposal at
 *  or above the bulk threshold. The server re-checks; this sizes the button. */
export function bulkEligible<T extends Pick<MappingAccount, "ai_confidence" | "locked" | "usali_line">>(
  accounts: readonly T[], threshold: number,
): T[] {
  return accounts.filter(a => {
    const c = num(a.ai_confidence);
    return !a.locked && !!a.usali_line && c !== null && c >= threshold;
  });
}

const ABOVE_GOP = new Set(["revenue", "cost_of_sales", "payroll", "other_expense", "undistributed"]);

/** Check 5, mirrored from mapping._needs_override: an account whose name
 *  reads as below GOP (depreciation, interest, rent, drawings, in English or
 *  Arabic) may sit on a line GOP reads only with a written reason, because
 *  that is the classic way a P&L flatters GOP. */
export function needsOverrideReason(a: Pick<MappingAccount, "below_gop_word">, line: string | null,
  catalog: readonly CatalogEntry[]): boolean {
  if (!a.below_gop_word || !line) return false;
  const cat = catalog.find(c => c.key === line)?.category;
  return !!cat && ABOVE_GOP.has(cat);
}

// ─── Errors ──────────────────────────────────────────────────────────────

/** Friendly copy for the finance error codes the README documents. */
export const CODE_TEXT: Record<string, string> = {
  not_published: "No month is published yet.",
  invalid_month: "That month is not valid. Use YYYY-MM.",
  period_month_required: "Pick the month this statement covers: the file has no month column.",
  not_publishable: "This month cannot be published until every check passes.",
  bulk_threshold: "Bulk confirm only takes proposals at 85% confidence or more.",
  confirm_one_at_a_time: "A proposal under 85% confidence has to be confirmed on its own.",
  override_reason_required: "Say why this account belongs below GOP before confirming it.",
  invalid_usali_line: "That USALI line is not in the list.",
  nothing_to_confirm: "Nothing is waiting for confirmation.",
  account_not_found: "That account no longer exists. Reload the page.",
  no_proposal: "This account has no proposal yet. Pick a line first.",
  not_connected: "Connect this accounting system first.",
  oauth_error: "The accounting system did not answer. Try again in a minute.",
  not_configured: "This connection needs app keys on the server. Ask Clefs support to add them.",
  no_published_months: "Publish at least one month before registering the baseline.",
  invalid_settings: "Some of those settings are not valid.",
  anomaly_not_found: "That finding was already cleared. Reload the page.",
  invalid_range: "Pick between 1 and 104 weeks.",
};

export function codeText(code: string | undefined, fallback: string): string {
  return code && CODE_TEXT[code] ? CODE_TEXT[code] : fallback;
}

// ─── Tile deltas ─────────────────────────────────────────────────────────

export interface Delta {
  value: string;
  direction: "up" | "down" | "flat";
  good?: boolean;
  label?: string;
}

/** Percent change against the previous figure for a StatTile. undefined
 *  when either side is n/a or the base is 0 (no honest percentage exists).
 *  `higherIsGood` false for costs, so a rising OTA cost reads red. */
export function changeDelta(cur: Dec | number | undefined, prev: Dec | number | undefined,
  higherIsGood: boolean, label?: string): Delta | undefined {
  const a = num(cur), b = num(prev);
  if (a === null || b === null || b === 0) return undefined;
  const pct = ((a - b) / Math.abs(b)) * 100;
  if (Math.abs(pct) < 0.05) return { value: "0%", direction: "flat", label };
  const up = pct > 0;
  return { value: `${up ? "+" : ""}${pct.toFixed(1)}%`, direction: up ? "up" : "down", good: up === higherIsGood, label };
}

/** Change in percentage points between two percent strings ("26.98" vs "24.10"). */
export function ppDelta(cur: Dec | number | undefined, prev: Dec | number | undefined,
  higherIsGood: boolean, label?: string): Delta | undefined {
  const a = num(cur), b = num(prev);
  if (a === null || b === null) return undefined;
  const d = a - b;
  if (Math.abs(d) < 0.05) return { value: "0 pp", direction: "flat", label };
  const up = d > 0;
  return { value: `${up ? "+" : ""}${d.toFixed(1)} pp`, direction: up ? "up" : "down", good: up === higherIsGood, label };
}

// ─── OAuth return (read by the server pages) ─────────────────────────────

export interface OAuthReturn { provider: string; status: string; error?: string }

/** The core callback lands on /finance with ?oauth=<provider>&oauth_status=
 *  connected|denied|error[&oauth_error=<code>]. The server page reads the
 *  params (no useSearchParams, so no Suspense bailout) and hands them down.
 *  Only finance providers count; Google's return belongs to other pages. */
export function readOAuthReturn(sp: Record<string, string | string[] | undefined>): OAuthReturn | undefined {
  const one = (v: string | string[] | undefined) => (typeof v === "string" ? v : undefined);
  const provider = one(sp.oauth);
  if (!provider || !["xero", "qbo", "zoho"].includes(provider)) return undefined;
  return { provider, status: one(sp.oauth_status) ?? "error", error: one(sp.oauth_error) };
}

// ─── Verified profit ─────────────────────────────────────────────────────

const MODULE_LABELS: Record<string, string> = {
  profit_guard: "Profit Guard", inquiries: "Direct inquiries", amenities: "Amenities", menu: "Menu and upsell",
  upsell: "Upsell", demand: "Demand Radar", service: "Service", reviews: "Reviews", guests: "Guests",
  visibility: "Visibility", concierge: "Concierge", finance: "Finance",
};

export function moduleLabel(m: string): string {
  return MODULE_LABELS[m] ?? m.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase());
}

/** by_module rows, largest first, with each one's share of the positive
 *  total for the bar (a net-negative module shows a zero-width bar). */
export function moduleShares(byModule: Record<string, number> | null | undefined): { module: string; minor: number; share: number }[] {
  const rows = Object.entries(byModule ?? {}).map(([module, minor]) => ({ module, minor: Number(minor) || 0 }));
  const total = rows.reduce((s, r) => s + Math.max(0, r.minor), 0);
  return rows
    .sort((a, b) => b.minor - a.minor)
    .map(r => ({ ...r, share: total > 0 ? Math.max(0, r.minor) / total : 0 }));
}

// ─── Chart axis labels ───────────────────────────────────────────────────

/** LineChart pins a bare YYYY-MM-DD to UTC midnight, so axis labels for
 *  months and weeks format in UTC too; formatting in the viewer's or the
 *  hotel's zone could shift a west-of-UTC label to the day before. */
export function monthFromEpoch(x: number): string {
  return monthLabel(new Date(x).toISOString().slice(0, 10), true);
}

export function dayFromEpoch(x: number): string {
  return new Intl.DateTimeFormat("en", { day: "numeric", month: "short", timeZone: "UTC" }).format(new Date(x));
}

/** The Monday `weeks` weeks before `today` (YYYY-MM-DD), for /weekly?from=. */
export function weeksAgo(today: string, weeks: number): string {
  const d = new Date(`${today}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - weeks * 7);
  const dow = (d.getUTCDay() + 6) % 7; // Monday = 0
  d.setUTCDate(d.getUTCDate() - dow);
  return d.toISOString().slice(0, 10);
}
