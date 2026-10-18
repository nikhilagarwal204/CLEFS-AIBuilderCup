/* ═══════════════════════════════════════════════════════════════════════
   Ask Clefs pure helpers: no React, no runtime imports (only erased type
   imports), so node --test loads this file directly (see __tests__).

   - Value formatting per metric unit. Cells arrive in MAJOR units (money in
     the hotel currency, percent as 0-100), so nothing here divides by a
     currency exponent; that happened server-side in core.metrics.
   - The chart_spec -> chart-kit mapping (ChartSpecRenderer only renders
     what these functions return, so the mapping is testable without a DOM).
   - Time bucket labels (YYYY-MM-DD calendar days, read as dates, never
     shifted through the browser's timezone).
   - Trace lines, log-row answers and starter questions from the catalog.
   ═══════════════════════════════════════════════════════════════════════ */

import type {
  Answer, AnswerTable, AskStatus, CatalogMetric, Cell, ChartSpec, Grain, LogRow, ResultBlock, TableColumn, Trace,
} from "./types";

// ─── Value formatting ────────────────────────────────────────────────────

export interface ValueFormat {
  unit?: string | null;
  currency?: string | null;
  locale?: string;
  /** "12.4k" style, for axis ticks and chart caps. */
  compact?: boolean;
  /** Fraction digits for the whole column (columnDigits), so one column
      never mixes "AED 4,500" with "AED 2,100.50". */
  digits?: number;
}

const EMPTY = "–";

function safeLocale(locale?: string): string {
  if (!locale) return "en";
  try {
    return Intl.NumberFormat.supportedLocalesOf([locale]).length ? locale : "en";
  } catch {
    return "en";
  }
}

/** Numbers that should be integers (counts, nights) keep no decimals; money
    under 10,000 keeps two (an ADR of 412.35 reads right, a month's revenue
    of 1.2M does not need fils); ratios and rates keep up to two. */
function fractionDigits(unit: string | null | undefined, v: number): number {
  switch (unit) {
    case "count":
    case "nights":
      return Number.isInteger(v) ? 0 : 1;
    case "money":
      return Number.isInteger(v) || Math.abs(v) >= 10_000 ? 0 : 2;
    case "percent":
    case "minutes":
      return 1;
    default:
      return 2;
  }
}

/** Minutes as "45 min", "2 h 5 min", "3 d 4 h": response times read this way. */
export function formatMinutes(v: number): string {
  const sign = v < 0 ? "-" : "";
  let m = Math.round(Math.abs(v));
  if (m < 60) return `${sign}${m} min`;
  const d = Math.floor(m / 1440);
  m -= d * 1440;
  const h = Math.floor(m / 60);
  m -= h * 60;
  if (d) return `${sign}${d} d${h ? ` ${h} h` : ""}`;
  return `${sign}${h} h${m ? ` ${m} min` : ""}`;
}

export function formatValue(v: Cell | undefined, f: ValueFormat = {}): string {
  if (v === null || v === undefined || v === "") return EMPTY;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return String(v);
  const locale = safeLocale(f.locale);
  const unit = f.unit ?? null;
  if (unit === "minutes" && !f.compact) return formatMinutes(n);
  const digits = f.compact ? 1 : f.digits ?? fractionDigits(unit, n);
  const base: Intl.NumberFormatOptions = f.compact
    ? { notation: "compact", maximumFractionDigits: digits }
    : { maximumFractionDigits: digits, minimumFractionDigits: unit === "money" && digits === 2 ? 2 : 0 };
  if (unit === "percent") {
    return new Intl.NumberFormat(locale, { ...base, style: "percent" }).format(n / 100);
  }
  if (unit === "money" && f.currency) {
    try {
      return new Intl.NumberFormat(locale, { ...base, style: "currency", currency: f.currency }).format(n);
    } catch {
      return `${f.currency} ${new Intl.NumberFormat(locale, base).format(n)}`;
    }
  }
  const s = new Intl.NumberFormat(locale, base).format(n);
  return unit === "minutes" ? `${s} min` : s;
}

/** One money precision per column: whole amounts, or anything from 10,000
    up, show no fils; smaller amounts with fils show two for every row.
    Other units keep their per-value rule (undefined). */
export function columnDigits(values: readonly (Cell | undefined)[], unit?: string | null): number | undefined {
  if (unit !== "money") return undefined;
  const nums = values.map(v => (typeof v === "number" ? v : Number(v))).filter(Number.isFinite);
  if (!nums.length || nums.every(Number.isInteger)) return 0;
  return Math.max(...nums.map(Math.abs)) >= 10_000 ? 0 : 2;
}

/** A change percentage (already x100, one decimal) as "+4.2%". */
export function formatChange(v: Cell | undefined, locale?: string): string {
  if (v === null || v === undefined || v === "") return EMPTY;
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return EMPTY;
  return new Intl.NumberFormat(safeLocale(locale), {
    style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero",
  }).format(n / 100);
}

// ─── Time buckets ────────────────────────────────────────────────────────

const DAY_RE = /^(\d{4})-(\d{2})-(\d{2})/;

/** A YYYY-MM-DD bucket as UTC-midnight epoch ms (a calendar day, not an
    instant: formatting it in UTC keeps it from shifting a day). */
export function bucketMs(bucket: string): number {
  const m = DAY_RE.exec(bucket);
  return m ? Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : NaN;
}

const BUCKET_OPTS: Record<Exclude<Grain, "total">, Intl.DateTimeFormatOptions> = {
  day: { day: "numeric", month: "short" },
  week: { day: "numeric", month: "short" },
  month: { month: "short", year: "numeric" },
};

/** "3 Sep", "Wk of 1 Sep", "Sep 2026". Anything unparseable passes through. */
export function bucketLabel(bucket: Cell | undefined, grain: Grain | string | null | undefined, locale?: string): string {
  if (bucket === null || bucket === undefined) return EMPTY;
  const ms = typeof bucket === "number" ? bucket : bucketMs(String(bucket));
  if (!Number.isFinite(ms)) return String(bucket);
  const g = grain === "week" || grain === "month" ? grain : "day";
  const s = new Intl.DateTimeFormat(safeLocale(locale), { ...BUCKET_OPTS[g], timeZone: "UTC" }).format(new Date(ms));
  return g === "week" ? `Wk of ${s}` : s;
}

// ─── Scaling for the chart kit ───────────────────────────────────────────
/* ColumnChart and HBarChart print raw numbers on caps and ticks ("{total}
   {unit}"), so a revenue of 123456.78 would read as-is. Values are scaled
   to thousands or millions and rounded before they reach the kit, and the
   card hint names the scale ("AED, thousands"). */

export interface KitScale {
  factor: number;
  /** Appended to each cap label by the kit. */
  suffix: string;
  /** Plain words for the chart hint, e.g. "AED, thousands". */
  hint: string | null;
}

export function kitScale(values: readonly number[], unit?: string | null, currency?: string | null): KitScale {
  const max = Math.max(0, ...values.filter(Number.isFinite).map(Math.abs));
  const money = unit === "money" && currency ? currency : null;
  if (unit === "percent") return { factor: 1, suffix: "%", hint: "percent" };
  if (max >= 1e6) return { factor: 1e6, suffix: "M", hint: money ? `${money}, millions` : "millions" };
  if (max >= 1e4) return { factor: 1e3, suffix: "k", hint: money ? `${money}, thousands` : "thousands" };
  return { factor: 1, suffix: "", hint: money ?? (unit === "minutes" ? "minutes" : null) };
}

/** Scaled and rounded to one decimal, so float noise never reaches a label. */
export function scaled(v: number, s: KitScale): number {
  return Math.round((v / s.factor) * 10) / 10;
}

function num(v: unknown): number | null {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
}

// ─── chart_spec -> chart kit ─────────────────────────────────────────────
/* The server picks the chart type (present.chart_spec); this maps it onto
   the kit's props. Two honest-chart rules the kit cannot enforce itself:
   - ColumnChart and HBarChart STACK series, so value and compare_value
     would read as one summed bar. A breakdown with a comparison plots the
     current value only (comparedHidden) and the table carries the change.
   - Both skip values <= 0, so a breakdown with a negative figure (a net
     loss by channel) goes to DivergingBar instead of silently dropping it. */

export interface KitSeries { key: string; label: string; dashed?: boolean }

export type ChartModel =
  | { kind: "kpi"; label: string; value: number | null; compare: number | null; change: number | null }
  | { kind: "column"; data: { label: string; tick: string; values: number[] }[]; series: KitSeries[];
      scale: KitScale; comparedHidden: boolean }
  | { kind: "hbar"; rows: { label: string; values: number[] }[]; series: KitSeries[]; scale: KitScale;
      comparedHidden: boolean }
  | { kind: "diverging"; rows: { label: string; value: number }[] }
  | { kind: "line"; series: (KitSeries & { points: { x: number; y: number | null }[] })[] }
  | { kind: "none" };

const TICK_CHARS = 10;

/** Column ticks fit about ten characters (the server's own hbar cut-over). */
export function tickLabel(s: string): string {
  return s.length > TICK_CHARS ? `${s.slice(0, TICK_CHARS - 1)}…` : s;
}

function xLabel(x: unknown, spec: ChartSpec, grain: Grain | string | null | undefined, locale?: string): string {
  if (x === null || x === undefined || x === "") return "(none)";
  return spec.x === "bucket" ? bucketLabel(x as string, grain, locale) : String(x);
}

export function chartModel(spec: ChartSpec | null | undefined, grain?: Grain | string | null, locale?: string): ChartModel {
  if (!spec || !Array.isArray(spec.data) || !spec.data.length) return { kind: "none" };
  const label = spec.label || spec.series[0]?.label || "Value";

  if (spec.type === "kpi") {
    const d = spec.data[0];
    return { kind: "kpi", label, value: num(d.value), compare: num(d.compare_value), change: num(d.change_pct) };
  }

  if (spec.type === "line") {
    const series = spec.series.map(s => ({
      key: s.key,
      label: s.key === "compare_value" ? "Comparison" : s.label || label,
      dashed: s.key === "compare_value",
      points: spec.data
        .map(d => ({ x: bucketMs(String(d.x ?? "")), y: num(d[s.key]) }))
        .filter(p => Number.isFinite(p.x)),
    }));
    return series.some(s => s.points.some(p => p.y !== null)) ? { kind: "line", series } : { kind: "none" };
  }

  // Breakdown (column or hbar): the first series is the current value.
  const primary = spec.series[0]?.key ?? "value";
  const comparedHidden = spec.series.length > 1;
  const pts = spec.data.map(d => ({ label: xLabel(d.x, spec, grain, locale), value: num(d[primary]) }));
  if (!pts.some(p => p.value !== null && p.value !== 0)) return { kind: "none" };
  if (pts.some(p => (p.value ?? 0) < 0)) {
    return { kind: "diverging", rows: pts.map(p => ({ label: p.label, value: p.value ?? 0 })) };
  }
  const scale = kitScale(pts.map(p => p.value ?? 0), spec.unit, spec.currency);
  const series: KitSeries[] = [{ key: primary, label }];
  if (spec.type === "column") {
    return {
      kind: "column", series, scale, comparedHidden,
      data: pts.map(p => ({ label: p.label, tick: tickLabel(p.label), values: [scaled(p.value ?? 0, scale)] })),
    };
  }
  return {
    kind: "hbar", series, scale, comparedHidden,
    rows: pts.map(p => ({ label: p.label, values: [scaled(p.value ?? 0, scale)] })),
  };
}

// ─── Table cells ─────────────────────────────────────────────────────────

/** One table cell as text: time buckets as dates, values in their unit,
    change as a signed percentage, dimension labels as given. */
export function cellText(
  col: TableColumn, cell: Cell | undefined, grain?: Grain | string | null, locale?: string, digits?: number,
): string {
  switch (col.kind) {
    case "time":
      return bucketLabel(cell ?? null, grain, locale);
    case "change":
      return formatChange(cell, locale);
    case "value":
    case "compare":
      return formatValue(cell, { unit: col.unit, currency: col.currency, locale, digits });
    default:
      return cell === null || cell === undefined || cell === "" ? "(none)" : String(cell);
  }
}

/** Raw values (not formatted), so a spreadsheet can sum them. Quotes every
    field that needs it and neutralises a leading =, +, - or @ so a hotel's
    own dimension labels can never run as a formula (CSV injection). */
export function tableToCsv(t: AnswerTable): string {
  const esc = (v: Cell | undefined): string => {
    if (v === null || v === undefined) return "";
    let s = String(v);
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = `'${s}`;
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const head = t.columns.map(c => {
    const unit = c.kind === "value" || c.kind === "compare"
      ? (c.unit === "money" && c.currency ? ` (${c.currency})` : c.unit === "percent" ? " (%)" : "")
      : c.kind === "change" ? " (%)" : "";
    return esc(`${c.label}${unit}`);
  });
  return [head.join(","), ...t.rows.map(r => r.map(esc).join(","))].join("\r\n");
}

/** Change direction and whether it is good news, for the KPI delta chip. */
export function changeTone(change: number | null, higherIsBetter: boolean | null | undefined): "good" | "bad" | "flat" | "neutral" {
  if (change === null || !Number.isFinite(change)) return "neutral";
  if (change === 0) return "flat";
  if (higherIsBetter === null || higherIsBetter === undefined) return "neutral";
  return (change > 0) === higherIsBetter ? "good" : "bad";
}

// ─── Status and reasons ──────────────────────────────────────────────────

export type StatusTone = "neutral" | "gold" | "ok" | "warn" | "bad" | "info";

export function statusMeta(status: AskStatus | string | null | undefined): { label: string; tone: StatusTone } {
  switch (status) {
    case "answered": return { label: "Answered", tone: "ok" };
    case "table_only": return { label: "Figures only", tone: "info" };
    case "unsupported": return { label: "Not something I can answer", tone: "warn" };
    case "refused": return { label: "Declined", tone: "warn" };
    case "error": return { label: "Could not answer", tone: "bad" };
    default: return { label: "Answer", tone: "neutral" };
  }
}

const REASONS: Record<string, string> = {
  guest_pii: "Ask Clefs never looks up a single guest's details. Ask about totals, trends or segments instead.",
  forecast_others: "Ask Clefs does not predict what other hotels will do. It answers from your own figures.",
  too_long: "That question is too long. Keep it under 500 characters.",
  empty: "Type a question first.",
  model_unsupported: "None of your metrics answers that yet.",
  no_metrics: "Your role has no metrics to ask about here yet.",
  ungrounded: "The written answer could not be checked against the figures, so only the figures are shown.",
  ai_disabled: "The AI is switched off for this hotel. Saved views still run.",
  ai_error: "The AI did not respond. Try again in a moment.",
  no_tool_call: "I could not turn that into a query. Try naming the metric and the period.",
  tool_call_budget: "That needed more than three lookups. Try asking one thing at a time.",
};

export function reasonText(reason: string | null | undefined): string | null {
  return reason ? REASONS[reason] ?? null : null;
}

// ─── "How this was computed" ─────────────────────────────────────────────

const SORTS: Record<string, string> = {
  value_desc: "Highest first",
  value_asc: "Lowest first",
  label_asc: "A to Z",
  time_asc: "Oldest first",
};

const GRAINS: Record<string, string> = { total: "One total", day: "By day", week: "By week", month: "By month" };

export function compareText(c: string | null | undefined): string | null {
  if (c === "prev_period") return "Previous period of the same length";
  if (c === "prev_year") return "Same period last year";
  return null;
}

export interface TraceLine { label: string; value: string; mono?: boolean }

/** The trace as plain labelled lines, in the order an owner reads a query:
    what, when, sliced how, filtered how, compared with what, then limits.
    `labels` maps metric and dimension keys to their catalog labels. */
export function traceLines(t: Trace, labels: Record<string, string> = {}): TraceLine[] {
  const name = (k: string) => labels[k] ?? k;
  const out: TraceLine[] = [{ label: "Metric", value: `${t.metric_label} (${t.metric})` }];
  if (t.derived) {
    const scale = t.derived.scale && t.derived.scale !== 1 ? ` x ${t.derived.scale}` : "";
    out.push({ label: "Formula", value: `${name(t.derived.numerator)} / ${name(t.derived.denominator)}${scale}` });
  }
  const p = t.period;
  out.push({ label: "Period", value: p.from === p.to ? `${p.label} (${p.from})` : `${p.label} (${p.from} to ${p.to})` });
  out.push({ label: "Grouping", value: [GRAINS[t.grain] ?? t.grain, ...t.dims.map(d => `by ${name(d)}`)].join(", ") });
  out.push({
    label: "Filters",
    value: t.filters.length ? t.filters.map(f => `${name(f.field)} is ${f.values.join(" or ")}`).join("; ") : "None",
  });
  const cmp = compareText(t.compare);
  if (cmp) out.push({ label: "Compared with", value: cmp });
  out.push({ label: "Order", value: `${SORTS[t.sort] ?? t.sort}, first ${t.limit} rows` });
  if (t.row_count !== null && t.row_count !== undefined) {
    // row_count is before the limit (ask.execute sorts, counts, then cuts).
    const shown = t.row_count > t.limit ? `${t.row_count} matched, first ${t.limit} shown` : String(t.row_count);
    out.push({ label: "Rows", value: `${shown}${t.truncated ? " (cut at the 500-row cap)" : ""}` });
  }
  out.push({ label: "Source", value: `${t.source}, read-only, your hotel only`, mono: true });
  return out;
}

// ─── Answers ─────────────────────────────────────────────────────────────

/** Every result block of an answer (up to three queries per question). Old
    rows without `results` fall back to the top-level block. */
export function resultsOf(a: Pick<Answer, "results" | "table" | "chart_spec" | "trace" | "query">): ResultBlock[] {
  if (Array.isArray(a.results) && a.results.length) return a.results;
  if (a.table || a.chart_spec || a.trace) {
    return [{ table: a.table ?? null, chart_spec: a.chart_spec ?? null, trace: a.trace ?? null, query: a.query ?? null }];
  }
  return [];
}

/** A stored log row (GET /ask/logs/{id}, the WhatsApp deep link) as an
    Answer the card can render. The log row's own columns win over the
    stored answer body, which omits log_id. */
export function answerFromLog(log: LogRow): Answer {
  const a = log.answer ?? {};
  return {
    table: a.table ?? null,
    chart_spec: a.chart_spec ?? null,
    trace: a.trace ?? null,
    query: a.query ?? null,
    results: a.results ?? [],
    answer_text: a.answer_text ?? null,
    suggestions: a.suggestions ?? [],
    ungrounded: a.ungrounded,
    log_id: log.id,
    question: log.question,
    status: log.status,
    unsupported: log.unsupported,
    reason: log.reason,
    grounded: log.grounded,
    regenerated: log.regenerated,
    latency_ms: log.latency_ms ?? undefined,
    tokens: log.tokens,
  };
}

/** A saved-view title from the question: trimmed, one line, 120 chars max
    (the server's limit), without a trailing question mark. */
export function viewTitle(question: string): string {
  const one = question.replace(/\s+/g, " ").trim().replace(/[?？]+$/, "");
  return one.length > 120 ? `${one.slice(0, 119)}…` : one || "Saved view";
}

// ─── Starter questions ───────────────────────────────────────────────────
/* Built from the viewer's own catalog, so every suggestion is one their
   role can actually be answered (a dept_head never sees a revenue prompt
   that would come back refused). Order follows what owners ask first. */

const STARTERS: readonly { metric: string; q: (m: CatalogMetric) => string }[] = [
  { metric: "ask.revpar", q: () => "RevPAR this month vs last year" },
  { metric: "ask.occupancy", q: () => "Occupancy by week for the next 30 days" },
  { metric: "ask.adr", q: m => (m.dimensions.some(d => d.key === "channel") ? "ADR by channel last month" : "ADR last month") },
];

export function starterQuestions(catalog: readonly CatalogMetric[] | undefined, max = 4): string[] {
  if (!catalog?.length) return [];
  const byKey = new Map(catalog.map(m => [m.key, m]));
  const out: string[] = [];
  for (const s of STARTERS) {
    const m = byKey.get(s.metric);
    if (m) out.push(s.q(m));
  }
  for (const m of catalog) {
    if (out.length >= max) break;
    if (m.derived || STARTERS.some(s => s.metric === m.key)) continue;
    const dim = m.dimensions[0];
    out.push(dim ? `${m.label} by ${dim.label.toLowerCase()} this month` : `${m.label} month to date`);
  }
  return out.slice(0, max);
}

/** Metric and dimension keys -> labels, for the trace disclosure. */
export function catalogLabels(catalog: readonly CatalogMetric[] | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const m of catalog ?? []) {
    out[m.key] = m.label;
    for (const d of m.dimensions) out[d.key] ??= d.label;
  }
  return out;
}
