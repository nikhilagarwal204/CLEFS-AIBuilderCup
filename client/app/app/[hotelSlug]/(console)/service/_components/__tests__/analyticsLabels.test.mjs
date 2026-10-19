/**
 * Owner view labels (05 spec: "response times by team, floor and shift").
 * The metrics engine groups by raw keys; an owner must never read "ird",
 * "front office" or a bare "4". `npm test` picks it up; alone, run it from
 * this folder (the brackets in the path read as a glob):
 * node --test analyticsLabels.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  GROUP_TITLES, distinctLabels, floorLabel, groupLabel, rankBySlowest, shiftLabel, shortMinutes, teamLabel, titleCase,
} from "../analyticsLabels.ts";

test("the department dimension is called Team", () => {
  assert.equal(GROUP_TITLES.department, "Team");
  assert.equal(GROUP_TITLES.floor, "Floor");
  assert.equal(GROUP_TITLES.shift, "Shift");
});

test("team names, never raw keys, from keys or legacy categories", () => {
  assert.equal(groupLabel("department", "ird"), "In-Room Dining");
  assert.equal(groupLabel("department", "front_office"), "Front Office");
  assert.equal(groupLabel("department", "front office"), "Front Office");
  assert.equal(groupLabel("department", "engineering"), "Engineering");
  assert.equal(groupLabel("department", "Food & Beverage"), "In-Room Dining");
  assert.equal(groupLabel("department", "Maintenance"), "Engineering");
  // the hotel's own names win over the seeds
  assert.equal(teamLabel("ird", [{ key: "ird", name: "Room Service" }]), "Room Service");
  // an unknown key is still readable
  assert.equal(teamLabel("pool_bar"), "Pool Bar");
});

test("floors and shifts", () => {
  assert.equal(groupLabel("floor", "4"), "Floor 4");
  assert.equal(floorLabel("Level 2"), "Level 2");
  assert.equal(groupLabel("shift", "evening"), "Evening");
  assert.equal(shiftLabel("evening", [{ key: "evening", name: "Evening", start_time: "15:00", end_time: "23:00" }]),
    "Evening (15:00-23:00)");
  assert.equal(shiftLabel("hk_day"), "Hk day");
  // a chart's narrow label column drops the hours
  const shifts = [{ key: "night", name: "Night", start_time: "23:00:00", end_time: "07:00:00" }];
  assert.equal(groupLabel("shift", "night", { shifts }), "Night (23:00-07:00)");
  assert.equal(groupLabel("shift", "night", { shifts }, true), "Night");
});

test("chart minutes stay short enough for the value column", () => {
  assert.equal(shortMinutes(6.64), "6.6m");
  assert.equal(shortMinutes(0.4), "<1m");
  assert.equal(shortMinutes(42.4), "42m");
  assert.equal(shortMinutes(65), "1h 5m");
  assert.equal(shortMinutes(null), "-");
});

test("the rest of the dimensions", () => {
  assert.equal(groupLabel("language", "ar", { languages: { ar: "Arabic" } }), "Arabic");
  assert.equal(groupLabel("language", "tl"), "TL");
  assert.equal(groupLabel("source", "phone_desk", { sources: { phone_desk: "Desk call" } }), "Desk call");
  assert.equal(groupLabel("request_type", "towels", { requestTypes: [{ key: "towels", label_en: "Towels" }] }), "Towels");
  assert.equal(groupLabel("request_type", "make_up_room"), "Make up room");
  assert.equal(groupLabel("stay_stage", "pre_arrival"), "Before arrival");
  assert.equal(groupLabel("staff", "Maria Santos"), "Maria Santos");
});

test("missing values read as Not recorded", () => {
  for (const k of [null, undefined, "", "(none)", "null"]) assert.equal(groupLabel("floor", k), "Not recorded");
});

test("title case keeps the words", () => {
  assert.equal(titleCase("front_office"), "Front Office");
  assert.equal(titleCase("in-room dining"), "In Room Dining");
});

test("two keys that read the same keep distinct chart labels", () => {
  const rows = [
    { key: "ird", label: groupLabel("department", "ird") },
    { key: "Food & Beverage", label: groupLabel("department", "Food & Beverage") },
    { key: "housekeeping", label: groupLabel("department", "housekeeping") },
  ];
  assert.deepEqual(distinctLabels(rows).map(r => r.label),
    ["In-Room Dining", "In-Room Dining (Food & Beverage)", "Housekeeping"]);
});

test("owner charts rank the slowest first and drop groups with no number", () => {
  const rows = [
    { key: "a", values: { m: 2 } },
    { key: "b", values: { m: 9 } },
    { key: "c", values: { m: null } },
    { key: "d", values: { m: 5 } },
  ];
  assert.deepEqual(rankBySlowest(rows, "m").map(r => r.key), ["b", "d", "a"]);
  assert.deepEqual(rankBySlowest(rows, "m", 2).map(r => r.key), ["b", "d"]);
});
