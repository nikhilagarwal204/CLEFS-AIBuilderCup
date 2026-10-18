/* ═══════════════════════════════════════════════════════════════════════
   Offline outbox core (03 spec 1.6): the queue rules with no browser API,
   so node:test can drive it (outbox.test.mjs) and outbox.ts only adds
   IndexedDB and HTTP. Keep this file free of imports.

   Rules:
   - Every device write is an item keyed by a client_event_id (a UUID). The
     server is idempotent on it, so a replay after a lost response is safe.
   - Items flush strictly in the order they were queued, and a flush stops
     at the first transient failure (network, 5xx, 429, 401): "ack" must
     never overtake the "complete" queued after it.
   - A conflict (someone else closed it) or a rejection (403, 404, illegal
     transition) is final: the item leaves the queue and the UI is told why.
   ═══════════════════════════════════════════════════════════════════════ */

export type OutboxKind = "transition" | "note" | "sos" | "learn" | "handover_ack" | "request";

export interface OutboxItem {
  client_event_id: string;
  kind: OutboxKind;
  /** Transition verb (ack, start, complete, decline, handoff, assign). */
  action?: string;
  request_id?: number | null;
  /** The request version the person saw when they tapped. */
  base_version?: number | null;
  /** Device clock at the tap, epoch ms (server time stays authoritative). */
  client_at: number;
  /** The acting staff member (X-Clefs-Staff). */
  staff_id: number | null;
  payload?: Record<string, unknown>;
  /** Monotonic order within this device; ties broken by client_at. */
  seq: number;
  attempts: number;
  last_error?: string | null;
}

export interface ConflictInfo {
  request_id?: number;
  state?: string | null;
  version?: number | null;
  by?: string | null;
  at?: number | string | null;
}

/** What one send produced. "retry" keeps the item and stops the flush. */
export type SendResult =
  | { status: "ok"; data?: unknown; replayed?: boolean }
  | { status: "conflict"; message: string; conflict?: ConflictInfo }
  | { status: "rejected"; message: string; code?: string; httpStatus?: number }
  | { status: "retry"; message: string; httpStatus?: number };

export interface OutboxStore {
  list(): Promise<OutboxItem[]>;
  put(item: OutboxItem): Promise<void>;
  remove(id: string): Promise<void>;
}

export interface OutboxHandlers {
  onApplied?: (item: OutboxItem, result: Extract<SendResult, { status: "ok" }>) => void;
  onConflict?: (item: OutboxItem, result: Extract<SendResult, { status: "conflict" }>) => void;
  onRejected?: (item: OutboxItem, result: Extract<SendResult, { status: "rejected" }>) => void;
}

export interface FlushOutcome {
  sent: number;
  applied: number;
  conflicts: number;
  rejected: number;
  /** Items still queued after this flush. */
  remaining: number;
  /** Set when the flush stopped on a transient failure. */
  stoppedOn?: string;
}

/** Send one item, or a batch whose results line up with the input order. */
export interface Transport {
  send(item: OutboxItem): Promise<SendResult>;
  /** Optional batch path; return null when the server has no batch route. */
  sendBatch?(items: OutboxItem[]): Promise<SendResult[] | null>;
}

// ─── Helpers ─────────────────────────────────────────────────────────────

/** A server that keeps answering 5xx for one item is not going to change its
    mind; after this many server errors the item is dropped as rejected.
    Network failures (status 0) never count: a long outage must not lose work. */
export const MAX_SERVER_ERRORS = 20;
export const BATCH_SIZE = 25;

/** UUID v4. crypto.randomUUID needs a secure context; a LAN kiosk on plain
    http has only getRandomValues, and a very old WebView may have neither. */
export function newEventId(): string {
  const c = (globalThis as { crypto?: Crypto }).crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  const b = new Uint8Array(16);
  if (c && typeof c.getRandomValues === "function") c.getRandomValues(b);
  else for (let i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = Array.from(b, x => x.toString(16).padStart(2, "0")).join("");
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

export function sortItems(items: OutboxItem[]): OutboxItem[] {
  return [...items].sort((a, b) => a.seq - b.seq || a.client_at - b.client_at);
}

export function makeItem(
  fields: Omit<OutboxItem, "client_event_id" | "seq" | "attempts" | "client_at"> & { client_at?: number; client_event_id?: string },
  seq: number,
): OutboxItem {
  return {
    ...fields,
    client_event_id: fields.client_event_id ?? newEventId(),
    client_at: fields.client_at ?? Date.now(),
    seq,
    attempts: 0,
    last_error: null,
  };
}

/** Next seq: one past the highest queued, and never below the wall clock so
    a cleared queue after a reload still sorts after anything older. */
export function nextSeq(items: OutboxItem[], now: number = Date.now()): number {
  const max = items.reduce((m, it) => Math.max(m, it.seq), 0);
  return Math.max(max + 1, now);
}

// ─── Flush ───────────────────────────────────────────────────────────────

async function settle(
  store: OutboxStore, item: OutboxItem, res: SendResult, handlers: OutboxHandlers, out: FlushOutcome,
): Promise<boolean> {
  out.sent += 1;
  if (res.status === "retry") {
    const counts = (res.httpStatus ?? 0) >= 500;
    const attempts = item.attempts + (counts ? 1 : 0);
    if (counts && attempts >= MAX_SERVER_ERRORS) {
      await store.remove(item.client_event_id);
      out.rejected += 1;
      handlers.onRejected?.(item, { status: "rejected", message: res.message, httpStatus: res.httpStatus });
      return true;
    }
    await store.put({ ...item, attempts, last_error: res.message });
    out.stoppedOn = res.message;
    return false;
  }
  await store.remove(item.client_event_id);
  if (res.status === "ok") { out.applied += 1; handlers.onApplied?.(item, res); }
  else if (res.status === "conflict") { out.conflicts += 1; handlers.onConflict?.(item, res); }
  else { out.rejected += 1; handlers.onRejected?.(item, res); }
  return true;
}

/**
 * Send everything queued, oldest first. Uses the batch route while the
 * transport has one, else one item at a time. Stops at the first transient
 * failure and leaves the rest queued, in order, for the next flush.
 */
export async function flushOutbox(
  store: OutboxStore, transport: Transport, handlers: OutboxHandlers = {}, batchSize: number = BATCH_SIZE,
): Promise<FlushOutcome> {
  const out: FlushOutcome = { sent: 0, applied: 0, conflicts: 0, rejected: 0, remaining: 0 };
  let queue = sortItems(await store.list());
  let batchOk = typeof transport.sendBatch === "function";
  while (queue.length && !out.stoppedOn) {
    const chunk = queue.slice(0, batchSize);
    const results = batchOk && transport.sendBatch ? await transport.sendBatch(chunk) : null;
    if (results === null) {
      // No batch route (or it is gone): one at a time, in order.
      batchOk = false;
      const item = chunk[0];
      if (!(await settle(store, item, await transport.send(item), handlers, out))) break;
      queue = queue.slice(1);
      continue;
    }
    for (let i = 0; i < chunk.length; i++) {
      const res = results[i] ?? { status: "retry" as const, message: "No result for this action" };
      if (!(await settle(store, chunk[i], res, handlers, out))) break;
    }
    queue = queue.slice(chunk.length);
  }
  out.remaining = (await store.list()).length;
  return out;
}
