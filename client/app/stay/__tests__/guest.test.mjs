// Guest page pure helpers (QA GS-9, GS-15). Node's runner, TS stripping:
//   cd client && node --test app/stay/__tests__/guest.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";
import { offeredLanguages } from "../concierge/languages.ts";
import { orderableHere } from "../menuFilter.ts";

const ALL = ["en", "ar", "ru", "hi", "ur", "zh", "de", "fr"];

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
