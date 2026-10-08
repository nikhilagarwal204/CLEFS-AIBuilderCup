/**
 * Trust pure helpers. Regression for OWN-05: the privacy queue rendered
 * identifiers_hint (an object from trust/dsr.py _hint) as a React child and
 * the whole Trust > Privacy page crashed with React #31. dsrPerson() must
 * only ever return plain strings, whatever shape the payload takes.
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test pure.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { dsrPerson } from "../pure.ts";

// The exact hint shape the server stores (trust/dsr.py _hint).
const SEEDED_HINT = { name: "Qasim", email: "q•••@example.com", phone: "•••• 4567", room: "412", has_reservation_ref: true };

const isStr = v => typeof v === "string";

// ─── OWN-05: the seeded request that crashed the page ───

test("object hint with empty identifiers yields strings, never the object", () => {
  const p = dsrPerson({ identifiers: {}, identifiers_hint: SEEDED_HINT });
  assert.ok(isStr(p.primary));
  assert.equal(p.primary, "Qasim");
  assert.equal(p.secondary, "q•••@example.com");
});

test("role-masked identifiers win over the intake hint", () => {
  const p = dsrPerson({
    identifiers: { name: "Qasim Al Mansoori", email: "qasim@example.com", phone: "+971501234567" },
    identifiers_hint: SEEDED_HINT,
  });
  assert.equal(p.primary, "Qasim Al Mansoori");
  assert.equal(p.secondary, "qasim@example.com");
});

// ─── Fallbacks ───

test("no name: contact first, room as the second line", () => {
  const p = dsrPerson({ identifiers: {}, identifiers_hint: { name: null, email: null, phone: "•••• 4567", room: "412" } });
  assert.deepEqual(p, { primary: "•••• 4567", secondary: "Room 412" });
});

test("only a room (numeric in JSON) still reads as text", () => {
  const p = dsrPerson({ identifiers: { room: 412 }, identifiers_hint: null });
  assert.deepEqual(p, { primary: "Room 412", secondary: null });
});

test("nothing known: a dash, no second line", () => {
  assert.deepEqual(dsrPerson({ identifiers: {}, identifiers_hint: null }), { primary: "-", secondary: null });
  assert.deepEqual(dsrPerson({ identifiers: null, identifiers_hint: {} }), { primary: "-", secondary: null });
  assert.deepEqual(dsrPerson({ identifiers: undefined, identifiers_hint: undefined }), { primary: "-", secondary: null });
});

test("legacy string hint is still shown", () => {
  assert.equal(dsrPerson({ identifiers: {}, identifiers_hint: "Q. (q•••@example.com)" }).primary, "Q. (q•••@example.com)");
});

test("non-text values (objects, booleans, blanks) are ignored, never returned", () => {
  const p = dsrPerson({
    identifiers: { name: { first: "Q" }, email: "   ", phone: true },
    identifiers_hint: { name: ["Q"], email: { v: 1 }, phone: false, room: NaN, has_reservation_ref: true },
  });
  assert.deepEqual(p, { primary: "-", secondary: null });
  for (const v of Object.values(p)) assert.ok(v === null || isStr(v));
});
