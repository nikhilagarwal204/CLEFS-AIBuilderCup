// Outbox core tests (03 spec 1.9: "a test for the outbox"). The client has
// no vitest dependency and package.json is frozen, so this runs on Node's own
// runner with its built-in TypeScript stripping:
//   cd client && node --test app/device/lib/outbox.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { flushOutbox, makeItem, nextSeq, newEventId, MAX_SERVER_ERRORS } from "./outboxCore.ts";

// ─── Fakes ───

function memoryStore(items = []) {
  const map = new Map(items.map(i => [i.client_event_id, i]));
  return {
    map,
    list: async () => Array.from(map.values()),
    put: async it => { map.set(it.client_event_id, it); },
    remove: async id => { map.delete(id); },
  };
}

/** A fake server that is idempotent on client_event_id, like the real one. */
function fakeServer() {
  const applied = [];
  const seen = new Set();
  return {
    applied,
    handle(item) {
      if (seen.has(item.client_event_id)) return { status: "ok", replayed: true };
      seen.add(item.client_event_id);
      applied.push(`${item.action}:${item.request_id}`);
      return { status: "ok" };
    },
  };
}

const item = (action, request_id, seq) =>
  makeItem({ kind: "transition", action, request_id, base_version: 1, staff_id: 7 }, seq);

// ─── Tests ───

test("flushes in queue order, oldest first", async () => {
  const store = memoryStore([item("complete", 1, 3), item("ack", 1, 1), item("start", 1, 2)]);
  const server = fakeServer();
  const out = await flushOutbox(store, { send: async it => server.handle(it) });
  assert.deepEqual(server.applied, ["ack:1", "start:1", "complete:1"]);
  assert.equal(out.applied, 3);
  assert.equal(out.remaining, 0);
});

test("a network failure stops the flush and keeps the rest in order", async () => {
  const a = item("ack", 1, 1), b = item("start", 1, 2), c = item("complete", 1, 3);
  const store = memoryStore([a, b, c]);
  const server = fakeServer();
  let online = false;
  const transport = {
    send: async it => (it === b || it.client_event_id === b.client_event_id) && !online
      ? { status: "retry", message: "offline", httpStatus: 0 }
      : server.handle(it),
  };
  const first = await flushOutbox(store, transport);
  assert.equal(first.applied, 1);
  assert.equal(first.remaining, 2);
  assert.equal(first.stoppedOn, "offline");
  // Network failures never count toward the give-up limit.
  assert.equal(store.map.get(b.client_event_id).attempts, 0);
  online = true;
  const second = await flushOutbox(store, transport);
  assert.equal(second.applied, 2);
  assert.deepEqual(server.applied, ["ack:1", "start:1", "complete:1"]);
});

test("a lost response is replayed safely (idempotent client_event_id)", async () => {
  const a = item("complete", 5, 1);
  const store = memoryStore([a]);
  const server = fakeServer();
  let drop = true;
  const transport = {
    send: async it => {
      const res = server.handle(it); // the server applied it...
      if (drop) { drop = false; return { status: "retry", message: "timeout", httpStatus: 0 }; } // ...the reply was lost
      return res;
    },
  };
  await flushOutbox(store, transport);
  const out = await flushOutbox(store, transport);
  assert.equal(out.applied, 1);
  assert.deepEqual(server.applied, ["complete:5"], "applied exactly once");
});

test("a conflict leaves the queue and tells the UI who got there first", async () => {
  const a = item("complete", 9, 1), b = item("ack", 10, 2);
  const store = memoryStore([a, b]);
  const conflicts = [];
  const transport = {
    send: async it => it.request_id === 9
      ? { status: "conflict", message: "Already done by Amina", conflict: { by: "Amina", state: "completed" } }
      : { status: "ok" },
  };
  const out = await flushOutbox(store, transport, { onConflict: (it, r) => conflicts.push(r.message) });
  assert.deepEqual(conflicts, ["Already done by Amina"]);
  assert.equal(out.conflicts, 1);
  assert.equal(out.applied, 1, "a conflict does not block the items behind it");
  assert.equal(out.remaining, 0);
});

test("falls back to one-at-a-time when the server has no batch route", async () => {
  const store = memoryStore([item("ack", 1, 1), item("start", 1, 2)]);
  const server = fakeServer();
  let batchCalls = 0;
  const transport = {
    sendBatch: async () => { batchCalls += 1; return null; },
    send: async it => server.handle(it),
  };
  const out = await flushOutbox(store, transport);
  assert.equal(batchCalls, 1, "a missing batch route is asked once per flush");
  assert.deepEqual(server.applied, ["ack:1", "start:1"]);
  assert.equal(out.remaining, 0);
});

test("batch results line up with the input; a retry in the middle stops there", async () => {
  const a = item("ack", 1, 1), b = item("ack", 2, 2), c = item("ack", 3, 3);
  const store = memoryStore([a, b, c]);
  const transport = {
    sendBatch: async items => items.map(it => it.request_id === 2
      ? { status: "retry", message: "busy", httpStatus: 503 }
      : { status: "ok" }),
    send: async () => ({ status: "ok" }),
  };
  const out = await flushOutbox(store, transport);
  assert.equal(out.applied, 1);
  assert.equal(out.remaining, 2, "b and c stay queued, in order");
  assert.equal(store.map.get(b.client_event_id).attempts, 1, "a 5xx counts toward giving up");
});

test("an item the server keeps failing with 5xx is dropped after MAX_SERVER_ERRORS", async () => {
  const a = item("complete", 1, 1);
  const store = memoryStore([{ ...a, attempts: MAX_SERVER_ERRORS - 1 }]);
  const rejected = [];
  const out = await flushOutbox(
    store,
    { send: async () => ({ status: "retry", message: "boom", httpStatus: 500 }) },
    { onRejected: it => rejected.push(it.client_event_id) },
  );
  assert.equal(out.rejected, 1);
  assert.deepEqual(rejected, [a.client_event_id]);
  assert.equal(out.remaining, 0);
});

test("a rejection (illegal transition, 403) is final and does not block the queue", async () => {
  const store = memoryStore([item("start", 1, 1), item("ack", 2, 2)]);
  const out = await flushOutbox(store, {
    send: async it => it.request_id === 1
      ? { status: "rejected", message: "Cannot start a request that is assigned", code: "illegal_transition", httpStatus: 409 }
      : { status: "ok" },
  });
  assert.equal(out.rejected, 1);
  assert.equal(out.applied, 1);
});

test("ids are UUIDs and seq keeps growing", () => {
  const id = newEventId();
  assert.match(id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  const s1 = nextSeq([], 1000);
  const s2 = nextSeq([{ seq: s1 }], 1000);
  assert.ok(s2 > s1);
  assert.equal(nextSeq([{ seq: 5 }], 1000), 1000, "never below the wall clock");
});

// ─── Alarm rule (alerts.ts) ───

const { alarmFor } = await import("./alerts.ts");
const card = (id, state, extra = {}) => ({ id, state, version: 1, timestamp: 0, room: "501", type: "x", description: "", category: null, priority: null, ...extra });
const scope = (over = {}) => ({ staffIds: [7], deviceId: 3, loopIds: new Set(), ...over });

test("chimes while a job for someone on this device is unacked, stops after the ack", () => {
  assert.equal(alarmFor([card(1, "assigned", { assigned_staff_id: 7 })], scope()), "chime");
  assert.equal(alarmFor([card(1, "acknowledged", { assigned_staff_id: 7 })], scope()), "off");
  assert.equal(alarmFor([card(1, "assigned", { assigned_staff_id: 8 })], scope()), "off", "someone else's job is quiet");
  assert.equal(alarmFor([card(1, "assigned", { assigned_device_id: 3 })], scope()), "chime", "the shared-device pool rings the device");
});

test("L1 and Security loops ring urgently until someone acks", () => {
  assert.equal(alarmFor([card(2, "new", { severity: "L1" })], scope()), "urgent");
  assert.equal(alarmFor([card(2, "in_progress", { severity: "L1" })], scope()), "off");
  assert.equal(alarmFor([card(3, "assigned", { assigned_staff_id: 99 })], scope({ loopIds: new Set([3]) })), "urgent");
});

// ─── Optimistic queue (optimistic.ts) ───

const { applyPending } = await import("./optimistic.ts");

test("queued taps show at once; completed jobs leave the list; the alarm stops on a queued ack", () => {
  const cards = [card(1, "assigned", { assigned_staff_id: 7 }), card(2, "acknowledged", { assigned_staff_id: 7 })];
  const pending = [
    makeItem({ kind: "transition", action: "ack", request_id: 1, base_version: 1, staff_id: 7 }, 1),
    makeItem({ kind: "transition", action: "complete", request_id: 2, base_version: 1, staff_id: 7 }, 2),
  ];
  const view = applyPending(cards, pending);
  assert.deepEqual(view.map(c => [c.id, c.state]), [[1, "acknowledged"]]);
  assert.equal(alarmFor(view, scope()), "off");
  assert.equal(alarmFor(cards, scope()), "chime", "without the outbox it would still ring");
});
