/* ═══════════════════════════════════════════════════════════════════════
   Per-viewer guest memory in localStorage, all behind try/catch.

   - guest_ref: the random handle the server records consent against. The
     server generates it on the first mint; the page keeps it so a return
     visit finds the consent already given (and may prewarm).
   - consent: what this guest chose on the ConsentSheet, pinned to the
     wording version shown. A new version asks again.
   - stay token: the 12 h key the context endpoint returns for a verified
     room, so a reload without ?k= stays verified.
   - tracker tokens: request/order tracking tokens per stay (hotel + room),
     so a reload still shows "Your requests".

   None of this is authoritative: the server re-checks consent and the
   room on every call, and a wiped storage just means asking again.
   ═══════════════════════════════════════════════════════════════════════ */

import type { ConsentGrants } from "./api";

const PREFIX = "clefs.stay.";
const TRACK_MAX = 12;
const TRACK_TTL_MS = 36 * 3600 * 1000;

function get(key: string): string | null {
  try {
    return window.localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

function set(key: string, value: string | null): void {
  try {
    if (value === null) window.localStorage.removeItem(PREFIX + key);
    else window.localStorage.setItem(PREFIX + key, value);
  } catch {
    /* private mode or blocked storage: the page works without it */
  }
}

function readJson<T>(key: string): T | null {
  const raw = get(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

// ─── guest_ref ───────────────────────────────────────────────────────────

export const getGuestRef = (slug: string) => get(`ref.${slug}`);
export const setGuestRef = (slug: string, ref: string) => set(`ref.${slug}`, ref);
/** After a withdrawal: the next visit is a new subject that must consent again. */
export const clearGuestRef = (slug: string) => set(`ref.${slug}`, null);

// ─── Consent ─────────────────────────────────────────────────────────────

export interface StoredConsent { version: string; grants: ConsentGrants; at: number }

export function getStoredConsent(slug: string, version: string): StoredConsent | null {
  const c = readJson<StoredConsent>(`consent.${slug}`);
  return c && c.version === version && c.grants ? c : null;
}

export function storeConsent(slug: string, version: string, grants: ConsentGrants): void {
  set(`consent.${slug}`, JSON.stringify({ version, grants, at: Date.now() }));
}

export function clearConsent(slug: string): void {
  set(`consent.${slug}`, null);
}

/** Voice (and so a silent prewarm) needs these three already granted. */
export function consentAllowsVoice(c: StoredConsent | null): boolean {
  return !!c && c.grants.ai_processing && c.grants.transfer_abroad && c.grants.recording;
}

export function consentAllowsText(c: StoredConsent | null): boolean {
  return !!c && c.grants.ai_processing && c.grants.transfer_abroad;
}

// ─── Stay token ──────────────────────────────────────────────────────────

const stayKey = (slug: string, target: string) => `st.${slug}.${target}`;
export const getStayToken = (slug: string, target: string) => get(stayKey(slug, target));
export const setStayToken = (slug: string, target: string, token: string | null) => set(stayKey(slug, target), token);

// ─── Tracker tokens ──────────────────────────────────────────────────────

export interface TrackedItem { token: string; kind: "request" | "order"; at: number; done?: boolean }

const trackKey = (slug: string, target: string) => `track.${slug}.${target}`;

export function getTracked(slug: string, target: string): TrackedItem[] {
  const list = readJson<TrackedItem[]>(trackKey(slug, target)) || [];
  const cutoff = Date.now() - TRACK_TTL_MS;
  return list.filter((i) => i && typeof i.token === "string" && i.at > cutoff);
}

export function saveTracked(slug: string, target: string, list: TrackedItem[]): void {
  set(trackKey(slug, target), JSON.stringify(list.slice(-TRACK_MAX)));
}
