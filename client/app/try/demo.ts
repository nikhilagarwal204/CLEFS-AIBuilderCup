/* ═══════════════════════════════════════════════════════════════════════
   The "Try it yourself" section's pure helpers (TryDemo.tsx, on the site
   root). No React and no runtime imports, so node --test loads this file
   directly (see __tests__).

   The server side is server/clefs/core/demo_access.py:
     GET  /public/demo/{slug}             the cast and the Room 204 link
     POST /public/demo/{slug}/staff-app   opens Maria's phone in this browser
     POST /public/demo/{slug}/console     signs this browser in as the duty manager
   ═══════════════════════════════════════════════════════════════════════ */

/** The demo hotel the page opens: Hotel Aozora Shinjuku, Tokyo (a build may
    point at another demo slug with NEXT_PUBLIC_DEMO_HOTEL). */
export const DEMO_HOTEL = (process.env.NEXT_PUBLIC_DEMO_HOTEL || "aozora-shinjuku").trim();

export const API = "/api/clefs";

// ─── Payloads ────────────────────────────────────────────────────────────

export interface DemoInfo {
  hotel: { slug: string; name: string; city: string | null; assistant_name: string | null; guest_languages: string[];
           refreshed_at: string | null };
  guest: { room: string; floor: string | null; path: string } | null;
  staff: { name: string; first_name: string; department: string | null; floors: string[]; languages: string[];
           pin: string } | null;
  console: { name: string; role: string };
  /** The go-live gate in brief. The landing does not show it: the story
      states how every hotel goes live, in plain words, instead. */
  gate?: unknown;
}

export interface ShiftSession {
  session_id: number;
  staff: { id: number; name: string; department: string | null };
  planned_end_at: string | number | null;
}

export interface StaffAppGrant {
  hotel_slug: string;
  device: { id: number; name: string; kind: string; floors: string[]; departments: string[] };
  session: ShiftSession;
  path: string;
  pin: string;
}

export interface ConsoleGrant {
  hotel_slug: string;
  path: string;
  role: string;
  user: { id: number; name: string; email: string };
  expires_at: string;
}

// ─── The cast ────────────────────────────────────────────────────────────

/** The deck's cast at the Tokyo demo hotel, shown until the server answers
    (and if it cannot), so the doors always read as whole sentences. */
export const DECK_CAST = {
  hotel: "Hotel Aozora Shinjuku",
  city: "Tokyo",
  room: "204",
  staffFirst: "Maria",
  staffLanguage: "fil",
  pin: "2046",
  manager: "Yuki Sato",
} as const;

export interface Cast {
  hotel: string;
  city: string;
  room: string;
  staffFirst: string;
  /** The language the attendant reads, by name ("Tagalog"). */
  staffLanguage: string;
  pin: string;
  manager: string;
}

/** What the three doors say: the server's live cast, else the deck's. */
export function castOf(info: DemoInfo | null | undefined): Cast {
  return {
    hotel: info?.hotel?.name || DECK_CAST.hotel,
    city: info?.hotel?.city || DECK_CAST.city,
    room: info?.guest?.room || DECK_CAST.room,
    staffFirst: info?.staff?.first_name || DECK_CAST.staffFirst,
    staffLanguage: languageName(info?.staff?.languages?.[0] || DECK_CAST.staffLanguage),
    pin: info?.staff?.pin || DECK_CAST.pin,
    manager: info?.console?.name || DECK_CAST.manager,
  };
}

// ─── Text ────────────────────────────────────────────────────────────────

/** An absolute link for a QR code or the clipboard. */
export function absoluteUrl(origin: string, path: string): string {
  return `${origin.replace(/\/+$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

/** "fil" -> "Tagalog": the language the cast reads, for the cards. */
const LANGUAGE_NAMES: Record<string, string> = {
  fil: "Tagalog", tl: "Tagalog", en: "English", ja: "Japanese", ko: "Korean", th: "Thai", zh: "Chinese",
  hi: "Hindi", ar: "Arabic", ru: "Russian", ur: "Urdu", de: "German", fr: "French",
};

export function languageName(code: string | null | undefined): string {
  const c = (code || "").toLowerCase();
  return LANGUAGE_NAMES[c] ?? LANGUAGE_NAMES[c.slice(0, 2)] ?? (c || "English");
}

/** What a failed tap says, by the API's error code: plain words, no codes. */
export function grantError(status: number, code?: string | null): string {
  if (status === 429) return "Too many tries from here. Wait a few minutes and try again.";
  if (status === 404) return "The demo hotel is not available right now. Please try again in a few minutes.";
  if (code === "csrf_failed") return "Open this page on the Clefs site itself and try again.";
  if (status === 0) return "No connection. Check your network and try again.";
  return "That did not work. Try again in a moment.";
}

// ─── The staff app's stored state ────────────────────────────────────────
// The device app (app/device) keeps the paired device and the open PIN
// sessions in IndexedDB under these keys (DeviceApp devKey / sessKey).
// Writing them right after the pairing means the app opens on Maria's
// queue (handover already acknowledged) instead of asking to pair again,
// even when a console session in the same browser masks the device.

export function deviceKeys(slug: string): { device: string; sessions: string } {
  return { device: `device:${slug}`, sessions: `sessions:${slug}` };
}

export function storedSessions(session: ShiftSession, now: number): Record<string, ShiftSession & { started_at: number; handover_acked: boolean }> {
  return { [String(session.staff.id)]: { ...session, started_at: now, handover_acked: true } };
}
