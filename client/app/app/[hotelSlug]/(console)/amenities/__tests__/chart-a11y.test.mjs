/**
 * Regressions for the amenities charts (ux-conventions-4 and -6):
 * - the grouped columns' screen-reader table and legend must not sit inside
 *   role="img" (ARIA makes an img's children presentational);
 * - the "Expected" benchmark series must clear 3:1 on the chart card
 *   (WCAG 1.4.11), which the kit's CHART_QUIET does not.
 * `npm test` picks it up; alone, run it from this folder (the brackets in the
 * path read as a glob): node --test chart-a11y.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { CHART_EXPECTED } from "../_components/lib.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const COMP = path.resolve(HERE, "../_components");
const KIT = path.resolve(HERE, "../../../../../ui/charts-extra.tsx");
const src = f => fs.readFileSync(path.join(COMP, f), "utf8");

// ─── Colour maths (WCAG 2.x relative luminance) ───

function rgb(hex) {
  const h = hex.replace("#", "");
  return [0, 2, 4].map(i => parseInt(h.slice(i, i + 2), 16));
}

function lum([r, g, b]) {
  const ch = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}

function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** An rgba() token composited over an opaque background. */
function over(rgba, bg) {
  const m = rgba.match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/);
  assert.ok(m, `not an rgba token: ${rgba}`);
  const [r, g, b, a] = m.slice(1).map(Number);
  return [r, g, b].map((c, i) => a * c + (1 - a) * bg[i]);
}

// The card surface is read from the kit so a palette change re-runs this.
const SURFACE = rgb(fs.readFileSync(KIT, "utf8").match(/SURFACE = "(#[0-9A-Fa-f]{6})"/)[1]);

// ─── Tests ───

test("the Expected benchmark clears 3:1 on the chart card", () => {
  const ratio = contrast(over(CHART_EXPECTED, SURFACE), SURFACE);
  assert.ok(ratio >= 3, `Expected is ${ratio.toFixed(2)}:1`);
});

test("both amenities charts draw Expected in the legible benchmark colour", () => {
  assert.match(src("Overview.tsx"), /label: "Expected", color: CHART_EXPECTED/);
  assert.match(src("ItemTrend.tsx"), /label: "Expected", color: CHART_EXPECTED/);
  for (const f of ["Overview.tsx", "ItemTrend.tsx"]) assert.doesNotMatch(src(f), /CHART_QUIET/);
});

test("grouped columns keep the data table outside role=img", () => {
  const s = src("GroupedColumns.tsx");
  const wrapper = s.match(/<div ref=\{ref\}[^>]*>/)[0];
  assert.doesNotMatch(wrapper, /role=/, "the wrapper holds the table, so it must not be an img");
  const svg = s.match(/<svg[^>]*>/)[0];
  assert.match(svg, /role="img"/);
  assert.match(svg, /aria-label=\{ariaLabel\}/);
  assert.doesNotMatch(svg, /aria-hidden/);
  // The table and legend come after the svg closes, so they are its siblings.
  const svgEnd = s.indexOf("</svg>");
  assert.ok(svgEnd > 0 && s.indexOf('className="amn-sr"') > svgEnd && s.indexOf('className="cx-legend"') > svgEnd);
});
