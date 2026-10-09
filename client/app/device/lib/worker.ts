/* ═══════════════════════════════════════════════════════════════════════
   The worker screen's contract with the server (05 spec, section 3, REST
   "Staff app") and the card rules the deck's wireframe needs. Pure, with
   only a type import, so node --test loads it (worker.test.mjs);
   deviceApi.ts wraps these shapes in apiFetch.

   Routes (device cookie + X-Clefs-Staff, under /device/service):
   - GET  /requests[?state=open] the job list; GET /requests/{id} one job.
                                 Each card carries display_title,
                                 display_text and display_language (the
                                 worker's reading language), original_text,
                                 guest_language, needs_proof, proof_status,
                                 proof_reason, eta_minutes, priority, accepted_at
   - GET  /me                    {staff: {first_name, role, department,
                                 reading_language...}, on_duty, counts:
                                 {active, done_today}}
   - POST /requests/{id}/proof   multipart "photo" (JPEG or PNG, max 3 MB)
                                 -> {verified, reason, completed, proof_status, request}
   - POST /requests/{id}/reply   JSON {text?} or {audio_b64?, mime?}
                                 -> {text_original, text_for_guest, language}
   - POST /duty                  JSON {on_duty} (Go Off Duty / Go On Duty)
   ═══════════════════════════════════════════════════════════════════════ */

import type { Card } from "./deviceApi";

// ─── Paths and bodies ────────────────────────────────────────────────────

/** The multipart field the proof route reads. */
export const PROOF_FIELD = "photo";
export const PROOF_FILENAME = "proof.jpg";
export const DUTY_PATH = "/device/service/duty";

function requestPath(requestId: number, leaf: "proof" | "reply"): string {
  if (!Number.isInteger(requestId) || requestId <= 0) throw new Error(`Bad request id: ${String(requestId)}`);
  return `/device/service/requests/${requestId}/${leaf}`;
}

export const proofPath = (requestId: number) => requestPath(requestId, "proof");
export const replyPath = (requestId: number) => requestPath(requestId, "reply");

/** The proof upload: the photo under "photo", plus an idempotency key the
    route may use (FastAPI ignores form fields a route does not declare). */
export function buildProofForm(photo: Blob, opts: { filename?: string; clientEventId?: string } = {}): FormData {
  const form = new FormData();
  form.append(PROOF_FIELD, photo, opts.filename ?? PROOF_FILENAME);
  if (opts.clientEventId) form.append("client_event_id", opts.clientEventId);
  return form;
}

export type ReplyInput = { text: string } | { audio_b64: string; mime: string };
export interface ReplyBody { text?: string; audio_b64?: string; mime?: string }

/** {text} for a typed reply, {audio_b64, mime} for a voice note; null when
    there is nothing to send (blank text, empty audio). */
export function replyBody(input: ReplyInput): ReplyBody | null {
  if ("text" in input) {
    const text = input.text.trim();
    return text ? { text } : null;
  }
  const audio = input.audio_b64.trim();
  const mime = input.mime.split(";")[0].trim().toLowerCase();
  return audio && mime.startsWith("audio/") ? { audio_b64: audio, mime } : null;
}

export function dutyBody(onDuty: boolean): { on_duty: boolean } {
  return { on_duty: Boolean(onDuty) };
}

// ─── Responses (tolerant: the server part ships in parallel) ─────────────

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});
const str = (v: unknown): string | null => (typeof v === "string" && v.trim() ? v.trim() : null);

export interface ProofResult {
  verified: boolean;
  /** True when the check closed the job (state completed). */
  completed: boolean;
  /** verified | rejected | unverified (Gemini could not tell; a supervisor looks). */
  status: string | null;
  /** Why the photo did not match (or why it was accepted), in the worker's language. */
  reason: string | null;
  request: Card | null;
}

export function parseProofResult(raw: unknown): ProofResult {
  const r = obj(raw);
  const request = r.request && typeof r.request === "object" && typeof obj(r.request).id === "number" ? (r.request as Card) : null;
  const verified = r.verified === true;
  return {
    verified,
    completed: r.completed === true || (verified && request?.state === "completed"),
    status: str(r.proof_status) ?? str(request?.proof_status) ?? (verified ? "verified" : null),
    reason: str(r.reason) ?? str(request?.proof_reason),
    request,
  };
}

/** What the worker is told after a photo that did not close the job:
    "rejected" (it does not show the job done) or "review" (the check could
    not decide, so a supervisor looks at it). */
export function proofOutcome(r: Pick<ProofResult, "verified" | "status">): "verified" | "rejected" | "review" {
  if (r.verified) return "verified";
  return r.status === "rejected" || r.status == null ? "rejected" : "review";
}

/** GET /device/service/me: the person at the screen. */
export interface StaffMe {
  staff: {
    id: number;
    name: string;
    first_name: string | null;
    role: string | null;
    department: string | null;
    department_key: string | null;
    /** staff_members.languages[0], the language the job text arrives in. */
    reading_language: string | null;
  };
  on_duty: boolean;
  counts: { active: number; done_today: number };
}

export function parseMe(raw: unknown): StaffMe | null {
  const r = obj(raw);
  const s = obj(r.staff);
  if (typeof s.id !== "number") return null;
  const c = obj(r.counts);
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) ? v : 0);
  return {
    staff: {
      id: s.id, name: str(s.name) ?? "", first_name: str(s.first_name), role: str(s.role), department: str(s.department),
      department_key: str(s.department_key),
      reading_language: str(s.reading_language) ?? (Array.isArray(s.languages) ? str(s.languages[0]) : null),
    },
    on_duty: r.on_duty !== false,
    counts: { active: num(c.active), done_today: num(c.done_today) },
  };
}

export interface ReplyResult {
  text_original: string;
  /** What the guest reads, in the guest's language. */
  text_for_guest: string;
  /** The guest's language code. */
  language: string | null;
}

export function parseReplyResult(raw: unknown): ReplyResult {
  const r = obj(raw);
  const original = str(r.text_original) ?? "";
  return { text_original: original, text_for_guest: str(r.text_for_guest) ?? original, language: str(r.language) };
}

/** The server's word on the new duty state, else what was asked for. */
export function parseDutyResult(raw: unknown, asked: boolean): boolean {
  const r = obj(raw);
  return typeof r.on_duty === "boolean" ? r.on_duty : asked;
}

// ─── Card rules ──────────────────────────────────────────────────────────

/** Mark as Done needs a photo the server checks (decision 8). Absent means
    a server without the proof gate: the job completes as before. */
export function needsProof(c: Pick<Card, "needs_proof">): boolean {
  return c.needs_proof === true;
}

/** URGENT is priority high (decision 11), not severity. */
export function isPriorityUrgent(c: Pick<Card, "priority" | "urgent">): boolean {
  return c.urgent === true || (c.priority ?? "").toLowerCase() === "high";
}

/** Emergencies show as "Emergency": the server's flag, L1 severity, an emergency sensitivity or type. */
export function isEmergency(c: Pick<Card, "severity" | "sensitivity" | "request_type_key" | "emergency">): boolean {
  return c.emergency === true || c.severity === "L1" || c.sensitivity === "emergency" || c.request_type_key === "emergency";
}

export function proofRejected(c: Pick<Card, "proof_status">): boolean {
  return c.proof_status === "rejected";
}

/** The photo went to a supervisor because the check could not decide. */
export function proofInReview(c: Pick<Card, "proof_status">): boolean {
  return c.proof_status === "unverified" || c.proof_status === "pending";
}

/** The job in the worker's language when the server translated it, else the English description. */
export function jobText(c: Pick<Card, "display_text" | "description">): string {
  return (c.display_text ?? "").trim() || (c.description ?? "").trim();
}

/** The English description, when it says something the translated text does not show. */
export function englishText(c: Pick<Card, "display_text" | "description">): string | null {
  const en = (c.description ?? "").trim();
  const shown = (c.display_text ?? "").trim();
  return en && shown && en !== shown ? en : null;
}

/** The guest's own words, when they differ from both texts on screen. */
export function guestWords(c: Pick<Card, "original_text" | "display_text" | "description">): string | null {
  const o = (c.original_text ?? "").trim();
  if (!o) return null;
  return o !== (c.display_text ?? "").trim() && o !== (c.description ?? "").trim() ? o : null;
}

export function guestLanguageOf(c: Pick<Card, "guest_language" | "language">): string | null {
  return c.guest_language || c.language || null;
}

/** On this person's list: theirs, or the device's own pool when nobody holds it. */
export function isMine(c: Pick<Card, "assigned_staff_id" | "assigned_device_id">, meId: number, deviceId: number | null): boolean {
  return c.assigned_staff_id === meId || (deviceId != null && c.assigned_device_id === deviceId && c.assigned_staff_id == null);
}

/** Emergencies first, then URGENT, keeping the server's order within each. */
export function sortForWorker<T extends Card>(cards: T[]): T[] {
  const rank = (c: Card) => (isEmergency(c) ? 0 : isPriorityUrgent(c) ? 1 : 2);
  return cards.map((c, i) => [c, i] as const).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(([c]) => c);
}

// ─── Departments (the wireframe's colours) ───────────────────────────────

export const DEPT_KEYS = ["housekeeping", "ird", "engineering", "front_office", "concierge", "security", "general"] as const;
export type DeptKey = (typeof DEPT_KEYS)[number];

const DEPT_COLORS: Record<DeptKey, string> = {
  housekeeping: "#a78bfa",
  ird: "#fb923c",
  engineering: "#60a5fa",
  front_office: "#34d399",
  concierge: "#E1AF68",
  security: "#f87171",
  general: "#94a3b8",
};

// Mirrors the server's DEPARTMENT_ALIASES (service/constants.py) for the
// free-text names a roster or legacy category carries.
const DEPT_ALIASES: Record<string, DeptKey> = {
  housekeeping: "housekeeping", hk: "housekeeping", rooms_division: "housekeeping", laundry: "housekeeping",
  ird: "ird", in_room_dining: "ird", food_and_beverage: "ird", f_and_b: "ird", f_b: "ird", fnb: "ird", room_service: "ird",
  kitchen: "ird", restaurant: "ird", food: "ird",
  engineering: "engineering", maintenance: "engineering", technical: "engineering",
  front_office: "front_office", front_desk: "front_office", reception: "front_office", guest_relations: "front_office",
  concierge: "concierge", bell_desk: "concierge", spa: "concierge",
  security: "security",
  general: "general", other: "general",
};

export function deptKeyOf(value: string | null | undefined): DeptKey | null {
  const k = (value ?? "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
  return k ? DEPT_ALIASES[k] ?? null : null;
}

export function deptColor(key: DeptKey | null): string {
  return DEPT_COLORS[key ?? "general"];
}

// ─── Done today ──────────────────────────────────────────────────────────

/** YYYY-MM-DD of an instant in a time zone (the hotel's day, not the phone's). */
export function dateKey(ms: number, tz: string): string {
  const d = new Date(ms);
  const fmt = (zone: string) => new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
  try { return fmt(tz); } catch { return fmt("UTC"); }
}

const toMs = (v: unknown): number | null => {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v) { const t = Date.parse(v); return Number.isFinite(t) ? t : null; }
  return null;
};

/** How far back to ask for closed jobs: a job filed yesterday evening and
    finished this morning still counts (the queue's `since` is the filing time). */
export const CLOSED_LOOKBACK_MS = 36 * 3_600_000;

/** Jobs this person completed on the hotel's calendar day of `now`. */
export function countDoneToday(cards: Card[], meId: number, tz: string, now: number): number {
  const today = dateKey(now, tz);
  return cards.filter(c => {
    if (c.state !== "completed") return false;
    if ((c.completed_by_staff_id ?? c.assigned_staff_id) !== meId) return false;
    const at = toMs(c.resolved_at);
    return at != null && dateKey(at, tz) === today;
  }).length;
}

// ─── Small text helpers ──────────────────────────────────────────────────

/** "Maria Santos" -> "Maria"; a trailing "(DEMO)" tag is not a name. */
export function firstName(name: string | null | undefined): string {
  const stripped = (name ?? "").replace(/\s*[([][^)\]]*[)\]]\s*$/u, "").trim();
  return stripped.split(/\s+/).find(p => /\p{L}/u.test(p)) ?? stripped;
}

/** SLA state of an open card, for the localized chip. */
export type SlaKind = "ackIn" | "ackLate" | "dueIn" | "overdue";
export interface SlaInfo { kind: SlaKind; ms: number; tone: "neutral" | "warn" | "bad" }

const UNACKED = new Set(["new", "assigned", "reopened"]);

export function slaInfo(c: Pick<Card, "state" | "ack_due_at" | "resolve_due_at">, now: number): SlaInfo | null {
  if (UNACKED.has(c.state) && c.ack_due_at) {
    const d = c.ack_due_at - now;
    return d >= 0 ? { kind: "ackIn", ms: d, tone: d < 60_000 ? "warn" : "neutral" } : { kind: "ackLate", ms: -d, tone: "bad" };
  }
  if (c.resolve_due_at && ["acknowledged", "in_progress", "assigned"].includes(c.state)) {
    const d = c.resolve_due_at - now;
    return d >= 0 ? { kind: "dueIn", ms: d, tone: d < 5 * 60_000 ? "warn" : "neutral" } : { kind: "overdue", ms: -d, tone: "bad" };
  }
  return null;
}

/** The ladder rung an escalation reached, as the spec names it (decision 7). */
export function escalationRole(level: unknown): "supervisor" | "security" | "duty_manager" | "gm" | null {
  if (level === "supervisor" || level === "security" || level === "duty_manager" || level === "gm") return level;
  if (level === 1 || level === "1") return "supervisor";
  if (level === 2 || level === "2") return "duty_manager";
  if (level === 3 || level === "3") return "gm";
  return null;
}
