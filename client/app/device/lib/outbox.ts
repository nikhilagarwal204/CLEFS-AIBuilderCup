/* ═══════════════════════════════════════════════════════════════════════
   Offline-safe device actions (03 spec 1.6): the IndexedDB store and the
   HTTP transport around outboxCore.ts.

   Every tap is queued first ({client_event_id, action, request_id,
   base_version, client_at}) and flushed on reconnect, every 5 s and on the
   `online` event. POST /device/service/actions takes a batch; until the ops
   part ships it, each item goes to its own route with the same
   client_event_id, so a replay is still idempotent server-side.

   The transport uses fetch directly (not apiFetch) because a 409 carries a
   `conflict: {by, at, state, version}` object that apiFetch drops, and the
   device must say "Already done by Amina", not "409".
   ═══════════════════════════════════════════════════════════════════════ */

import { apiUrl, hotelPath } from "../../lib/api";
import { canDo, STAFF_HEADER, type Card } from "./deviceApi";
import {
  flushOutbox, makeItem, nextSeq, sortItems,
  type ConflictInfo, type FlushOutcome, type OutboxHandlers, type OutboxItem, type OutboxStore, type SendResult, type Transport,
} from "./outboxCore";
import { idbAll, idbDelete, idbSet, OUTBOX } from "./idb";

export type { OutboxItem, SendResult, ConflictInfo } from "./outboxCore";

export const FLUSH_EVERY_MS = 5_000;
/** An ack that reached the server this long after a reassignment is refused
    (03 spec 1.6: accepted only within 60 s of client and server time). */
export const REASSIGN_GRACE_MS = 60_000;
/** Ask again for a missing batch route after this long (a deploy may add it). */
const BATCH_RECHECK_MS = 10 * 60_000;

// ─── Store ───────────────────────────────────────────────────────────────

export const idbOutboxStore: OutboxStore = {
  list: () => idbAll<OutboxItem>(OUTBOX),
  put: item => idbSet(OUTBOX, item.client_event_id, item),
  remove: id => idbDelete(OUTBOX, id),
};

// ─── Raw HTTP ────────────────────────────────────────────────────────────

interface Raw { status: number; body: Record<string, unknown> | null }

async function send(method: string, path: string, json: unknown, staffId: number | null): Promise<Raw> {
  const headers: Record<string, string> = { Accept: "application/json", "Content-Type": "application/json" };
  if (staffId) headers[STAFF_HEADER] = String(staffId);
  try {
    const res = await fetch(apiUrl(path), {
      method, headers, body: json === undefined ? undefined : JSON.stringify(json),
      credentials: "same-origin", cache: "no-store",
    });
    let body: Record<string, unknown> | null = null;
    try { body = (await res.json()) as Record<string, unknown>; } catch { body = null; }
    return { status: res.status, body };
  } catch {
    return { status: 0, body: null };
  }
}

const detailOf = (r: Raw, fallback: string) =>
  (typeof r.body?.detail === "string" && r.body.detail) || fallback;

/** One HTTP answer as an outbox outcome. */
export function classify(r: Raw): SendResult {
  if (r.status >= 200 && r.status < 300) return { status: "ok", data: r.body ?? undefined, replayed: Boolean(r.body?.replayed) };
  if (r.status === 0) return { status: "retry", message: "Offline. Will send when the connection is back.", httpStatus: 0 };
  // 401: the device cookie or session is gone. Keep the work; the app re-pairs or re-logs.
  if (r.status === 401 || r.status === 408 || r.status === 425 || r.status === 429 || r.status >= 500) {
    return { status: "retry", message: detailOf(r, `Server busy (${r.status})`), httpStatus: r.status };
  }
  const code = typeof r.body?.code === "string" ? r.body.code : undefined;
  const conflict = r.body?.conflict as ConflictInfo | undefined;
  if (r.status === 409 && conflict && code !== "illegal_transition") {
    const by = conflict.by ? ` by ${conflict.by}` : "";
    const message = conflict.state === "completed" ? `Already done${by}` :
      conflict.state === "cancelled" ? "This request was cancelled" : detailOf(r, "Someone else changed this request");
    return { status: "conflict", message, conflict };
  }
  return { status: "rejected", message: detailOf(r, `Refused (${r.status})`), code, httpStatus: r.status };
}

const missing = (r: Raw) => [404, 405, 501].includes(r.status) && r.body?.code !== "request_not_found";

// ─── Per-kind sends (the fallback path, and SOS / LEARN / handover) ──────

type Fresh = (id: number, staffId: number | null) => Promise<Card | null>;

async function sendTransition(slug: string, it: OutboxItem, fresh: Fresh): Promise<SendResult> {
  const path = hotelPath(slug, `/service/requests/${it.request_id}/transition`);
  const p = it.payload ?? {};
  const body = (version: number | null | undefined) => ({
    action: it.action, staff_id: p.target_staff_id ?? undefined, note: p.note ?? undefined,
    resolution_code: p.resolution_code ?? undefined, expected_version: version ?? undefined,
    client_event_id: it.client_event_id,
  });
  const first = await send("POST", path, body(it.base_version), it.staff_id);
  if (!(first.status === 409 && first.body?.code === "version_conflict")) return classify(first);

  // Someone moved the card after the tap (an SLA step, a note, a reassign).
  // Re-read it: if this action still makes sense for this person, apply it on
  // the fresh version; if the job went to someone else, only an ack tapped
  // within 60 s of that reassignment still counts.
  const now = it.request_id ? await fresh(it.request_id, it.staff_id) : null;
  if (!now || !it.action) return classify(first);
  if (!canDo(now.state, it.action)) return classify(first);
  const mine = it.action === "assign" || now.assigned_staff_id == null || now.assigned_staff_id === it.staff_id;
  if (!mine) {
    const movedAt = now.assigned_at ?? 0;
    if (Math.abs(movedAt - it.client_at) > REASSIGN_GRACE_MS) {
      return {
        status: "conflict",
        message: now.assigned_to ? `Reassigned to ${now.assigned_to}` : "This job was reassigned",
        conflict: { request_id: now.id, state: now.state, version: now.version, by: now.assigned_to ?? null, at: movedAt },
      };
    }
  }
  return classify(await send("POST", path, body(now.version), it.staff_id));
}

async function sendSos(slug: string, it: OutboxItem): Promise<SendResult> {
  const p = it.payload ?? {};
  const r = await send("POST", "/device/service/sos", {
    client_event_id: it.client_event_id, staff_id: it.staff_id, floor: p.floor ?? null,
    note: p.note ?? null, client_at: it.client_at,
  }, it.staff_id);
  if (!missing(r)) return classify(r);
  // No SOS route yet: file an L1 emergency request instead. The emergency
  // type routes to Security and L1 pages the duty manager and GM (sla.py),
  // and the idempotency key makes a replay land on the same request.
  const who = typeof p.staff_name === "string" ? p.staff_name : "A staff member";
  const where = p.floor ? ` on floor ${p.floor}` : "";
  const device = typeof p.device_name === "string" ? ` (device ${p.device_name})` : "";
  // staff_requests.room is NOT NULL (a missing room 500s today), so the
  // floor stands in for it; an unknown room is only flagged, never refused.
  const room = p.floor ? `Floor ${String(p.floor)}`.slice(0, 24) : "Staff SOS";
  return classify(await send("POST", hotelPath(slug, "/service/requests"), {
    room,
    description: `STAFF SOS: ${who} needs urgent help${where}${device}.`,
    request_type_key: "emergency", severity: "L1", priority: "high", source: "device",
    idempotency_key: `sos:${it.client_event_id}`,
  }, it.staff_id));
}

/** LEARN checkboxes and the handover ack are also kept on the device, so a
    missing server route is "done" rather than a job that never leaves. */
async function sendSoft(method: string, path: string, json: unknown, staffId: number | null): Promise<SendResult> {
  const r = await send(method, path, json, staffId);
  if (missing(r)) return { status: "ok", data: { stored_locally: true } };
  return classify(r);
}

export function sendOne(slug: string, it: OutboxItem, fresh: Fresh): Promise<SendResult> {
  const p = it.payload ?? {};
  switch (it.kind) {
    case "transition":
      return sendTransition(slug, it, fresh);
    case "note":
      return send("POST", hotelPath(slug, `/service/requests/${it.request_id}/notes`), { body: p.body }, it.staff_id).then(classify);
    case "request":
      return send("POST", hotelPath(slug, "/service/requests"), { ...p, source: "device", idempotency_key: it.client_event_id }, it.staff_id).then(classify);
    case "sos":
      return sendSos(slug, it);
    case "learn":
      return p.case_id
        // PATCH merges LEARN ticks on the server (devices_api.patch_case), so a
        // replay of the same ticks is harmless without a client_event_id.
        ? sendSoft("PATCH", `/device/service/recovery-cases/${p.case_id}`, { learn: p.learn }, it.staff_id)
        : sendSoft("POST", "/device/service/recovery-cases", {
          request_id: it.request_id, reason: p.reason ?? "complaint", learn: p.learn, client_event_id: it.client_event_id,
        }, it.staff_id);
    case "handover_ack":
      return sendSoft("POST", "/device/service/handover/ack", { ...p, client_event_id: it.client_event_id, client_at: it.client_at }, it.staff_id);
    default:
      return Promise.resolve({ status: "rejected", message: `Unknown action ${String(it.kind)}` });
  }
}

// ─── Batch ───────────────────────────────────────────────────────────────

let batchMissingUntil = 0;

/** POST /device/service/actions with the spec's item shape. Returns null
    (use the per-item path) when the route is missing or the chunk holds
    kinds the batch route does not take. */
async function sendBatch(items: OutboxItem[]): Promise<SendResult[] | null> {
  if (Date.now() < batchMissingUntil) return null;
  if (items.some(it => it.kind !== "transition" && it.kind !== "note")) return null;
  const r = await send("POST", "/device/service/actions", {
    actions: items.map(it => ({
      client_event_id: it.client_event_id, kind: it.kind, action: it.action ?? (it.kind === "note" ? "note" : null),
      request_id: it.request_id, base_version: it.base_version, client_at: it.client_at, staff_id: it.staff_id,
      ...(it.payload ?? {}),
    })),
  }, items[0]?.staff_id ?? null);
  if (missing(r)) { batchMissingUntil = Date.now() + BATCH_RECHECK_MS; return null; }
  if (r.status < 200 || r.status >= 300) {
    const whole = classify(r);
    return items.map(() => whole);
  }
  const results = Array.isArray(r.body?.results) ? (r.body!.results as Record<string, unknown>[]) : [];
  const byId = new Map(results.map(x => [String(x.client_event_id), x]));
  return items.map(it => {
    const x = byId.get(it.client_event_id);
    if (!x) return { status: "retry", message: "No result for this action" };
    const st = String(x.status ?? "");
    const httpStatus = Number(x.http_status ?? x.status_code ?? 0) || undefined;
    if (st === "ok" || st === "replayed" || st === "applied") return { status: "ok", data: x, replayed: st === "replayed" };
    if (st === "conflict") return classify({ status: 409, body: { detail: x.detail, code: x.code ?? "already_closed", conflict: x.conflict ?? {} } });
    if (st === "retry" || (httpStatus && httpStatus >= 500)) return { status: "retry", message: String(x.detail ?? "Server busy"), httpStatus };
    return { status: "rejected", message: String(x.detail ?? "Refused"), code: typeof x.code === "string" ? x.code : undefined, httpStatus };
  });
}

// ─── DeviceOutbox ────────────────────────────────────────────────────────

export interface DeviceOutboxOptions extends OutboxHandlers {
  /** Re-read one card (for version conflicts). */
  fresh: Fresh;
  /** Pending count changed (after enqueue or flush). */
  onChange?: (pending: OutboxItem[]) => void;
  /** A flush finished. */
  onFlushed?: (out: FlushOutcome) => void;
}

export type EnqueueFields = Omit<OutboxItem, "client_event_id" | "seq" | "attempts" | "client_at">;

export class DeviceOutbox {
  private flushing: Promise<FlushOutcome> | null = null;
  private again = false;

  constructor(private slug: string, private opts: DeviceOutboxOptions, private store: OutboxStore = idbOutboxStore) {}

  async pending(): Promise<OutboxItem[]> {
    return sortItems(await this.store.list());
  }

  /** Queue first (durable), then try to send at once. */
  async enqueue(fields: EnqueueFields): Promise<OutboxItem> {
    const seq = nextSeq(await this.store.list());
    const item = makeItem(fields, seq);
    await this.store.put(item);
    this.opts.onChange?.(await this.pending());
    void this.flush();
    return item;
  }

  /** Single-flight: a flush asked for while one runs runs once more after it. */
  flush(): Promise<FlushOutcome> {
    if (this.flushing) { this.again = true; return this.flushing; }
    const transport: Transport = {
      send: it => sendOne(this.slug, it, this.opts.fresh),
      sendBatch,
    };
    this.flushing = flushOutbox(this.store, transport, this.opts)
      .then(async out => {
        this.opts.onFlushed?.(out);
        this.opts.onChange?.(await this.pending());
        return out;
      })
      .finally(() => {
        this.flushing = null;
        if (this.again) { this.again = false; void this.flush(); }
      });
    return this.flushing;
  }

  /** Flush every 5 s, on `online`, and when the tab comes back. Returns stop. */
  start(): () => void {
    const tick = () => { void this.flush(); };
    const onVisible = () => { if (document.visibilityState === "visible") tick(); };
    const timer = setInterval(tick, FLUSH_EVERY_MS);
    window.addEventListener("online", tick);
    document.addEventListener("visibilitychange", onVisible);
    tick();
    return () => {
      clearInterval(timer);
      window.removeEventListener("online", tick);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }

  async clear(): Promise<void> {
    for (const it of await this.store.list()) await this.store.remove(it.client_event_id);
    this.opts.onChange?.([]);
  }
}
