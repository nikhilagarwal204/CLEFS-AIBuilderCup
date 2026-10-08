// Task-sheet history wording (QA GS-11). Node's runner with TS stripping:
//   cd client && node --test app/device/lib/history.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { eventWho } from "./history.ts";

const ev = (over) => ({
  id: 1, request_id: 7, at: null, kind: "transition", action: null, from_state: null, to_state: null,
  version: 1, actor_type: null, actor_staff_id: null, actor_label: null, device_id: null, floor: null,
  department_key: null, payload: null, ...over,
});
const names = new Map([[8, "Ahmed Khan (DEMO)"]]);
const staffName = (id) => names.get(id);

test("a device action names the staff member, not the device", () => {
  const e = ev({ actor_type: "device", actor_staff_id: 8, actor_label: "QA gs desk 2", action: "ack", to_state: "acknowledged" });
  assert.equal(eventWho(e, staffName), "Ahmed Khan (DEMO)");
});

test("an unknown staff id on a device action does not show the device name", () => {
  const e = ev({ actor_type: "device", actor_staff_id: 99, actor_label: "QA gs desk 2", action: "ack" });
  assert.equal(eventWho(e, staffName), "Staff");
  assert.equal(eventWho(e), "Staff");
});

test("the automatic reassign after a guest reopen reads Auto, not guest", () => {
  const e = ev({ actor_type: "guest", actor_label: "guest", action: "assign", to_state: "assigned" });
  assert.equal(eventWho(e, staffName), "Auto");
});

test("what a guest really did stays attributed to the guest", () => {
  assert.equal(eventWho(ev({ actor_type: "guest", actor_label: "guest", action: "reopen", to_state: "reopened" })), "Guest");
  assert.equal(eventWho(ev({ actor_type: "guest", actor_label: "whatsapp", kind: "created" })), "Guest");
  assert.equal(eventWho(ev({ actor_type: "guest", kind: "note" })), "Guest");
});

test("console users and the system keep their labels", () => {
  assert.equal(eventWho(ev({ actor_type: "user", actor_label: "Karim (Duty manager)" })), "Karim (Duty manager)");
  assert.equal(eventWho(ev({ actor_type: "system", actor_label: "system", kind: "sla_breach" })), "system");
  assert.equal(eventWho(ev({})), "System");
});
