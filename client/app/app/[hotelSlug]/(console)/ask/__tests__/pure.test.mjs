/**
 * Ask Clefs pure helpers: unit formatting (cells arrive in major units),
 * the chart_spec -> chart kit mapping (no stacked comparisons, negatives go
 * to the diverging chart), calendar-day buckets that never shift a day, the
 * CSV export's formula guard and the trace lines.
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test pure.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  answerFromLog, bucketLabel, bucketMs, catalogLabels, cellText, changeTone, chartModel, columnDigits, formatChange, formatMinutes,
  formatValue, kitScale, reasonText, resultsOf, scaled, starterQuestions, statusMeta, tableToCsv, tickLabel, traceLines,
  viewTitle,
} from "../pure.ts";

// ─── Values ───

test("money keeps fils only under 10,000 and uses the currency", () => {
  assert.match(formatValue(412.35, { unit: "money", currency: "AED", locale: "en" }), /AED\s?412\.35/);
  assert.match(formatValue(123456.78, { unit: "money", currency: "AED", locale: "en" }), /AED\s?123,457/);
  assert.equal(formatValue(null, { unit: "money", currency: "AED" }), "–");
});

test("percent is 0-100 on the wire, counts stay whole, minutes read as time", () => {
  assert.equal(formatValue(82.4, { unit: "percent", locale: "en" }), "82.4%");
  assert.equal(formatValue(1234, { unit: "count", locale: "en" }), "1,234");
  assert.equal(formatMinutes(45), "45 min");
  assert.equal(formatMinutes(125), "2 h 5 min");
  assert.equal(formatMinutes(1500), "1 d 1 h");
  assert.equal(formatValue(2.456, { unit: "ratio", locale: "en" }), "2.46");
  assert.equal(formatValue("n/a", { unit: "count" }), "n/a");
});

test("change is signed and one decimal", () => {
  assert.equal(formatChange(4.23, "en"), "+4.2%");
  assert.equal(formatChange(-10, "en"), "-10%");
  assert.equal(formatChange(0, "en"), "0%");
  assert.equal(formatChange(null), "–");
});

test("an unknown locale falls back instead of throwing", () => {
  assert.equal(formatValue(5, { unit: "count", locale: "xx-invalid-@@" }), "5");
});

// ─── Buckets ───

test("a calendar-day bucket never shifts a day, whatever the browser zone", () => {
  assert.equal(bucketMs("2026-09-01"), Date.UTC(2026, 8, 1));
  assert.equal(bucketLabel("2026-09-01", "day", "en-US"), "Sep 1");
  assert.equal(bucketLabel("2026-09-01", "week", "en-US"), "Wk of Sep 1");
  assert.equal(bucketLabel("2026-09-01", "month", "en-US"), "Sep 2026");
  assert.equal(bucketLabel("not-a-date", "day"), "not-a-date");
  assert.equal(bucketLabel(null, "day"), "–");
});

// ─── Charts ───

const base = { unit: "money", currency: "AED", label: "Room revenue" };

test("kpi carries value, comparison and change", () => {
  const m = chartModel({ ...base, type: "kpi", x: null, series: [{ key: "value", label: "Room revenue" }],
    data: [{ value: 120000, compare_value: 100000, change_pct: 20 }] });
  assert.deepEqual(m, { kind: "kpi", label: "Room revenue", value: 120000, compare: 100000, change: 20 });
});

test("a breakdown with a comparison plots the current value only (the kit would stack them)", () => {
  const m = chartModel({ ...base, type: "column", x: "channel",
    series: [{ key: "value", label: "Room revenue" }, { key: "compare_value", label: "Comparison" }],
    data: [{ x: "BOOKING", value: 52000, compare_value: 40000 }, { x: "DIRECT", value: 31000.4, compare_value: 30000 }] });
  assert.equal(m.kind, "column");
  assert.equal(m.comparedHidden, true);
  assert.equal(m.series.length, 1);
  assert.deepEqual(m.scale, { factor: 1000, suffix: "k", hint: "AED, thousands" });
  assert.deepEqual(m.data.map(d => d.values), [[52], [31]]);
});

test("negatives go to the diverging chart instead of being dropped", () => {
  const m = chartModel({ ...base, type: "hbar", x: "channel", series: [{ key: "value", label: "Net" }],
    data: [{ x: "A", value: 500 }, { x: "B", value: -200 }] });
  assert.deepEqual(m, { kind: "diverging", rows: [{ label: "A", value: 500 }, { label: "B", value: -200 }] });
});

test("hbar keeps long labels whole; column ticks are cut to ten characters", () => {
  const m = chartModel({ ...base, unit: "count", type: "hbar", x: "department", series: [{ key: "value", label: "Requests" }],
    data: [{ x: "Housekeeping and laundry", value: 12 }, { x: null, value: 3 }] });
  assert.equal(m.kind, "hbar");
  assert.deepEqual(m.rows.map(r => r.label), ["Housekeeping and laundry", "(none)"]);
  assert.equal(tickLabel("Housekeeping"), "Housekeep…");
  assert.equal(tickLabel("Spa"), "Spa");
});

test("line maps buckets to UTC ms; comparison is dashed; grouped lines keep their keys", () => {
  const single = chartModel({ ...base, type: "line", x: "bucket",
    series: [{ key: "value", label: "Room revenue" }, { key: "compare_value", label: "Comparison" }],
    data: [{ x: "2026-09-01", value: 10, compare_value: 8 }, { x: "2026-09-02", value: null, compare_value: 9 }] });
  assert.equal(single.kind, "line");
  assert.deepEqual(single.series[0].points, [{ x: Date.UTC(2026, 8, 1), y: 10 }, { x: Date.UTC(2026, 8, 2), y: null }]);
  assert.equal(single.series[1].dashed, true);
  const grouped = chartModel({ ...base, type: "line", x: "bucket",
    series: [{ key: "s0", label: "BOOKING" }, { key: "s1", label: "DIRECT" }],
    data: [{ x: "2026-09-01", s0: 5 }, { x: "2026-10-01", s0: 6, s1: 2 }] });
  assert.deepEqual(grouped.series.map(s => [s.label, s.points.map(p => p.y)]), [["BOOKING", [5, 6]], ["DIRECT", [null, 2]]]);
});

test("nothing to plot yields none", () => {
  assert.deepEqual(chartModel(null), { kind: "none" });
  assert.deepEqual(chartModel({ ...base, type: "column", x: "c", series: [], data: [] }), { kind: "none" });
  assert.deepEqual(chartModel({ ...base, type: "column", x: "c", series: [{ key: "value", label: "x" }],
    data: [{ x: "A", value: 0 }] }), { kind: "none" });
});

test("kit scale picks thousands or millions and rounds away float noise", () => {
  assert.equal(kitScale([2_500_000], "money", "AED").hint, "AED, millions");
  assert.deepEqual(kitScale([82.4], "percent"), { factor: 1, suffix: "%", hint: "percent" });
  assert.equal(kitScale([12], "count").hint, null);
  assert.equal(scaled(0.1 + 0.2, { factor: 1, suffix: "", hint: null }), 0.3);
});

// ─── Table, CSV, trace, answers ───

const table = {
  columns: [
    { key: "bucket", label: "Month", kind: "time" },
    { key: "channel", label: "Channel", kind: "dimension" },
    { key: "value", label: "Room revenue", kind: "value", unit: "money", currency: "AED" },
    { key: "change_pct", label: "Change", kind: "change", unit: "percent" },
  ],
  rows: [["2026-09-01", "=HYPERLINK(\"x\")", 1234.5, -3.2], ["2026-10-01", "Direct, web", 99, null]],
  total: 1333.5, compare_total: null, row_count: 2, limited: false, truncated: false,
};

test("cells format by column kind", () => {
  assert.equal(cellText(table.columns[0], "2026-09-01", "month", "en-US"), "Sep 2026");
  assert.equal(cellText(table.columns[1], null), "(none)");
  assert.match(cellText(table.columns[2], 1234.5, "month", "en"), /AED\s?1,234\.50/);
  assert.equal(cellText(table.columns[3], -3.2, "month", "en"), "-3.2%");
});

test("CSV keeps raw values, units in headers, and never lets a label run as a formula", () => {
  const csv = tableToCsv(table).split("\r\n");
  assert.equal(csv[0], "Month,Channel,Room revenue (AED),Change (%)");
  assert.equal(csv[1], `2026-09-01,"'=HYPERLINK(""x"")",1234.5,-3.2`);
  assert.equal(csv[2], `2026-10-01,"Direct, web",99,`);
});

test("change tone follows higher_is_better; unknown direction stays neutral", () => {
  assert.equal(changeTone(5, true), "good");
  assert.equal(changeTone(5, false), "bad");
  assert.equal(changeTone(-5, false), "good");
  assert.equal(changeTone(0, true), "flat");
  assert.equal(changeTone(5, null), "neutral");
  assert.equal(changeTone(null, true), "neutral");
});

test("statuses and reasons read as plain sentences", () => {
  assert.equal(statusMeta("table_only").label, "Figures only");
  assert.equal(statusMeta("error").tone, "bad");
  assert.match(reasonText("guest_pii"), /single guest/);
  assert.equal(reasonText("something_new"), null);
  assert.equal(reasonText(null), null);
});

test("trace lines cover metric, formula, period, grouping, filters, comparison and source", () => {
  const lines = traceLines({
    metric: "ask.adr", metric_label: "ADR", description: null, dims: ["channel"],
    filters: [{ field: "channel", values: ["BOOKING", "EXPEDIA"] }],
    period: { preset: "last_month", from: "2026-08-01", to: "2026-08-31", label: "Last month" },
    grain: "total", compare: "prev_year", sort: "value_desc", limit: 10, unit: "money", currency: "AED",
    row_count: 2, truncated: false, derived: { numerator: "stays.room_revenue", denominator: "stays.room_nights", scale: 1 },
    source: "clefs.core.metrics",
  }, { channel: "Channel", "stays.room_revenue": "Room revenue", "stays.room_nights": "Room nights" });
  const by = Object.fromEntries(lines.map(l => [l.label, l.value]));
  assert.equal(by.Metric, "ADR (ask.adr)");
  assert.equal(by.Formula, "Room revenue / Room nights");
  assert.equal(by.Period, "Last month (2026-08-01 to 2026-08-31)");
  assert.equal(by.Grouping, "One total, by Channel");
  assert.equal(by.Filters, "Channel is BOOKING or EXPEDIA");
  assert.equal(by["Compared with"], "Same period last year");
  assert.equal(by.Order, "Highest first, first 10 rows");
  assert.equal(by.Rows, "2");
  assert.match(by.Source, /^clefs\.core\.metrics/);
});

test("a stored log row renders as an answer; results fall back to the top-level block", () => {
  const a = answerFromLog({
    id: 7, channel: "WHATSAPP", question: "ADR last month?", status: "answered", unsupported: false, reason: null,
    grounded: true, regenerated: false, answer: { answer_text: "ADR was AED 412.", table, chart_spec: null, trace: null },
    tool_calls: [], saved_view_id: null, latency_ms: 900, tokens: 300, feedback: null, feedback_note: null,
    created_at: "2026-09-01T00:00:00Z",
  });
  assert.equal(a.log_id, 7);
  assert.equal(a.answer_text, "ADR was AED 412.");
  assert.equal(resultsOf(a).length, 1);
  assert.equal(resultsOf({ results: [], table: null, chart_spec: null, trace: null, query: null }).length, 0);
});

test("view titles are one line, no trailing question mark, 120 chars max", () => {
  assert.equal(viewTitle("  ADR by\nchannel last month?? "), "ADR by channel last month");
  assert.equal(viewTitle("x".repeat(200)).length, 120);
  assert.equal(viewTitle("   "), "Saved view");
});

test("starter questions come only from the viewer's own catalog", () => {
  const metric = (key, label, dims = [], derived = false) =>
    ({ key, label, description: null, unit: "money", currency: "AED", dimensions: dims, higher_is_better: true, synonyms: [], derived });
  assert.deepEqual(starterQuestions(undefined), []);
  const dept = starterQuestions([metric("svc.requests", "Requests", [{ key: "department", label: "Department" }])]);
  assert.deepEqual(dept, ["Requests by department this month"]);
  const owner = starterQuestions([
    metric("ask.adr", "ADR", [{ key: "channel", label: "Channel" }], true),
    metric("ask.revpar", "RevPAR", [], true),
    metric("stays.room_revenue", "Room revenue"),
    metric("rev.rating", "Average rating"),
    metric("x.extra", "Extra"),
  ]);
  assert.deepEqual(owner, ["RevPAR this month vs last year", "ADR by channel last month", "Room revenue month to date",
    "Average rating month to date"]);
  const labels = catalogLabels([metric("ask.adr", "ADR", [{ key: "channel", label: "Channel" }])]);
  assert.deepEqual(labels, { "ask.adr": "ADR", channel: "Channel" });
});

test("one money precision per column; whole amounts never show fils", () => {
  assert.equal(columnDigits([10200, 4500, 2100, 0], "money"), 0);
  assert.equal(columnDigits([412.35, 380, null], "money"), 2);
  assert.equal(columnDigits([12500.5, 380.25], "money"), 0);
  assert.equal(columnDigits([1.5, 2], "count"), undefined);
  assert.match(formatValue(4500, { unit: "money", currency: "AED", locale: "en" }), /AED\s?4,500$/);
  assert.match(formatValue(380, { unit: "money", currency: "AED", locale: "en", digits: 2 }), /AED\s?380\.00$/);
});

test("trace rows say how many matched and how many the limit kept", () => {
  const t = { metric: "m", metric_label: "M", description: null, dims: [], filters: [],
    period: { preset: null, from: "2026-07-01", to: "2026-09-29", label: "1 Jul - 29 Sep" }, grain: "week", compare: null,
    sort: "time_asc", limit: 20, unit: "count", currency: null, row_count: 34, truncated: false, derived: null,
    source: "clefs.core.metrics" };
  const by = Object.fromEntries(traceLines(t).map(l => [l.label, l.value]));
  assert.equal(by.Rows, "34 matched, first 20 shown");
  assert.equal(by.Grouping, "By week");
  assert.equal(by.Period, "1 Jul - 29 Sep (2026-07-01 to 2026-09-29)");
  assert.equal(by["Compared with"], undefined);
});
