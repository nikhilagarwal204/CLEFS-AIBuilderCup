/**
 * Demand Radar console model (02 spec 4.4 client): the AdjustSlider never
 * leaves [floor, ceiling], approve vs adjust, levers carried in the note,
 * fixed DI bands for the heatmap, the comp strip scale, pace alignment,
 * change-history wording and the manual-event checks. `npm test` picks it
 * up; alone, run it from this folder (the brackets in the path read as a
 * glob): node --test model.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  addDays, campaignText, changeFromBar, changeText, clampRate, decideBody, guardrailBody, guardrailScope, heatLevel, isoWeekday, leadPad, leverNote,
  manualEventBody, nearestIndex, NOTE_MAX, num, paceGap, paceSeries, rankValue, ratePositions, sharesTotal, signedPct,
  sliderBounds, stripGeometry, validateGuardrail, validateManualEvent,
} from "../_lib/model.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

// ─── Numbers and dates ───

test("num reads Decimal strings and never returns NaN", () => {
  assert.equal(num("412.50"), 412.5);
  assert.equal(num(7), 7);
  assert.equal(num(""), null);
  assert.equal(num("abc"), null);
  assert.equal(num(null), null);
  assert.equal(signedPct("12.5"), "+12.5%");
  assert.equal(signedPct("-3"), "-3%");
  assert.equal(signedPct(null), "-");
});

test("calendar arithmetic is timezone-free", () => {
  assert.equal(addDays("2026-12-31", 1), "2027-01-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(isoWeekday("2026-09-28"), 1); // Monday
  assert.equal(isoWeekday("2026-10-04"), 7); // Sunday
  assert.equal(leadPad("2026-10-01"), 3); // Thursday sits under Thu
});

// ─── Heatmap bands ───

test("heat levels use fixed DI bands, null for no data", () => {
  assert.equal(heatLevel(null), null);
  assert.equal(heatLevel(10), 0);
  assert.equal(heatLevel(35), 1);
  assert.equal(heatLevel(50), 2);
  assert.equal(heatLevel(79), 3);
  assert.equal(heatLevel(80), 4);
  assert.equal(heatLevel(100), 4);
});

// ─── AdjustSlider and decisions ───

test("slider bounds are the recommendation's floor and ceiling", () => {
  const b = sliderBounds({ floor: "400.00", ceiling: "650.00", recommended: "520.00", decided: null });
  assert.deepEqual(b, { min: 400, max: 650, step: 5, value: 520 });
  // A decided rate starts the slider where the owner left it.
  assert.equal(sliderBounds({ floor: "400", ceiling: "650", recommended: "520", decided: "545" }).value, 545);
  // A proposal outside the bounds (stale data) is pulled inside.
  assert.equal(sliderBounds({ floor: "400", ceiling: "650", recommended: "900", decided: null }).value, 650);
  assert.equal(sliderBounds({ floor: "700", ceiling: "650", recommended: "680", decided: null }), null);
  assert.equal(sliderBounds({ floor: null, ceiling: "650", recommended: "600", decided: null }), null);
});

test("clampRate rounds to 5 and the bounds always win", () => {
  assert.equal(clampRate(512, 400, 650), 510);
  assert.equal(clampRate(513, 400, 650), 515);
  assert.equal(clampRate(398, 401, 650), 401);
  assert.equal(clampRate(900, 400, 652), 652);
});

test("rate positions keep an odd floor and ceiling reachable and nothing outside", () => {
  const p = ratePositions(312, 338);
  assert.deepEqual(p, [312, 315, 320, 325, 330, 335, 338]);
  assert.ok(p.every(v => v >= 312 && v <= 338));
  assert.deepEqual(ratePositions(400, 400), [400]);
  assert.deepEqual(ratePositions(410, 400), []);
  assert.equal(p[nearestIndex(p, 327)], 325);
  assert.equal(p[nearestIndex(p, 1000)], 338);
});

test("approve keeps the proposal; a moved slider becomes adjust", () => {
  const rec = { recommended: "520.00" };
  assert.deepEqual(decideBody(rec, "approve", 520), { verb: "approve" });
  assert.deepEqual(decideBody(rec, "approve", 545, "x"), { verb: "adjust", recommended: "545.00", note: "x" });
  assert.deepEqual(decideBody(rec, "reject", 545), { verb: "reject" });
  assert.deepEqual(decideBody(rec, "approve", null), { verb: "approve" });
});

test("ticked levers travel in the note, trimmed to the route limit", () => {
  const levers = [
    { code: "close_discounts", label: "Close discounted plans" },
    { code: "min_stay_2", label: "Minimum stay 2 nights" },
  ];
  assert.equal(leverNote(levers, new Set()), undefined);
  assert.equal(leverNote(levers, new Set(["min_stay_2"])), "Levers: Minimum stay 2 nights");
  assert.equal(leverNote(levers, new Set(["close_discounts"]), " GITEX week "), "Levers: Close discounted plans. GITEX week");
  const long = leverNote(levers, new Set(), "x".repeat(400));
  assert.equal(long.length, NOTE_MAX);
  assert.ok(long.endsWith("..."));
});

test("change against BAR", () => {
  assert.equal(changeFromBar(550, "500"), 10);
  assert.equal(changeFromBar(450, "500.00"), -10);
  assert.equal(changeFromBar(450, null), null);
  assert.equal(changeFromBar(450, "0"), null);
});

// ─── CompRateStrip, pace, history ───

test("strip geometry places band and BAR on one scale, BAR outside the band still visible", () => {
  const g = stripGeometry({ min: "400", median: "450", max: "500" }, "560");
  assert.ok(g.min < g.median && g.median < g.max && g.max < g.bar);
  assert.ok(g.min > 0 && g.bar < 100);
  assert.equal(stripGeometry(null, null), null);
  const onlyBar = stripGeometry(null, "500");
  assert.equal(onlyBar.min, null);
  assert.ok(onlyBar.bar > 0 && onlyBar.bar < 100);
});

test("pace series aligns own OTB and baseline by lead, far lead first", () => {
  const own = [{ days_before: 0, rooms_otb: 60 }, { days_before: 14, rooms_otb: 30 }, { days_before: 7, rooms_otb: 45 }];
  const base = [{ days_before: 14, rooms_otb: 35 }, { days_before: 7, rooms_otb: "50" }, { days_before: 0, rooms_otb: null }];
  const s = paceSeries(own, base);
  assert.deepEqual(s.leads, [14, 7, 0]);
  assert.deepEqual(s.own, [30, 45, 60]);
  assert.deepEqual(s.baseline, [35, 50, null]);
  // The gap is read at the latest lead where both exist.
  assert.equal(paceGap(own, base), -5);
  assert.equal(paceGap([], []), null);
});

test("change rows read as one sentence", () => {
  assert.equal(
    changeText({ field: "dates", old: { start: "2027-05-04", end: "2027-05-07" }, new: { start: "2027-04-27", end: "2027-04-30" }, reason: "rescheduled" }),
    "Dates: 2027-05-04 to 2027-05-07 -> 2027-04-27 to 2027-04-30 (rescheduled by the organiser)",
  );
  assert.equal(changeText({ field: "status", old: "SCHEDULED", new: "CANCELLED", reason: null }), "Status: SCHEDULED -> CANCELLED");
  assert.equal(changeText({ field: "venue_codes", old: null, new: ["DWTC"], reason: "update" }), "Venue: none -> DWTC (update)");
});

// ─── Forms, campaigns, markets ───

const form = (over = {}) => ({
  title: "Dubai Dental Expo", category: "EXPO", start: "2026-11-02", end: "2026-11-04",
  venue_text: "DWTC", attendance: "", url: "", description: "", ...over,
});

test("manual event checks mirror the server", () => {
  assert.deepEqual(validateManualEvent(form()), {});
  assert.ok(validateManualEvent(form({ title: " " })).title);
  assert.ok(validateManualEvent(form({ start: "" })).start);
  assert.ok(validateManualEvent(form({ end: "2026-11-01" })).end);
  assert.ok(validateManualEvent(form({ attendance: "12k" })).attendance);
  assert.ok(validateManualEvent(form({ attendance: "9000000" })).attendance);
  assert.ok(validateManualEvent(form({ url: "javascript:alert(1)" })).url);
  assert.deepEqual(manualEventBody(form({ attendance: " 4000 ", url: "https://x.ae/e" })), {
    title: "Dubai Dental Expo", category: "EXPO", start: "2026-11-02", end: "2026-11-04", venue_text: "DWTC",
    attendance: 4000, urls: ["https://x.ae/e"],
  });
  // A one-day event sends no end.
  assert.equal(manualEventBody(form({ end: "2026-11-02" })).end, undefined);
});

test("campaign copy, market shares and rank value", () => {
  assert.equal(campaignText({ copy: { subject: "GITEX week", body: "Book direct" } }), "Subject: GITEX week\n\nBook direct");
  assert.equal(campaignText({ copy: { caption: "See you at GITEX" } }), "See you at GITEX");
  assert.equal(campaignText({ copy: {} }), "");
  assert.equal(sharesTotal([{ share_pct: "20" }, { share_pct: "17.5" }, { share_pct: 16 }]), 53.5);
  assert.equal(rankValue({ impact: "12000.00", confidence: 0.5 }), 6000);
  assert.equal(rankValue({ impact: null, confidence: 0.5 }), null);
});

// ─── Source guards ───

function sources(dir) {
  const out = [];
  for (const name of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, name.name);
    if (name.isDirectory() && name.name !== "__tests__") out.push(...sources(p));
    else if (/\.(tsx?|mjs)$/.test(name.name)) out.push(p);
  }
  return out;
}

test("no em dash anywhere in the demand console", () => {
  for (const f of sources(ROOT)) {
    assert.ok(!fs.readFileSync(f, "utf8").includes(String.fromCharCode(0x2014)), `em dash in ${path.relative(ROOT, f)}`);
  }
});

// ─── Guardrails ───

test("guardrail form mirrors the server checks", () => {
  const ok = { room_type_code: "", month: "", dow: "", floor: "350", ceiling: "900", step: "" };
  assert.deepEqual(validateGuardrail(ok), {});
  assert.ok(validateGuardrail({ ...ok, floor: "0" }).floor);
  assert.ok(validateGuardrail({ ...ok, ceiling: "300" }).ceiling);
  assert.ok(validateGuardrail({ ...ok, step: "150" }).step);
  assert.deepEqual(guardrailBody({ ...ok, month: "12", dow: "5", step: "10" }), {
    room_type_code: "*", month: 12, dow: 5, floor: "350", ceiling: "900", max_step_pct: 10,
  });
  assert.equal(guardrailScope({ room_type_code: "*", month: 12, dow: 5 }), "All rooms, Dec, Fri");
  assert.equal(guardrailScope({ room_type_code: "DLX", month: null, dow: null }), "DLX, any month, any day");
});

const read = rel => fs.readFileSync(path.join(ROOT, rel), "utf8");

test("comp prices carry the Indicative label wherever they are drawn", () => {
  const strip = read("_components/CompRateStrip.tsx");
  assert.match(strip, /label = "Indicative"/);
  assert.match(strip, /dr-label-ind/);
  assert.match(read("_components/CompSetPanel.tsx"), /found\.label/);
});

test("outcomes say observed, not causal; opportunity is an estimate", () => {
  assert.match(read("outcomes/page.tsx"), /Observed, not causal/);
  assert.match(read("_components/RecommendationCard.tsx"), /impact_label/);
});

test("no causal promises in the console copy (the server lint's banned phrases)", () => {
  const banned = /\b(will increase|guaranteed|will boost|will raise|will drive|will fill|certain to|definitely|will sell out|will generate)\b/i;
  for (const f of sources(ROOT)) {
    const text = fs.readFileSync(f, "utf8").replace(/CODE_TEXT[\s\S]*?\n};/, "");
    assert.ok(!banned.test(text), `causal claim in ${path.relative(ROOT, f)}`);
  }
});

test("the AdjustSlider only offers positions inside the bounds", () => {
  const slider = read("_components/AdjustSlider.tsx");
  assert.match(slider, /ratePositions\(floor, ceiling\)/);
  assert.match(slider, /clampRate\(n, floor, ceiling\)/);
});
