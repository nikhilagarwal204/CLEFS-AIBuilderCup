/**
 * Builds every Clefs raster brand asset from ONE master file:
 *
 *   brand/source/clefs-key-master.jpg   1024x1536, gold key on slate
 *
 * The master is a rendered image, not vector, so the key is chroma-keyed off its
 * slate ground rather than re-drawn. Gold and slate separate almost perfectly on
 * (R - B): slate sits at -45..+8 (it is blue-biased, and even its bright mineral
 * veins stay neutral) while the gold sits at +70..+167. Luminance alone does NOT
 * work — the veins are as bright as the gold's shadow side.
 *
 * The same master supplies the stone texture: three bands of it are clear of the
 * key, and the biggest clean square lives under the key's teeth.
 *
 * Run from client/ so that `sharp` resolves:  node ../brand/build-brand.mjs
 */
import { execFileSync } from "node:child_process";
import { writeFileSync, mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const BRAND = dirname(fileURLToPath(import.meta.url));
const REPO = join(BRAND, "..");
// sharp ships with the Next app, not at the repo root, so resolve it from there.
const sharp = createRequire(join(REPO, "client", "package.json"))("sharp");

const MASTER = join(BRAND, "source", "clefs-key-master.jpg");
const OUT_APP = join(REPO, "client", "app");
const OUT_PUBLIC = join(REPO, "client", "public");
const OUT_BRAND = join(OUT_PUBLIC, "brand");
// Full-quality masters, kept in the repo but never served: decks, print, press.
const OUT_DIST = join(BRAND, "dist");
const TMP = mkdtempSync(join(tmpdir(), "clefs-brand-"));
const CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

mkdirSync(OUT_BRAND, { recursive: true });
mkdirSync(OUT_DIST, { recursive: true });

/* ── Palette, sampled from the master itself so the site cannot drift from the
      logo. Gold stops are percentiles of the key's own pixels. ───────────── */
export const GOLD = {
  900: "#5B2700", 800: "#885224", 700: "#A7763E", 600: "#C59253",
  500: "#E1AF68", 400: "#F2C67D", 300: "#FDDA96", 200: "#FFEDA1",
};
export const SLATE = {
  950: "#03060C", 900: "#070C14", 850: "#0B121B",
  800: "#0F1720", 700: "#16202D", 600: "#1E2B3A",
};
const GOLD_TEXT = `background:linear-gradient(158deg,${GOLD[200]} 0%,${GOLD[300]} 14%,${GOLD[500]} 46%,${GOLD[600]} 68%,${GOLD[700]} 86%,${GOLD[800]} 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent;`;

const log = (label, file, extra = "") =>
  console.log(`  ${label.padEnd(16)} -> ${file}${extra ? "  " + extra : ""}`);

/* ══ 1. Key cutout ══════════════════════════════════════════════════════════
   Alpha ramps over the empty part of the (R - B) histogram. Partial-alpha edge
   pixels are un-premultiplied against the slate so the key carries no dark
   halo when it lands on a light background (print, decks, light UI). */
async function cutKey() {
  const { data, info } = await sharp(MASTER).raw().toBuffer({ resolveWithObject: true });
  const { width: W, height: H, channels: C } = info;
  const LO = 16, HI = 58;          // (R - B) ramp: fully slate -> fully gold
  const BG = [20, 28, 40];         // mean slate, for un-premultiplying edges
  const out = Buffer.alloc(W * H * 4);

  for (let i = 0; i < W * H; i++) {
    let r = data[i * C], g = data[i * C + 1], b = data[i * C + 2];
    const a = Math.min(1, Math.max(0, (r - b - LO) / (HI - LO)));
    if (a > 0.004) {
      r = (r - (1 - a) * BG[0]) / a;
      g = (g - (1 - a) * BG[1]) / a;
      b = (b - (1 - a) * BG[2]) / a;
    }
    out[i * 4]     = Math.min(255, Math.max(0, Math.round(r)));
    out[i * 4 + 1] = Math.min(255, Math.max(0, Math.round(g)));
    out[i * 4 + 2] = Math.min(255, Math.max(0, Math.round(b)));
    out[i * 4 + 3] = Math.round(a * 255);
  }

  const keyed = await sharp(out, { raw: { width: W, height: H, channels: 4 } })
    .png().toBuffer();
  // Trim to the drawn pixels, then re-add a 3% breathing margin.
  const trimmed = await sharp(keyed).trim({ threshold: 8 }).toBuffer();
  const m = await sharp(trimmed).metadata();
  const px = Math.round(m.width * 0.03), py = Math.round(m.height * 0.03);
  return sharp(trimmed)
    .extend({ top: py, bottom: py, left: px, right: px,
              background: { r: 0, g: 0, b: 0, alpha: 0 } })
    .png().toBuffer();
}

/* ══ 2. Stone texture ═══════════════════════════════════════════════════════
   A mid-grey, seamless tile meant to sit under `mix-blend-mode: soft-light`.
   Centring the mean on 128 is what makes it neutral: the tile then contributes
   only grain and veining, and every colour decision stays in CSS.

   This is synthesised rather than cropped out of the master. The master's clean
   bands are ~300px and every way of tiling a 300px crop betrays itself — a 2x2
   mirror in particular turns the slate's diagonal cracks into chevrons and
   diamonds, which reads as wallpaper. Periodic value noise wraps exactly by
   construction, at any size, with no motif to recognise.

   Two layers, both banded to match real slate:
     grain  smooth fBm, the fine mineral mottling
     veins  ridged fBm (1 - |n|), whose creases land as thin bright filaments */

// Periodic value noise. Hashing the lattice modulo `period` is the whole trick:
// the lattice wraps, so the noise wraps, so the tile is seamless on all edges.
function noiseLayer(size, period, seed) {
  const hash = (x, y) => {
    let h = (x % period) * 374761393 + (y % period) * 668265263 + seed * 1442695040888963407;
    h = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967295;
  };
  const smooth = (t) => t * t * (3 - 2 * t);
  const cell = size / period;
  const out = new Float64Array(size * size);
  for (let y = 0; y < size; y++) {
    const gy = y / cell, y0 = Math.floor(gy), fy = smooth(gy - y0);
    for (let x = 0; x < size; x++) {
      const gx = x / cell, x0 = Math.floor(gx), fx = smooth(gx - x0);
      const a = hash(x0, y0), b = hash(x0 + 1, y0);
      const c = hash(x0, y0 + 1), d = hash(x0 + 1, y0 + 1);
      out[y * size + x] = (a + (b - a) * fx) + ((c + (d - c) * fx) - (a + (b - a) * fx)) * fy;
    }
  }
  return out;
}

async function buildTexture(size = 512) {
  const grain = new Float64Array(size * size);
  const veins = new Float64Array(size * size);

  // Octaves are given as feature size in PIXELS, so a 256px and a 512px tile
  // come out with identical grain — only the period before it repeats differs.
  // Amplitudes deliberately do NOT follow the usual fBm 1/f rule: 1/f puts most
  // of the energy in the lowest octave, and at page scale that reads as drifting
  // cloud rather than stone. Weighting the fine octaves highest gives the tight
  // mineral grain slate actually has, and has the side benefit that the tile's
  // repeat is far harder to see.
  const octaves = [[64, 0.26], [32, 0.30], [16, 0.44], [8, 0.78], [4, 1.0], [2, 0.55]];
  let norm = 0;
  octaves.forEach(([cell, amp], i) => {
    const n = noiseLayer(size, size / cell, 11 + i * 7);
    for (let k = 0; k < grain.length; k++) grain[k] += (n[k] - 0.5) * amp;
    norm += amp;
  });

  [32, 16, 8].forEach((cell, i) => {
    const n = noiseLayer(size, size / cell, 101 + i * 13);
    const amp = 1 / (i + 1);
    for (let k = 0; k < veins.length; k++) {
      // Ridged: the noise's zero crossings become thin crests. Raising to a
      // high power keeps only the crest, which is what a hairline crack is.
      veins[k] += Math.pow(1 - Math.abs(n[k] * 2 - 1), 46) * amp;
    }
  });

  const px = Buffer.alloc(size * size);
  for (let k = 0; k < px.length; k++) {
    const v = 128 + (grain[k] / norm) * 40 + veins[k] * 4;
    px[k] = Math.min(255, Math.max(0, Math.round(v)));
  }
  return sharp(px, { raw: { width: size, height: size, channels: 1 } })
    .blur(0.6)              // knock the hardest noise pixels off the veins
    .png().toBuffer();
}

/* The same grain as an RGBA luminance-modulation layer: white where the tile is
   above mid-grey, black where it is below, alpha carrying the magnitude. Drawn
   normally at low opacity this adds and subtracts light on ANY ground.
   A mid-grey tile under `soft-light` cannot do that — soft-light (like overlay)
   scales its effect by the backdrop, so over a near-black page it is invisible,
   which is exactly the page this brand is built on. */
async function grainRGBA(tile, gain = 1.25) {
  const { data, info } = await sharp(tile).greyscale().raw()
    .toBuffer({ resolveWithObject: true });
  const { width: w, height: h, channels: c } = info;
  const out = Buffer.alloc(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    const dev = (data[i * c] - 128) * gain;
    const up = dev >= 0;
    out[i * 4] = out[i * 4 + 1] = out[i * 4 + 2] = up ? 255 : 0;
    out[i * 4 + 3] = Math.min(255, Math.round(Math.abs(dev)));
  }
  return sharp(out, { raw: { width: w, height: h, channels: 4 } }).png().toBuffer();
}

/* A full-bleed slate plate: the grain tiled out over the brand gradient, for
   the hero backdrop and the icons. Baked at build time so those need one
   request instead of a live stack of CSS layers over a repeating image. */
async function buildPlate(w, h, grain, { aura = 0.16 } = {}) {
  // Never let the tile exceed the canvas: sharp refuses a composite larger than
  // its base, and the icon sizes below are far smaller than the natural tile.
  const tiles = [];
  if (grain) {
    const step = Math.min(760, w, h);
    const t = await sharp(grain).resize(step, step).png().toBuffer();
    for (let y = 0; y < Math.ceil(h / step); y++)
      for (let x = 0; x < Math.ceil(w / step); x++)
        tiles.push({ input: t, left: x * step, top: y * step });
  }

  const grade = Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">
       <defs>
         <linearGradient id="v" x1="0" y1="0" x2="0" y2="1">
           <stop offset="0%"   stop-color="${SLATE[800]}"/>
           <stop offset="55%"  stop-color="${SLATE[900]}"/>
           <stop offset="100%" stop-color="${SLATE[950]}"/>
         </linearGradient>
         <radialGradient id="aura" cx="50%" cy="2%" r="76%">
           <stop offset="0%"   stop-color="${GOLD[500]}" stop-opacity="${aura}"/>
           <stop offset="100%" stop-color="${GOLD[500]}" stop-opacity="0"/>
         </radialGradient>
         <radialGradient id="edge" cx="50%" cy="50%" r="72%">
           <stop offset="55%"  stop-color="#000" stop-opacity="0"/>
           <stop offset="100%" stop-color="#000" stop-opacity="0.5"/>
         </radialGradient>
       </defs>
       <rect width="${w}" height="${h}" fill="url(#v)"/>
       <rect width="${w}" height="${h}" fill="url(#aura)"/>
       <rect width="${w}" height="${h}" fill="url(#edge)"/>
     </svg>`);

  return sharp(await sharp(grade).png().toBuffer())
    .composite(tiles).png().toBuffer();
}

/* ── Chrome does the typesetting: it is the only thing here that can pull real
      Playfair Display off Google Fonts. ─────────────────────────────────── */
function shoot(html, w, h, out, transparent) {
  const f = join(TMP, `p${Math.random().toString(36).slice(2)}.html`);
  writeFileSync(f, html);
  execFileSync(CHROME, [
    "--headless=new", "--disable-gpu", "--hide-scrollbars",
    "--force-device-scale-factor=1", "--virtual-time-budget=9000",
    ...(transparent ? ["--default-background-color=00000000"] : []),
    `--screenshot=${out}`, `--window-size=${w},${h}`, `file://${f}`,
  ], { stdio: "ignore" });
}
const FONTS = `<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@500;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">`;

/* An .ico wrapping a single PNG. Vista+ and every current browser read this;
   sharp cannot emit .ico and the alternative is another dependency. */
function icoFromPng(png) {
  const head = Buffer.alloc(6);
  head.writeUInt16LE(0, 0); head.writeUInt16LE(1, 2); head.writeUInt16LE(1, 4);
  const entry = Buffer.alloc(16);
  entry[0] = 0; entry[1] = 0;      // 0 == 256px
  entry[2] = 0; entry[3] = 0;
  entry.writeUInt16LE(1, 4); entry.writeUInt16LE(32, 6);
  entry.writeUInt32LE(png.length, 8); entry.writeUInt32LE(22, 12);
  return Buffer.concat([head, entry, png]);
}

/* ══ Build ═════════════════════════════════════════════════════════════════ */
console.log("\nClefs brand build\n");

const key = await cutKey();
const keyMeta = await sharp(key).metadata();
// Full-quality master stays in brand/dist for decks, print and press kits.
// public/ gets the shipped cut: WebP for the site, a quantised PNG as fallback.
await sharp(key).toFile(join(OUT_DIST, "clefs-key.png"));
await sharp(key).png({ palette: true, colours: 200, effort: 10 }).toFile(join(OUT_BRAND, "clefs-key.png"));
await sharp(key).webp({ quality: 92, alphaQuality: 100 }).toFile(join(OUT_BRAND, "clefs-key.webp"));
log("key mark", "public/brand/clefs-key.{webp,png}", `${keyMeta.width}x${keyMeta.height}`);

// 256 for CSS (small file, repeats every 256px — invisible at 4% opacity),
// 512 for the baked plate, where more of the tile is on screen at once.
const grainCss = await grainRGBA(await buildTexture(256));
const grain = await grainRGBA(await buildTexture(512));
await sharp(grainCss).png({ palette: true, colours: 48, effort: 10 })
  .toFile(join(OUT_BRAND, "slate-grain.png"));
log("stone grain", "public/brand/slate-grain.png", "256x256 seamless");

const plate = await buildPlate(2560, 1600, grain);
await sharp(plate).webp({ quality: 82 }).toFile(join(OUT_BRAND, "slate-plate.webp"));
await sharp(plate).jpeg({ quality: 84, mozjpeg: true }).toFile(join(OUT_BRAND, "slate-plate.jpg"));
log("hero plate", "public/brand/slate-plate.{webp,jpg}", "2560x1600");

/* ── App icons: the key at 84% height on the graded ground. The mark is 1:2, so
      in a square it can only ever be ~40% wide — that tall-monogram-in-a-square
      is the conventional luxury icon shape, and the silhouette still reads at
      16px even though the five stars close up.

      Icons get the gradient WITHOUT grain. Random noise is incompressible, and
      it cost 295KB for a 512px favicon that no browser ever renders above 64px,
      where the grain is invisible anyway. ──────────────────────────────────── */
async function iconAt(size) {
  const ground = await buildPlate(size, size, null, { aura: 0.2 });
  const kh = Math.round(size * 0.84);
  const k = await sharp(key).resize({ height: kh }).toBuffer();
  const km = await sharp(k).metadata();
  return sharp(ground)
    .composite([{ input: k, left: Math.round((size - km.width) / 2), top: Math.round((size - kh) / 2) }])
    .png({ palette: true, colours: 220, effort: 10 }).toBuffer();
}
await sharp(await iconAt(512)).toFile(join(OUT_APP, "icon.png"));
log("favicon", "app/icon.png", "512x512");
await sharp(await iconAt(180)).toFile(join(OUT_APP, "apple-icon.png"));
log("apple icon", "app/apple-icon.png", "180x180");
writeFileSync(join(OUT_PUBLIC, "favicon.ico"), icoFromPng(await iconAt(64)));
log("legacy ico", "public/favicon.ico", "64x64");
// Web-push / Notification API icon: needs a plain square PNG, not an .ico.
await sharp(await iconAt(192)).toFile(join(OUT_BRAND, "notification-icon.png"));
log("notif icon", "public/brand/notification-icon.png", "192x192");

/* Everything below needs headless Chrome, which is most of the runtime.
   CLEFS_FAST=1 skips it — for iterating on the texture and icons only. */
if (process.env.CLEFS_FAST) { console.log("\n  (CLEFS_FAST: skipped Chrome-typeset assets)\n"); process.exit(0); }

/* ── Lockups. The key goes in via file:// so Chrome composites the real
      cutout rather than an approximation. ───────────────────────────────── */
const KEY_URL = `file://${join(OUT_DIST, "clefs-key.png")}`;
const PLATE_URL = `file://${join(OUT_BRAND, "slate-plate.jpg")}`;

// Horizontal lockup, transparent: key + wordmark, for nav and footer.
shoot(`<!doctype html><html><head><meta charset="utf-8">${FONTS}<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1600px;height:600px;background:transparent}
.w{width:1600px;height:600px;display:flex;align-items:center;justify-content:center;gap:64px}
.k{height:440px}
.n{font-family:'Playfair Display',Georgia,serif;font-weight:600;font-size:250px;line-height:1;
   letter-spacing:0.14em;text-indent:0.14em;${GOLD_TEXT}}
</style></head><body><div class="w"><img class="k" src="${KEY_URL}"><div class="n">CLEFS</div></div></body></html>`,
  1600, 600, join(TMP, "lockup_raw.png"), true);
{
  const b = await sharp(join(TMP, "lockup_raw.png")).trim({ threshold: 1 }).toBuffer();
  const m = await sharp(b).metadata();
  const p = Math.round(m.height * 0.05);
  const padded = await sharp(b).extend({ top: p, bottom: p, left: p, right: p,
      background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
  await sharp(padded).toFile(join(OUT_DIST, "clefs-lockup.png"));
  await sharp(padded).png({ palette: true, colours: 200, effort: 10 })
    .toFile(join(OUT_BRAND, "clefs-lockup.png"));
  await sharp(padded).webp({ quality: 92, alphaQuality: 100 })
    .toFile(join(OUT_BRAND, "clefs-lockup.webp"));
  log("lockup (h)", "public/brand/clefs-lockup.{webp,png}");
}

// Vertical lockup, transparent: key over wordmark + rule + tagline. Hero use.
shoot(`<!doctype html><html><head><meta charset="utf-8">${FONTS}<style>
*{margin:0;padding:0;box-sizing:border-box}
html,body{width:1400px;height:1900px;background:transparent}
.w{width:1400px;height:1900px;display:flex;flex-direction:column;align-items:center;justify-content:center}
.k{height:1050px;margin-bottom:56px}
.n{font-family:'Playfair Display',Georgia,serif;font-weight:600;font-size:250px;line-height:1;
   letter-spacing:0.15em;text-indent:0.15em;${GOLD_TEXT}}
.r{width:620px;height:1px;background:linear-gradient(90deg,transparent,${GOLD[600]},transparent);margin:44px 0 30px}
.t{font-family:'Inter',sans-serif;font-weight:500;font-size:40px;letter-spacing:0.42em;
   text-indent:0.42em;color:${GOLD[700]};white-space:nowrap}
</style></head><body><div class="w"><img class="k" src="${KEY_URL}"><div class="n">CLEFS</div>
<div class="r"></div><div class="t">THE FIVE-STAR AI CONCIERGE</div></div></body></html>`,
  1400, 1900, join(TMP, "vlockup_raw.png"), true);
{
  const b = await sharp(join(TMP, "vlockup_raw.png")).trim({ threshold: 1 }).toBuffer();
  const m = await sharp(b).metadata();
  const p = Math.round(m.width * 0.04);
  const padded = await sharp(b).extend({ top: p, bottom: p, left: p, right: p,
      background: { r: 0, g: 0, b: 0, alpha: 0 } }).png().toBuffer();
  await sharp(padded).toFile(join(OUT_DIST, "clefs-lockup-vertical.png"));
  await sharp(padded).webp({ quality: 92, alphaQuality: 100 })
    .toFile(join(OUT_BRAND, "clefs-lockup-vertical.webp"));
  log("lockup (v)", "public/brand/clefs-lockup-vertical.webp");
}

/* ── Social cards. Opaque: a transparent PNG composites badly on every social
      surface, and the stone plate is the point of the brand anyway. ─────── */
const CARD_CSS = `
  *{margin:0;padding:0;box-sizing:border-box}
  .bg{position:absolute;inset:0;background:url('${PLATE_URL}') center/cover no-repeat}
  .aura{position:absolute;inset:0;background:
     radial-gradient(60% 90% at 50% 40%, rgba(225,175,104,0.13), transparent 70%)}
  .n{font-family:'Playfair Display',Georgia,serif;font-weight:600;line-height:1;${GOLD_TEXT}}
  .t{font-family:'Inter',sans-serif;font-weight:500;color:${GOLD[700]};white-space:nowrap}`;

// 1200x630 OpenGraph / Twitter card, key beside the wordmark.
shoot(`<!doctype html><html><head><meta charset="utf-8">${FONTS}<style>${CARD_CSS}
html,body{width:1200px;height:630px;background:${SLATE[900]};overflow:hidden}
.w{position:relative;width:1200px;height:630px;display:flex;align-items:center;justify-content:center;gap:74px}
.k{height:454px}
.txt{display:flex;flex-direction:column;align-items:flex-start}
.n{font-size:150px;letter-spacing:0.13em;text-indent:0.13em}
.r{width:100%;height:1px;background:linear-gradient(90deg,${GOLD[600]},transparent);margin:34px 0 24px}
.t{font-size:23px;letter-spacing:0.3em}
</style></head><body><div class="w"><div class="bg"></div><div class="aura"></div>
<img class="k" src="${KEY_URL}" style="position:relative">
<div class="txt" style="position:relative"><div class="n">CLEFS</div><div class="r"></div>
<div class="t">THE FIVE-STAR AI CONCIERGE</div></div></div></body></html>`,
  1200, 630, join(TMP, "og.png"), false);
await sharp(join(TMP, "og.png")).jpeg({ quality: 88, mozjpeg: true })
  .toFile(join(OUT_APP, "opengraph-image.jpg"));
log("og card", "app/opengraph-image.jpg", "1200x630");

// 1024x1024 square, for schema.org Organization.logo and any square slot.
shoot(`<!doctype html><html><head><meta charset="utf-8">${FONTS}<style>${CARD_CSS}
html,body{width:1024px;height:1024px;background:${SLATE[900]};overflow:hidden}
.w{position:relative;width:1024px;height:1024px;display:flex;flex-direction:column;
   align-items:center;justify-content:center}
.k{height:560px;position:relative}
.n{font-size:118px;letter-spacing:0.15em;text-indent:0.15em;margin-top:46px;position:relative}
.r{width:330px;height:1px;background:linear-gradient(90deg,transparent,${GOLD[600]},transparent);
   margin:26px 0 20px;position:relative}
.t{font-size:19px;letter-spacing:0.34em;text-indent:0.34em;position:relative}
</style></head><body><div class="w"><div class="bg"></div><div class="aura"></div>
<img class="k" src="${KEY_URL}"><div class="n">CLEFS</div><div class="r"></div>
<div class="t">THE FIVE-STAR AI CONCIERGE</div></div></body></html>`,
  1024, 1024, join(TMP, "square.png"), false);
await sharp(join(TMP, "square.png")).jpeg({ quality: 90, mozjpeg: true })
  .toFile(join(OUT_PUBLIC, "logo.jpg"));
await sharp(join(TMP, "square.png")).toFile(join(OUT_DIST, "clefs-card-square.png"));
log("square card", "public/logo.jpg", "1024x1024");

console.log("\ndone\n");
