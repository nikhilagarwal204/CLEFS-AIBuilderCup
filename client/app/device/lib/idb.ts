/* ═══════════════════════════════════════════════════════════════════════
   Tiny IndexedDB wrapper for the floor device (03 spec 1.6). Plain
   IndexedDB, no dependency: two object stores.

   - "kv":     small device facts (paired hotel, device info, feed cursor,
               handover acks, LEARN checkboxes), keyed by string.
   - "outbox": queued actions keyed by client_event_id (see outboxCore.ts).

   Every call degrades to an in-memory map when IndexedDB is missing or
   throws (private windows, some kiosk browsers, SSR), so the app still
   works for the life of the page; it just cannot survive a reload offline.
   ═══════════════════════════════════════════════════════════════════════ */

const DB_NAME = "clefs-device";
const DB_VERSION = 1;
export const KV = "kv";
export const OUTBOX = "outbox";

type StoreName = typeof KV | typeof OUTBOX;

let dbPromise: Promise<IDBDatabase | null> | null = null;
// Fallback when IndexedDB is unavailable. Same shape as the two stores.
const memory: Record<StoreName, Map<string, unknown>> = { kv: new Map(), outbox: new Map() };

function openDb(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise;
  dbPromise = new Promise(resolve => {
    try {
      if (typeof indexedDB === "undefined") return resolve(null);
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(KV)) db.createObjectStore(KV);
        if (!db.objectStoreNames.contains(OUTBOX)) db.createObjectStore(OUTBOX);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => resolve(null);
      // Another tab holds an older version open: fall back rather than hang.
      req.onblocked = () => resolve(null);
    } catch {
      resolve(null);
    }
  });
  return dbPromise;
}

function run<T>(store: StoreName, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest): Promise<T | undefined> {
  return openDb().then(db => {
    if (!db) return undefined;
    return new Promise<T | undefined>(resolve => {
      try {
        const tx = db.transaction(store, mode);
        const req = fn(tx.objectStore(store));
        // Resolve on transaction completion so a write is durable before we
        // report success (the outbox relies on this ordering).
        tx.oncomplete = () => resolve(req.result as T);
        tx.onerror = () => resolve(undefined);
        tx.onabort = () => resolve(undefined);
      } catch {
        resolve(undefined);
      }
    });
  });
}

// ─── Public API ──────────────────────────────────────────────────────────

export async function idbGet<T>(store: StoreName, key: string): Promise<T | undefined> {
  const db = await openDb();
  if (!db) return memory[store].get(key) as T | undefined;
  return run<T>(store, "readonly", s => s.get(key));
}

export async function idbSet(store: StoreName, key: string, value: unknown): Promise<void> {
  const db = await openDb();
  if (!db) { memory[store].set(key, value); return; }
  await run(store, "readwrite", s => s.put(value, key));
}

export async function idbDelete(store: StoreName, key: string): Promise<void> {
  const db = await openDb();
  if (!db) { memory[store].delete(key); return; }
  await run(store, "readwrite", s => s.delete(key));
}

export async function idbAll<T>(store: StoreName): Promise<T[]> {
  const db = await openDb();
  if (!db) return Array.from(memory[store].values()) as T[];
  return (await run<T[]>(store, "readonly", s => s.getAll())) ?? [];
}

export async function idbClear(store: StoreName): Promise<void> {
  const db = await openDb();
  if (!db) { memory[store].clear(); return; }
  await run(store, "readwrite", s => s.clear());
}
