/* Reviews console helpers: labels, error text and the clipboard. Pure
   functions only, so every tab formats a platform or a stage the same way. */

import { ApiError } from "../../../../lib/api";
import type { Tone } from "../../../../ui";
import type { Reply, ReplyStatus, Review, Severity, SourceKind } from "./types";

// ─── Labels ──────────────────────────────────────────────────────────────

const PLATFORM_LABELS: Record<string, string> = {
  google: "Google", tripadvisor: "Tripadvisor", booking: "Booking.com", agoda: "Agoda",
  expedia: "Expedia", email: "Email", generic: "Other",
};

export function platformLabel(p: string | null | undefined): string {
  if (!p) return "Unknown";
  return PLATFORM_LABELS[p] ?? p.charAt(0).toUpperCase() + p.slice(1);
}

export const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  gbp: "Google Business Profile",
  serpapi_google: "Google Maps (via SerpApi)",
  tripadvisor: "Tripadvisor",
  csv: "CSV import",
  email: "Forwarded emails",
};

export const REPLY_STATUS: Record<ReplyStatus, { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "gold" },
  approved: { label: "Approved", tone: "info" },
  copied: { label: "Copied", tone: "ok" },
  posted: { label: "Posted", tone: "ok" },
  discarded: { label: "Discarded", tone: "neutral" },
};

export function severityTone(s: Severity | null | undefined): Tone {
  if (s === "high") return "bad";
  if (s === "medium") return "warn";
  return "neutral";
}

/** "4.5/5" style score, or the raw /10 score for platforms that use it. */
export function ratingText(r: Pick<Review, "rating" | "rating_scale" | "rating_norm_5">): string {
  if (r.rating === null || r.rating === undefined) return "No score";
  const scale = r.rating_scale ?? 5;
  const v = Number.isInteger(r.rating) ? String(r.rating) : r.rating.toFixed(1);
  return `${v}/${scale}`;
}

/** Stars on the /5 scale, rounded to the nearest half-free integer for display. */
export function starCount(r: Pick<Review, "rating_norm_5">): number {
  const v = r.rating_norm_5;
  if (v === null || v === undefined) return 0;
  return Math.max(0, Math.min(5, Math.round(v)));
}

/** True when the review still wants a reply from the hotel. */
export function isUnanswered(r: Review): boolean {
  return !r.has_owner_response && r.reply_status !== "posted" && r.reply_status !== "copied";
}

/** A reply the team can still act on (edit, approve, post, copy). */
export function replyOpen(r: Reply | null | undefined): boolean {
  return !!r && (r.status === "draft" || r.status === "approved");
}

// ─── Errors ──────────────────────────────────────────────────────────────

/** Friendly copy for the reviews error codes the server documents; anything
 *  else falls back to the server's own message. */
const CODE_TEXT: Record<string, string> = {
  reply_policy: "This reply breaks a reply rule (length, dash, liability or compensation wording). Edit it first.",
  not_postable: "This reply cannot be posted from here. Connect Google Business Profile, or copy it and post by hand.",
  illegal_transition: "This reply was already copied or posted, so it can no longer change.",
  vendor_error: "Google did not accept the reply. Try again in a minute, or copy it and post by hand.",
  too_many_requests: "Sync was run a lot this hour. Try again later.",
  owner_only: "Only the owner can turn on automatic replies.",
  consent_needs_gbp: "Automatic replies need a connected Google Business Profile source.",
  auto_reply_consent_required: "Automatic replies need the owner's consent on a Google source first.",
  source_exists: "That source is already connected.",
  invalid_config: "Some of those details are not valid.",
  not_configured: "This needs a Google API key on the server. Ask Clefs support to add it.",
  invalid_place_id: "One of the place ids is not valid.",
  csv_missing: "Choose a file or paste CSV text first.",
  csv_columns_missing: "The file is missing columns the preset needs.",
};

export function errText(err: unknown): string {
  if (err instanceof ApiError && err.code && CODE_TEXT[err.code]) return CODE_TEXT[err.code];
  return err instanceof Error ? err.message : String(err);
}

// ─── Clipboard ───────────────────────────────────────────────────────────

/** Copy text; falls back to a hidden textarea where the async clipboard API
 *  is blocked (http origins, some embedded webviews on the front desk PC). */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch { /* fall through to the legacy path */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
