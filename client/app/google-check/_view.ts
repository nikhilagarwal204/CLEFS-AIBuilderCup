/**
 * Pure view model for /google-check/r/<token>. Kept free of runtime imports
 * (type imports only) so __tests__/view.test.mjs can transpile and test it
 * with node:test, the same way scripts/check-claims.mjs loads claims.ts.
 *
 * The rule it encodes (04 section 2.4): the SerpApi vendor disclosure renders
 * whenever the parity table or the organic rank is shown, and above them. The
 * component renders parity and rank ONLY from an "unlocked" view, and renders
 * the disclosure from the same view, so one cannot appear without the other.
 */
import type { CheckResult, Failure, ParityRow } from "./_lib";

// ─── Types ───────────────────────────────────────────────────────────────────

export type View =
  | { kind: "loading" }
  | { kind: "failure"; failure: Failure }
  | { kind: "no_match" }
  | { kind: "needs_pick" }
  | { kind: "teaser"; official_present: boolean | null }
  | {
      kind: "unlocked";
      official_present: boolean | null;
      parity: ParityRow[];
      rank: number | null;
      gap_minor: number | null;
      gap_pct: number | null;
      disclosure: string | null;
    };

// ─── View model ──────────────────────────────────────────────────────────────

const DUBAI_DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Dubai", day: "numeric", month: "short", year: "numeric" });

function day(iso: string): string {
  // Stay dates are calendar dates ("2026-10-12"): format them at noon UTC so
  // no timezone shift can move them to the previous day.
  const d = new Date(/^\d{4}-\d{2}-\d{2}$/.test(iso) ? `${iso}T12:00:00Z` : iso);
  return Number.isNaN(d.getTime()) ? iso : DUBAI_DATE.format(d);
}

/** "Google Hotels data via SerpApi, a third-party service; prices indicative; checked {date} for stays {dates}". */
export function disclosureText(checkedAt: string, stays: [string, string][]): string {
  const dates = stays.map(([a, b]) => `${day(a)} to ${day(b)}`).join(" and ");
  return `Google Hotels data via SerpApi, a third-party service; prices indicative; checked ${day(checkedAt)}${dates ? ` for stays ${dates}` : ""}.`;
}

function num(v: unknown): number | null {
  const n = typeof v === "string" ? Number(v) : v;
  return typeof n === "number" && Number.isFinite(n) ? n : null;
}

export function viewOf(result: CheckResult | null, failure: Failure | null): View {
  if (failure) return { kind: "failure", failure };
  if (!result) return { kind: "loading" };
  const official = result.teaser ? Boolean(result.teaser.official_present) : null;
  const u = result.unlocked;
  if (u) {
    const parity = Array.isArray(u.parity) ? u.parity : [];
    const rank = num(u.organic_rank);
    const shown = parity.length > 0 || rank !== null;
    return {
      kind: "unlocked",
      // The teaser answer wins; an older result without one falls back to
      // "an official row is in the table".
      official_present: official ?? (parity.some((p) => p.official) ? true : null),
      parity,
      rank,
      gap_minor: num(u.gap_minor),
      gap_pct: num(u.gap_pct),
      disclosure: shown ? disclosureText(u.checked_at, Array.isArray(u.stay_dates) ? u.stay_dates : []) : null,
    };
  }
  if (result.status === "no_match") return { kind: "no_match" };
  if (result.status === "needs_pick") return { kind: "needs_pick" };
  return { kind: "teaser", official_present: official };
}

/**
 * A token from the URL: opaque, URL-safe, bounded. Lives here, not in _lib.ts,
 * because the server page imports it and _lib.ts pulls in the client API hooks.
 */
export function validToken(t: string): boolean {
  return /^[A-Za-z0-9_-]{8,128}$/.test(t);
}

/** True when parity or rank is on screen; the disclosure must then be too. */
export function showsParityOrRank(v: View): boolean {
  return v.kind === "unlocked" && (v.parity.length > 0 || v.rank !== null);
}
