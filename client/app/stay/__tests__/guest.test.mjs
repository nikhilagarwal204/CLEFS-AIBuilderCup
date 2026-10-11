// Guest page pure helpers (QA GS-9, GS-15). Node's runner, TS stripping:
//   cd client && node --test app/stay/__tests__/guest.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { offeredLanguages } from "../concierge/languages.ts";
import { orderableHere } from "../menuFilter.ts";
import { dirFor, translate } from "../../lib/i18n.ts";
import en from "../i18n/en.ts";
import ja from "../i18n/ja.ts";
import ko from "../i18n/ko.ts";
import th from "../i18n/th.ts";

// The guest UI languages (i18n/index.ts, the server's GUEST_LANGUAGES).
const ALL = ["en", "ar", "ru", "hi", "ur", "zh", "de", "fr", "ja", "ko", "th"];

// ─── offeredLanguages (GS-9) ───

test("a hotel with five languages offers those five, in its order", () => {
  assert.deepEqual(offeredLanguages(["en", "ar", "ru", "hi", "zh"], ALL, "en"), ["en", "ar", "ru", "hi", "zh"]);
});

test("region tags normalise and unsupported ones drop", () => {
  assert.deepEqual(offeredLanguages(["ar-AE", "EN_gb", "tl", "ar"], ALL, "ar"), ["ar", "en"]);
});

test("the current language stays listed even when the hotel lacks it", () => {
  assert.deepEqual(offeredLanguages(["en", "ar"], ALL, "de"), ["en", "ar", "de"]);
});

test("no configured languages offers every supported one", () => {
  assert.deepEqual(offeredLanguages([], ALL, "en"), ALL);
  assert.deepEqual(offeredLanguages(undefined, ALL), ALL);
  assert.deepEqual(offeredLanguages(["xx"], ALL, "en"), ALL);
});

// ─── Japanese, Korean and Thai (the Tokyo hotel's JAPAC guests) ───

test("a JAPAC hotel offers Japanese, Korean and Thai, from region tags too", () => {
  const tokyo = ["en", "ja", "ko", "zh", "th", "hi", "fr", "de", "ru", "ar", "ur"];
  assert.deepEqual(offeredLanguages(tokyo, ALL, "ja"), tokyo);
  assert.deepEqual(offeredLanguages(["ja-JP", "ko_KR", "th-TH"], ALL, "en"), ["ja", "ko", "th", "en"]);
});

test("Japanese, Korean and Thai read left to right; Arabic and Urdu stay right to left", () => {
  for (const lang of ["ja", "ko", "th", "ja-JP"]) assert.equal(dirFor(lang), "ltr", lang);
  for (const lang of ["ar", "ur"]) assert.equal(dirFor(lang), "rtl", lang);
});

test("the JAPAC dictionaries name themselves and fill their placeholders", () => {
  assert.deepEqual([ja.meta.name, ko.meta.name, th.meta.name], ["日本語", "한국어", "ไทย"]);
  const t = (dict) => (key, vars) => translate(dict, key, vars, en);
  assert.equal(t(ja)("emergency.call", { number: "119" }), "119に電話");
  assert.equal(t(ja)("emergency.kinds.fire_ambulance"), "消防・救急");
  assert.equal(t(ko)("job.etaMinutes", { n: 5 }), "약 5분");
  assert.equal(t(th)("footer.languages", { n: 11 }), "ผู้ช่วย AI · รองรับ 11 ภาษา");
  assert.equal(t(en)("emergency.kinds.fire_ambulance"), "Fire and ambulance");
});

// ─── orderableHere (GS-15) ───

const item = (item_id, outlet) => ({ item_id, outlet, section: null, name: `i${item_id}`, description: null,
  price: "1", price_minor: 100, currency: "AED" });
const outlets = [
  { key: "ird", name: "In-Room Dining", kind: "ird", delivers_to_room: true, open_now: true },
  { key: "lobby-cafe", name: "Lobby Cafe", kind: "cafe", delivers_to_room: false, open_now: true },
];
const items = [item(1, "ird"), item(2, "lobby-cafe"), item(3, "unknown")];

test("in a room, items from outlets that do not deliver to rooms are hidden", () => {
  assert.deepEqual(orderableHere(items, outlets, "107").map((i) => i.item_id), [1, 3]);
  assert.deepEqual(orderableHere(items, outlets, 107).map((i) => i.item_id), [1, 3]);
});

test("a location page (no room) keeps every outlet", () => {
  assert.deepEqual(orderableHere(items, outlets, null).map((i) => i.item_id), [1, 2, 3]);
  assert.deepEqual(orderableHere(items, [], "107").map((i) => i.item_id), [1, 2, 3]);
});
