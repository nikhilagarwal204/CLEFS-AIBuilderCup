/**
 * UI state tests for /google-check/r/<token> (04 section 2.5): the vendor
 * disclosure renders whenever the parity table or the rank is shown, and the
 * 429 and no_match states render. Run from client/:
 *
 *   node --test app/google-check/__tests__/
 *
 * _view.ts has only type imports, so it is transpiled with the TypeScript
 * compiler and imported from a data: URL, exactly as scripts/check-claims.mjs
 * loads the claims registry. No bundler, no DOM.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const GC = path.resolve(HERE, "..");

async function load(file) {
  const src = fs.readFileSync(path.join(GC, file), "utf8");
  const js = ts.transpileModule(src, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  return import("data:text/javascript;base64," + Buffer.from(js).toString("base64"));
}

const { viewOf, showsParityOrRank, disclosureText } = await load("_view.ts");

const ROW = { source: "Booking.com", channel: "ota", price_minor: 45000, currency: "AED", official: false };
const unlocked = (over = {}) => ({
  token: "tok_12345678",
  status: "ready",
  teaser: { official_present: false },
  unlocked: {
    parity: [ROW], gap_minor: 2500, gap_pct: 5.5, organic_rank: 4,
    checked_at: "2026-09-29T08:00:00Z", stay_dates: [["2026-10-12", "2026-10-13"]], note_claim_ids: [],
    ...over,
  },
});

// ─── Disclosure invariant ────────────────────────────────────────────────────

test("parity and rank shown -> disclosure present and names SerpApi", () => {
  const v = viewOf(unlocked(), null);
  assert.equal(v.kind, "unlocked");
  assert.ok(showsParityOrRank(v));
  assert.match(v.disclosure, /Google Hotels data via SerpApi, a third-party service; prices indicative; checked /);
  assert.match(v.disclosure, /for stays 12 Oct 2026 to 13 Oct 2026/);
});

test("rank only -> disclosure still present", () => {
  const v = viewOf(unlocked({ parity: [] }), null);
  assert.ok(showsParityOrRank(v));
  assert.ok(v.disclosure);
});

test("parity only -> disclosure still present", () => {
  const v = viewOf(unlocked({ organic_rank: null }), null);
  assert.ok(showsParityOrRank(v));
  assert.ok(v.disclosure);
});

test("nothing to show -> no parity, no rank, no disclosure", () => {
  const v = viewOf(unlocked({ parity: [], organic_rank: null }), null);
  assert.equal(showsParityOrRank(v), false);
  assert.equal(v.disclosure, null);
});

test("invariant over mixed shapes: shown implies disclosed", () => {
  const variants = [
    unlocked(), unlocked({ parity: [] }), unlocked({ organic_rank: null }), unlocked({ organic_rank: "7" }),
    unlocked({ parity: null }), unlocked({ stay_dates: null }), unlocked({ parity: [], organic_rank: null }),
  ];
  for (const r of variants) {
    const v = viewOf(r, null);
    if (showsParityOrRank(v)) assert.ok(v.disclosure, JSON.stringify(r.unlocked));
  }
});

test("teaser and other states never expose parity or rank", () => {
  for (const r of [
    { token: "t", status: "ready", teaser: { official_present: true } },
    { token: "t", status: "no_match" },
    { token: "t", status: "needs_pick", candidates: [] },
  ]) {
    assert.equal(showsParityOrRank(viewOf(r, null)), false);
  }
});

// ─── States ──────────────────────────────────────────────────────────────────

test("429 renders the rate-limited state", () => {
  const v = viewOf(null, "rate_limited");
  assert.deepEqual(v, { kind: "failure", failure: "rate_limited" });
  const lib = fs.readFileSync(path.join(GC, "_lib.ts"), "utf8");
  assert.match(lib, /rate_limited: "You have reached today's limit, try again tomorrow\."/);
});

test("no_match renders the not-found-on-Google state", () => {
  assert.equal(viewOf({ token: "t", status: "no_match" }, null).kind, "no_match");
  const lib = fs.readFileSync(path.join(GC, "_lib.ts"), "utf8");
  assert.match(lib, /We could not find this hotel on Google Hotels; check the name and city\./);
});

test("unavailable (404 or 503 before the server lands) renders the unavailable state", () => {
  assert.deepEqual(viewOf(null, "unavailable"), { kind: "failure", failure: "unavailable" });
});

test("teaser keeps the official-price answer", () => {
  const v = viewOf({ token: "t", status: "ready", teaser: { official_present: true } }, null);
  assert.deepEqual(v, { kind: "teaser", official_present: true });
});

test("disclosureText formats calendar dates without a timezone shift", () => {
  assert.match(disclosureText("2026-09-29", [["2026-10-01", "2026-10-02"]]), /checked 29 Sept? 2026 for stays 1 Oct 2026 to 2 Oct 2026/);
});

test("validToken accepts opaque URL-safe tokens and rejects the rest", async () => {
  const { validToken } = await load("_view.ts");
  assert.ok(validToken("tok_12345678"));
  assert.ok(validToken("AbC-dEf_0123456789"));
  for (const bad of ["short", "has space here", "../../etc/passwd", "x".repeat(129), "tok%2F123456"]) {
    assert.equal(validToken(bad), false, bad);
  }
});
