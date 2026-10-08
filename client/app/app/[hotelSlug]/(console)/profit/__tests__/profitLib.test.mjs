/**
 * Profit Guard console helpers (02 spec 3.10): typed amounts become minor
 * units without float drift, the buttons offered match the server's status
 * machine, a dispute packet takes one OTA and one currency, the L8 card
 * tiers are 30/14/7/1 days, evidence rows link their screenshots, and a
 * multi-month waterfall reconciles Gross to Net. `npm test` picks it up;
 * alone, run it from this folder: node --test profitLib.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  calcInputs, cardTier, disputeRuleText, vccRuleText, cardTierTone, channelTotals, detectorLabel, findingMoves, humanKey, majorToMinor,
  minorToDecimal, monthLabel, num, otaLabel, owedMinor, packetCandidates, panelEvidence, parityGrid, pgApi, pgHref,
  sortDeadlines, splitConfidence,
} from "../lib.ts";

const f = (over = {}) => ({
  id: 1, detector: "L3", ota_code: "BOOKING", class: "RECOVERABLE", status: "DETECTED", currency: "AED",
  amount_minor: 8470, recovered_minor: 0, dispute_packet_id: null, ...over,
});

test("paths", () => {
  assert.equal(pgApi("al barsha", "/findings"), "/hotels/al%20barsha/profit-guard/findings");
  assert.equal(pgHref("x", "/vcc"), "/app/x/profit/vcc");
});

test("majorToMinor parses by string and refuses junk", () => {
  assert.equal(majorToMinor("84.70"), 8470);
  assert.equal(majorToMinor("1,234.5"), 123450);
  assert.equal(majorToMinor("0.1"), 10);
  assert.equal(majorToMinor("12"), 1200);
  assert.equal(majorToMinor("12.345"), null);
  assert.equal(majorToMinor("-3"), null);
  assert.equal(majorToMinor("abc"), null);
  assert.equal(majorToMinor("5.5", 3), 5500);
});

test("minorToDecimal round-trips", () => {
  assert.equal(minorToDecimal(8470), "84.70");
  assert.equal(minorToDecimal(5), "0.05");
  assert.equal(minorToDecimal(-120), "-1.20");
  assert.equal(minorToDecimal(7, 0), "7");
});

test("num keeps unknown as null", () => {
  assert.equal(num(null), null);
  assert.equal(num(""), null);
  assert.equal(num("x"), null);
  assert.equal(num("0.850"), 0.85);
});

test("findingMoves mirrors the ledger status machine", () => {
  assert.deepEqual(findingMoves(f()), {
    accept: true, dispute: true, dismiss: true, reopen: false, markRecovered: false, markRejected: false,
  });
  const disputed = findingMoves(f({ status: "DISPUTED", dispute_packet_id: 3 }), true);
  assert.equal(disputed.dispute, false);
  assert.equal(disputed.markRecovered, true);
  assert.equal(disputed.markRejected, true);
  assert.equal(disputed.dismiss, true);
  assert.equal(findingMoves(f({ class: "PREVENTABLE" })).dispute, false);
  assert.equal(findingMoves(f({ status: "REJECTED" })).dispute, true);
  assert.equal(findingMoves(f({ status: "DISMISSED" })).reopen, true);
  assert.equal(findingMoves(f({ status: "RECOVERED" })).dismiss, false);
});

test("packetCandidates needs one OTA and one currency", () => {
  assert.equal(packetCandidates([f(), f({ id: 2 })]).reason, null);
  assert.match(packetCandidates([f(), f({ id: 2, ota_code: "AGODA" })]).reason, /one OTA/);
  assert.match(packetCandidates([f(), f({ id: 2, currency: "USD" })]).reason, /currencies/);
  assert.match(packetCandidates([f({ class: "COMPLIANCE" })]).reason, /recoverable/);
});

test("owedMinor never goes below zero", () => {
  assert.equal(owedMinor(f({ recovered_minor: 1000 })), 7470);
  assert.equal(owedMinor(f({ recovered_minor: 9999 })), 0);
});

test("card tiers 30/14/7/1", () => {
  assert.equal(cardTier(0), 1);
  assert.equal(cardTier(1), 1);
  assert.equal(cardTier(5), 7);
  assert.equal(cardTier(14), 14);
  assert.equal(cardTier(30), 30);
  assert.equal(cardTier(31), null);
  assert.equal(cardTierTone(-2), "bad");
  assert.equal(cardTierTone(6), "warn");
});

test("sortDeadlines puts the soonest first and unknown last", () => {
  const rows = [{ deadline_at: "2026-10-03T00:00:00Z" }, { deadline_at: null }, { deadline_at: "2026-10-01T08:00:00Z" }];
  assert.deepEqual(sortDeadlines(rows).map(r => r.deadline_at), ["2026-10-01T08:00:00Z", "2026-10-03T00:00:00Z", null]);
});

test("calc inputs label money keys and keep the rest", () => {
  const rows = calcInputs({ inputs: { price_minor: 45000, tol_pct: "1.0", rooms_booked: 2, x: null } });
  assert.deepEqual(rows.map(r => [r.label, r.minor, r.text]), [
    // Decimal strings lose trailing zeros ("1.0" -> "1", QA OWN-13 showed "18.00000000").
    ["Price", 45000, "45000"], ["Tol %", null, "1"], ["Rooms booked", null, "2"], ["X", null, "n/a"],
  ]);
  assert.equal(humanKey("commission_pct"), "Commission %");
  const s = splitConfidence([{ label: "Base", formula: "a" }, { label: "Confidence", formula: "b" }]);
  assert.equal(s.money.length, 1);
  assert.equal(s.confidence.label, "Confidence");
});

test("row evidence becomes readable and links its screenshot", () => {
  const out = panelEvidence([
    { kind: "row", label: "BOOKING logged_out desktop", ref: { table: "pfg_rate_evidence", id: 9, screenshot_file_id: "abc" } },
    { kind: "row", label: "", ref: { table: "pfg_ota_statement_lines", id: 42 } },
    { kind: "quote", label: "GDT 2.3.2", value: "Commission is not due" },
  ]);
  assert.equal(out.length, 4);
  assert.equal(out[0].value, "Rate evidence #9");
  assert.equal(out[1].kind, "file");
  assert.equal(out[1].ref.file_id, "abc");
  assert.equal(out[2].label, "Statement line #42");
  assert.equal(out[3].kind, "quote");
});

test("channel totals reconcile Gross to Net across months", () => {
  const row = (month, gross, net) => ({
    month, channel: "BOOKING", currency: "AED", room_nights: 10, gross_minor: gross, room_rev_minor: gross - 2000,
    sc_minor: 500, pass_through_tax_minor: 2000, commission_minor: 1500, commission_on_tax_minor: 0,
    payment_cost_minor: 100, fx_loss_minor: 0, cancellation_cost_minor: 0, program_cost_minor: 0, net_minor: net,
    waterfall: [],
  });
  const [t] = channelTotals([row("2026-08", 50000, 46400), row("2026-09", 60000, 56400)]);
  assert.equal(t.gross_minor, 110000);
  assert.equal(t.room_nights, 20);
  assert.equal(t.net_adr_minor, Math.round(102800 / 20));
  const end = t.waterfall.reduce((run, s) => (s.kind === "total" ? s.value_minor : run + s.value_minor), 0);
  assert.equal(t.waterfall.at(-1).label, "Net");
  assert.equal(end, 102800);
  // Walking the deltas from Gross lands on Net when SC is revenue.
  const deltas = t.waterfall.filter(s => s.kind === "delta").reduce((a, s) => a + s.value_minor, 0);
  assert.equal(110000 + deltas, 102800);
});

test("labels", () => {
  assert.equal(detectorLabel("L14"), "No-show not yet reported");
  assert.equal(detectorLabel("l1b"), "Fees not set up as charges");
  assert.equal(detectorLabel("ZZ"), "ZZ");
  assert.equal(otaLabel("BOOKING"), "Booking.com");
  assert.equal(otaLabel("hotel_beds"), "Hotel Beds");
  assert.equal(monthLabel("2026-09"), "Sep 2026");
  assert.equal(monthLabel("2026-09", true), "Sep 26");
});

test("parity grid compares like for like and flags undercuts beyond tolerance", () => {
  const ev = (id, channel, price, over = {}) => ({
    id, channel, price_minor: price, currency: "AED", stay_date: "2026-10-10", los: 1, adults: 2, device: "DESKTOP",
    login_state: "LOGGED_OUT", room_label: "Deluxe", comparable_key: "2026-10-10|1|2|deluxe|DESKTOP|LOGGED_OUT",
    captured_at: `2026-10-01T0${id}:00:00Z`, ...over,
  });
  const rows = parityGrid([
    ev(1, "DIRECT_WEB", 50000), ev(2, "BOOKING", 49600), ev(3, "AGODA", 49000),
    ev(4, "BOOKING", 48000, { captured_at: "2026-09-01T00:00:00Z" }), // older: ignored
    ev(5, "EXPEDIA", 40000, { login_state: "MEMBER", comparable_key: "2026-10-10|1|2|deluxe|DESKTOP|MEMBER" }),
  ], 1.0);
  assert.equal(rows.length, 2);
  const loggedOut = rows.find(r => r.login_state === "LOGGED_OUT");
  assert.equal(loggedOut.prices.BOOKING.price_minor, 49600);
  // 1% tolerance against the cheapest other channel: Agoda's limit is 496 x 0.99 = 491.04, so 490 breaches;
  // Booking's is 490 x 0.99 = 485.10, so 496 does not.
  assert.deepEqual(loggedOut.breaches, ["AGODA"]);
  assert.equal(loggedOut.anchor, "direct");
  // A member price alone has nothing like-for-like to compare against.
  assert.deepEqual(rows.find(r => r.login_state === "MEMBER").breaches, []);
});

test("contract rules read as words", () => {
  assert.equal(disputeRuleText({ kind: "HOURS_AFTER_CHECKOUT", hours: 48, applies_to: "no_show_or_change" }),
    "48 h after check-out (no show or change)");
  assert.equal(disputeRuleText({ kind: "BUSINESS_DAYS_AFTER_INVOICE", d: 14 }), "14 business days after the invoice");
  assert.equal(disputeRuleText({ kind: "DAY_OF_NEXT_MONTH", day: 4 }), "by the 4th of the next month");
  assert.equal(vccRuleText({ charge_within_days_after_checkout: 365 }), "charge within 365 days after check-out");
  assert.equal(vccRuleText({}), "None");
});
