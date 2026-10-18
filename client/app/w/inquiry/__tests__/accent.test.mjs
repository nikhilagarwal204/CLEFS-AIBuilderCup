/**
 * Widget Send button contrast: the label colour follows the hotel's accent
 * so it always reaches WCAG AA (4.5:1) for the 16 px label.
 * `npm test` picks this up; alone: node --test accent.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { accentInk, contrast, DEFAULT_ACCENT, luminance } from "../styles.ts";

const ratio = (a, b) => contrast(luminance(a), luminance(b));

test("light gold and navy accents get a readable label", () => {
  assert.equal(accentInk("#E1AF68"), "#111111"); // white was 1.99:1
  assert.equal(accentInk("#1a3d7c"), "#ffffff"); // #111 was 1.8:1
  assert.equal(accentInk("#ffffff"), "#111111");
  assert.equal(accentInk("#000000"), "#ffffff");
});

test("every accent reaches AA with its computed ink", () => {
  for (let r = 0; r < 256; r += 17) {
    for (let g = 0; g < 256; g += 17) {
      for (let b = 0; b < 256; b += 17) {
        const hex = "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
        assert.ok(ratio(hex, accentInk(hex)) >= 4.5, `${hex} on ${accentInk(hex)}`);
      }
    }
  }
});

test("the default accent passes AA under white", () => {
  assert.equal(accentInk(DEFAULT_ACCENT), "#ffffff");
  assert.ok(ratio(DEFAULT_ACCENT, "#ffffff") >= 4.5);
  assert.equal(luminance("red"), null);
  assert.equal(accentInk("nope"), "#ffffff");
});
