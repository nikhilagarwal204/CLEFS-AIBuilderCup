/**
 * View-model tests for the visibility console (04 spec 5.5 and 5.11). Run
 * from client/:
 *
 *   node "app/app/[hotelSlug]/(console)/visibility/__tests__/model.test.mjs"
 *
 * (npm test picks it up through its app/** glob; passing the directory to
 * node --test does not work because "[hotelSlug]" reads as a glob class.)
 *
 * model.ts has type imports only, so it is transpiled with the TypeScript
 * compiler and imported from a data: URL (the google-check/_view.ts trick).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));

/* stripImports: format.ts imports React and the hotel context for its hook;
   the pure money helpers under test never touch them, so those lines are
   dropped and the real format.ts functions run (no stand-in). */
async function load(file, { stripImports = false } = {}) {
  let src = fs.readFileSync(path.resolve(HERE, "..", file), "utf8");
  if (stripImports) src = src.replace(/^import [^;]+;$/gm, "");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import("data:text/javascript;base64," + Buffer.from(js).toString("base64"));
}

const m = await load("model.ts");
const f = await load("../../../../lib/format.ts", { stripImports: true });
const MONEY = { currencyDigits: f.currencyDigits, minorToMajor: f.minorToMajor };
const money = f.makeFormatter("AED", "Asia/Dubai", "en-AE").money;
const plain = s => s.replace(/\s/g, " ");

// ─── Minimum-sample gate ─────────────────────────────────────────────────

test("an insufficient cell never exposes a rate, even when the row carries one", () => {
  const v = m.cellView({ sufficient: false, rate: "0.4000", ci_low: "0.2000", ci_high: "0.6000", runs: 29, display: "40%" });
  assert.equal(v.sufficient, false);
  assert.equal(v.rate, null);
  assert.equal(v.low, null);
  assert.equal(v.text, "insufficient sample (n=29)");
});

test("a sufficient cell shows the rate with its Wilson interval", () => {
  const v = m.cellView({ sufficient: true, rate: "0.3000", ci_low: "0.1800", ci_high: "0.4500", runs: 40, display: "" });
  assert.equal(v.text, "30% (18-45%)");
  assert.equal(v.rate, 0.3);
  assert.equal(v.n, 40);
});

test("SoV segments are empty below the gate and put the hotel first above it", () => {
  const ents = [
    { entity: "c1", name: "Comp", mentions: 9, sov: "0.5000", ci_low: "0.3", ci_high: "0.7" },
    { entity: "self", name: "Us", mentions: 3, sov: "0.2000", ci_low: null, ci_high: null },
    { entity: "other", name: "Other hotels", mentions: 0, sov: "0", ci_low: null, ci_high: null },
  ];
  assert.deepEqual(m.sovSegments(ents, false), []);
  const segs = m.sovSegments(ents, true);
  assert.deepEqual(segs.map(s => s.key), ["self", "c1"]);
  assert.equal(segs[1].text, "50% (30-70%)");
});

// ─── Evidence drawer ─────────────────────────────────────────────────────

test("highlight marks the claimed quote case- and whitespace-insensitively", () => {
  const segs = m.highlight("Check-in is from  12 PM. Parking: none.", "check-in is from 12 pm");
  assert.deepEqual(segs, [{ text: "Check-in is from  12 PM", mark: true }, { text: ". Parking: none.", mark: false }]);
  assert.deepEqual(m.highlight("abc", ""), [{ text: "abc", mark: false }]);
  assert.deepEqual(m.highlight("", "x"), []);
  // Regex metacharacters in a quote are literal.
  assert.equal(m.highlight("Price (AED 500+) today", "(AED 500+)").filter(s => s.mark).length, 1);
});

test("citations: http(s) only, anchored citations first, one row per URL", () => {
  const out = m.orderedCitations([
    { url: "https://b.example/x", kind: "source" },
    { url: "javascript:alert(1)", kind: "citation" },
    { url: "https://a.example/", kind: "citation" },
    { url: "https://b.example/x", kind: "citation" },
  ]);
  assert.deepEqual(out.map(c => c.url), ["https://a.example/", "https://b.example/x"]);
  assert.equal(m.safeHref("ftp://x.example"), null);
});

// ─── Setup, costs, settings ──────────────────────────────────────────────

test("setup codes become checklist items; unknown codes still render", () => {
  const items = m.setupItems(["property_token", "serpapi_key", "property_token", "brand_new_code"]);
  assert.deepEqual(items.map(i => i.code), ["property_token", "serpapi_key", "brand_new_code"]);
  assert.equal(items[0].where, "settings");
  assert.equal(items[1].where, "platform");
  assert.equal(items[2].label, "brand new code");
});

test("micros and the budget bar", () => {
  assert.equal(plain(m.microsText(12_345_678, money)), "USD 12.35");
  assert.equal(plain(m.microsText(1_234_567_890, money)), "USD 1,234.57");  // grouped through format.ts
  assert.equal(m.microsText(null, money), "n/a");
  assert.equal(m.budgetUsedPct({ spent_micros: 15_000_000, budget_micros: 30_000_000, remaining_micros: 0, exhausted: false }), 50);
  assert.equal(m.budgetUsedPct({ spent_micros: 90, budget_micros: 30, remaining_micros: 0, exhausted: true }), 100);
});

test("settings draft round-trips and mirrors the server's 422 rules", () => {
  const base = {
    place_id: "ChIJ1", gbp_location: null, gh_property_token: null, website_url: "https://hotel.example",
    booking_engine_host: null, brand_aliases: ["The Hotel"], rank_keywords: null, area: "Dubai Marina", landmarks: [],
    room_page_urls: [], free_booking_link_pattern: null, grid_size: 7, grid_spacing_m: 800,
    ai_markets: [{ market: "GB", city: "London" }], monthly_budget_minor: 3000, budget_currency: "USD",
    engines_enabled: ["gemini_web_search"], serpapi_enabled: true, updated_at: null,
  };
  const d = m.draftFrom(base, MONEY);
  assert.equal(d.monthly_budget, "30.00");
  assert.deepEqual(m.validateDraft(d, MONEY), {});
  const body = m.bodyFrom(d, MONEY);
  assert.equal(body.monthly_budget_minor, 3000);
  assert.deepEqual(body.ai_markets, [{ market: "GB", city: "London" }]);
  assert.deepEqual(body.rank_keywords, []);
  const bad = { ...d, website_url: "hotel.example", rank_keywords: "a\nb\nc\nd\ne\nf", grid_size: "11" };
  assert.deepEqual(Object.keys(m.validateDraft(bad, MONEY)).sort(), ["grid_size", "rank_keywords", "website_url"]);
  assert.deepEqual(m.parseMarkets("gb London\nDE\nxx1\nGB again"), [{ market: "GB", city: "London" }, { market: "DE", city: null }]);
});

// ─── Budget minor units follow the currency's digits (ux-conventions-1) ──

test("a 3-decimal budget currency round-trips in its own minor units", () => {
  // 50 BHD is 50000 fils; the old * 100 sent 5000 (read by the server as 5 BHD).
  const bhd = m.draftFrom({ monthly_budget_minor: 50_000, budget_currency: "BHD" }, MONEY);
  assert.equal(bhd.monthly_budget, "50.000");
  assert.equal(m.bodyFrom(bhd, MONEY).monthly_budget_minor, 50_000);
  const typed = { ...bhd, monthly_budget: "50", budget_currency: "kwd" };
  assert.deepEqual(m.validateDraft(typed, MONEY).monthly_budget, undefined);
  assert.equal(m.bodyFrom(typed, MONEY).monthly_budget_minor, 50_000);
  assert.equal(m.bodyFrom(typed, MONEY).budget_currency, "KWD");
});

test("a 0-decimal budget currency is not multiplied by 100", () => {
  const jpy = m.draftFrom({ monthly_budget_minor: 3000, budget_currency: "JPY" }, MONEY);
  assert.equal(jpy.monthly_budget, "3000");
  assert.equal(m.bodyFrom(jpy, MONEY).monthly_budget_minor, 3000);
  assert.match(m.validateDraft({ ...jpy, monthly_budget: "30.5" }, MONEY).monthly_budget, /whole amount/);
});

test("majorToMinor shifts exactly and refuses what it cannot represent", () => {
  assert.equal(m.majorToMinor("1.005", 3), 1005);  // no float drift (1.005 * 1000 is 1004.999...)
  assert.equal(m.majorToMinor("19.9", 2), 1990);
  assert.equal(m.majorToMinor(" 7 ", 2), 700);
  assert.equal(m.majorToMinor("1.234", 2), null);
  assert.equal(m.majorToMinor("-5", 2), null);
  assert.equal(m.majorToMinor("abc", 2), null);
  assert.match(m.validateDraft({ ...m.draftFrom({ budget_currency: "USD" }, MONEY), monthly_budget: "1.234" }, MONEY)
    .monthly_budget, /at most 2 decimals/);
});

test("rank history is plotted oldest first", () => {
  assert.deepEqual(m.solvSeries([
    { run_at: "2026-09-21T04:00:00Z", solv: "20.00" },
    { run_at: "2026-09-07T04:00:00Z", solv: "10.00" },
    { run_at: "2026-09-14T04:00:00Z", solv: null },
  ]), [10, null, 20]);
});

test("no em dash anywhere in the visibility console", () => {
  const dir = path.resolve(HERE, "..");
  const files = fs.readdirSync(dir).filter(f => /\.(ts|tsx)$/.test(f));
  assert.ok(files.length > 5);
  for (const f of files) {
    assert.equal(fs.readFileSync(path.join(dir, f), "utf8").includes(String.fromCharCode(0x2014)), false, f);
  }
});

test("an unconvertible budget draws no bar; the ladder reads in plain words", () => {
  assert.equal(m.budgetUsedPct({ spent_micros: 5, budget_micros: null, remaining_micros: null, exhausted: false }), 0);
  assert.equal(m.stepText("drop_engine:perplexity_sonar"), "Dropped the most expensive engine (Perplexity Sonar)");
  assert.equal(m.stepText("repeats_3"), "Cut repeats to 3 per prompt");
});

// ─── Vendor-risk note link (spec-vis-vendor-risk-link) ───────────────────

test("the Google tab links the server's vendor-risk note, same-site paths only", () => {
  assert.equal(m.vendorRiskHref("/subprocessors#serpapi"), "/subprocessors#serpapi");
  for (const bad of [null, undefined, "", "//evil.example/x", "/\\evil.example", "javascript:alert(1)", "https://x.example"]) {
    assert.equal(m.vendorRiskHref(bad), null, String(bad));
  }
});

// ─── Draft replies notice (spec-vis-draft-replies-missing) ───────────────

test("the draft-replies toast never claims a reply was posted", () => {
  const made = m.draftRepliesNotice({ drafted: 3, considered: 3 });
  assert.equal(made.tone, "success");
  assert.equal(made.title, "3 replies drafted");
  assert.match(made.body, /approve/);
  assert.doesNotMatch(`${made.title} ${made.body}`, /\bposted to Google\b|\breplied\b/i);
  assert.equal(m.draftRepliesNotice({ drafted: 1, considered: 1 }).title, "1 reply drafted");
  assert.equal(m.draftRepliesNotice({ drafted: 0, considered: 0 }).tone, "info");
  assert.equal(m.draftRepliesNotice({ drafted: 0, considered: 2 }).tone, "error");
});
