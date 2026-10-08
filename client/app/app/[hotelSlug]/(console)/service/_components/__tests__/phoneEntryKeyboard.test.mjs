/**
 * Phone entry keyboard flow (QA GS-14) and the inquiry hand-off (04 spec
 * 4.3). "502, Enter, 1, 3, note, Enter" must file towels without a Tab:
 * the department pick moves focus to the request types, types have digit
 * hotkeys, a type moves focus to the quantity or note, and each chip group
 * is one Tab stop. A caller asking about a booking goes to
 * inquiries/new?source=phone_desk&room=. Alone, run it from this folder
 * (the brackets in the path read as a glob): node --test phoneEntryKeyboard.test.mjs
 *
 * Like phoneEntryA11y, this reads the source (no bundler, no DOM).
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SRC = fs.readFileSync(path.resolve(HERE, "..", "PhoneEntryConsole.tsx"), "utf8");
const INQ_PAGE = fs.readFileSync(path.resolve(HERE, "..", "..", "..", "inquiries", "new", "page.tsx"), "utf8");
const INQ_FORM = fs.readFileSync(path.resolve(HERE, "..", "..", "..", "inquiries", "new", "NewInquiry.tsx"), "utf8");

function body(name) {
  const at = SRC.indexOf(`const ${name} = `);
  assert.ok(at >= 0, `missing ${name}`);
  return SRC.slice(at, SRC.indexOf("\n  };\n", at));
}

test("picking a department moves focus to the request types", () => {
  assert.match(body("pickDept"), /focusNext\("types"\)/);
  assert.match(SRC, /focusReq\.to === "types"\) rovingStop\(gridRef\.current\)\?\.focus\(\)/);
  assert.match(SRC, /className="svc-pe-grid" ref=\{gridRef\}/);
});

test("picking a type moves focus to the quantity, else the note", () => {
  assert.match(body("pickType"), /focusNext\("detail"\)/);
  assert.match(SRC, /\(qtyRef\.current \?\? noteRef\.current\)\?\.focus\(\)/);
  assert.match(SRC, /ref=\{qtyRef\} aria-label="One more"/);
});

test("request types carry digit hotkeys once a department is chosen", () => {
  assert.match(SRC, /aria-keyshortcuts=\{i < 9 \? String\(i \+ 1\) : undefined\} onClick=\{\(\) => pickType\(t\)\}/);
  assert.match(SRC, /digit && \(inTypes \|\| !typeKey\)\) \{\s*const ty = types\[digit - 1\]/);
});

test("each chip group is a single Tab stop walked with arrows", () => {
  assert.match(SRC, /tabIndex=\{\(dept \? dept === d\.key : i === 0\) \? 0 : -1\}/);
  assert.match(SRC, /tabIndex=\{\(types\.some\(x => x\.key === typeKey\) \? typeKey === t\.key : i === 0\) \? 0 : -1\}/);
  assert.match(SRC, /group && ARROWS\.includes\(e\.key\)/);
});

test("the footer links a booking caller to a phone-desk inquiry with the room", () => {
  assert.match(SRC, /can\("inquiries\.respond"\) && \(/);
  assert.match(SRC, /inquiries\/new\?source=phone_desk\$\{room \? `&room=\$\{encodeURIComponent\(room\)\}` : ""\}/);
  assert.match(INQ_PAGE, /<NewInquiry room=\{room\} \/>/);
  assert.match(INQ_FORM, /In-house guest, room \$\{room\}: /);
});

test("the inquiry page accepts only a room label from the query", () => {
  const re = INQ_PAGE.match(/const room = (\/.*\/u)\.test\(raw\)/);
  assert.ok(re, "room pattern missing");
  const pattern = new Function(`return ${re[1]}`)();
  assert.ok(pattern.test("502") && pattern.test("PH 1") && pattern.test("A-12"));
  assert.ok(!pattern.test("<script>") && !pattern.test("502; drop") && !pattern.test("x".repeat(21)));
});
