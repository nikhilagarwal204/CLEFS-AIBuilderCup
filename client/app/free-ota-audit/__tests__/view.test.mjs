/**
 * View-model tests for /free-ota-audit (02 spec 3.11). Run from client/:
 *
 *   node --test app/free-ota-audit/__tests__/
 *
 * _view.ts has only type imports, so it is transpiled with the TypeScript
 * compiler and imported from a data: URL, exactly as the google-check tests
 * and scripts/check-claims.mjs do. No bundler, no DOM.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");

async function load(file) {
  const src = fs.readFileSync(path.join(ROOT, file), "utf8");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import("data:text/javascript;base64," + Buffer.from(js).toString("base64"));
}

const V = await load("_view.ts");

// ─── Fixtures ────────────────────────────────────────────────────────────────

const money = (minor) => ({ minor, amount: (minor / 100).toFixed(2) });

// The server's shape since the GTM guard: found counts RECOVERABLE findings
// only (low == high), and the L1/L2 lines are a count to confirm.
const TEASER = {
  ota_code: "BOOKING", currency: "AED", lines: 120, period: { from: "2026-08-01", to: "2026-08-31" },
  commission: money(4_500_000), found: { low: money(80_000), high: money(80_000) },
  findings: 3, by_detector: { L3: 2, L12: 1, L2: 0 }, to_confirm: 2, open_deadlines: 1,
};

const finding = (over = {}) => ({
  detector: "L3", class: "RECOVERABLE", scenario: null, title: "Commission charged on a no-show",
  amount_minor: 30_000, amount: "300.00", currency: "AED", confidence: "0.80", missing: ["fee_collected"],
  row: 4, page: null, reservation: "4455667788", guest: "A. M.", check_in: "2026-08-02", check_out: "2026-08-03",
  status: "NO_SHOW", deadline_at: "2026-08-04T20:00:00+00:00", window_open: false, calc: [], ...over,
});

const FULL = {
  findings: [
    finding(),
    finding({ amount_minor: 50_000, amount: "500.00", row: 9 }),
    finding({ detector: "L12", amount_minor: 10_000, amount: "100.00", row: 12, guest: "AL MANSOURI/AHMED" }),
    finding({ detector: "L1", class: "TO_CONFIRM", amount_minor: 40_000, amount: "400.00", row: 2, scenario: "fees_configured" }),
  ],
  structural: money(0), deadlines: [], status_counts: { OK: 116, NO_SHOW: 4 },
  skipped: { other_currency_lines: 0, ota_unknown: false }, assumptions: {}, not_checked: [], notes: [],
};

const audit = (over = {}) => ({
  status: "READY", created_at: "2026-09-29T08:00:00Z", expires_at: "2026-10-29T08:00:00Z",
  unlocked: false, teaser: TEASER, full: null, ai_snapshot: null, lead: null, ...over,
});

// ─── Tests ───────────────────────────────────────────────────────────────────

test("a locked audit renders the teaser only, even if a payload carries more", () => {
  const v = V.viewOf(audit({ full: FULL, ai_snapshot: { status: "ready", per_engine: [] } }), null);
  assert.equal(v.kind, "teaser");
  assert.equal(v.groups, undefined);
  assert.equal(v.snapshot, undefined);
});

test("an unlocked audit groups findings by detector in reading order", () => {
  const v = V.viewOf(audit({ unlocked: true, full: FULL, lead: { name: "Sara", hotel_name: "Palm Inn" } }), null);
  assert.equal(v.kind, "unlocked");
  assert.deepEqual(v.groups.map((g) => g.detector), ["L3", "L12", "L1"]);
  assert.deepEqual(v.groups.map((g) => g.section), ["found", "found", "confirm"]);
  const l3 = v.groups[0];
  assert.equal(l3.count, 2);
  assert.equal(l3.total_minor, 80_000);
  assert.equal(l3.findings[0].amount_minor, 50_000, "largest first");
  assert.match(l3.total, /^AED 800\.00$/);
  assert.equal(v.groups[2].total, null, "a line to confirm is never summed");
  assert.equal(v.hotel, "Palm Inn");
});

test("guest names are always initials, whatever the server sends", () => {
  const v = V.viewOf(audit({ unlocked: true, full: FULL }), null);
  const l12 = v.groups.find((g) => g.detector === "L12");
  assert.equal(l12.findings[0].guest, "A. M. A.");
  assert.equal(V.maskGuest("A. M."), "A. M.");
  assert.equal(V.maskGuest("Ahmed Al Mansouri Junior"), "A. A. M.");
  assert.equal(V.maskGuest("  "), null);
  assert.equal(V.maskGuest(null), null);
  assert.equal(V.maskGuest("123 456"), "Guest");
});

test("the headline is one figure of counted findings; a payload whose ends differ still reads as a range", () => {
  assert.equal(V.rangeText(TEASER), "AED 800.00");
  assert.equal(V.rangeText({ ...TEASER, found: { low: money(5000), high: money(8000) } }), "AED 50.00 to AED 80.00");
});

test("teaser rows drop zero counts, keep reading order and never list a line to confirm", () => {
  assert.deepEqual(V.teaserRows(TEASER).map((r) => r.detector), ["L3", "L12"]);
  const old = { ...TEASER, by_detector: { ...TEASER.by_detector, L1: 2, L1b: 2, L2: 1 } };
  assert.deepEqual(V.teaserRows(old).map((r) => r.detector), ["L3", "L12"], "L1, L1b and L2 are to confirm");
});

test("the L2 title is exact and byte-identical to the server's L2_TITLE", () => {
  const title = "Above the percentage this check assumed: check your contract and programme terms";
  assert.equal(V.DETECTORS.L2.title, title);
  assert.doesNotMatch(V.DETECTORS.L2.title + V.DETECTORS.L2.blurb, /usual|standard/i);
  assert.match(V.DETECTORS.L2.blurb, /a default percentage per OTA, not a market rate; your contract decides/);
  const py = path.resolve(ROOT, "../../../server/clefs/modules/profit_guard/public_audit.py");
  if (!fs.existsSync(py)) return; // client-only checkout
  const m = /\nL2_TITLE\s*=\s*"((?:[^"\\]|\\.)*)"/.exec(fs.readFileSync(py, "utf8"));
  assert.equal(m?.[1], title);
});

test("lines to confirm and Agoda's row never get an AED total, whatever class a payload carries", () => {
  const groups = V.groupFindings([
    finding(),
    finding({ detector: "L1", class: "TO_CONFIRM", scenario: "fees_configured", amount_minor: 1216, amount: "12.16" }),
    finding({ detector: "L1b", class: "TO_CONFIRM", scenario: "fees_not_configured", amount_minor: 1216, amount: "12.16" }),
    finding({ detector: "L2", class: "CHECK_CONTRACT", amount_minor: 3000, amount: "30.00", row: 5 }),
    finding({ detector: "L2", class: "RECOVERABLE", amount_minor: 2000, amount: "20.00", row: 6 }),
    finding({ detector: "L1", class: "STRUCTURAL", scenario: "agoda_tax_inclusive", amount_minor: 1979, amount: "19.79" }),
  ], "AED");
  const by = Object.fromEntries(groups.map((g) => [g.detector, g]));
  assert.deepEqual(groups.map((g) => [g.detector, g.section]), [
    ["L3", "found"], ["L2", "confirm"], ["L1", "confirm"], ["L1b", "confirm"], ["L1_AGODA", "info"]]);
  assert.match(by.L3.total, /^AED 300\.00$/);
  for (const d of ["L2", "L1", "L1b", "L1_AGODA"]) assert.equal(by[d].total, null, d);
  assert.equal(by.L2.count, 2, "an L2 sent as RECOVERABLE is still a line to confirm");
  assert.equal(by.L2.title, V.DETECTORS.L2.title);
  assert.match(by.L1_AGODA.blurb, /^For information/);
  assert.match(by.L1.blurb, /^To confirm with you/);
});

test("an Agoda-only statement: the teaser finds nothing and the report agrees", () => {
  // A plain 18% Agoda line: only the STRUCTURAL commission on taxes (AED 19.79).
  const agoda = { ...TEASER, ota_code: "AGODA", found: { low: money(0), high: money(0) }, findings: 0, by_detector: {}, to_confirm: 0 };
  const groups = V.groupFindings([
    finding({ detector: "L1", class: "STRUCTURAL", scenario: "agoda_tax_inclusive", amount_minor: 1979, amount: "19.79", status: "OK" }),
  ], "AED");
  assert.equal(V.teaserLead(agoda), "nothing");
  const cards = V.reportCards(groups);
  assert.deepEqual(cards.confirm, [], "never 'to confirm' beside a teaser that found nothing");
  assert.deepEqual(cards.counted, []);
  assert.deepEqual(cards.info.map((g) => [g.detector, g.total]), [["L1_AGODA", null]]);
  // Beside a counted no-show, Agoda's row still stays out of the lines to confirm.
  const mixed = V.reportCards(V.groupFindings([finding(), ...groups[0].findings], "AED"));
  assert.deepEqual([mixed.counted.length, mixed.confirm.length, mixed.info.length], [1, 0, 1]);
});

test("a teaser without to_confirm (an API older than the guard) gets no AED figure", () => {
  // The pre-guard shape: L2 inside found.low, L1 widening found.high.
  const old = { ...TEASER, found: { low: money(10_500), high: money(13_175) }, findings: 4,
    by_detector: { L1: 2, L2: 1, L3: 1 } };
  delete old.to_confirm;
  assert.equal(V.figureOk(old), false);
  assert.equal(V.teaserLead(old), "found");
  assert.deepEqual(V.teaserRows(old).map((r) => r.detector), ["L3"], "only the counted check is listed");
  assert.equal(V.figureOk(TEASER), true);
  assert.equal(V.figureOk({ ...TEASER, to_confirm: 0 }), true, "0 is a count, the teaser is current");
});

test("the fee blurbs promise no timing and match the server's amounts", () => {
  assert.doesNotMatch(V.DETECTORS.L1b.blurb, /next (statement|month)|from now on/i);
  assert.match(V.DETECTORS.L1b.blurb, /the fix is the extranet setting, not a dispute/);
  // pct x (MF + VAT): the VAT part does not depend on the municipality fee.
  const agoda = V.DETECTORS.L1_AGODA.blurb;
  assert.match(agoda, /municipality fee and VAT/);
  assert.doesNotMatch(agoda, /applies only if/i);
});

test("the teaser leads with the lines to confirm when nothing counts, with no AED", () => {
  const only = { ...TEASER, found: { low: money(0), high: money(0) }, findings: 0, by_detector: {}, to_confirm: 2 };
  assert.equal(V.teaserLead(only), "confirm", "never 'nothing found' while lines wait to be confirmed");
  assert.equal(V.confirmText(V.confirmLines(only)), "2 lines to confirm with you");
  assert.equal(V.confirmText(1), "1 line to confirm with you");
  assert.doesNotMatch(V.confirmText(1234), /AED|\$/);
  assert.equal(V.teaserLead(TEASER), "found");
  assert.equal(V.teaserLead({ ...only, to_confirm: 0 }), "nothing");
  assert.equal(V.confirmLines({ ...only, to_confirm: undefined }), 0, "a payload without the count");
  assert.equal(V.TEASER_SUBLINE, "Each line needs your contract and PMS to confirm; a founder will review it with you.");
});

test("the AI snapshot is always labelled API simulation", () => {
  assert.equal(V.snapshotOf(null).kind, "unavailable");
  assert.match(V.snapshotOf(null).label, /^API simulation/);
  assert.match(V.snapshotOf({ status: "ready", label: "Google AI Mode via SerpApi (third-party); indicative" }).label, /^API simulation/);
  const ready = V.snapshotOf({
    status: "ready", label: "API simulation: consumer app answers vary",
    per_engine: [{ engine: "gemini_web_search", mentioned: 2, runs: 9, display: "mentioned in 2 of 9 answers" }],
    top_cited_domains: ["tripadvisor.com"],
    answers_about_you: [{
      prompt: "Tell me about Palm Inn", engine: "gemini_web_search", answer: "A hotel.",
      citations: [{ url: "https://example.com/a", title: "A" }, { url: "javascript:alert(1)", title: "bad" }],
    }],
  });
  assert.equal(ready.kind, "ready");
  assert.equal(ready.label, "API simulation: consumer app answers vary");
  assert.equal(ready.engines[0].name, "Gemini (Google Search grounding)");
  assert.equal(ready.answers[0].citations.length, 1, "non-http citation dropped");
  assert.equal(V.snapshotOf({ status: "running" }).kind, "running");
  assert.equal(V.snapshotOf({ status: "ready", per_engine: [], answers_about_you: [] }).kind, "unavailable");
});

test("the unlocked view carries the snapshot; unavailable when the server has none", () => {
  const v = V.viewOf(audit({ unlocked: true, full: FULL, ai_snapshot: { status: "unavailable" } }), null);
  assert.equal(v.snapshot.kind, "unavailable");
  assert.match(v.snapshot.label, /API simulation/);
});

test("API errors map to the documented page states", () => {
  assert.equal(V.failureOf({ status: 429, code: "rate_limited" }), "rate_limited");
  assert.equal(V.failureOf({ status: 413, code: "too_many_rows" }), "too_many_rows");
  assert.equal(V.failureOf({ status: 413, code: "file_too_large" }), "too_large");
  assert.equal(V.failureOf({ status: 413, code: "too_many_pages" }), "too_many_pages");
  assert.equal(V.failureOf({ status: 415, code: "unsupported_file_type" }), "unsupported");
  assert.equal(V.failureOf({ status: 422, code: "consent_required" }), "consent");
  assert.equal(V.failureOf({ status: 422, code: "pdf_no_text" }), "unreadable");
  assert.equal(V.failureOf({ status: 422, code: "unrecognised_columns" }), "unreadable");
  assert.equal(V.failureOf({ status: 404, code: "not_found" }, true), "not_found");
  assert.equal(V.failureOf({ status: 404, code: "not_found" }), "unavailable", "404 on upload = not deployed");
  assert.equal(V.failureOf({ status: 404 }, true), "unavailable");
  assert.equal(V.failureOf({ status: 0, code: "network_error" }), "network");
  assert.equal(V.failureOf({ status: 500 }), "unavailable");
  for (const k of Object.keys(V.FAILURE_TEXT)) assert.ok(V.FAILURE_TEXT[k].length > 10);
});

test("files are prechecked against the server limits", () => {
  assert.equal(V.precheckFile("statement.csv", 2048), null);
  assert.equal(V.precheckFile("Statement.XLSX", 2048), null);
  // 4 MB, under Vercel's 4.5 MB function body limit (server MAX_BYTES).
  assert.equal(V.MAX_BYTES, 4_000_000);
  assert.equal(V.precheckFile("invoice.pdf", V.MAX_BYTES), null);
  assert.match(V.precheckFile("invoice.pdf", V.MAX_BYTES + 1), /4 MB/);
  assert.match(V.FAILURE_TEXT.too_large, /4 MB/);
  assert.match(V.precheckFile("photo.png", 2048), /CSV/);
  assert.match(V.precheckFile("empty.csv", 0), /empty/);
});

test("tokens are URL-safe and bounded", () => {
  assert.ok(V.validToken("abcdEFGH_-12345678901234567890ab"));
  assert.ok(!V.validToken("short"));
  assert.ok(!V.validToken("has space in it and is long enough"));
  assert.ok(!V.validToken("../../etc/passwd/../../../../x"));
});

test("failures and empty reports have their own states", () => {
  assert.deepEqual(V.viewOf(null, "rate_limited"), { kind: "failure", failure: "rate_limited" });
  assert.deepEqual(V.viewOf(null, null), { kind: "loading" });
  assert.deepEqual(V.viewOf(audit({ teaser: null }), null), { kind: "empty" });
});

test("hours left and Dubai dates", () => {
  const now = Date.parse("2026-09-29T08:00:00Z");
  assert.equal(V.hoursLeft("2026-09-30T08:00:00Z", now), 24);
  assert.equal(V.hoursLeft(null, now), null);
  assert.equal(V.day("2026-10-12"), "12 Oct 2026");
  // 21:00 UTC on 30 Sep is 1 Oct in Dubai.
  assert.equal(V.day("2026-09-30T21:00:00Z"), "1 Oct 2026");
});

test("the consent wording matches the server's recorded CONSENT_TEXTS", () => {
  const py = path.resolve(ROOT, "../../../server/clefs/modules/profit_guard/public_audit.py");
  if (!fs.existsSync(py)) return; // client-only checkout
  const src = fs.readFileSync(py, "utf8");
  const block = /\nCONSENT_TEXTS\s*=\s*\{([\s\S]*?)\n\}/.exec(src)?.[1] ?? "";
  const text = (key) => {
    const m = new RegExp(`"${key}":\\s*((?:"(?:[^"\\\\]|\\\\.)*"\\s*)+),`).exec(block);
    return m ? [...m[1].matchAll(/"((?:[^"\\\\]|\\\\.)*)"/g)].map((x) => x[1]).join("") : null;
  };
  assert.equal(text("contact"), V.CONSENT_CONTACT);
  assert.equal(text("index"), V.CONSENT_INDEX);
  assert.equal(text("lead_transfer_abroad"), V.CONSENT_LEAD_TRANSFER);
  const upload = /\nUPLOAD_CONSENT_TEXTS\s*=\s*\{([\s\S]*?)\n\}/.exec(src)?.[1] ?? "";
  const utext = (key) => {
    const m = new RegExp(`"${key}":\\s*((?:"(?:[^"\\\\]|\\\\.)*"\\s*)+),`).exec(upload);
    return m ? [...m[1].matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((x) => x[1]).join("") : null;
  };
  assert.equal(utext("processing"), V.CONSENT_PROCESSING);
  assert.equal(utext("transfer_abroad"), V.CONSENT_TRANSFER);
});

test("the transfer tick no longer claims a CSV stays in the UAE", () => {
  // Review compliance-ota-audit-no-transfer-consent: every format is stored
  // in Singapore, so the tick is required for every file.
  assert.doesNotMatch(V.CONSENT_TRANSFER, /stays in Clefs/);
  assert.match(V.CONSENT_TRANSFER, /Singapore/);
  assert.match(V.CONSENT_LEAD_TRANSFER, /outside the UAE/);
});

test("a PDF is recognised and the transfer refusal maps to its message", () => {
  assert.equal(V.isPdfFile("statement.PDF"), true);
  assert.equal(V.isPdfFile("export", "application/pdf"), true);
  assert.equal(V.isPdfFile("statement.csv", "text/csv"), false);
  assert.equal(V.failureOf({ status: 422, code: "transfer_consent_required" }), "transfer");
});

test("minor units format without float drift", () => {
  assert.equal(V.moneyMinor(123456, "AED", "1234.56"), "AED 1,234.56");
  assert.equal(V.moneyMinor(5, "AED"), "AED 0.05");
  assert.equal(V.moneyMinor(-250, "AED", "-2.50"), "-AED 2.50");
});

test("the Leakage Index renders only when published at or above its minimum", () => {
  assert.equal(V.indexVisible(null), false);
  assert.equal(V.indexVisible({ published: false, n: 12, min_n: 30 }), false);
  assert.equal(V.indexVisible({ published: true, n: 29, min_n: 30 }), false);
  assert.equal(V.indexVisible({ published: true, n: 30, min_n: 30 }), true);
  assert.equal(V.rateText("0.0421"), "4.2%");
  assert.equal(V.rateText("0"), "0.0%");
  assert.equal(V.rateText(undefined), "");
});
