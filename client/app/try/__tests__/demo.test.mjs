/**
 * "Try it yourself" pure helpers (app/try/demo.ts): the demo hotel, the cast
 * the three doors name, plain error wording and the IndexedDB state the staff
 * app opens on. `npm test` picks it up; alone:
 * node --test app/try/__tests__/demo.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  DECK_CAST, DEMO_HOTEL, absoluteUrl, castOf, deviceKeys, grantError, languageName, storedSessions,
} from "../demo.ts";

test("the root opens the Tokyo demo hotel", () => {
  if (!process.env.NEXT_PUBLIC_DEMO_HOTEL) assert.equal(DEMO_HOTEL, "aozora-shinjuku");
});

test("absolute links for the QR code and the clipboard", () => {
  assert.equal(absoluteUrl("https://clefs.example/", "/stay/aozora-shinjuku/204?k=ABC"),
    "https://clefs.example/stay/aozora-shinjuku/204?k=ABC");
  assert.equal(absoluteUrl("https://clefs.example", "stay"), "https://clefs.example/stay");
});

test("before the server answers, the doors name the deck's cast", () => {
  assert.deepEqual(castOf(null), {
    hotel: "Hotel Aozora Shinjuku", city: "Tokyo", room: "204", staffFirst: "Maria",
    staffLanguage: "Tagalog", pin: "2046", manager: "Yuki Sato",
  });
  assert.equal(DECK_CAST.manager, "Yuki Sato");
});

test("once it answers, the doors name the live cast", () => {
  const info = {
    hotel: { slug: "aozora-shinjuku", name: "Hotel Aozora Shinjuku", city: "Tokyo", assistant_name: "Clefs",
             guest_languages: ["en", "ja", "ko"], refreshed_at: null },
    guest: { room: "305", floor: "3", path: "/stay/aozora-shinjuku/305?k=K" },
    staff: { name: "Priya Sharma", first_name: "Priya", department: "Housekeeping", floors: ["5"], languages: ["hi", "en"],
             pin: "1234" },
    console: { name: "Ken Ito", role: "Duty manager" },
  };
  assert.deepEqual(castOf(info), {
    hotel: "Hotel Aozora Shinjuku", city: "Tokyo", room: "305", staffFirst: "Priya",
    staffLanguage: "Hindi", pin: "1234", manager: "Ken Ito",
  });
  // A hotel with no city on file still reads as a sentence.
  assert.equal(castOf({ ...info, hotel: { ...info.hotel, city: null } }).city, "Tokyo");
});

test("language codes read as names", () => {
  assert.equal(languageName("fil"), "Tagalog");
  assert.equal(languageName("ja"), "Japanese");
  assert.equal(languageName("ko"), "Korean");
  assert.equal(languageName("th"), "Thai");
  assert.equal(languageName("hi"), "Hindi");
  assert.equal(languageName(""), "English");
});

test("errors name the cause in plain words", () => {
  assert.match(grantError(429), /Too many tries/);
  assert.match(grantError(404), /not available right now/);
  assert.match(grantError(403, "csrf_failed"), /Clefs site/);
  assert.match(grantError(0), /No connection/);
  assert.match(grantError(500), /did not work/);
});

test("the staff app opens on Maria's session with the handover read", () => {
  assert.deepEqual(deviceKeys("aozora-shinjuku"), { device: "device:aozora-shinjuku", sessions: "sessions:aozora-shinjuku" });
  const session = { session_id: 9, staff: { id: 41, name: "Maria Santos", department: "Housekeeping" }, planned_end_at: "2026-10-20T08:00:00+00:00" };
  assert.deepEqual(storedSessions(session, 1000), { 41: { ...session, started_at: 1000, handover_acked: true } });
});
