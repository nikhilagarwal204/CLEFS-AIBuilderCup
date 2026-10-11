/* ═══════════════════════════════════════════════════════════════════════
   Concierge console helpers: API and console paths, labels, draft <->
   entry conversion and the client-side transcript gate. No React state, so
   every page imports it without pulling in another page's code.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, hotelPath } from "../../../../lib/api";
import type { Tone } from "../../../../ui";
import type { CallStatus, KbEntry, KbEntryDraft, KbKind, KbStatus, PbxKind } from "./types";

// ─── Paths ───────────────────────────────────────────────────────────────

/** Concierge console API under /hotels/{slug}/concierge. */
export const ccApi = (slug: string, p = "") => hotelPath(slug, `/concierge${p}`);

/** Voice console API under /hotels/{slug}/voice (the voice module's router). */
export const vcApi = (slug: string, p = "") => hotelPath(slug, `/voice${p}`);

/** Console route under /app/{slug}/concierge. */
export const ccHref = (slug: string, p = "") => `/app/${encodeURIComponent(slug)}/concierge${p}`;

// ─── Permissions ─────────────────────────────────────────────────────────
// The server is the authority (a 403 still shows as an error); the client
// only hides what would fail. concierge.manage is owner + gm, which is also
// the "GM only" gate the spec puts on the prompt preview.

export const PERM = {
  view: "concierge.view",
  manage: "concierge.manage",
  voiceView: "voice.view",
  voiceManage: "voice.manage",
  settings: "core.settings.manage",
} as const;

// ─── Errors ──────────────────────────────────────────────────────────────

/** A human sentence for a failed call. Known codes get plain wording; the
    server's own detail is used for the rest (it is already written for
    people: "front_desk_uri must be a sip: or sips: URI (never tel:)"). */
export function errText(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.code) {
      case "network_error": return "Could not reach Clefs. Check the connection and try again.";
      case "kb_entry_not_pending": return "Only answers waiting for approval can be rejected.";
      case "no_legacy_text": return "This hotel has no old free-text knowledge to import.";
      case "kb_import_not_done": return "The import is still running. Approve it once it finishes.";
      case "source_url_required": return "A local place needs a source link, so guests are only told about places you can vouch for.";
      case "answer_required": return "Write the English answer first.";
    }
    if (err.status === 403) return "Your role cannot do this. Ask the owner or GM.";
    return err.message || `Request failed (${err.status})`;
  }
  return (err as Error)?.message || "Something went wrong.";
}

/** True when a route is not deployed yet (another lane still building it). */
export function isMissingRoute(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405) && !err.code;
}

// ─── Labels ──────────────────────────────────────────────────────────────

export const KIND_LABEL: Record<KbKind, string> = {
  fact: "Fact",
  faq: "Question and answer",
  policy: "Policy",
  local_place: "Local place",
  venue_note: "Venue note",
};

export const KIND_OPTIONS = (Object.keys(KIND_LABEL) as KbKind[]).map(k => ({ value: k, label: KIND_LABEL[k] }));

export const STATUS_TONE: Record<KbStatus, Tone> = { pending: "warn", approved: "ok", rejected: "bad", archived: "neutral" };
export const STATUS_LABEL: Record<KbStatus, string> = {
  pending: "Waiting for approval",
  approved: "Live for guests",
  rejected: "Rejected",
  archived: "Withdrawn",
};

export const FACT_KEYS: readonly { value: string; label: string; hint: string }[] = [
  { value: "checkin_time", label: "Check-in time", hint: "HH:MM, for example 15:00" },
  { value: "checkout_time", label: "Check-out time", hint: "HH:MM, for example 12:00" },
  { value: "parking", label: "Parking", hint: "free, paid, valet or none" },
  { value: "pool", label: "Pool", hint: "yes or no" },
  { value: "breakfast", label: "Breakfast", hint: "included, paid or none" },
  { value: "star_rating", label: "Star rating", hint: "1 to 5" },
  { value: "pets", label: "Pets", hint: "yes or no" },
  { value: "wifi", label: "Wi-Fi", hint: "free or paid" },
];

/** The guest UI languages (the server's concierge GUEST_LANGUAGES), in order. */
export const LANGUAGE_NAMES: Record<string, string> = {
  en: "English", ar: "Arabic", ru: "Russian", hi: "Hindi", ur: "Urdu", zh: "Chinese", de: "German", fr: "French",
  ja: "Japanese", ko: "Korean", th: "Thai",
};
export const GUEST_LANGUAGES = Object.keys(LANGUAGE_NAMES);

export const PBX_LABEL: Record<PbxKind, string> = {
  grandstream_ucm: "Grandstream UCM",
  "3cx": "3CX",
  asterisk: "Asterisk / FreePBX",
  freeswitch: "FreeSWITCH",
  fxo_gateway: "Analog FXO gateway (HT813, GXW410x)",
  bridge: "SIP bridge (adds the line header)",
  other: "Other SIP PBX",
};

export const CALL_TONE: Record<CallStatus, Tone> = {
  incoming: "info", accepting: "info", accepted: "info", active: "gold",
  ended: "ok", rejected: "warn", failed: "bad", stale: "neutral",
};

// ─── Entry drafts ────────────────────────────────────────────────────────

export function emptyDraft(sectionId: number | null, kind: KbKind = "faq"): KbEntryDraft {
  return {
    section_id: sectionId, kind, question_en: "", answer_en: "", question_ar: "", answer_ar: "", tags: [],
    fact_key: "", fact_value: "", location_slug: "", source_url: "", source_label: "", valid_from: "", valid_to: "",
  };
}

export function draftFrom(e: KbEntry): KbEntryDraft {
  return {
    section_id: e.section_id, kind: e.kind,
    question_en: e.question_en ?? "", answer_en: e.answer_en ?? "",
    question_ar: e.question_ar ?? "", answer_ar: e.answer_ar ?? "",
    tags: e.tags ?? [], fact_key: e.fact_key ?? "", fact_value: e.fact_value ?? "",
    location_slug: e.location_slug ?? "", source_url: e.source_url ?? "", source_label: e.source_label ?? "",
    valid_from: e.valid_from ?? "", valid_to: e.valid_to ?? "",
  };
}

/** The request body for a draft: blank optional text becomes null so PATCH
    clears it (the server treats a sent null as "clear", an absent key as
    "leave"). fact_value is only sent with a fact_key. */
export function draftBody(d: KbEntryDraft): Record<string, unknown> {
  const opt = (s: string) => (s.trim() ? s.trim() : null);
  return {
    section_id: d.section_id, kind: d.kind,
    question_en: opt(d.question_en), answer_en: d.answer_en.trim(),
    question_ar: opt(d.question_ar), answer_ar: opt(d.answer_ar),
    tags: d.tags, fact_key: opt(d.fact_key), fact_value: d.fact_key ? opt(d.fact_value) : null,
    location_slug: opt(d.location_slug), source_url: opt(d.source_url), source_label: opt(d.source_label),
    valid_from: opt(d.valid_from), valid_to: opt(d.valid_to),
  };
}

/** Which fields changed, for PATCH (so an unchanged approved answer is not
    needlessly bumped back to pending). */
export function draftDiff(before: KbEntryDraft, after: KbEntryDraft): Record<string, unknown> {
  const a = draftBody(before), b = draftBody(after), out: Record<string, unknown> = {};
  for (const k of Object.keys(b)) if (JSON.stringify(a[k]) !== JSON.stringify(b[k])) out[k] = b[k];
  return out;
}

// ─── Transcripts: masked by role ─────────────────────────────────────────
// The server masks (clefs.core.pii, field class "transcript"): full for
// owner, gm and front_desk, own department for dept_head, none for the rest.
// The console repeats the rule so a role the matrix hides never renders a
// message even if an older route returns one (fail closed: unknown = none).

export type TranscriptPolicy = "full" | "none";

export function transcriptPolicy(role: string | null, isAdmin: boolean): TranscriptPolicy {
  if (isAdmin) return "full";
  return role === "owner" || role === "gm" || role === "front_desk" ? "full" : "none";
}

/** Guest-typed text can hold a phone number or an email; staff who see
    transcripts at all see them in full (the matrix row), but the list view
    shortens them so a screen over the shoulder does not show a whole number. */
export function redactContact(text: string): string {
  return text
    .replace(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi, m => `${m.slice(0, 2)}***@${m.split("@")[1]}`)
    .replace(/\+?\d[\d\s-]{7,}\d/g, m => `${m.slice(0, 3)}******${m.slice(-2)}`);
}

export function durationText(s: number | null | undefined): string {
  if (s == null) return "";
  const m = Math.floor(s / 60), r = s % 60;
  return m ? `${m}m ${String(r).padStart(2, "0")}s` : `${r}s`;
}
