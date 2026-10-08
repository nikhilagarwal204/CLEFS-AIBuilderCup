/**
 * Settings > Tax convention mirror: rate parsing (OWN-06: the server sent the
 * AED 10/20 Tourism Dirham rates as "1E+1"/"2E+1", str(Decimal.normalize())
 * form, and rateUnits threw "bad rate 1E+1", blanking Settings > Tax and
 * Profit Guard > Tax), plus the calc.py goldens the preview must reproduce.
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test convention.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  decompose, normalizeRules, plainDecimal, rateUnits, ratesOn, tdMinor,
} from "../convention.ts";

// ─── Rate parsing ───

test("plainDecimal expands every form Python's normalize() prints", () => {
  // Exactly what str(Decimal(x).normalize()) gives for numeric(18,8) rates.
  const cases = [
    ["1E+1", "10"], ["2E+1", "20"], ["1E+2", "100"], ["1E-8", "0.00000001"],
    ["0.1", "0.1"], ["0.07", "0.07"], ["0", "0"], ["0E-8", "0"], ["1500.5", "1500.5"],
    ["10.00000000", "10"], ["2.5e-3", "0.0025"], ["1.5E+1", "15"], ["-1E+1", "-10"],
    ["-0E-8", "0"], [".5", "0.5"], ["7.", "7"], [" 0.05 ", "0.05"], ["+3", "3"],
  ];
  for (const [input, want] of cases) assert.equal(plainDecimal(input), want, input);
});

test("plainDecimal refuses what is not a finite decimal", () => {
  for (const bad of ["", ".", "E+1", "abc", "1E", "1e+1000", "NaN", "Infinity", "1,5", "0x10"]) {
    assert.throws(() => plainDecimal(bad), /bad rate/, bad);
  }
});

test("rateUnits reads exponent form as the same rate (OWN-06 regression)", () => {
  assert.equal(rateUnits("1E+1"), rateUnits("10"));
  assert.equal(rateUnits("1E+1"), BigInt(10) * BigInt(10) ** BigInt(8));
  assert.equal(rateUnits("2E+1"), BigInt(2000000000));
  assert.equal(rateUnits("1E-8"), BigInt(1));
  assert.equal(rateUnits("0.07"), BigInt(7000000));
  assert.equal(rateUnits("-1E-1"), -BigInt(10000000));
  assert.throws(() => rateUnits("oops"), /bad rate/);
});

test("tdMinor charges AED 10 per unit-night from '1E+1'", () => {
  assert.equal(tdMinor("1E+1", 1, 2), BigInt(1000));
  assert.equal(tdMinor("2E+1", 3, 2), BigInt(6000));
});

test("normalizeRules rewrites exponent rates and leaves the rest alone", () => {
  const a = { id: 1, rate: "1E+1" };
  const b = { id: 2, rate: "0.07" };
  const c = { id: 3, rate: "garbage" };
  const out = normalizeRules([a, b, c]);
  assert.deepEqual(out.map(r => r.rate), ["10", "0.07", "garbage"]);
  assert.equal(out[1], b, "an already plain rule is the same object");
  assert.equal(a.rate, "1E+1", "input rows are not mutated");
});

// ─── Goldens through the wire format ───

// Dubai rules as GET /profit/tax/fee-rules serializes them today (normalize()).
const rule = (id, fee_code, calc_mode, rate, base = null, extra = {}) => ({
  id, rule_key: `dxb_${fee_code.toLowerCase()}`, hotel_slug: null, emirate: "DXB",
  property_category: "*", fee_code, calc_mode, rate, currency: calc_mode === "PER_ROOM_NIGHT" ? "AED" : null,
  base, vat_applies: false, cap_consecutive_nights: null, per_bedroom: false,
  effective_from: "2020-01-01", effective_to: null, rule_status: "VERIFIED", label: null,
  params: null, source_url: null, verified_by: null, verified_at: null, override: null, ...extra,
});
const WIRE = [
  rule(1, "SC", "PERCENT", "0.1", ["ROOM"]),
  rule(2, "MF", "PERCENT", "0.07", ["ROOM", "SC"]),
  rule(3, "VAT", "PERCENT", "0.05", ["ROOM", "SC", "MF", "TD"]),
  rule(4, "TD", "PER_ROOM_NIGHT", "1E+1", null, { property_category: "hotel_4", cap_consecutive_nights: 30 }),
  rule(5, "TD", "PER_ROOM_NIGHT", "2E+1", null, { property_category: "hotel_5", cap_consecutive_nights: 30 }),
];

test("the calc.py goldens hold when TD arrives as '1E+1'", () => {
  for (const rules of [WIRE, normalizeRules(WIRE)]) {
    const r = ratesOn(rules, "2026-09-29", "hotel_4");
    assert.equal(r.tdRate === "1E+1" || r.tdRate === "10", true);
    const td = tdMinor(r.tdRate, 1, 2);
    assert.equal(td, BigInt(1000));
    const a = decompose(BigInt(50000), td, r.fees, "A");
    assert.deepEqual([a.room, a.parts.SC, a.parts.MF, a.parts.VAT].map(Number), [39649, 3965, 3053, 2333]);
    const b = decompose(BigInt(50000), td, r.fees, "B");
    assert.deepEqual([b.room, b.parts.SC, b.parts.MF, b.parts.VAT].map(Number), [39886, 3989, 2792, 2333]);
    const c = decompose(BigInt(50000), td, r.fees, "C");
    assert.deepEqual([c.room, c.parts.SC, c.parts.MF, c.parts.VAT].map(Number), [40165, 4016, 2811, 2008]);
  }
  assert.equal(tdMinor(ratesOn(WIRE, "2026-09-29", "hotel_5").tdRate, 1, 2), BigInt(2000));
});
