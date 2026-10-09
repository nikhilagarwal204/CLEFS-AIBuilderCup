// The "Job sent to staff" card's logic (05 spec 3, decision 13). Node's
// runner, TS stripping:
//   cd client && node --test app/stay/__tests__/jobs.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  toMs, formatClock, trackerState, mergeJob, timeTaken, jobPhase, isSettled, askedFor, staffLine, sentMessage,
} from "../concierge/jobs.ts";
import { translate } from "../../lib/i18n.ts";
import en from "../i18n/en.ts";
import ar from "../i18n/ar.ts";

const tFor = (dict) => (key, vars) => translate(dict, key, vars, en);
const t = tFor(en);

const CREATED = 1_760_000_000_000;
const card = (over = {}) => ({
  item_label: "Towels", quantity: 2, room: "204", staff_first_name: "Maria", department_label: "Housekeeping",
  eta_minutes: 5, created_at: CREATED, accepted_at: null, state: "assigned", request_type: "towels",
  department_key: "housekeeping", ...over,
});

// ─── Times ───

test("toMs reads epoch ms, epoch seconds, numeric strings and ISO times", () => {
  assert.equal(toMs(CREATED), CREATED);
  assert.equal(toMs(CREATED / 1000), CREATED);
  assert.equal(toMs(String(CREATED)), CREATED);
  assert.equal(toMs("2026-10-19T12:00:00Z"), Date.UTC(2026, 9, 19, 12));
  assert.equal(toMs("2026-10-19 12:00:00+00:00"), Date.UTC(2026, 9, 19, 12), "a Python str() of a datetime");
  for (const bad of [null, undefined, "", "soon", 0, -5, true, Number.NaN]) assert.equal(toMs(bad), null);
});

test("formatClock is mm:ss, and h:mm:ss from 100 minutes", () => {
  assert.equal(formatClock(0), "00:00");
  assert.equal(formatClock(41_000), "00:41");
  assert.equal(formatClock(41_999), "00:41");
  assert.equal(formatClock(725_000), "12:05");
  assert.equal(formatClock(99 * 60_000 + 59_000), "99:59");
  assert.equal(formatClock(6_000_000), "1:40:00");
  assert.equal(formatClock(-3000), "00:00");
});

test("time taken runs from the ask until a staff member accepts, then freezes at the server's number", () => {
  const asked = CREATED - 3000;
  assert.deepEqual(timeTaken(card(), asked + 12_000, asked), { ms: 12_000, frozen: false });
  assert.deepEqual(timeTaken(card(), CREATED + 9000), { ms: 9000, frozen: false }, "after a reload: from creation");
  const accepted = card({ accepted_at: CREATED + 41_000 });
  assert.deepEqual(timeTaken(accepted, CREATED + 300_000, asked), { ms: 41_000, frozen: true },
    "accepted_at - created_at, what the console shows");
  assert.deepEqual(timeTaken(card(), CREATED + 999_000, null, CREATED + 60_000), { ms: 60_000, frozen: true },
    "a job finished without an accept stops where it finished");
});

// ─── State ───

test("Service states map to the tracker's four words", () => {
  assert.equal(trackerState("new"), "received");
  assert.equal(trackerState("reopened"), "received");
  assert.equal(trackerState("acknowledged"), "assigned");
  assert.equal(trackerState("in_progress"), "on_the_way");
  assert.equal(trackerState("completed"), "done");
  assert.equal(trackerState("on_the_way"), "on_the_way");
  assert.equal(trackerState("mystery"), null);
});

test("the pill follows the job: sent, accepted, on the way, done, then the question", () => {
  assert.equal(jobPhase(card(), undefined), "sent");
  assert.equal(jobPhase(card({ accepted_at: CREATED + 5000 }), { state: "assigned" }), "accepted");
  assert.equal(jobPhase(card({ state: "in_progress" }), undefined), "on_the_way");
  assert.equal(jobPhase(card(), { state: "on_the_way" }), "on_the_way");
  assert.equal(jobPhase(card(), { state: "done", needs_guest_confirmation: true }), "confirm");
  assert.equal(jobPhase(card(), { state: "done", needs_guest_confirmation: false, can_confirm: true }), "done");
  assert.equal(jobPhase(card(), { state: "done", can_confirm: true }), "confirm", "an older server's Resolved?");
  assert.equal(jobPhase(card(), { state: "done", can_confirm: false }), "done");
  assert.equal(jobPhase(card({ accepted_at: CREATED }), { state: "received" }, true), "reopened");
  assert.equal(jobPhase(card(), { state: "done", cancelled: true }), "cancelled");
});

test("a job settles once answered, closed or no longer asking", () => {
  assert.equal(isSettled({ state: "assigned" }), false);
  assert.equal(isSettled({ state: "done", needs_guest_confirmation: true, can_confirm: true }), false);
  assert.equal(isSettled({ state: "done", needs_guest_confirmation: false, guest_confirmation: null }), true);
  assert.equal(isSettled({ state: "done", guest_confirmation: "yes", can_confirm: false }), true);
  assert.equal(isSettled({ state: "done", closed_at: CREATED }), true);
  assert.equal(isSettled({ state: "done", can_confirm: true, guest_confirmation: null }), false);
  assert.equal(isSettled({ state: "done", can_confirm: false, guest_confirmation: null }), true);
  assert.equal(isSettled({ state: "done", cancelled: true }), true);
});

test("the tracker refreshes who, how soon and when accepted; the card keeps what was asked", () => {
  const merged = mergeJob(card({ staff_first_name: null }), {
    state: "assigned", staff_first_name: "Priya", eta_minutes: 7, accepted_at: CREATED + 30_000,
    request_type_label: "Towel", department_label: "Housekeeping",
  });
  assert.equal(merged.staff_first_name, "Priya");
  assert.equal(merged.eta_minutes, 7);
  assert.equal(merged.accepted_at, CREATED + 30_000);
  assert.equal(merged.item_label, "Towels");
  assert.equal(merged.quantity, 2);
  const fromTracker = mergeJob(null, { request_type_label: "Water", room: "305", created_at_ms: CREATED, state: "received" });
  assert.equal(fromTracker.item_label, "Water");
  assert.equal(fromTracker.created_at, CREATED);
});

// ─── Words ───

test("the card reads like the deck in English", () => {
  assert.equal(askedFor(t, card()), "Extra towels (2)");
  assert.equal(askedFor(t, card({ quantity: 1 })), "Extra towels");
  assert.equal(askedFor(t, card({ request_type: "spa", item_label: "Spa booking", quantity: null })), "Spa booking",
    "a type the page has no words for: the server's label");
  assert.equal(staffLine(t, card()), "Maria · Housekeeping");
  assert.equal(staffLine(t, card({ staff_first_name: null })), "Housekeeping");
  assert.equal(staffLine(t, card({ staff_first_name: null, department_label: null, department_key: null })), null);
});

test("the item and the team come in the guest's language", () => {
  const tAr = tFor(ar);
  assert.equal(askedFor(tAr, card()), "مناشف إضافية (2)");
  assert.equal(staffLine(tAr, card()), "Maria · التدبير المنزلي");
});

test("a chip's confirmation says only what is known", () => {
  assert.equal(sentMessage(t, card()), "Of course. Housekeeping has been notified. Maria is on the way, about 5 minutes.");
  assert.equal(sentMessage(t, card({ staff_first_name: null })),
    "Of course. Housekeeping has been notified. Someone will be with you in about 5 minutes.");
  assert.equal(sentMessage(t, card({ eta_minutes: null })), "Of course. Housekeeping has been notified and your request is on its way.");
  assert.equal(sentMessage(t, null), en.chat.requestSent);
  assert.equal(sentMessage(t, card({ staff_first_name: "Ahmed" }), true), "The front desk has your request. Ahmed will contact you shortly.");
  assert.equal(sentMessage(t, null, true), en.chat.frontDesk);
});
