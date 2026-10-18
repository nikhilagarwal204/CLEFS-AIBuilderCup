/**
 * Google free-booking-link and parity check: client side of the public API in
 * 04 section 5.9 (visibility lane, wave 2). Shapes are copied from that table;
 * until the server lands every call degrades to an "unavailable" state
 * instead of throwing at the visitor.
 *
 *   POST /public/visibility/google-check            {hotel_name, city, consent_processing, consent_transfer_abroad}
 *   POST /public/visibility/google-check/{t}/pick   {property_token}
 *   POST /public/visibility/google-check/{t}/lead   {name, email?, whatsapp?, consent_contact_email?, consent_contact_whatsapp?}
 *   GET  /public/visibility/google-check/{t}
 *
 * Errors: 422 invalid_input, 429 rate_limited, 503 budget_exhausted, 404
 * not_found (unknown or expired token; also "endpoint not deployed yet").
 */
import { api, ApiError } from "../lib/api";

// ─── Types ───────────────────────────────────────────────────────────────────

export type CheckStatus = "needs_pick" | "ready" | "no_match";

export type Candidate = { property_token: string; name: string; address: string };

export type ParityRow = { source: string; channel: string; price_minor: number; currency: string; official: boolean };

export type Unlocked = {
  parity: ParityRow[];
  gap_minor: number | null;
  gap_pct: number | string | null;
  organic_rank: number | null;
  checked_at: string;
  stay_dates: [string, string][];
  note_claim_ids: string[];
};

export type CheckResult = {
  token: string;
  status?: CheckStatus;
  candidates?: Candidate[];
  teaser?: { official_present: boolean } | null;
  unlocked?: Unlocked | null;
  hotel_name?: string;
  city?: string;
};

export type LeadBody = {
  name: string;
  email?: string | null;
  whatsapp?: string | null;
  consent_contact_email?: boolean;
  consent_contact_whatsapp?: boolean;
};

/** What the page shows when a call fails. */
export type Failure = "unavailable" | "rate_limited" | "not_found" | "invalid";

// ─── Calls ───────────────────────────────────────────────────────────────────

const BASE = "/public/visibility/google-check";
const quiet = { silent401: true } as const;

export function startCheck(hotel_name: string, city: string): Promise<CheckResult> {
  return api.post<CheckResult>(BASE, { hotel_name, city, consent_processing: true, consent_transfer_abroad: true }, quiet);
}

export function pickCandidate(token: string, property_token: string): Promise<CheckResult> {
  return api.post<CheckResult>(`${BASE}/${encodeURIComponent(token)}/pick`, { property_token }, quiet);
}

export function unlock(token: string, body: LeadBody): Promise<CheckResult> {
  return api.post<CheckResult>(`${BASE}/${encodeURIComponent(token)}/lead`, body, quiet);
}

export function getCheck(token: string): Promise<CheckResult> {
  return api.get<CheckResult>(`${BASE}/${encodeURIComponent(token)}`, undefined, quiet);
}

/**
 * Map an error to a page state. `tokenCall` distinguishes a 404 on a token
 * (expired or unknown check) from a 404 on the start call, which only means the
 * endpoint is not deployed yet.
 */
export function failureOf(err: unknown, tokenCall = false): Failure {
  if (!(err instanceof ApiError)) return "unavailable";
  if (err.status === 429 || err.code === "rate_limited") return "rate_limited";
  if (err.status === 422 || err.code === "invalid_input") return "invalid";
  if (err.status === 404 && tokenCall && err.code === "not_found") return "not_found";
  return "unavailable";
}

export const FAILURE_TEXT: Record<Failure, string> = {
  unavailable: "Check unavailable, try again later.",
  rate_limited: "You have reached today's limit, try again tomorrow.",
  not_found: "This check has expired or does not exist. Run a new check.",
  invalid: "Some details are missing. Check the form and try again.",
};

/**
 * The words to show for a failure. WHY: a 422 carries the server's own
 * sentence for the one field that is wrong ("Enter the WhatsApp number with
 * its country code, e.g. +971501234567"); the generic "Some details are
 * missing" hid it and left the visitor guessing (QA PUB-5). Only a plain,
 * short string detail is shown: a pydantic list or a long blob falls back to
 * the generic text.
 */
export function failureMessage(err: unknown, tokenCall = false): string {
  const kind = failureOf(err, tokenCall);
  if (kind === "invalid" && err instanceof ApiError) {
    const d = typeof err.detail === "string" ? err.detail.trim() : "";
    if (d && d.length <= 200 && !/[<{[]/.test(d[0])) return d;
  }
  return FAILURE_TEXT[kind];
}

export const NO_MATCH_TEXT = "We could not find this hotel on Google Hotels; check the name and city.";

