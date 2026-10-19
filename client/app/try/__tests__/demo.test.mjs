/**
 * /try pure helpers: the lines the gate strip shows, the cast's wording and
 * the IndexedDB state the staff app opens on. `npm test` picks it up; alone:
 * node --test app/try/__tests__/demo.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  absoluteUrl, deviceKeys, floorsLine, grantError, languageName, shadowLine, storedSessions, testsLine,
} from "../demo.ts";

test("absolute links for the QR code and the clipboard", () => {
  assert.equal(absoluteUrl("https://clefs.example/", "/stay/demo-deira-crest/204?k=ABC"),
    "https://clefs.example/stay/demo-deira-crest/204?k=ABC");
  assert.equal(absoluteUrl("https://clefs.example", "try"), "https://clefs.example/try");
});

test("the gate strip says what the server measured, never more", () => {
  const base = { passed: 22, total: 22, expected: 22, judge_model: "gemini-3.5-flash", finished_at: null };
  assert.equal(testsLine({ ...base, state: "passed" }), "22/22 safety and accuracy tests passed, judged by Gemini");
  assert.equal(testsLine({ ...base, state: "failed", passed: 21 }),
    "21/22 safety and accuracy tests passed: not live until all 22 pass");
  assert.equal(testsLine({ ...base, state: "running", passed: 0, total: 0 }),
    "The 22 safety and accuracy tests are running now, judged by Gemini");
  assert.equal(testsLine({ ...base, state: "none", passed: 0, total: 0 }), "The 22 safety and accuracy tests have not run yet");
  assert.match(testsLine(null), /manager console/);
  assert.equal(shadowLine({ state: "done", day: 7, days: 7, started_at: null, until: null }), "Shadow mode: 7 of 7 days done");
  assert.equal(shadowLine({ state: "running", day: 3, days: 7, started_at: null, until: null }), "Shadow mode: day 3 of 7");
});

test("the cast reads naturally", () => {
  assert.equal(languageName("fil"), "Tagalog");
  assert.equal(languageName("hi"), "Hindi");
  assert.equal(floorsLine(["1", "2", "3", "4"]), "floors 1 to 4");
  assert.equal(floorsLine(["5", "7"]), "floors 5, 7");
  assert.equal(floorsLine([]), "every floor");
});

test("errors name the cause", () => {
  assert.match(grantError(429), /Too many tries/);
  assert.match(grantError(404), /not set up/);
  assert.match(grantError(403, "csrf_failed"), /Clefs site/);
  assert.match(grantError(0), /No connection/);
});

test("the staff app opens on Maria's session with the handover read", () => {
  assert.deepEqual(deviceKeys("demo-deira-crest"), { device: "device:demo-deira-crest", sessions: "sessions:demo-deira-crest" });
  const session = { session_id: 9, staff: { id: 41, name: "Maria Santos", department: "Housekeeping" }, planned_end_at: "2026-10-20T08:00:00+00:00" };
  assert.deepEqual(storedSessions(session, 1000), { 41: { ...session, started_at: 1000, handover_acked: true } });
});
