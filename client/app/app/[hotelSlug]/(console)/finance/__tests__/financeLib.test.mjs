/**
 * Finance console helpers (04 spec 6.8): n/a is never zero, months carry no
 * timezone, the mapping review groups by department with unmapped first,
 * bulk confirm sizes only at 0.85 or more, and the below-GOP guard mirrors
 * the server. `npm test` picks it up; alone, run it from this folder (the
 * brackets in the path read as a glob): node --test financeLib.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  addMonths, anomalyLine, batchStatus, bulkEligible, codeText, finApi, finHref, groupByDept, majorToMinor, monthKey,
  monthLabel, nameDir, needsOverrideReason, num, pctText, plottableWeeks, ratioText,
} from "../lib.ts";

const CATALOG = [
  { key: "rooms.revenue", schedule: 1, dept: "rooms", category: "revenue", is_payroll: false, label: "Rooms revenue" },
  { key: "ag.other", schedule: 5, dept: "ag", category: "undistributed", is_payroll: false, label: "A&G other" },
  { key: "below_line.depreciation", schedule: null, dept: "below_line", category: "below_line", is_payroll: false,
    label: "Depreciation" },
];

test("num keeps n/a as null and never as zero", () => {
  assert.equal(num(null), null);
  assert.equal(num(undefined), null);
  assert.equal(num(""), null);
  assert.equal(num("abc"), null);
  assert.equal(num("152.50"), 152.5);
  assert.equal(num(0), 0);
});

test("percent and ratio text", () => {
  assert.equal(pctText("32.83"), "32.8%");
  assert.equal(pctText("32.83", 2), "32.83%");
  assert.equal(pctText(null), "n/a");
  assert.equal(ratioText("-0.478"), "-47.8%");
  assert.equal(ratioText("0.0520"), "+5.2%");
  assert.equal(ratioText(null), "n/a");
});

test("majorToMinor reads typed amounts", () => {
  assert.equal(majorToMinor("424,100"), 42410000);
  assert.equal(majorToMinor("1234.5"), 123450);
  assert.equal(majorToMinor(""), null);
  assert.equal(majorToMinor("12a"), null);
});

test("months are calendar keys with no day shift", () => {
  assert.equal(monthKey("2026-08-01"), "2026-08");
  assert.equal(monthLabel("2026-08-01"), "August 2026");
  assert.equal(monthLabel("2026-01", true), "Jan 2026");
  assert.equal(addMonths("2026-01", -1), "2025-12");
  assert.equal(addMonths("2025-12", 1), "2026-01");
  assert.equal(addMonths("2026-08", -12), "2025-08");
});

test("the NRevPAR strip skips weeks with no available rooms", () => {
  const weeks = [{ available_rn: 560 }, { available_rn: 0 }, { available_rn: null }, { available_rn: 70 }];
  assert.equal(plottableWeeks(weeks).length, 2);
});

test("paths", () => {
  assert.equal(finApi("al bahar", "/weekly"), "/hotels/al%20bahar/finance/weekly");
  assert.equal(finHref("x", "/mapping"), "/app/x/finance/mapping");
});

test("Arabic names render right to left", () => {
  assert.equal(nameDir("ar", "ايجار"), "rtl");
  assert.equal(nameDir("mixed", "Rent ايجار"), "auto");
  assert.equal(nameDir(null, "رواتب"), "auto");
  assert.equal(nameDir("en", "Salaries"), undefined);
});

test("groups by department with unmapped first", () => {
  const accts = [
    { usali_dept: "ag", usali_line: "ag.other" },
    { usali_dept: null, usali_line: null },
    { usali_dept: "rooms", usali_line: "rooms.revenue" },
    { usali_dept: null, usali_line: "below_line.depreciation" },
  ];
  const groups = groupByDept(accts, CATALOG);
  assert.deepEqual(groups.map(g => g.dept), ["unmapped", "rooms", "ag", "below_line"]);
  assert.equal(groups[0].label, "Not mapped yet");
});

test("bulk confirm only counts unlocked proposals at 0.85 or more", () => {
  const accts = [
    { ai_confidence: "0.92", locked: false, usali_line: "ag.other" },
    { ai_confidence: "0.85", locked: false, usali_line: "ag.other" },
    { ai_confidence: "0.84", locked: false, usali_line: "ag.other" },
    { ai_confidence: "0.99", locked: true, usali_line: "ag.other" },
    { ai_confidence: null, locked: false, usali_line: null },
  ];
  assert.equal(bulkEligible(accts, 0.85).length, 2);
});

test("below-GOP words above GOP need a reason, as on the server", () => {
  const rent = { below_gop_word: true };
  assert.equal(needsOverrideReason(rent, "ag.other", CATALOG), true);
  assert.equal(needsOverrideReason(rent, "below_line.depreciation", CATALOG), false);
  assert.equal(needsOverrideReason({ below_gop_word: false }, "ag.other", CATALOG), false);
});

test("labels", () => {
  assert.equal(batchStatus("review").label, "Ready to publish");
  assert.equal(batchStatus("weird").label, "weird");
  assert.equal(anomalyLine({ evidence: { value_pct: "31.20", expected_pct: "24.60" }, zscore: "3.12" }),
    "31.2% against 24.6% usual, z 3.1");
});

test("tile deltas: n/a and zero bases give none, costs read red when rising", async () => {
  const { changeDelta, ppDelta } = await import("../lib.ts");
  assert.equal(changeDelta(null, "10", true), undefined);
  assert.equal(changeDelta("10", "0", true), undefined);
  assert.deepEqual(changeDelta("110", "100", true), { value: "+10.0%", direction: "up", good: true, label: undefined });
  assert.equal(changeDelta("110", "100", false).good, false);
  assert.equal(ppDelta("26.98", "24.10", false).value, "+2.9 pp");
  assert.equal(ppDelta("26.98", "26.98", false).direction, "flat");
});

test("chart axis labels stay on the calendar day and weeks start on Monday", async () => {
  const { monthFromEpoch, dayFromEpoch, weeksAgo, moduleShares } = await import("../lib.ts");
  assert.equal(monthFromEpoch(Date.UTC(2026, 7, 1)), "Aug 2026");
  assert.equal(dayFromEpoch(Date.UTC(2026, 7, 3)), "Aug 3");
  assert.equal(weeksAgo("2026-09-29", 13), "2026-06-29");
  assert.equal(new Date("2026-06-29T00:00:00Z").getUTCDay(), 1);
  const rows = moduleShares({ amenities: 73000, profit_guard: 302000, inquiries: 121000 });
  assert.deepEqual(rows.map(r => r.module), ["profit_guard", "inquiries", "amenities"]);
  assert.equal(Math.round(rows[0].share * 1000), 609);
});

test("a month-less P&L reads as a month to pick, not a raw code (QA OWN-07)", () => {
  const t = codeText("period_month_required", "fallback");
  assert.notEqual(t, "fallback");
  assert.match(t, /month/);
  assert.doesNotMatch(t, /trial balance/);
  assert.equal(codeText(undefined, "fallback"), "fallback");
});
