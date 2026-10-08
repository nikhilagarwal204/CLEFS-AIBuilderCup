/* ═══════════════════════════════════════════════════════════════════════
   Device-side HTTP surface (03 spec 1.6 and 1.7, built against the service
   README and CONTRACTS.md part 12).

   Foundation routes (exist): POST /auth/devices/pair, GET /auth/devices/roster,
   POST /auth/devices/shift/start|end, GET /hotels/{slug}/context.
   Service routes (exist): GET /hotels/{slug}/service/queue, POST .../requests,
   GET .../requests/{id}, POST .../requests/{id}/transition|notes,
   GET /device/service/feed.
   Service routes the ops part owns and may not have shipped yet:
   POST /device/service/actions (batch), GET /device/service/handover,
   POST /device/service/handover/ack, POST /device/service/sos,
   GET|POST /device/service/recovery-cases..., POST /device/service/push-subscriptions.
   Each of those has a documented fallback here so a 404 never breaks a shift.

   Auth is the HttpOnly clefs_device cookie through the same-origin proxy.
   The spec's "Device-Token in IndexedDB" is superseded by the foundation's
   cookie (never readable by script, never in a URL); the acting person is
   the X-Clefs-Staff header, validated against the device's open PIN shifts.
   ═══════════════════════════════════════════════════════════════════════ */

import { ApiError, apiFetch, hotelPath } from "../../lib/api";

// ─── Types ───────────────────────────────────────────────────────────────

export const STAFF_HEADER = "X-Clefs-Staff";

export interface DeviceInfo {
  id: number;
  name: string;
  kind: string;
  floors: string[];
  departments: string[];
}

export interface PairResult {
  device: DeviceInfo;
  hotel_slug: string;
}

export interface RosterStaff {
  id: number;
  name: string;
  department: string | null;
  role: string | null;
  has_pin: boolean;
  on_shift: boolean;
}

export interface ShiftSession {
  session_id: number;
  staff: { id: number; name: string; department: string | null };
  planned_end_at: string | number | null;
}

export interface HotelContext {
  hotel: { slug: string; name: string; timezone: string; currency: string; locale: string };
  principal_kind?: string;
  /** "device" only for a device paired to THIS hotel. */
  role?: string | null;
  permissions?: string[];
}

/** A request card (service README "HTTP API": instants are epoch ms). */
export interface Card {
  id: number;
  room: string | null;
  type: string | null;
  subtype?: string | null;
  description: string | null;
  category: string | null;
  priority: string | null;
  timestamp: number;
  state: string;
  version: number;
  source?: string | null;
  language?: string | null;
  original_text?: string | null;
  translated?: boolean | null;
  floor?: string | null;
  department_key?: string | null;
  request_type_key?: string | null;
  quantity?: number | null;
  severity?: string | null;
  sensitivity?: string | null;
  sensitive_kind?: string | null;
  assigned_to?: string | null;
  assigned_staff_id?: number | null;
  assigned_device_id?: number | null;
  assigned_at?: number | null;
  acknowledged_at?: number | null;
  started_at?: number | null;
  ack_due_at?: number | null;
  resolve_due_at?: number | null;
  sla_ack_breached?: boolean | null;
  sla_resolve_breached?: boolean | null;
  escalation_level?: number | null;
  reopen_count?: number | null;
  guest_confirmation?: string | null;
  flags?: Record<string, unknown> | null;
}

export interface FeedEvent {
  id: number;
  request_id: number | null;
  at: string | null;
  kind: string;
  action: string | null;
  from_state: string | null;
  to_state: string | null;
  version: number | null;
  /** "staff" | "user" | "device" | "guest" | "system" (server EVENT_KEYS). */
  actor_type?: string | null;
  actor_staff_id: number | null;
  actor_label: string | null;
  device_id: number | null;
  floor: string | null;
  department_key: string | null;
  payload: Record<string, unknown> | null;
}

export interface FeedPage {
  events: FeedEvent[];
  cursor: number;
}

export const OPEN_STATES = ["scheduled", "new", "assigned", "acknowledged", "in_progress", "reopened"];
export const UNACKED_STATES = ["new", "assigned", "reopened"];

/** Device actions legal from each state (mirror of state.py TRANSITIONS;
    reassign/cancel/unassign need service.manage, which a device lacks). */
export const DEVICE_ACTIONS: Record<string, string[]> = {
  new: ["assign"],
  reopened: ["assign"],
  assigned: ["ack", "complete"],
  acknowledged: ["start", "complete", "decline"],
  in_progress: ["complete", "handoff"],
};

export function canDo(state: string, action: string): boolean {
  return (DEVICE_ACTIONS[state] ?? []).includes(action);
}

// ─── Handover (computed on read, 03 spec 1.6) ────────────────────────────

export interface HandoverNote { id: number; body: string; author?: string | null; created_at?: string | number | null; department_key?: string | null }
export interface HandoverRoom { room: string; status?: string | null; note?: string | null }
export interface HandoverArrival { room?: string | null; name?: string | null; vip?: boolean | string | null; returning?: boolean | null }
export interface HandoverIncident { id: number; kind: string; level?: string | null; room?: string | null; floor?: string | null; status?: string | null; opened_at?: string | number | null }
export interface HandoverLostFound { id: number; description: string; location?: string | null; room?: string | null; found_at?: string | number | null }

export interface Handover {
  generated_at: number;
  open: Card[];
  overdue: Card[];
  engineering_open: Card[];
  rooms_out_of_order: HandoverRoom[];
  arrivals: HandoverArrival[];
  incidents: HandoverIncident[];
  lost_found: HandoverLostFound[];
  notes: HandoverNote[];
  /** False when the server route is missing and this was built from the queue. */
  from_server: boolean;
}

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

/** Tolerant parse: the ops part owns the payload, so every list is optional. */
export function parseHandover(raw: Record<string, unknown>): Handover {
  return {
    generated_at: Number(raw.generated_at) || Date.now(),
    open: arr<Card>(raw.open ?? raw.open_requests),
    overdue: arr<Card>(raw.overdue ?? raw.overdue_requests),
    engineering_open: arr<Card>(raw.engineering_open ?? raw.engineering),
    rooms_out_of_order: arr<HandoverRoom>(raw.rooms_out_of_order ?? raw.ooo_rooms),
    arrivals: arr<HandoverArrival>(raw.arrivals ?? raw.vip_arrivals),
    incidents: arr<HandoverIncident>(raw.incidents),
    lost_found: arr<HandoverLostFound>(raw.lost_found),
    notes: arr<HandoverNote>(raw.notes ?? raw.handover_notes),
    from_server: true,
  };
}

/** Fallback when GET /device/service/handover is not there yet: what the
    queue alone can tell (open and overdue work in scope). */
export function handoverFromQueue(cards: Card[], now: number = Date.now()): Handover {
  const open = cards.filter(c => OPEN_STATES.includes(c.state));
  return {
    generated_at: now,
    open,
    overdue: open.filter(c => isOverdue(c, now)),
    engineering_open: open.filter(c => norm(c.department_key) === "engineering"),
    rooms_out_of_order: [], arrivals: [], incidents: [], lost_found: [], notes: [],
    from_server: false,
  };
}

// ─── Scope and time helpers ──────────────────────────────────────────────

/** "Food & Beverage" / "food_beverage" / "F&B" style names compare loosely. */
export function norm(v: string | null | undefined): string {
  return (v ?? "").toLowerCase().replace(/&/g, " and ").replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "");
}

/** The device sees its floors and departments; an unscoped device sees all.
    Cards with no floor (lobby, hotel-wide) are always in scope, matching the
    server's feed filter. */
export function inScope(c: Card, device: DeviceInfo | null): boolean {
  if (!device) return true;
  const floors = device.floors ?? [];
  const depts = (device.departments ?? []).map(norm);
  if (floors.length && c.floor && !floors.includes(String(c.floor))) return false;
  if (depts.length && c.department_key) {
    const k = norm(c.department_key), cat = norm(c.category);
    if (!depts.some(d => d === k || d === cat || k.startsWith(d) || d.startsWith(k))) return false;
  }
  return true;
}

export function isOverdue(c: Card, now: number = Date.now()): boolean {
  if (!OPEN_STATES.includes(c.state)) return false;
  if (UNACKED_STATES.includes(c.state) && c.ack_due_at && now > c.ack_due_at) return true;
  return Boolean(c.resolve_due_at && now > c.resolve_due_at) || Boolean(c.sla_ack_breached && UNACKED_STATES.includes(c.state));
}

export function isUrgent(c: Card): boolean {
  return c.severity === "L1" || c.sensitivity === "emergency" || c.sensitivity === "sensitive";
}

/** A complaint, or a guest "not resolved", gets the LEARN card (03 spec 1.5). */
export function needsRecovery(c: Card): boolean {
  const t = `${norm(c.request_type_key)} ${norm(c.category)} ${norm(c.type)}`;
  return /complain|feedback|escalat/.test(t) || (c.reopen_count ?? 0) > 0 || c.guest_confirmation === "no";
}

// ─── Calls ───────────────────────────────────────────────────────────────

export function staffHeaders(staffId: number | null | undefined): Record<string, string> {
  return staffId ? { [STAFF_HEADER]: String(staffId) } : {};
}

/** 404/405/501 on an ops route means "not shipped yet": use the fallback. */
export function isMissingRoute(err: unknown): boolean {
  return err instanceof ApiError && [404, 405, 501].includes(err.status) && err.code !== "request_not_found";
}

export function pairDevice(code: string): Promise<PairResult> {
  return apiFetch<PairResult>("/auth/devices/pair", { method: "POST", json: { code: code.trim().toUpperCase() }, silent401: true });
}

/** Also the "am I still paired?" probe: 401 means the cookie is gone or revoked. */
export function getRoster(): Promise<RosterStaff[]> {
  return apiFetch<RosterStaff[]>("/auth/devices/roster", { silent401: true });
}

export function startShift(staffId: number, pin: string): Promise<ShiftSession> {
  return apiFetch<ShiftSession>("/auth/devices/shift/start", {
    method: "POST", json: { staff_member_id: staffId, pin }, silent401: true,
  });
}

export function endShift(sessionId: number, handoverNote?: string): Promise<{ ok: boolean }> {
  return apiFetch("/auth/devices/shift/end", {
    method: "POST", json: { session_id: sessionId, handover_note: handoverNote || undefined }, silent401: true,
  });
}

export function getContext(slug: string): Promise<HotelContext> {
  return apiFetch<HotelContext>(hotelPath(slug, "/context"), { silent401: true });
}

export function getQueue(slug: string, staffId: number | null): Promise<Card[]> {
  return apiFetch<Card[]>(hotelPath(slug, "/service/queue"), {
    query: { state: "open", limit: 300 }, headers: staffHeaders(staffId), silent401: true,
  });
}

export function getRequest(slug: string, id: number, staffId: number | null): Promise<{ request: Card; events: FeedEvent[] }> {
  return apiFetch(hotelPath(slug, `/service/requests/${id}`), { headers: staffHeaders(staffId), silent401: true });
}

/** The feed after `cursor`. `wait` asks for a long-poll (25 s in the spec);
    a server that ignores it answers at once and feed.ts paces itself. */
export function getFeed(cursor: number, signal?: AbortSignal, wait = 25): Promise<FeedPage> {
  return apiFetch<FeedPage>("/device/service/feed", { query: { cursor, wait, limit: 200 }, signal, silent401: true });
}

export async function getHandover(slug: string, staffId: number | null, queue: Card[] | null): Promise<Handover> {
  try {
    const raw = await apiFetch<Record<string, unknown>>("/device/service/handover", {
      headers: staffHeaders(staffId), silent401: true,
    });
    return parseHandover(raw ?? {});
  } catch (err) {
    if (!isMissingRoute(err)) throw err;
    return handoverFromQueue(queue ?? (await getQueue(slug, staffId)));
  }
}

export interface RecoveryCase {
  id: number;
  request_id: number | null;
  status: string;
  learn: Record<string, boolean>;
}

/** Open recovery case for a request, or null (none, or the route is not there). */
export async function getRecoveryCase(requestId: number, staffId: number | null): Promise<RecoveryCase | null> {
  try {
    const rows = await apiFetch<RecoveryCase[] | { cases: RecoveryCase[] }>("/device/service/recovery-cases", {
      query: { request_id: requestId }, headers: staffHeaders(staffId), silent401: true,
    });
    const list = Array.isArray(rows) ? rows : rows?.cases ?? [];
    return list.find(c => c.status === "open") ?? list[0] ?? null;
  } catch (err) {
    if (isMissingRoute(err)) return null;
    throw err;
  }
}
