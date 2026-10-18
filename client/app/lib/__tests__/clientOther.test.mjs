// Regression tests for the client-other fixes. Node's own runner with its
// built-in TypeScript stripping (package.json is frozen, no vitest):
//   cd client && node --test app/lib/__tests__/clientOther.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { initials } from "../initials.ts";
import { SUBPROCESSORS, personalDataSubprocessors } from "../subprocessors.ts";

// ─── initials (QA GS-12, PUB-4) ───

test("initials skip a trailing (DEMO) tag instead of rendering 'A('", () => {
  assert.equal(initials("Ahmed Khan (DEMO)"), "AK");
  assert.equal(initials("Rana (DEMO)"), "R");
  assert.equal(initials("Karim Haddad [demo]"), "KH");
});

test("initials use letters only and fall back to '?'", () => {
  assert.equal(initials("Ahmed Khan"), "AK");
  assert.equal(initials("  maria   de la cruz "), "MC");
  assert.equal(initials("(DEMO)"), "?");
  assert.equal(initials(""), "?");
  assert.equal(initials(null), "?");
  assert.equal(initials("- 2 Omar"), "O");
  assert.equal(initials("سارة علي"), "سع");
});

// ─── sub-processors (review compliance-subprocessors-missing-msg91) ───

test("MSG91 is disclosed as an Indian SMS sub-processor with a transfer", () => {
  const msg91 = SUBPROCESSORS.find((s) => s.name === "MSG91");
  assert.ok(msg91, "MSG91 must be listed: notify routes +91 SMS to it");
  assert.equal(msg91.location, "India");
  assert.equal(msg91.transfer_abroad, true);
  assert.ok(msg91.data.includes("staff"));
  assert.ok(personalDataSubprocessors().some((s) => s.name === "MSG91"));
});

test("every SMS vendor that receives personal data is named", () => {
  const sms = SUBPROCESSORS.filter((s) => /\bSMS\b/.test(s.purpose)).map((s) => s.name).sort();
  assert.deepEqual(sms, ["MSG91", "Twilio"]);
});
