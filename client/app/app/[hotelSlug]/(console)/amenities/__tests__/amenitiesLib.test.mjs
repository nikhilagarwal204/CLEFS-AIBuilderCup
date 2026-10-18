/**
 * Amenities console helpers (02 spec 5.3): the quick-entry payload, the
 * stepper clamp, the shift rule the server also uses, and the coverage
 * wording for days without occupancy. `npm test` picks it up; alone, run it
 * from this folder (the brackets in the path read as a glob):
 * node --test amenitiesLib.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  buildIssueLines, clampQty, coverageNote, num, parseQtyInput, qtyText, shiftForHour, totalPieces, varianceTone,
} from "../_components/lib.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

test("num reads server decimal strings and never returns NaN", () => {
  assert.equal(num("2"), 2);
  assert.equal(num("-0.5"), -0.5);
  assert.equal(num(null), 0);
  assert.equal(num("abc"), 0);
  assert.equal(qtyText("3.00"), "3");
  assert.equal(qtyText("2.25"), "2.3");
  assert.equal(qtyText("1158"), "1,158");
  assert.equal(qtyText("12034.5"), "12,034.5");
});

test("steppers stay whole and bounded", () => {
  assert.equal(clampQty(-3), 0);
  assert.equal(clampQty(2.6), 3);
  assert.equal(clampQty(5000), 999);
  assert.equal(clampQty(Number.NaN), 0);
  assert.equal(parseQtyInput("12 pcs"), 12);
  assert.equal(parseQtyInput(""), 0);
});

test("shift boundaries match the server's infer_shift", () => {
  assert.equal(shiftForHour(6), "NIGHT");
  assert.equal(shiftForHour(7), "MORNING");
  assert.equal(shiftForHour(14), "MORNING");
  assert.equal(shiftForHour(15), "EVENING");
  assert.equal(shiftForHour(22), "EVENING");
  assert.equal(shiftForHour(23), "NIGHT");
  assert.equal(shiftForHour(0), "NIGHT");
});

test("quick entry drops zero counts and leaves blank floor and staff out", () => {
  const lines = buildIssueLines({ 5: 2, 3: 0, 9: 1 }, {
    shift: "NIGHT", floor: " ", staffName: "", staffId: null, reason: "REPLENISH",
  });
  assert.deepEqual(lines, [
    { item_id: 5, qty: 2, shift: "NIGHT", reason: "REPLENISH" },
    { item_id: 9, qty: 1, shift: "NIGHT", reason: "REPLENISH" },
  ]);
});

test("quick entry prefers the picked staff id over a typed name", () => {
  const [a] = buildIssueLines({ 1: 4 }, { shift: "MORNING", floor: "6", staffName: "Aisha", staffId: 12, reason: "RETURN" });
  assert.equal(a.staff_member_id, 12);
  assert.equal(a.staff_name, undefined);
  assert.equal(a.to_floor, "6");
  // RETURN goes up positive; the server stores it negative.
  assert.equal(a.qty, 4);
  const [b] = buildIssueLines({ 1: 1 }, { shift: "MORNING", floor: "", staffName: " Ravi ", staffId: null, reason: "VIP" });
  assert.equal(b.staff_name, "Ravi");
  assert.equal(totalPieces({ 1: 2, 2: 3, 3: -1 }), 5);
});

test("variance tone treats above-expected as the leak", () => {
  assert.equal(varianceTone("22.5"), "bad");
  assert.equal(varianceTone("8"), "warn");
  assert.equal(varianceTone("-12"), "ok");
  assert.equal(varianceTone(null), "neutral");
});

test("coverage note only speaks when days are missing", () => {
  assert.equal(coverageNote(30, 0), null);
  assert.match(coverageNote(27, 3), /3 days without occupancy data are left out/);
  assert.match(coverageNote(0, 30), /No occupancy data/);
});

test("no em dash anywhere in the amenities console", () => {
  const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(e =>
    e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)]);
  for (const file of walk(ROOT)) {
    if (!/\.(tsx?|mjs)$/.test(file)) continue;
    assert.ok(!fs.readFileSync(file, "utf8").includes(String.fromCharCode(0x2014)), `em dash in ${path.relative(ROOT, file)}`);
  }
});

test("report windows end yesterday, inclusive", async () => {
  const { windowEndingYesterday, addDays } = await import("../_components/lib.ts");
  assert.deepEqual(windowEndingYesterday(7, "2026-09-29"), { from: "2026-09-22", to: "2026-09-28" });
  assert.deepEqual(windowEndingYesterday(1, "2026-03-01"), { from: "2026-02-28", to: "2026-02-28" });
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
});

test("every 5.3 console route exists", () => {
  for (const route of ["page.tsx", "items/[id]/page.tsx", "issue/page.tsx", "catalog/page.tsx", "par/page.tsx",
    "benchmarks/page.tsx"]) {
    assert.ok(fs.existsSync(path.join(ROOT, route)), `missing ${route}`);
  }
});

test("benchmarks say they need 3 hotels before anything is shown", () => {
  const src = fs.readFileSync(path.join(ROOT, "_components", "BenchmarksView.tsx"), "utf8");
  assert.match(src, /Needs ≥\$\{b\?\.min_hotels \?\? 3\} hotels in cohort/);
  // Only published rows are drawn; suppressed categories never leak a figure.
  assert.match(src, /filter\(r => r\.published\)/);
});

test("quick entry steppers are labelled and the payload builder is the one used", () => {
  const src = fs.readFileSync(path.join(ROOT, "_components", "IssuanceQuickEntry.tsx"), "utf8");
  assert.match(src, /aria-label=\{`One less \$\{item\.name_en\}`\}/);
  assert.match(src, /aria-label=\{`One more \$\{item\.name_en\}`\}/);
  assert.match(src, /buildIssueLines\(counts/);
  // Counts are never persisted: only the context is saved to localStorage.
  assert.doesNotMatch(src, /setItem\([^)]*counts/);
});

test("variance money never uses the kit's gain-green tone", () => {
  for (const f of ["Overview.tsx", "GroupReport.tsx", "ItemTrend.tsx"]) {
    const src = fs.readFileSync(path.join(ROOT, "_components", f), "utf8");
    assert.doesNotMatch(src, /tone="auto"/, `${f} colours a leak as a gain`);
  }
});

test("cohort keys read as a place and a class", async () => {
  const { cohortLabel } = await import("../_components/lib.ts");
  assert.equal(cohortLabel("AE-DXB-HOTEL_4"), "Dubai, 4-star hotels");
  assert.equal(cohortLabel("AE-SHJ-HOTEL_APT"), "Sharjah, hotel apt");
  assert.equal(cohortLabel(null), "hotels of the same city and class");
});
