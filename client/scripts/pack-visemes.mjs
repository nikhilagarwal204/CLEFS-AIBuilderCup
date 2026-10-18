#!/usr/bin/env node
/**
 * Packs a set of mouth-only portrait variants into a viseme sprite sheet.
 *
 * Input is deliberately the easiest thing to produce: eight full-size portraits
 * that are identical except for the mouth, which is what mouth-only inpainting
 * on a single locked still gives you. This script works out the rest - which
 * pixels actually changed, where to crop, how to feather the seam, and what
 * anchor the runtime needs - so nobody has to line sprites up by hand.
 *
 *   node scripts/pack-visemes.mjs <src-dir> [out-dir]
 *
 * Expects in <src-dir>:
 *   base.png          the locked portrait, mouth at rest
 *   rest.png  closed.png  slight.png  mid.png
 *   open.png  wide.png    spread.png  round.png
 *
 * Writes to <out-dir> (default public/avatar):
 *   avatar-base.webp      the portrait
 *   avatar-visemes.webp   horizontal strip of feathered mouth sprites
 *   visemes.json          manifest consumed by app/lib/visemeRig.ts
 *
 * See VISEMES.md for what each shape should look like.
 */

import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

// Where each shape sits on the two axes the runtime drives. Openness is the
// jaw; spread is lip width, 0.5 neutral. These are the rig's coordinates, not
// anything the artwork has to encode.
const VISEME_TABLE = [
  { id: "rest", openness: 0.0, spread: 0.5 },
  { id: "closed", openness: 0.08, spread: 0.5 },
  { id: "slight", openness: 0.26, spread: 0.5 },
  { id: "mid", openness: 0.48, spread: 0.5 },
  { id: "open", openness: 0.7, spread: 0.5 },
  { id: "wide", openness: 0.92, spread: 0.5 },
  { id: "spread", openness: 0.6, spread: 0.88 },
  { id: "round", openness: 0.55, spread: 0.12 },
];

/** Fraction of the changed-region size added as blending margin. */
const MARGIN = 0.14;
/** Feather width as a fraction of the sprite's smaller side. */
const FEATHER = 0.16;
/** A pixel counts as "changed" above this 0-255 difference. */
const DIFF_THRESHOLD = 18;

async function grayscale(file) {
  const img = sharp(file).grayscale().raw();
  const { data, info } = await img.toBuffer({ resolveWithObject: true });
  return { data, w: info.width, h: info.height };
}

function changedBounds(base, variants) {
  let minX = base.w, minY = base.h, maxX = -1, maxY = -1;
  for (const v of variants) {
    for (let y = 0; y < base.h; y++) {
      const row = y * base.w;
      for (let x = 0; x < base.w; x++) {
        if (Math.abs(v.data[row + x] - base.data[row + x]) <= DIFF_THRESHOLD) continue;
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) throw new Error("no differences found - are the variants identical to base.png?");
  return { minX, minY, maxX, maxY };
}

/** Feathered alpha so the sprite dissolves into the portrait with no seam. */
function featherMask(w, h) {
  const mask = Buffer.alloc(w * h);
  const f = Math.max(2, Math.round(Math.min(w, h) * FEATHER));
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const d = Math.min(x, y, w - 1 - x, h - 1 - y);
      const t = Math.min(1, d / f);
      // smoothstep: a linear ramp still shows a visible edge where it starts
      mask[y * w + x] = Math.round(255 * t * t * (3 - 2 * t));
    }
  }
  return mask;
}

async function main() {
  const srcDir = process.argv[2];
  if (!srcDir) {
    console.error("usage: node scripts/pack-visemes.mjs <src-dir> [out-dir]");
    process.exit(1);
  }
  const outDir = process.argv[3] ?? path.join("public", "avatar");

  const basePath = path.join(srcDir, "base.png");
  const base = await grayscale(basePath);
  const meta = await sharp(basePath).metadata();

  const variants = [];
  for (const v of VISEME_TABLE) {
    const p = path.join(srcDir, `${v.id}.png`);
    try {
      await fs.access(p);
    } catch {
      throw new Error(`missing ${v.id}.png in ${srcDir}`);
    }
    const g = await grayscale(p);
    if (g.w !== base.w || g.h !== base.h) {
      throw new Error(`${v.id}.png is ${g.w}x${g.h}, base is ${base.w}x${base.h} - all variants must match the base exactly`);
    }
    variants.push({ ...v, path: p, gray: g });
  }

  const b = changedBounds(base, variants.map((v) => v.gray));
  const mx = Math.round((b.maxX - b.minX) * MARGIN);
  const my = Math.round((b.maxY - b.minY) * MARGIN);
  const left = Math.max(0, b.minX - mx);
  const top = Math.max(0, b.minY - my);
  const right = Math.min(base.w, b.maxX + mx + 1);
  const bottom = Math.min(base.h, b.maxY + my + 1);
  const fw = right - left;
  const fh = bottom - top;

  console.log(`base            ${base.w}x${base.h}`);
  console.log(`mouth changes   x ${b.minX}..${b.maxX}  y ${b.minY}..${b.maxY}`);
  console.log(`sprite cell     ${fw}x${fh} at anchor (${left}, ${top})`);

  const mask = await sharp(featherMask(fw, fh), {
    raw: { width: fw, height: fh, channels: 1 },
  }).png().toBuffer();

  const cells = [];
  for (const v of variants) {
    const cell = await sharp(v.path)
      .extract({ left, top, width: fw, height: fh })
      .ensureAlpha()
      .composite([{ input: mask, blend: "dest-in" }])
      .png()
      .toBuffer();
    cells.push(cell);
  }

  await fs.mkdir(outDir, { recursive: true });

  const sheet = sharp({
    create: {
      width: fw * cells.length,
      height: fh,
      channels: 4,
      background: { r: 0, g: 0, b: 0, alpha: 0 },
    },
  }).composite(cells.map((input, i) => ({ input, left: i * fw, top: 0 })));

  const sheetName = "avatar-visemes.webp";
  const baseName = "avatar-base.webp";
  await sheet.webp({ quality: 92, alphaQuality: 100 }).toFile(path.join(outDir, sheetName));
  await sharp(basePath).webp({ quality: 92 }).toFile(path.join(outDir, baseName));

  const manifest = {
    version: 1,
    base: baseName,
    sheet: sheetName,
    frame: { w: fw, h: fh },
    baseSize: { w: meta.width, h: meta.height },
    anchor: { x: left, y: top },
    visemes: variants.map((v, i) => ({
      id: v.id,
      openness: v.openness,
      spread: v.spread,
      index: i,
    })),
  };
  await fs.writeFile(
    path.join(outDir, "visemes.json"),
    JSON.stringify(manifest, null, 2) + "\n",
  );

  const sheetStat = await fs.stat(path.join(outDir, sheetName));
  const baseStat = await fs.stat(path.join(outDir, baseName));
  console.log(`\nwrote ${outDir}/`);
  console.log(`  ${baseName}   ${(baseStat.size / 1024).toFixed(0)} KB`);
  console.log(`  ${sheetName}  ${(sheetStat.size / 1024).toFixed(0)} KB  (${cells.length} sprites)`);
  console.log(`  visemes.json`);
}

main().catch((err) => {
  console.error(`\npack-visemes: ${err.message}`);
  process.exit(1);
});
