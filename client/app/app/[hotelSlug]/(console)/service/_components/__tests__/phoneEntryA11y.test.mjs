/**
 * Phone entry room combobox (03 spec 1.8): arrows plus Enter is the main way
 * an operator picks a room, so a screen reader must hear the highlighted
 * suggestion. That needs aria-activedescendant on the input pointing at an
 * id every option carries. `npm test` picks it up; alone, run it from this
 * folder (the brackets in the path read as a glob): node --test phoneEntryA11y.test.mjs
 *
 * The component pulls React and the console kit, so this reads the source
 * (no bundler, no DOM) and checks the wiring plus the id helper itself.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, "..", "PhoneEntryConsole.tsx"), "utf8");

function tagWith(marker) {
  const at = SRC.indexOf(marker);
  assert.ok(at >= 0, `missing ${marker}`);
  const start = SRC.lastIndexOf("<", at);
  return SRC.slice(start, SRC.indexOf(">", SRC.indexOf("onKeyDown", start)) + 1);
}

test("the room input announces the highlighted suggestion", () => {
  const input = tagWith('role="combobox"');
  assert.match(input, /aria-controls="svc-pe-rooms"/);
  assert.match(input, /aria-activedescendant=\{[^}]*roomOptionId\(suggestions\[suggestIdx\]\.room\)/);
});

test("every room option carries the id the input points at", () => {
  assert.match(SRC, /<li key=\{r\.room\} id=\{roomOptionId\(r\.room\)\} role="option"/);
});

test("option ids are valid and stable for odd room labels", () => {
  const body = SRC.match(/function roomOptionId\(room: string\): string \{([\s\S]*?)\n\}/);
  assert.ok(body, "roomOptionId helper missing");
  const roomOptionId = new Function("room", body[1]);
  assert.equal(roomOptionId("502"), "svc-pe-room-502");
  assert.equal(roomOptionId("PH 1"), "svc-pe-room-PH_1");
  assert.doesNotMatch(roomOptionId("A/12 b"), /\s|\//);
});
