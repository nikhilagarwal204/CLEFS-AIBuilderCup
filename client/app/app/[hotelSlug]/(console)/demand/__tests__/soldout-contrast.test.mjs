/**
 * Regression (ux-conventions-3): the heatmap's "N sold out" marker must stay
 * legible on every heat level. It sits on its own dark chip, so the text
 * contrast is measured against the chip composited over each HEAT_RAMP fill
 * and must clear WCAG AA for small text (4.5:1). `npm test` picks it up;
 * alone, run it from this folder: node --test soldout-contrast.test.mjs
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { drStyles } from "../_lib/styles.ts";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const KIT = path.resolve(HERE, "../../../../../ui/charts-extra.tsx");

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

// ─── Rule extraction ───

/** Declarations of the LAST rule whose selector is exactly `sel`. */
function rule(sel) {
  const re = new RegExp(`(^|\\n)${sel.replace(/[.[\]]/g, m => `\\${m}`)}\\s*\\{([^}]*)\\}`, "g");
  let m, body = null;
  while ((m = re.exec(drStyles))) body = m[2];
  assert.ok(body, `no ${sel} rule`);
  return body;
}

// The kit's ramp is read from source so a palette change re-runs this check.
const HEAT_RAMP = [...fs.readFileSync(KIT, "utf8").match(/HEAT_RAMP = \[([^\]]*)\]/)[1].matchAll(/#[0-9A-Fa-f]{6}/g)]
  .map(m => m[0]);

test("sold-out marker clears 4.5:1 on every heat level", () => {
  assert.equal(HEAT_RAMP.length, 5);
  const body = rule(".dr-cell__soldout");
  const fg = rgb(body.match(/(?:^|[\s;])color:\s*(#[0-9A-Fa-f]{6})/)[1]);
  const bg = body.match(/background:\s*rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/);
  assert.ok(bg, "the marker needs its own chip background");
  const [cr, cg, cb, a] = bg.slice(1).map(Number);
  assert.ok(a >= 0.85, "the chip must be near-opaque so the heat fill barely shows through");
  for (const fill of HEAT_RAMP) {
    const under = rgb(fill);
    const chip = [cr, cg, cb].map((c, i) => a * c + (1 - a) * under[i]);
    assert.ok(contrast(fg, chip) >= 4.5, `${fill}: ${contrast(fg, chip).toFixed(2)}:1`);
  }
});

test("no heat-dependent colour override remains on the marker", () => {
  assert.doesNotMatch(drStyles, /\.dr-cell\[data-dark\]\s+\.dr-cell__soldout/);
});
