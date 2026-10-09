/**
 * Room QR cards (05 spec 1: "printable room QR cards"): the card URL is the
 * web origin plus the room's keyed stay path, the QR is drawn from the
 * qrcode package's modules, and the card text says "in your language".
 * `npm test` picks it up; alone, run it from this folder (the brackets in
 * the path read as a glob): node --test qrCards.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { create } from "qrcode";
import { CARDS_PER_SHEET, cardMessage, floorsOf, qrPath, selectCards, sheets, stayUrl } from "../qrCards.ts";

const ORIGIN = "https://clefs-632652019887.asia-south1.run.app";

test("the card URL is origin plus the keyed stay path", () => {
  assert.equal(stayUrl(ORIGIN, "/stay/demo-deira-crest/204?k=7F3A9C"), `${ORIGIN}/stay/demo-deira-crest/204?k=7F3A9C`);
  assert.equal(stayUrl(`${ORIGIN}/`, "/stay/demo-deira-crest/204?k=7F3A9C"), `${ORIGIN}/stay/demo-deira-crest/204?k=7F3A9C`);
  // a room number with a space is encoded once, never twice
  assert.equal(stayUrl(ORIGIN, "/stay/h/PH 1?k=K"), `${ORIGIN}/stay/h/PH%201?k=K`);
  assert.equal(stayUrl(ORIGIN, "/stay/h/PH%201?k=K"), `${ORIGIN}/stay/h/PH%201?k=K`);
  // unknown origin (the server render): no URL yet
  assert.equal(stayUrl("", "/stay/h/204?k=K"), "");
  assert.equal(stayUrl(ORIGIN, ""), "");
});

test("the card says in your language, no app needed", () => {
  assert.equal(cardMessage("Clefs"), "Scan to talk or type to Clefs, in your language. No app needed.");
  assert.equal(cardMessage(null), "Scan to talk or type to Clefs, in your language. No app needed.");
  assert.equal(cardMessage("  "), "Scan to talk or type to Clefs, in your language. No app needed.");
  assert.doesNotMatch(cardMessage("Layla"), /any language/i);
});

test("the QR path draws one rectangle per run of dark modules", () => {
  const rows = ["1101", "0000", "0111", "1000"];
  const m = { size: 4, get: (r, c) => rows[r][c] === "1" };
  assert.equal(qrPath(m), "M0 0h2v1h-2zM3 0h1v1h-1zM1 2h3v1h-3zM0 3h1v1h-1z");
});

test("a real symbol from the qrcode package keeps its finder patterns", () => {
  const url = stayUrl(ORIGIN, "/stay/demo-deira-crest/204?k=7F3A9C2B11D04E55");
  const qr = create(url, { errorCorrectionLevel: "M" });
  const d = qrPath(qr.modules);
  assert.ok(qr.modules.size >= 25);
  // the top-left finder pattern starts with a run of 7 dark modules
  assert.ok(d.startsWith("M0 0h7v1h-7z"), d.slice(0, 40));
  // and every module is inside the symbol
  for (const [, x, y, run] of d.matchAll(/M(\d+) (\d+)h(\d+)/g)) {
    assert.ok(Number(x) + Number(run) <= qr.modules.size && Number(y) < qr.modules.size);
  }
});

test("floors in building order, cards by floor and search, sheets of four", () => {
  const rooms = [
    { room_number: "1201", floor: "12", qr_key_version: 1, stay_path: "/a" },
    { room_number: "204", floor: "2", qr_key_version: 1, stay_path: "/b" },
    { room_number: "PH-1", floor: "Penthouse", qr_key_version: 2, stay_path: "/c" },
    { room_number: "202", floor: "2", qr_key_version: 1, stay_path: "/d" },
    { room_number: "Lobby", floor: null, qr_key_version: 1, stay_path: "/e" },
  ];
  assert.deepEqual(floorsOf(rooms), ["2", "12", "Penthouse"]);
  assert.deepEqual(selectCards(rooms, { floor: "2" }).map(r => r.room_number), ["202", "204"]);
  // numeric room order: 202 before 1201
  assert.deepEqual(selectCards(rooms, { query: "20" }).map(r => r.room_number), ["202", "204", "1201"]);
  assert.equal(selectCards(rooms, {}).length, 5);
  assert.equal(CARDS_PER_SHEET.a4, 4);
  assert.deepEqual(sheets([1, 2, 3, 4, 5], CARDS_PER_SHEET.a4), [[1, 2, 3, 4], [5]]);
  assert.deepEqual(sheets([1, 2], CARDS_PER_SHEET.a6), [[1], [2]]);
});
