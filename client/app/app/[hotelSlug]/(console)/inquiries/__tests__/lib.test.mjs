/**
 * Inquiry Guard console helpers (04 spec 4.11 UI). The quote editor turns a
 * typed rate into minor units and colours each night against its guardrails;
 * a float slip or a wrong band would put a wrong price in front of a guest,
 * so both are pinned here. `npm test` picks this up; alone, run it from this
 * folder (the brackets in the path read as a glob): node --test lib.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  activeFilterCount, checklistState, DEFAULT_FILTERS, errorText, filterQuery, isMissingRoute, majorToMinor, minorToInput,
  nightsBetween, rateBand, roomNights, slaState, windowOpen,
} from "../lib.ts";

const NOW = Date.parse("2026-09-29T10:00:00Z");

// ─── Money input ───

test("majorToMinor is exact and strict", () => {
  assert.equal(majorToMinor("504.34"), 50434);
  assert.equal(majorToMinor("1,008.68"), 100868);
  assert.equal(majorToMinor("0.30"), 30);
  assert.equal(majorToMinor("450"), 45000);
  assert.equal(majorToMinor("450."), 45000);
  assert.equal(majorToMinor(" 007.5 "), 750);
  assert.equal(majorToMinor("1.005"), null, "three places on a 2-digit currency");
  assert.equal(majorToMinor("1.005", 3), 1005, "KWD has 3 minor digits");
  assert.equal(majorToMinor("-5"), null);
  assert.equal(majorToMinor("abc"), null);
  assert.equal(majorToMinor(""), null);
});

test("minorToInput round-trips", () => {
  for (const v of [1, 30, 50434, 100868, 45000]) assert.equal(majorToMinor(minorToInput(v)), v);
  assert.equal(minorToInput(null), "");
  assert.equal(minorToInput(1234, 0), "1234");
});

// ─── Rate band ───

test("rateBand orders floor, ceiling, then BAR", () => {
  const line = { bar_minor: 50000, floor_minor: 40000, ceiling_minor: 90000 };
  assert.equal(rateBand(line, null), "missing");
  assert.equal(rateBand(line, 39999), "below_floor");
  assert.equal(rateBand(line, 90001), "above_ceiling");
  assert.equal(rateBand(line, 49999), "below_bar", "1 fil below BAR goes to approval (4.12)");
  assert.equal(rateBand(line, 50000), "at_bar");
  const bare = { bar_minor: null, floor_minor: null, ceiling_minor: null };
  assert.equal(rateBand(bare, 45000), "no_guardrails", "the Demand stub: staff type the rate");
});

// ─── SLA, windows, room-nights ───

test("slaState", () => {
  const base = { first_response_at: null, first_response_due_at: null, sla_breached: false };
  assert.equal(slaState(base, NOW), "none");
  assert.equal(slaState({ ...base, first_response_due_at: "2026-09-29T11:00:00Z" }, NOW), "due");
  assert.equal(slaState({ ...base, first_response_due_at: "2026-09-29T09:00:00Z" }, NOW), "breached");
  assert.equal(slaState({ ...base, sla_breached: true }, NOW), "breached");
  assert.equal(slaState({ ...base, sla_breached: true, first_response_at: "2026-09-29T09:30:00Z" }, NOW), "answered");
});

test("windowOpen", () => {
  assert.equal(windowOpen("2026-09-29T10:01:00Z", NOW), true);
  assert.equal(windowOpen("2026-09-29T10:00:00Z", NOW), false);
  assert.equal(windowOpen(null, NOW), false);
  assert.equal(windowOpen("nope", NOW), false);
});

test("roomNights prefers the stated stay", () => {
  assert.deepEqual(roomNights({ nights: 3, rooms: 2, room_nights_est: 9, room_nights_estimated: true }),
    { value: 6, estimated: false });
  assert.deepEqual(roomNights({ nights: null, rooms: null, room_nights_est: 2, room_nights_estimated: true }),
    { value: 2, estimated: true });
  assert.deepEqual(roomNights({ nights: null, rooms: null, room_nights_est: null, room_nights_estimated: null }),
    { value: null, estimated: false });
  assert.equal(nightsBetween("2026-10-01", "2026-10-04"), 3);
  assert.equal(nightsBetween("2026-10-04", "2026-10-01"), null);
});

// ─── Filters and errors ───

test("filterQuery defaults to the open pipeline", () => {
  const q = filterQuery(DEFAULT_FILTERS);
  assert.equal(q.stage, "new,replied,quoted,payment_pending");
  assert.equal(q.segment, undefined);
  assert.equal(activeFilterCount(DEFAULT_FILTERS), 1);
  const q2 = filterQuery({ ...DEFAULT_FILTERS, stage: [], sla: "breached", check_in_from: "2026-10-01" });
  assert.deepEqual(q2, { limit: 200, sla: "breached", check_in_from: "2026-10-01" });
});

test("errorText and isMissingRoute", () => {
  assert.match(errorText({ code: "quote_locked", message: "x" }), /new version/);
  assert.equal(errorText({ code: "zzz", message: "server words" }), "server words");
  assert.match(errorText({ status: 403 }), /permission/);
  assert.equal(isMissingRoute({ status: 404 }), true);
  assert.equal(isMissingRoute({ status: 404, code: "not_found" }), false);
  assert.equal(isMissingRoute({ status: 405 }), true);
});

// ─── Setup checklist ───

test("checklistState never reports 'not connected' from missing data", () => {
  const ok = { data: { accounts: [] }, loading: false };
  assert.equal(checklistState({ loading: true }, ok), "loading");
  assert.equal(checklistState(ok, { loading: true }), "loading");
  assert.equal(checklistState({ loading: false, error: new Error("502") }, ok), "error");
  assert.equal(checklistState(ok, { loading: false, error: new Error("net") }), "error");
  assert.equal(checklistState(ok, { data: [], loading: false }), "ready");
  // A reload after data arrived keeps showing the settled data.
  assert.equal(checklistState({ ...ok, loading: true }, { data: [], loading: false }), "ready");
});
