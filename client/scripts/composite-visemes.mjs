#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const VISEME_GENERATIONS = {
  closed: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_closed_1789205118428.jpg",
  slight: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_slight_1789205140965.jpg",
  mid: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_mid_1789205163613.jpg",
  open: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_open_1789205186654.jpg",
  wide: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_wide_v2_1789205638700.jpg",
  spread: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_spread_1789205262807.jpg",
  round: "/Users/nikhilagarwal/.gemini/antigravity/brain/ac815215-2b42-416a-81cc-930831975b92/viseme_round_1789205293449.jpg",
};

const BASE_IMAGE_PATH = "public/female.png";
const OUT_DIR = "avatar-source";

async function main() {
  await fs.mkdir(OUT_DIR, { recursive: true });

  // Base and Rest
  await sharp(BASE_IMAGE_PATH).png().toFile(path.join(OUT_DIR, "base.png"));
  await sharp(BASE_IMAGE_PATH).png().toFile(path.join(OUT_DIR, "rest.png"));
  console.log("Copied base.png and rest.png");

  const baseSharp = sharp(BASE_IMAGE_PATH);
  const baseMeta = await baseSharp.metadata();
  const baseRaw = await baseSharp.raw().toBuffer({ resolveWithObject: true });
  const bw = baseMeta.width;
  const bh = baseMeta.height;
  const baseGray = await sharp(BASE_IMAGE_PATH).grayscale().raw().toBuffer({ resolveWithObject: true });

  // Target mouth/lower-face patch in base coordinates:
  const left = 230;
  const top = 590;
  const w = 250;
  const h = 175;

  for (const [id, genPath] of Object.entries(VISEME_GENERATIONS)) {
    console.log(`Processing ${id}...`);
    const genMeta = await sharp(genPath).metadata();
    const sx = genMeta.width / bw;
    const sy = genMeta.height / bh;

    // Find best dx, dy by matching nose region (y: 500..580, x: 300..420)
    const genResizedGray = await sharp(genPath).resize(bw, bh, { fit: "fill" }).grayscale().raw().toBuffer({ resolveWithObject: true });
    let bestScore = Infinity;
    let bestDx = 0;
    let bestDy = -15;

    for (let dy = -20; dy <= -10; dy++) {
      for (let dx = -6; dx <= 6; dx++) {
        let diff = 0;
        let count = 0;
        for (let y = 500; y < 580; y += 2) {
          for (let x = 300; x < 420; x += 2) {
            const gy = y + dy;
            const gx = x + dx;
            if (gy < 0 || gy >= bh || gx < 0 || gx >= bw) continue;
            diff += Math.abs(baseGray.data[y * bw + x] - genResizedGray.data[gy * bw + gx]);
            count++;
          }
        }
        const score = diff / count;
        if (score < bestScore) {
          bestScore = score;
          bestDx = dx;
          bestDy = dy;
        }
      }
    }

    console.log(`  alignment for ${id}: dx=${bestDx}, dy=${bestDy}, score=${bestScore.toFixed(2)}`);

    const gLeft = Math.round((left + bestDx) * sx);
    const gTop = Math.round((top + bestDy) * sy);
    const gW = Math.round(w * sx);
    const gH = Math.round(h * sy);

    const genPatch = await sharp(genPath)
      .extract({ left: gLeft, top: gTop, width: gW, height: gH })
      .resize(w, h)
      .raw()
      .toBuffer({ resolveWithObject: true });

    // Compute skin boundary color calibration
    let dR = 0;
    let dG = 0;
    let dB = 0;
    let count = 0;
    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        if (x < 7 || x >= w - 7 || y < 7 || y >= h - 7) {
          const pIdx = (y * w + x) * 3;
          const bIdx = ((top + y) * bw + (left + x)) * 3;
          dR += baseRaw.data[bIdx] - genPatch.data[pIdx];
          dG += baseRaw.data[bIdx + 1] - genPatch.data[pIdx + 1];
          dB += baseRaw.data[bIdx + 2] - genPatch.data[pIdx + 2];
          count++;
        }
      }
    }
    const offR = dR / count;
    const offG = dG / count;
    const offB = dB / count;

    // Superellipse feather mask
    const rgba = Buffer.alloc(w * h * 4);
    const pcx = w / 2;
    const pcy = h / 2;
    const prx = w * 0.48;
    const pry = h * 0.48;

    for (let y = 0; y < h; y++) {
      for (let x = 0; x < w; x++) {
        const pIdx = (y * w + x) * 3;
        const oIdx = (y * w + x) * 4;

        rgba[oIdx] = Math.min(255, Math.max(0, Math.round(genPatch.data[pIdx] + offR)));
        rgba[oIdx + 1] = Math.min(255, Math.max(0, Math.round(genPatch.data[pIdx + 1] + offG)));
        rgba[oIdx + 2] = Math.min(255, Math.max(0, Math.round(genPatch.data[pIdx + 2] + offB)));

        const nx = Math.abs(x - pcx) / prx;
        const ny = Math.abs(y - pcy) / pry;
        const dist = Math.pow(Math.pow(nx, 2.5) + Math.pow(ny, 2.5), 1 / 2.5);
        let alpha = 0;
        if (dist < 0.65) {
          alpha = 1;
        } else if (dist < 1.0) {
          const t = (1.0 - dist) / 0.35;
          alpha = t * t * (3 - 2 * t);
        }
        rgba[oIdx + 3] = Math.round(alpha * 255);
      }
    }

    const patchWithAlpha = await sharp(rgba, {
      raw: { width: w, height: h, channels: 4 },
    }).png().toBuffer();

    const outFile = path.join(OUT_DIR, `${id}.png`);
    await sharp(BASE_IMAGE_PATH)
      .composite([{ input: patchWithAlpha, left, top }])
      .png()
      .toFile(outFile);

    console.log(`  wrote ${outFile}`);
  }

  console.log("\nAll 9 PNGs ready in " + OUT_DIR);
}

main().catch((err) => {
  console.error("composite-visemes error:", err);
  process.exit(1);
});
