/**
 * The go-live gate's headline (05 spec, the deck's "22 safety tests" and
 * "7 days in shadow mode"): the Trust pages show exactly what the server
 * measured. `npm test` picks it up; alone, run it from this folder (the
 * brackets in the path read as a glob): node --test gate.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { gateTestsText, shadowText } from "../pure.ts";

const run = { passed: 22, total: 22, expected: 22, run_id: 7, judge_model: "gemini-3.5-flash", finished_at: "2026-10-19T05:00:00Z" };

test("22/22 passed, judged by Gemini", () => {
  const t = gateTestsText(run);
  assert.equal(t.value, "22/22 passed");
  assert.equal(t.ok, true);
  assert.match(t.sub, /^Judged by Gemini \(gemini-3\.5-flash\)\./);
});

test("21/22 is not ready, and a run without the standard suite says so", () => {
  assert.deepEqual([gateTestsText({ ...run, passed: 21 }).value, gateTestsText({ ...run, passed: 21 }).ok], ["21/22 passed", false]);
  assert.equal(gateTestsText({ ...run, total: 0, passed: 0 }).value, "Not in the last run");
  assert.equal(gateTestsText({ ...run, run_id: null, total: 0, passed: 0 }).value, "Not run yet");
  assert.equal(gateTestsText(undefined).ok, false);
});

test("the shadow week", () => {
  const base = { day: 7, days: 7, started_at: null, until: null, escalations: "live" };
  assert.deepEqual([shadowText({ ...base, state: "done" }).value, shadowText({ ...base, state: "done" }).ok], ["7 of 7 days done", true]);
  assert.deepEqual([shadowText({ ...base, state: "running", day: 3 }).value, shadowText({ ...base, state: "running", day: 3 }).ok], ["Day 3 of 7", false]);
  assert.equal(shadowText({ ...base, state: "not_started" }).ok, false);
  assert.equal(shadowText({ ...base, state: "off" }).ok, true);
});
