/* ═══════════════════════════════════════════════════════════════════════
   Hotel-local calendar math for the browser (03 spec 1.8 "today").

   "Today" on a Service screen is the hotel's day: a Dubai night manager on a
   laptop set to London, or a head office in India, must see the same
   midnight as the property. Everything here goes through Intl with the
   hotel's IANA zone, so it is DST-safe without a date library.
   ═══════════════════════════════════════════════════════════════════════ */

import { DEFAULT_TIMEZONE } from "../../../../../lib/format";

interface Parts { y: number; m: number; d: number; h: number; mi: number; s: number }

const fmtCache = new Map<string, Intl.DateTimeFormat>();

function formatter(tz: string): Intl.DateTimeFormat {
  let f = fmtCache.get(tz);
  if (!f) {
    try {
      f = new Intl.DateTimeFormat("en-US", {
        timeZone: tz, hourCycle: "h23",
        year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
    } catch {
      // An unknown zone name from a bad setting: fall back to the default zone.
      f = formatter(DEFAULT_TIMEZONE);
    }
    fmtCache.set(tz, f);
  }
  return f;
}

/** Wall-clock parts of an instant in `tz`. */
export function zonedParts(ts: number, tz: string): Parts {
  const out: Record<string, number> = {};
  for (const p of formatter(tz).formatToParts(new Date(ts))) {
    if (p.type !== "literal") out[p.type] = Number(p.value);
  }
  return { y: out.year, m: out.month, d: out.day, h: out.hour % 24, mi: out.minute, s: out.second };
}

/** Offset of `tz` from UTC at an instant, in ms (Dubai: +4 h). */
function offsetMs(ts: number, tz: string): number {
  const p = zonedParts(ts, tz);
  const asUtc = Date.UTC(p.y, p.m - 1, p.d, p.h, p.mi, p.s);
  return asUtc - Math.floor(ts / 1000) * 1000;
}

/** The instant of hotel-local midnight for a calendar day given as y/m/d
    (`d` may overflow: Date.UTC normalises it, which is how days are added). */
function midnightOf(y: number, m: number, d: number, tz: string): number {
  const guess = Date.UTC(y, m - 1, d);
  // Two passes: the offset at the guess, then at the corrected instant, so a
  // DST change on that day still lands on local 00:00.
  const first = guess - offsetMs(guess, tz);
  return guess - offsetMs(first, tz);
}

/** Hotel-local midnight at or before `ts`, `days` days away (0 = that day). */
export function hotelDayStart(ts: number, tz: string, days = 0): number {
  const p = zonedParts(ts, tz);
  return midnightOf(p.y, p.m, p.d + days, tz);
}

/** Hotel-local hour 0-23 of an instant. */
export function hotelHour(ts: number, tz: string): number {
  return zonedParts(ts, tz).h;
}

/** YYYY-MM-DD in the hotel zone. */
export function hotelDayKey(ts: number, tz: string): string {
  const p = zonedParts(ts, tz);
  return `${p.y}-${String(p.m).padStart(2, "0")}-${String(p.d).padStart(2, "0")}`;
}

/** Monday of the hotel-local week containing `ts`, as YYYY-MM-DD. */
export function hotelWeekStart(ts: number, tz: string): string {
  const p = zonedParts(ts, tz);
  const dow = new Date(Date.UTC(p.y, p.m - 1, p.d)).getUTCDay(); // 0 = Sunday
  const back = (dow + 6) % 7;
  return new Date(Date.UTC(p.y, p.m - 1, p.d - back)).toISOString().slice(0, 10);
}

/** Calendar-day arithmetic on a YYYY-MM-DD key (no zone involved). */
export function addDays(key: string, days: number): string {
  const d = new Date(`${key}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The instant of a hotel-local wall time ("07:00" on 2026-09-30). */
export function hotelInstant(day: string, hhmm: string, tz: string): number {
  const [y, m, d] = day.split("-").map(Number);
  const [hh, mm] = hhmm.split(":").map(Number);
  const guess = Date.UTC(y, m - 1, d, hh || 0, mm || 0);
  const first = guess - offsetMs(guess, tz);
  return guess - offsetMs(first, tz);
}
