/**
 * Brief pure helpers: the decisions offered per state (must stay inside
 * core TRANSITIONS), the Adjust bounds convention (must match the server's
 * whatsapp.adjust_bounds, which the mobile brief and WhatsApp share), money
 * input parsing without float rounding, and ISO week keys (the weekly
 * report path, checked against Python's isocalendar).
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test pure.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addDays, adjustBounds, adjustFields, decisionsFor, isoWeekOf, parseMajorDigits, plural, shiftIsoWeek,
  periodLabel, signalDecideBody, signalDecisionsFor,
} from "../pure.ts";

// ─── Decisions ───

// clefs.core.actions TRANSITIONS keys plus REMINDABLE (remind keeps the state).
const LEGAL = new Set([
  "proposed:approve", "proposed:adjust", "approved:adjust", "proposed:assign", "approved:assign", "assigned:assign",
  "in_progress:assign", "proposed:dispute", "proposed:ignore", "proposed:snooze", "approved:snooze", "assigned:snooze",
  "snoozed:snooze",
  ...["proposed", "approved", "assigned", "in_progress", "submitted", "awaiting_counterparty", "rejected_by_counterparty"]
    .map(s => `${s}:remind`),
]);

test("every offered decision is a legal core transition (snoozed wakes on any)", () => {
  for (const s of ["proposed", "approved", "assigned", "in_progress", "submitted", "awaiting_counterparty", "rejected_by_counterparty"]) {
    for (const d of decisionsFor(s, "pg.dispute")) assert.ok(LEGAL.has(`${s}:${d}`), `${s}:${d}`);
  }
  assert.deepEqual([...decisionsFor("snoozed", null)], ["approve", "assign", "remind"]);
});

test("terminal and unknown states offer nothing", () => {
  for (const s of ["verified", "rejected", "cancelled", "expired", "failed", "observed", null, undefined, "nope"]) {
    assert.deepEqual([...decisionsFor(s, "pg.x")], []);
  }
});

test("dispute only for Profit Guard types", () => {
  assert.ok(decisionsFor("proposed", "pg.no_show_commission").includes("dispute"));
  assert.ok(decisionsFor("proposed", "profit_guard.x").includes("dispute"));
  assert.ok(!decisionsFor("proposed", "inquiries.approve").includes("dispute"));
  assert.ok(!decisionsFor("proposed", null).includes("dispute"));
});

// ─── Adjust bounds ───

test("explicit params.adjust wins", () => {
  const b = adjustBounds({ adjust: { field: "rate_minor", min_minor: 38000, max_minor: 52000, label: "rate" }, rate_minor: 45000 }, "AED");
  assert.deepEqual(b, { field: "rate_minor", label: "rate", currency: "AED", min_minor: 38000, max_minor: 52000 });
});

test("min_/max_ pair, then floor/ceiling", () => {
  assert.deepEqual(adjustBounds({ price_minor: 100, min_price_minor: 50, max_price_minor: 150 }, "AED"),
    { field: "price_minor", label: "price", currency: "AED", min_minor: 50, max_minor: 150 });
  assert.deepEqual(adjustBounds({ rate_minor: 45000, floor_minor: 38000, ceiling_minor: 52000 }, "AED"),
    { field: "rate_minor", label: "rate", currency: "AED", min_minor: 38000, max_minor: 52000 });
});

test("the proposal is adjusted, never the baseline (QA OWN-01, dr.rate_change key order)", () => {
  const rate = { current_bar_minor: 118500, recommended_minor: 136000, floor_minor: 100000, ceiling_minor: 150000 };
  assert.deepEqual(adjustBounds(rate, "AED"),
    { field: "recommended_minor", label: "recommended", currency: "AED", min_minor: 100000, max_minor: 150000 });
  // A baseline alone is never offered: editing it would be stored where nothing reads it.
  assert.equal(adjustBounds({ current_bar_minor: 118500, floor_minor: 1, ceiling_minor: 2 }, "AED"), null);
  assert.deepEqual(adjustFields({ old_price_minor: 1, price_minor: 2, proposed_price_minor: 3 }),
    ["proposed_price_minor", "price_minor"]);
});

test("no bounds, or non-integer amounts, means null", () => {
  assert.equal(adjustBounds({ rate_minor: 45000 }, "AED"), null);
  assert.equal(adjustBounds({ rate_minor: "45000", floor_minor: 1, ceiling_minor: 2 }, "AED"), null);
  assert.equal(adjustBounds(null, "AED"), null);
  assert.equal(adjustBounds({ adjust: { field: "x" } }, "AED"), null);
});

// ─── Money input ───

test("major text to minor units, exactly", () => {
  assert.equal(parseMajorDigits("455", 2), 45500);
  assert.equal(parseMajorDigits("AED 1,020.50", 2), 102050);
  assert.equal(parseMajorDigits("0.1", 2), 10);
  assert.equal(parseMajorDigits("12.345", 3), 12345);
  assert.equal(parseMajorDigits("1500", 0), 1500);
  assert.equal(parseMajorDigits("455.", 2), 45500);
});

test("refuses junk, negatives and extra decimals", () => {
  for (const t of ["", "abc", "-5", "1.234", "1e3", "4.5.6"]) assert.equal(parseMajorDigits(t, 2), null, t);
  assert.equal(parseMajorDigits("1.5", 0), null);
});

// ─── ISO weeks ───

test("ISO week keys match Python isocalendar", () => {
  assert.equal(isoWeekOf("2026-09-29"), "2026-W40");
  assert.equal(isoWeekOf("2026-01-01"), "2026-W01");
  assert.equal(isoWeekOf("2027-01-01"), "2026-W53");
  assert.equal(isoWeekOf("2020-12-31"), "2020-W53");
  assert.equal(isoWeekOf("2021-01-03"), "2020-W53");
});

test("shifting weeks crosses years", () => {
  assert.equal(shiftIsoWeek("2026-W40", -1), "2026-W39");
  assert.equal(shiftIsoWeek("2026-W01", -1), "2025-W52");
  assert.equal(shiftIsoWeek("2020-W53", 1), "2021-W01");
  assert.equal(shiftIsoWeek("bad", 1), null);
});

test("addDays is calendar arithmetic", () => {
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(isoWeekOf(addDays("2026-09-29", -7)), "2026-W39");
});

// ─── Signal-only items (QA OWN-02) ───

test("signal-only items get a bar, verified and closed ones do not", () => {
  assert.deepEqual([...signalDecisionsFor({ section: "money_lost", recommended_action: { type: "pg.vcc_charge" } })],
    ["approve", "assign", "snooze", "ignore"]);
  assert.deepEqual([...signalDecisionsFor({ section: "service_risk", recommended_action: null })], ["assign", "snooze", "ignore"]);
  assert.deepEqual([...signalDecisionsFor({ section: "verified", recommended_action: { type: "x" } })], []);
  assert.deepEqual([...signalDecisionsFor({ section: "service_risk" }, "snoozed")], []);
  assert.deepEqual([...signalDecisionsFor({ section: "service_risk" }, "surfaced")], ["assign", "snooze", "ignore"]);
});

test("bar decisions map to the signal route's verbs", () => {
  assert.deepEqual(signalDecideBody("approve"), { decision: "propose" });
  assert.deepEqual(signalDecideBody("assign", { assignee_user_id: 7, note: " go " }), { decision: "assign", assignee_user_id: 7, note: "go" });
  assert.equal(signalDecideBody("assign"), null);
  assert.deepEqual(signalDecideBody("ignore"), { decision: "dismiss", note: undefined });
  assert.equal(signalDecideBody("dispute"), null);
  assert.equal(plural(1, "action"), "1 action");
  assert.equal(plural(2, "action"), "2 actions");
});

test("weekly and monthly headers name their period, not the build day (QA OWN-14)", () => {
  assert.equal(periodLabel("monthly", "2026-08-01", "2026-08-31"), "August 2026");
  // ICU spells September "Sep" or "Sept" depending on its version.
  assert.match(periodLabel("weekly", "2026-09-21", "2026-09-27"), /^21 Sept? to 27 Sept? 2026$/);
  assert.equal(periodLabel("daily", "2026-09-29", "2026-09-29"), null);
  assert.equal(periodLabel("monthly", null, null), null);
});
