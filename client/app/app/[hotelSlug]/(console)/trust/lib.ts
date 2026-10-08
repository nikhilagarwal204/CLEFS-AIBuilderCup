/* ═══════════════════════════════════════════════════════════════════════
   Trust console helpers: paths, labels and small formatters shared by the
   audit, evals, privacy, pilots and benchmarks pages. No React state here,
   so every page can import it without pulling in another page's code.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, apiUrl, hotelPath, withQuery, type Query } from "../../../../lib/api";
import type { Tone } from "../../../../ui";
import type { Formatter } from "../../../../lib/format";
import type { Comparator, CriterionEval, DsrStatus, DsrType, MetricInfo, MetricQueryBody, PilotStatus, VerifyMethod } from "./types";

// ─── Paths ───────────────────────────────────────────────────────────────

/** API path under /hotels/{slug}/trust. */
export const trustApi = (slug: string, p = "") => hotelPath(slug, `/trust${p}`);

/** Console route under /app/{slug}/trust. */
export const trustHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/trust${p}`;

/** A same-origin URL the browser can open directly (CSV, signed file links).
    Goes through the /api/clefs proxy so the session cookie rides along. */
export const downloadHref = (path: string, q?: Query) => apiUrl(withQuery(path, q));

/** The console page that re-runs one metric query. Every figure in the ROI
    report links here, so a reader can see exactly which query produced it.
    The body travels as JSON in ?q= (short: a handful of keys). */
export function metricHref(slug: string, query: MetricQueryBody): string {
  return `${trustHref(slug, "/metric")}?q=${encodeURIComponent(JSON.stringify(query))}`;
}

export function parseMetricQuery(raw: string | null): MetricQueryBody | null {
  if (!raw) return null;
  try {
    const q = JSON.parse(raw) as Partial<MetricQueryBody>;
    const iso = /^\d{4}-\d{2}-\d{2}$/;
    if (typeof q.metric !== "string" || !q.metric || !iso.test(String(q.start)) || !iso.test(String(q.end))) return null;
    return {
      metric: q.metric,
      start: String(q.start),
      end: String(q.end),
      grain: q.grain ?? "total",
      group_by: Array.isArray(q.group_by) ? q.group_by.map(String).slice(0, 2) : undefined,
      filters: q.filters && typeof q.filters === "object" ? q.filters : undefined,
      compare: q.compare ?? null,
    };
  } catch {
    return null;
  }
}

// ─── Errors ──────────────────────────────────────────────────────────────

export function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return (e as Error)?.message || "Something went wrong.";
}

// ─── Numbers ─────────────────────────────────────────────────────────────

/** A Decimal string 0-1 (pass rates, scores) as a whole percent. */
export function ratePct(rate: string | number | null | undefined, digits = 0): string {
  if (rate === null || rate === undefined || rate === "") return "-";
  const n = Number(rate);
  if (!Number.isFinite(n)) return "-";
  return `${(n * 100).toFixed(digits)}%`;
}

/** A Decimal string or number with trailing zeros dropped (targets, values). */
export function plainNum(v: string | number | null | undefined, maxDigits = 2): string {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-AE", { maximumFractionDigits: maxDigits });
}

/** Short hash for display: first 10 hex chars. The full value sits in a title. */
export const shortHash = (h: string | null | undefined) => (h ? `${h.slice(0, 10)}…` : "-");

/** A metric value (Decimal string or number, major units for money) in the
    metric's own unit. `info` comes from GET /hotels/{slug}/metrics; without
    it the number prints plain rather than guessing a unit. */
export function metricValue(v: string | number | null | undefined, info: MetricInfo | undefined, fmt: Formatter): string {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  switch (info?.unit) {
    case "money": return fmt.money(String(v), { currency: info.currency || fmt.currency });
    case "percent": return `${plainNum(n, 1)}%`;
    case "minutes": return fmt.mins(n);
    case "nights": return `${plainNum(n, 0)} nights`;
    default: return plainNum(n, 2);
  }
}

/** "at least 80" / "improve by 10% vs baseline": one criterion's target in words. */
export function criterionTarget(c: Pick<CriterionEval, "comparator" | "target">, info: MetricInfo | undefined, fmt: Formatter): string {
  if (c.comparator === "improve_pct") return `improve by ${plainNum(c.target, 1)}% vs baseline`;
  if (c.comparator === "reduce_pct") return `reduce by ${plainNum(c.target, 1)}% vs baseline`;
  return `${COMPARATOR_LABEL[c.comparator] ?? c.comparator} ${metricValue(c.target, info, fmt)}`;
}

/** A signed percent change for a Decimal string (already in percent). */
export function changePct(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n > 0 ? "+" : ""}${plainNum(n, 1)}%`;
}

// ─── Labels ──────────────────────────────────────────────────────────────

export const DSR_TYPE_LABEL: Record<DsrType, string> = {
  access: "Access (copy of data)",
  portability: "Portability (machine-readable)",
  rectify: "Correct data",
  erase: "Erase",
  restrict: "Restrict processing",
  object: "Object to processing",
};

export const DSR_STATUS: Record<DsrStatus, { label: string; tone: Tone }> = {
  received: { label: "Received", tone: "warn" },
  verified: { label: "Verified", tone: "info" },
  in_progress: { label: "In progress", tone: "info" },
  completed: { label: "Completed", tone: "ok" },
  rejected: { label: "Rejected", tone: "neutral" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const VERIFY_METHODS: { value: VerifyMethod; label: string }[] = [
  { value: "matched_reservation", label: "Matched a reservation" },
  { value: "id_document", label: "Checked ID document" },
  { value: "email_reply", label: "Reply from the email on file" },
  { value: "phone_callback", label: "Called back the number on file" },
  { value: "in_person", label: "In person at the desk" },
];

export const PILOT_STATUS: Record<PilotStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "neutral" },
  active: { label: "Active", tone: "info" },
  completed: { label: "Completed", tone: "gold" },
  converted: { label: "Converted", tone: "ok" },
  cancelled: { label: "Cancelled", tone: "neutral" },
};

export const COMPARATOR_LABEL: Record<Comparator, string> = {
  gte: "at least",
  lte: "at most",
  gt: "above",
  lt: "below",
  improve_pct: "improve by % vs baseline",
  reduce_pct: "reduce by % vs baseline",
};

export const EVAL_CATEGORY_LABEL: Record<string, string> = {
  emergency: "Emergencies",
  injection: "Prompt injection",
  refund_legal: "Refunds and legal",
  routing: "Request routing",
  multilingual: "Multilingual",
  unknowns: "Unknowns",
  price_promises: "Price promises",
  off_policy: "Off-policy asks",
  kb: "Knowledge base",
  general: "General",
};

export const catLabel = (c: string) => EVAL_CATEGORY_LABEL[c] ?? c.replace(/_/g, " ");

export const MODULE_LABEL: Record<string, string> = {
  profit: "Profit Guard", demand: "Demand Radar", service: "Service Control", concierge: "Guest Concierge",
  voice: "Phone AI", upsell: "Menu & upsell", reviews: "Reviews", guests: "Guests", brief: "Owner Brief",
  finance: "Finance", visibility: "Visibility", amenities: "Amenities", inquiries: "Inquiries", ask: "Ask",
  trust: "Trust", core: "Core",
};

export const moduleLabel = (m: string) => MODULE_LABEL[m] ?? m;

// ─── Dates ───────────────────────────────────────────────────────────────

/** A calendar date "YYYY-MM-DD" (pilot dates, hotel-local days) as "12 Oct 2026".
    Formatted in UTC on purpose: the string is already a local day, and
    shifting it through a timezone could move it by one. */
export function dayLabel(d: string | null | undefined, locale = "en-AE"): string {
  if (!d) return "-";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(d);
  if (!m) return d;
  const dt = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  try {
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(dt);
  } catch {
    return d.slice(0, 10);
  }
}
