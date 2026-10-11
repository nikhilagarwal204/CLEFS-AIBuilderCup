/* ═══════════════════════════════════════════════════════════════════════
   Formatting: money, dates, durations, percentages.

   Everything goes through Intl with the HOTEL's currency, timezone and
   locale (useFormat binds them from HotelProvider). The legacy dashboard
   hardcoded "en-IN" and the browser's timezone; a Dubai GM reading on a
   phone set to London time must still see Dubai times, so the timezone is
   always explicit here.

   Money inputs come in two shapes and callers must say which:
   - Decimal strings / numbers in MAJOR units ("1234.50"), the API default.
   - Integer MINOR units (fils) with { minor: true }, as persisted.
   Guessing would silently be off by 100x, so there is no auto-detection.
   ═══════════════════════════════════════════════════════════════════════ */

import { useMemo } from "react";
import { useHotelOptional } from "./hotel";
import type { Timestamp } from "./types";

// ─── Defaults (UAE canon: AED, Asia/Dubai) ───────────────────────────────

export const DEFAULT_CURRENCY = "AED";
export const DEFAULT_TIMEZONE = "Asia/Dubai";
export const DEFAULT_LOCALE = "en-AE";
const EMPTY = "-";

// Intl constructors are slow relative to format(); cache by options key.
const nfCache = new Map<string, Intl.NumberFormat>();
const dfCache = new Map<string, Intl.DateTimeFormat>();

function safeLocale(locale?: string | null): string {
  if (!locale) return DEFAULT_LOCALE;
  try {
    return Intl.getCanonicalLocales(locale)[0] ?? DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

function numberFormat(locale: string, opts: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(opts)}`;
  let f = nfCache.get(key);
  if (!f) {
    try {
      f = new Intl.NumberFormat(locale, opts);
    } catch {
      // Unknown currency code or option: fall back rather than crash a page.
      const rest: Intl.NumberFormatOptions = { ...opts };
      delete rest.currency;
      delete rest.style;
      delete rest.currencyDisplay;
      f = new Intl.NumberFormat(locale, rest);
    }
    nfCache.set(key, f);
  }
  return f;
}

function dateFormat(locale: string, opts: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(opts)}`;
  let f = dfCache.get(key);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat(locale, opts);
    } catch {
      f = new Intl.DateTimeFormat(DEFAULT_LOCALE, { ...opts, timeZone: undefined });
    }
    dfCache.set(key, f);
  }
  return f;
}

// ─── Money ───────────────────────────────────────────────────────────────

// The minor units money is STORED in: server core/money.py MINOR_UNITS (ISO
// 4217), pinned here because a browser's Intl data can disagree with ISO 4217
// (recent ICU gives IDR 0 digits where ISO and the server use 2), and the two
// sides reading one amount in different units is a 100x error. Codes not
// listed fall back to Intl.
const MINOR_UNITS: Readonly<Record<string, number>> = {
  AED: 2, USD: 2, EUR: 2, GBP: 2, INR: 2, SAR: 2, QAR: 2, CNY: 2,
  RUB: 2, CHF: 2, AUD: 2, CAD: 2, SGD: 2, THB: 2, EGP: 2, PKR: 2,
  HKD: 2, TWD: 2, IDR: 2, PHP: 2,
  OMR: 3, KWD: 3, BHD: 3, JOD: 3,
  JPY: 0, KRW: 0, VND: 0,
};

/** ISO 4217 minor-unit digits (AED 2, JPY 0, KWD/BHD/OMR 3). */
export function currencyDigits(currency: string): number {
  const pinned = MINOR_UNITS[(currency || "").trim().toUpperCase()];
  if (pinned !== undefined) return pinned;
  try {
    return new Intl.NumberFormat("en", { style: "currency", currency }).resolvedOptions().maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

/** Shift an integer minor-unit amount to a major-unit decimal string, exactly. */
export function minorToMajor(minor: number | bigint | string, currency: string): string {
  const digits = currencyDigits(currency);
  let s: string;
  if (typeof minor === "bigint") s = minor.toString();
  else if (typeof minor === "number") s = Math.round(minor).toString();
  else s = minor.trim().replace(/\.\d*$/, "");
  const neg = s.startsWith("-");
  if (neg) s = s.slice(1);
  if (digits === 0) return (neg ? "-" : "") + s;
  s = s.padStart(digits + 1, "0");
  return `${neg ? "-" : ""}${s.slice(0, -digits)}.${s.slice(-digits)}`;
}

export interface MoneyOptions {
  /** The amount is integer minor units (fils), as persisted. */
  minor?: boolean;
  /** "AED 1.2M" style, for tiles and axes. */
  compact?: boolean;
  signDisplay?: "auto" | "always" | "exceptZero" | "never";
  currencyDisplay?: "code" | "symbol" | "narrowSymbol" | "name";
  maximumFractionDigits?: number;
  minimumFractionDigits?: number;
}

export function formatMoney(
  amount: number | string | bigint | null | undefined,
  currency: string = DEFAULT_CURRENCY,
  locale: string = DEFAULT_LOCALE,
  opts: MoneyOptions = {},
): string {
  if (amount === null || amount === undefined || amount === "") return EMPTY;
  const major = opts.minor ? minorToMajor(amount, currency) : amount;
  const n = typeof major === "bigint" ? Number(major) : Number(major);
  if (!Number.isFinite(n)) return EMPTY;
  const o: Intl.NumberFormatOptions = {
    style: "currency",
    currency: currency || DEFAULT_CURRENCY,
    currencyDisplay: opts.currencyDisplay ?? "code",
    signDisplay: opts.signDisplay,
  };
  if (opts.compact) {
    o.notation = "compact";
    // One decimal only once a suffix carries it ("AED 1.2K"). Under 1,000
    // there is no suffix, and "AED 768.9" read as a clipped amount (QA
    // OWN-13): round to whole units ("AED 769") instead.
    o.maximumFractionDigits = opts.maximumFractionDigits ?? (Math.abs(n) < 1000 ? 0 : 1);
  } else {
    if (opts.maximumFractionDigits !== undefined) o.maximumFractionDigits = opts.maximumFractionDigits;
    if (opts.minimumFractionDigits !== undefined) o.minimumFractionDigits = opts.minimumFractionDigits;
  }
  return numberFormat(safeLocale(locale), o).format(n);
}

export function formatNumber(value: number | null | undefined, locale: string = DEFAULT_LOCALE, maxDigits = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  return numberFormat(safeLocale(locale), { maximumFractionDigits: maxDigits }).format(value);
}

// ─── Dates and times ─────────────────────────────────────────────────────

export type DateStyle = "short" | "medium" | "long";

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Accepts epoch ms (legacy tables), epoch seconds, ISO strings and Dates. */
export function toDate(ts: Timestamp | Date | null | undefined): Date | null {
  if (ts === null || ts === undefined || ts === "") return null;
  if (ts instanceof Date) return Number.isNaN(ts.getTime()) ? null : ts;
  if (typeof ts === "number") {
    // Epoch seconds are ~1.8e9 today, milliseconds ~1.8e12.
    const d = new Date(ts < 1e11 ? ts * 1000 : ts);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (/^\d+$/.test(ts)) return toDate(Number(ts));
  // A bare YYYY-MM-DD is a calendar day, not an instant: pin it to UTC midnight
  // and format it in UTC (see isCalendarDate) so it never shifts a day.
  const d = new Date(DATE_ONLY.test(ts) ? `${ts}T00:00:00Z` : ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

function isCalendarDate(ts: Timestamp | Date | null | undefined): boolean {
  return typeof ts === "string" && DATE_ONLY.test(ts);
}

const DATE_TIME_OPTS: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" },
  medium: { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" },
  long: { weekday: "long", day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" },
};
const DATE_OPTS: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { day: "numeric", month: "short" },
  medium: { weekday: "short", day: "numeric", month: "short", year: "numeric" },
  long: { weekday: "long", day: "numeric", month: "long", year: "numeric" },
};
const TIME_OPTS: Record<DateStyle, Intl.DateTimeFormatOptions> = {
  short: { hour: "2-digit", minute: "2-digit" },
  medium: { hour: "2-digit", minute: "2-digit" },
  long: { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZoneName: "short" },
};

function fmt(
  ts: Timestamp | Date | null | undefined,
  tz: string | undefined,
  locale: string | undefined,
  opts: Intl.DateTimeFormatOptions,
): string {
  const d = toDate(ts);
  if (!d) return EMPTY;
  const timeZone = isCalendarDate(ts) ? "UTC" : tz || DEFAULT_TIMEZONE;
  return dateFormat(safeLocale(locale), { ...opts, timeZone }).format(d);
}

export function formatDateTime(ts: Timestamp | Date | null | undefined, tz?: string, locale?: string, style: DateStyle = "short"): string {
  return fmt(ts, tz, locale, DATE_TIME_OPTS[style]);
}

export function formatDate(ts: Timestamp | Date | null | undefined, tz?: string, locale?: string, style: DateStyle = "short"): string {
  return fmt(ts, tz, locale, DATE_OPTS[style]);
}

export function formatTime(ts: Timestamp | Date | null | undefined, tz?: string, locale?: string, style: DateStyle = "short"): string {
  return fmt(ts, tz, locale, TIME_OPTS[style]);
}

/** YYYY-MM-DD of an instant in the hotel timezone (the business-calendar key). */
export function localDateKey(ts: Timestamp | Date, tz: string = DEFAULT_TIMEZONE): string {
  const d = toDate(ts);
  if (!d) return "";
  const parts = dateFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(d);
  const get = (t: string) => parts.find(p => p.type === t)?.value ?? "";
  return `${get("year")}-${get("month")}-${get("day")}`;
}

const REL_UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "3 minutes ago", "in 2 hours", "just now". */
export function relativeTime(ts: Timestamp | Date | null | undefined, now: number = Date.now(), locale: string = DEFAULT_LOCALE): string {
  const d = toDate(ts);
  if (!d) return EMPTY;
  const diffS = Math.round((d.getTime() - now) / 1000);
  const abs = Math.abs(diffS);
  if (abs < 45) return "just now";
  let rtf: Intl.RelativeTimeFormat;
  try {
    rtf = new Intl.RelativeTimeFormat(safeLocale(locale), { numeric: "auto" });
  } catch {
    rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
  }
  for (const [unit, secs] of REL_UNITS) {
    if (abs >= secs) return rtf.format(Math.round(diffS / secs), unit);
  }
  return rtf.format(Math.round(diffS / 60), "minute");
}

// ─── Durations and SLA ───────────────────────────────────────────────────

/** 0.5 -> "<1m", 45 -> "45m", 125 -> "2h 5m", 1500 -> "1d 1h". */
export function fmtMins(mins: number | null | undefined): string {
  if (mins === null || mins === undefined || !Number.isFinite(mins)) return EMPTY;
  const neg = mins < 0;
  const m = Math.abs(mins);
  let out: string;
  // Round ONCE to whole minutes, then split with integer division. Rounding
  // each unit separately gave "2h 60m" and "40d 24h" (QA OWN-12); here a
  // remainder can never reach its unit's size, and 59.7 carries to "1h".
  const whole = Math.round(m);
  if (m < 1) out = "<1m";
  else if (whole < 60) out = `${whole}m`;
  else if (whole < 1440) {
    const h = Math.floor(whole / 60);
    const r = whole % 60;
    out = r ? `${h}h ${r}m` : `${h}h`;
  } else {
    const d = Math.floor(whole / 1440);
    const h = Math.floor((whole % 1440) / 60);
    out = h ? `${d}d ${h}h` : `${d}d`;
  }
  return neg ? `-${out}` : out;
}

export type SlaTone = "ok" | "warn" | "bad" | "none";

/** Within 75% of target is comfortable, up to 100% is at risk, beyond is breached. */
export function slaTone(mins: number | null | undefined, targetMins: number): SlaTone {
  if (mins === null || mins === undefined || !Number.isFinite(mins) || !(targetMins > 0)) return "none";
  if (mins <= targetMins * 0.75) return "ok";
  if (mins <= targetMins) return "warn";
  return "bad";
}

/** CSS colour for an elapsed-vs-target figure. Falls back to literals when the
    UI kit's tokens are not on the page (e.g. inside the legacy dashboard). */
export function slaColor(mins: number | null | undefined, targetMins: number): string {
  switch (slaTone(mins, targetMins)) {
    case "ok": return "var(--cx-ok, #34C77B)";
    case "warn": return "var(--cx-warn, #E9A93B)";
    case "bad": return "var(--cx-bad, #E5484D)";
    default: return "var(--clefs-ink-faint, rgba(242,238,230,0.34))";
  }
}

// ─── Percentages ─────────────────────────────────────────────────────────

export interface PctOptions {
  /** Input is a 0..1 ratio rather than 0..100. */
  ratio?: boolean;
  digits?: number;
  /** "+4.2%" for deltas. */
  signed?: boolean;
  locale?: string;
}

export function formatPct(value: number | null | undefined, opts: PctOptions = {}): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY;
  const r = opts.ratio ? value : value / 100;
  return numberFormat(safeLocale(opts.locale), {
    style: "percent",
    maximumFractionDigits: opts.digits ?? 1,
    minimumFractionDigits: 0,
    signDisplay: opts.signed ? "exceptZero" : "auto",
  }).format(r);
}

// ─── Hook bound to the hotel ─────────────────────────────────────────────

export interface Formatter {
  currency: string;
  tz: string;
  locale: string;
  money(amount: number | string | bigint | null | undefined, opts?: MoneyOptions & { currency?: string | null }): string;
  dateTime(ts: Timestamp | Date | null | undefined, style?: DateStyle): string;
  date(ts: Timestamp | Date | null | undefined, style?: DateStyle): string;
  time(ts: Timestamp | Date | null | undefined, style?: DateStyle): string;
  relative(ts: Timestamp | Date | null | undefined): string;
  pct(value: number | null | undefined, opts?: Omit<PctOptions, "locale">): string;
  number(value: number | null | undefined, maxDigits?: number): string;
  mins(mins: number | null | undefined): string;
  dayKey(ts: Timestamp | Date): string;
}

export function makeFormatter(currency: string, tz: string, locale: string): Formatter {
  return {
    currency,
    tz,
    locale,
    money: (amount, opts) => formatMoney(amount, opts?.currency || currency, locale, opts),
    dateTime: (ts, style) => formatDateTime(ts, tz, locale, style),
    date: (ts, style) => formatDate(ts, tz, locale, style),
    time: (ts, style) => formatTime(ts, tz, locale, style),
    relative: ts => relativeTime(ts, Date.now(), locale),
    pct: (value, opts) => formatPct(value, { ...opts, locale }),
    number: (value, maxDigits) => formatNumber(value, locale, maxDigits),
    mins: fmtMins,
    dayKey: ts => localDateKey(ts, tz),
  };
}

/** Formatters bound to the current hotel's currency, timezone and locale. */
export function useFormat(): Formatter {
  const h = useHotelOptional();
  const currency = h?.hotel?.currency || DEFAULT_CURRENCY;
  const tz = h?.hotel?.timezone || DEFAULT_TIMEZONE;
  const locale = h?.hotel?.locale || DEFAULT_LOCALE;
  return useMemo(() => makeFormatter(currency, tz, locale), [currency, tz, locale]);
}
