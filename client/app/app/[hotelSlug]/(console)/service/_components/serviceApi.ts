/* ═══════════════════════════════════════════════════════════════════════
   Service Control v2: client types and calls (03 spec 1.7, service README).

   One file for every Service console surface (StaffDashboard's v2 edits, the
   phone-entry console, config, roster, analytics) so a server shape change is
   fixed in one place. Paths are contract paths; apiFetch adds /api/clefs.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, UNAUTHORIZED_EVENT, api, apiUrl, hotelPath } from "../../../../../lib/api";

// ─── Request card (GET queue, POST requests, transitions) ───

export type ServiceState =
  | "scheduled" | "new" | "assigned" | "acknowledged" | "in_progress" | "completed" | "cancelled" | "reopened";

export const OPEN_STATES: readonly ServiceState[] = ["scheduled", "new", "assigned", "acknowledged", "in_progress", "reopened"];
export const UNACKED_STATES: readonly ServiceState[] = ["new", "assigned", "reopened"];

/** The v2 fields a card carries on top of the legacy request row. All
    optional: a pre-v2 row (or an older server) simply lacks them. */
export interface V2Fields {
  state?: ServiceState | null;
  version?: number | null;
  source?: string | null;
  language?: string | null;
  original_text?: string | null;
  translated?: boolean | null;
  floor?: string | null;
  room_type?: string | null;
  department_key?: string | null;
  request_type_key?: string | null;
  severity?: "L1" | "L2" | "L3" | null;
  sensitivity?: string | null;
  assigned_staff_id?: number | null;
  assigned_at?: number | null;
  acknowledged_at?: number | null;
  started_at?: number | null;
  ack_due_at?: number | null;
  resolve_due_at?: number | null;
  sla_ack_breached?: boolean | null;
  sla_resolve_breached?: boolean | null;
  escalation_level?: number | null;
  reopen_count?: number | null;
  merged_count?: number | null;
  guest_confirmation?: "yes" | "no" | "unknown" | null;
  flags?: Record<string, unknown> | null;
}

export interface ServiceCard extends V2Fields {
  id: number;
  room: string;
  type: string;
  subtype?: string | null;
  description: string;
  category: string;
  priority: "high" | "medium" | "low";
  status: "new" | "progress" | "completed";
  timestamp: number;
  assigned_to?: string | null;
  accepted_at?: number | null;
  resolved_at?: number | null;
}

export interface ServiceEvent {
  id: number;
  request_id: number;
  at: string | null;
  kind: string;
  action?: string | null;
  from_state?: string | null;
  to_state?: string | null;
  version?: number | null;
  actor_label?: string | null;
  payload?: Record<string, unknown> | null;
}

export type TransitionAction =
  | "release" | "assign" | "ack" | "start" | "complete" | "reassign" | "decline" | "handoff"
  | "cancel" | "reopen" | "unassign";

export interface TransitionBody {
  action: TransitionAction;
  staff_id?: number;
  note?: string;
  resolution_code?: string;
  expected_version?: number;
  client_event_id?: string;
}

export interface TransitionConflict {
  request_id: number;
  state: string;
  version: number;
  by?: string | null;
  at?: number | string | null;
}

export interface CreateBody {
  room?: string;
  description: string;
  type?: string;
  request_type_key?: string;
  category?: string;
  priority?: "high" | "medium" | "low";
  severity?: "L1" | "L2" | "L3";
  language?: string;
  amenity_item_id?: number;
  quantity?: number;
  idempotency_key?: string;
  source?: "console" | "phone_desk" | "device";
}

export interface CreateResult {
  request: ServiceCard;
  merged: boolean;
  replayed: boolean;
  tracking_token?: string | null;
  routed?: { pool?: string; staff_id?: number | null; name?: string | null; over_cap?: boolean } | null;
}

// ─── Calls ───

const svc = (slug: string, p: string) => hotelPath(slug, `/service${p}`);

export function fetchQueue(slug: string, q: { state?: "open" | "all" | "closed"; since?: number; limit?: number } = {}) {
  // silent401: a legacy open-access visitor is refused here by design and
  // falls back to the legacy list; that is not a reason to go to /login.
  return api.get<ServiceCard[]>(svc(slug, "/queue"), { state: q.state ?? "open", since: q.since, limit: q.limit ?? 200 },
    { silent401: true });
}

/** True when a v2 route is closed to this viewer or absent: legacy open-access
    visitors (anonymous) may use only the legacy routes, and an older server
    has no v2 routes at all. Callers then take the legacy path. */
export function takesLegacyPath(err: unknown): boolean {
  return err instanceof ApiError && [401, 404, 405].includes(err.status) && (err.status !== 404 || !err.code);
}

/** Open requests, v2 queue first, else the legacy list (open rows only). */
export async function fetchOpenRequests(slug: string): Promise<ServiceCard[]> {
  try {
    return await fetchQueue(slug, { state: "open", limit: 500 });
  } catch (err) {
    if (!takesLegacyPath(err)) throw err;
  }
  const rows = await api.get<ServiceCard[]>(hotelPath(slug, "/requests"), undefined, { silent401: true });
  return rows.filter(r => r.status !== "completed");
}

/** POST v2 intake; on the legacy path, the legacy POST /requests (no
    routing preview, no merge flag). `legacy` names the legacy type and
    category the old list expects. */
export async function createRequestCompat(
  slug: string, body: CreateBody, legacy: { type: string; category: string },
): Promise<CreateResult> {
  try {
    return await createRequest(slug, body);
  } catch (err) {
    if (!takesLegacyPath(err)) throw err;
  }
  const row = await api.post<ServiceCard>(hotelPath(slug, "/requests"), {
    room: body.room ?? "", type: legacy.type, description: body.description, priority: body.priority ?? "medium",
    category: legacy.category, status: "new", timestamp: Date.now(),
  }, { silent401: true });
  return { request: row, merged: false, replayed: false, tracking_token: null, routed: null };
}

export function fetchRequest(slug: string, id: number) {
  return api.get<{ request: ServiceCard; events: ServiceEvent[] }>(svc(slug, `/requests/${id}`));
}

export function createRequest(slug: string, body: CreateBody) {
  return api.post<CreateResult>(svc(slug, "/requests"), body, { silent401: true });
}

/** A 409 from a transition keeps the server's `conflict` block ("Already done
    by Amina"). apiFetch keeps only `detail`, so this call reads the body itself. */
export class TransitionError extends ApiError {
  conflict: TransitionConflict | null;
  constructor(status: number, message: string, code: string | undefined, conflict: TransitionConflict | null) {
    super(status, message, code);
    this.name = "TransitionError";
    this.conflict = conflict;
  }
}

export async function transition(slug: string, id: number, body: TransitionBody) {
  const path = svc(slug, `/requests/${id}/transition`);
  let res: Response;
  try {
    res = await fetch(apiUrl(path), {
      method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ client_event_id: newEventId(), ...body }),
    });
  } catch {
    throw new ApiError(0, "Can't reach the Clefs server. Check your connection and try again.", "network_error");
  }
  const data = (await res.json().catch(() => null)) as
    | ({ detail?: unknown; code?: string; conflict?: TransitionConflict } & Record<string, unknown>)
    | null;
  if (res.ok) return data as unknown as { request: ServiceCard; event: ServiceEvent; replayed: boolean };
  // Same contract as apiFetch: a 401 tells SessionProvider to send us to /login.
  if (res.status === 401 && typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent(UNAUTHORIZED_EVENT, { detail: { path } }));
  }
  const message = typeof data?.detail === "string" ? data.detail : `Request failed (${res.status})`;
  throw new TransitionError(res.status, message, data?.code, data?.conflict ?? null);
}

export function addNote(slug: string, id: number, body: string) {
  return api.post<ServiceEvent>(svc(slug, `/requests/${id}/notes`), { body });
}

// ─── Errors ───

/** A replay-safe id per user action (the server is idempotent on it). */
export function newEventId(): string {
  const c = typeof globalThis !== "undefined" ? (globalThis.crypto as Crypto | undefined) : undefined;
  if (c?.randomUUID) return c.randomUUID();
  // RFC 4122 v4 shape from Math.random: only reached on very old browsers.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, ch => {
    const r = (Math.random() * 16) | 0;
    return (ch === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** The conflict payload of a 409 from a transition (someone else got there first). */
export function conflictOf(err: unknown): TransitionConflict | null {
  return err instanceof TransitionError ? err.conflict : null;
}

/** "Already done by Amina" style text for a transition 409. */
export function conflictText(err: unknown): string | null {
  if (!(err instanceof ApiError) || err.status !== 409) return null;
  const c = conflictOf(err);
  // The server words it ("Already done by Amina"); keep that when present.
  if (err.code === "already_closed") return err.message || (c?.by ? `Already done by ${c.by}.` : "Already done by someone else.");
  if (err.code === "version_conflict") return "Someone else changed this request. The list has been refreshed.";
  return err.message || "That step is not possible any more.";
}

/** True when the route does not exist on this server yet (another part of
    the rollout ships it). Pages show a calm "not available yet" instead of
    an error toast. */
export function isMissingRoute(err: unknown): boolean {
  return err instanceof ApiError && (err.status === 404 || err.status === 405) && !err.code;
}

export function errorText(err: unknown, fallback = "Something went wrong. Try again."): string {
  if (err instanceof ApiError) return err.message || fallback;
  if (err instanceof Error && err.message) return err.message;
  return fallback;
}

// ─── Display helpers ───

/** v2 state, or the legacy status mapped onto it for pre-v2 rows. */
export function effectiveState(r: Pick<ServiceCard, "status"> & V2Fields): ServiceState {
  if (r.state) return r.state;
  if (r.status === "completed") return "completed";
  if (r.status === "progress") return "assigned";
  return "new";
}

export function isOpen(r: Pick<ServiceCard, "status"> & V2Fields): boolean {
  return OPEN_STATES.includes(effectiveState(r));
}

export type ChipTone = "neutral" | "info" | "gold" | "ok" | "warn" | "bad";

/** The card's state chip: Assigned / Acked / On the way / Overdue (03 spec 1.8). */
export function stateChip(r: Pick<ServiceCard, "status"> & V2Fields, now: number): { label: string; tone: ChipTone } {
  const s = effectiveState(r);
  const open = OPEN_STATES.includes(s);
  // now <= 0: the clock has not started (first render); trust only the
  // server's breach flags until it has.
  const known = now > 0;
  const overdue = open && (
    !!r.sla_resolve_breached || !!r.sla_ack_breached ||
    (known && UNACKED_STATES.includes(s) && !!r.ack_due_at && r.ack_due_at < now) ||
    (known && !!r.resolve_due_at && r.resolve_due_at < now)
  );
  if (overdue) return { label: "Overdue", tone: "bad" };
  switch (s) {
    case "scheduled": return { label: "Scheduled", tone: "neutral" };
    case "new": return { label: "New", tone: "info" };
    case "reopened": return { label: "Reopened", tone: "warn" };
    case "assigned": return { label: "Assigned", tone: "gold" };
    case "acknowledged": return { label: "Acked", tone: "ok" };
    case "in_progress": return { label: "On the way", tone: "ok" };
    case "completed": return { label: "Done", tone: "neutral" };
    case "cancelled": return { label: "Cancelled", tone: "neutral" };
  }
}

/** The SLA clock that matters now: ack while nobody has acknowledged, then resolution. */
export function slaTarget(r: Pick<ServiceCard, "status"> & V2Fields): { kind: "ack" | "resolve"; due: number } | null {
  const s = effectiveState(r);
  if (!OPEN_STATES.includes(s)) return null;
  if (UNACKED_STATES.includes(s) && r.ack_due_at) return { kind: "ack", due: r.ack_due_at };
  if (r.resolve_due_at) return { kind: "resolve", due: r.resolve_due_at };
  return null;
}

/** "4m left" / "3m over" for a due instant. */
export function countdownText(due: number, now: number): { text: string; over: boolean; soon: boolean } {
  const mins = Math.round((due - now) / 60_000);
  const abs = Math.abs(mins);
  const span = abs >= 60 ? `${Math.floor(abs / 60)}h ${abs % 60}m` : `${abs}m`;
  if (mins < 0) return { text: `${span} over`, over: true, soon: false };
  return { text: `${span} left`, over: false, soon: mins <= 2 };
}

export const SOURCE_LABELS: Record<string, string> = {
  web: "QR", voice: "Voice", sip: "Room phone", whatsapp: "WhatsApp", text: "Chat", phone_desk: "Desk call",
  console: "Console", device: "Floor device", staff: "Staff", legacy: "Legacy", whatsapp_journey: "WhatsApp",
  inquiries: "Inquiry", j7_feedback: "Feedback", journey_gate: "Journey",
};

export const LANGUAGE_LABELS: Record<string, string> = {
  en: "English", ar: "Arabic", ru: "Russian", hi: "Hindi", ur: "Urdu", zh: "Chinese", de: "German", fr: "French",
};
