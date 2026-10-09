/* ═══════════════════════════════════════════════════════════════════════
   /try: the reviewer page's pure helpers. No React and no runtime imports,
   so node --test loads this file directly (see __tests__).

   The server side is server/clefs/core/demo_access.py:
     GET  /public/demo/{slug}             cast, keyed Room 204 link, gate summary
     POST /public/demo/{slug}/staff-app   pairs this browser as Maria's phone
     POST /public/demo/{slug}/console     signs this browser in as the duty manager
   ═══════════════════════════════════════════════════════════════════════ */

/** The demo hotel the page opens (a build may point at another demo slug). */
export const DEMO_HOTEL = (process.env.NEXT_PUBLIC_DEMO_HOTEL || "demo-deira-crest").trim();

export const API = "/api/clefs";

// ─── Payloads ────────────────────────────────────────────────────────────

export interface GateTests {
  state: "passed" | "failed" | "running" | "none";
  passed: number;
  total: number;
  expected: number;
  judge_model: string | null;
  finished_at: string | null;
}

export interface GateShadow {
  state: "done" | "running" | "not_started" | "off";
  day: number;
  days: number;
  started_at: string | null;
  until: string | null;
}

export interface DemoInfo {
  hotel: { slug: string; name: string; city: string | null; assistant_name: string | null; guest_languages: string[];
           refreshed_at: string | null };
  guest: { room: string; floor: string | null; path: string } | null;
  staff: { name: string; first_name: string; department: string | null; floors: string[]; languages: string[];
           pin: string } | null;
  console: { name: string; role: string };
  gate: { tests: GateTests; shadow: GateShadow } | null;
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

// ─── Text ────────────────────────────────────────────────────────────────

/** An absolute link for a QR code or the clipboard. */
export function absoluteUrl(origin: string, path: string): string {
  return `${origin.replace(/\/+$/, "")}${path.startsWith("/") ? path : `/${path}`}`;
}

/** "22/22 safety and accuracy tests passed, judged by Gemini" and friends. */
export function testsLine(t: GateTests | null | undefined): string {
  if (!t) return "The safety and accuracy tests show in the manager console.";
  const n = t.expected || 22;
  switch (t.state) {
    case "passed":
      return `${t.passed}/${n} safety and accuracy tests passed, judged by Gemini`;
    case "running":
      return `The ${n} safety and accuracy tests are running now, judged by Gemini`;
    case "failed":
      return t.total
        ? `${t.passed}/${n} safety and accuracy tests passed: not live until all ${n} pass`
        : `The last test run did not include the ${n} standard tests`;
    default:
      return `The ${n} safety and accuracy tests have not run yet`;
  }
}

/** "Shadow mode: 7 of 7 days done" and friends. */
export function shadowLine(s: GateShadow | null | undefined): string {
  if (!s) return "Shadow mode shows in the manager console.";
  const n = s.days || 7;
  switch (s.state) {
    case "done":
      return `Shadow mode: ${n} of ${n} days done`;
    case "running":
      return `Shadow mode: day ${s.day} of ${n}`;
    case "off":
      return "Shadow mode: no service desk at this hotel";
    default:
      return "Shadow mode starts with the first job";
  }
}

/** "fil" -> "Tagalog": the languages the cast reads, for the cards. */
const LANGUAGE_NAMES: Record<string, string> = {
  fil: "Tagalog", tl: "Tagalog", en: "English", hi: "Hindi", ar: "Arabic", ru: "Russian", ur: "Urdu",
  zh: "Chinese", de: "German", fr: "French",
};

export function languageName(code: string | null | undefined): string {
  const c = (code || "").toLowerCase();
  return LANGUAGE_NAMES[c] ?? LANGUAGE_NAMES[c.slice(0, 2)] ?? (c || "English");
}

/** "floors 1 to 4" from ["1", "2", "3", "4"]. */
export function floorsLine(floors: string[] | null | undefined): string {
  const nums = (floors ?? []).map(f => Number(f)).filter(n => Number.isFinite(n)).sort((a, b) => a - b);
  if (!nums.length) return "every floor";
  if (nums.length === 1) return `floor ${nums[0]}`;
  const run = nums.every((n, i) => i === 0 || n === nums[i - 1] + 1);
  return run ? `floors ${nums[0]} to ${nums[nums.length - 1]}` : `floors ${nums.join(", ")}`;
}

/** What a failed tap says, by the API's error code. */
export function grantError(status: number, code?: string | null): string {
  if (status === 429) return "Too many tries from here. Wait a few minutes and try again.";
  if (status === 404) return "The demo hotel is not set up right now. Try again after the next refresh.";
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
