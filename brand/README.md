# Clefs — brand assets

Everything in `client/public/brand/`, `client/app/icon.png`, `client/app/apple-icon.png`,
`client/app/opengraph-image.jpg`, `client/public/logo.jpg` and `client/public/favicon.ico`
is **generated**. Never hand-edit them — change the source or the build script and
re-run it.

## The one source

```
brand/source/clefs-key-master.jpg     1024×1536, gold key on slate
```

An ornate calligraphic key whose bow is a C–L monogram and whose bit carries
**five engraved stars** — one per star of the five-star properties Clefs is sold
into. This is a rendered image, not vector artwork, so the build treats it as a
photograph of the mark rather than a drawing of it.

## Building

Run from `client/` so that `sharp` resolves:

```
cd client && node ../brand/build-brand.mjs
```

`CLEFS_FAST=1` skips the headless-Chrome steps (lockups and social cards) and is
for iterating on the texture and icons only.

Requires macOS Google Chrome at the usual path — it is the only thing here that
can typeset the wordmark in real Playfair Display.

| Output | Size | Notes |
|---|---|---|
| `client/public/brand/clefs-key.webp` / `.png` | 520×1031 | The mark, transparent. WebP is what the site uses. |
| `client/public/brand/clefs-lockup.webp` / `.png` | — | Horizontal: mark + wordmark. |
| `client/public/brand/clefs-lockup-vertical.webp` | — | Vertical: mark over wordmark, rule, tagline. |
| `client/public/brand/slate-grain.png` | 256×256 | Seamless stone grain, see below. |
| `client/public/brand/slate-plate.webp` / `.jpg` | 2560×1600 | Pre-graded stone plate. |
| `client/public/brand/notification-icon.png` | 192×192 | Web Notification API icon (that API needs a PNG, not an `.ico`). |
| `client/app/icon.png` | 512×512 | Favicon, via Next's `app/icon.png` convention. |
| `client/app/apple-icon.png` | 180×180 | iOS home screen. |
| `client/app/opengraph-image.jpg` | 1200×630 | Drives **both** `og:image` and `twitter:image`. |
| `client/public/logo.jpg` | 1024×1024 | `schema.org` `Organization.logo`. |
| `client/public/favicon.ico` | 64×64 | Legacy `/favicon.ico` probes. |
| `brand/dist/*` | full quality | **Not served.** Print, decks, press kits. |

## How the key is cut out

Gold and slate separate almost perfectly on **(R − B)**: the slate is blue-biased
and sits at −45…+8, the gold at +70…+167, with an empty valley between. Alpha is
a linear ramp across that valley.

Luminance alone does **not** work — the stone's bright mineral veins are as light
as the gold's shadow side, so a luma key eats the shaft and keeps the cracks.

Edge pixels are un-premultiplied against the mean slate colour, which is why the
mark can be dropped onto a white page or a light deck with no dark halo. The five
stars come through as knockouts, exactly as an engraved bit should.

## The stone texture

`slate-grain.png` is **synthesised**, not cropped out of the master.

The master's largest key-free region is about 300px square, and every way of
tiling a 300px crop betrays itself. A 2×2 mirror — the usual trick — turns the
slate's diagonal cracks into chevrons and diamonds, and the result reads as
wallpaper. Periodic value noise wraps exactly by construction, at any tile size,
with no motif to recognise.

Two details matter:

- **Octave weighting.** Amplitudes deliberately do not follow the usual fBm `1/f`
  rule. `1/f` puts most of the energy in the lowest octave, which at page scale
  reads as drifting cloud rather than stone. Weighting the fine octaves highest
  gives the tight mineral grain slate actually has, and makes the repeat far
  harder to see.
- **It is a luminance-modulation layer, not a grey tile.** White where the stone
  is above mid-grey, black where below, alpha carrying the magnitude — so at
  normal blending it adds *and* subtracts light on any ground. The obvious
  alternative, a mid-grey tile under `mix-blend-mode: soft-light`, does not work
  here: soft-light scales its effect by the backdrop, so over a near-black page
  it is invisible. Near-black is the entire brand.

The app icons are built from the gradient **without** grain. Random noise is
incompressible, and it cost 295KB for a 512px favicon that no browser renders
above 64px, where the grain cannot be seen anyway.

## Palette

Sampled out of the master itself, so the site cannot drift away from the mark.
The gold stops are percentiles of the key's own pixels; the slate stops are the
stone it is photographed on. Both live in `client/app/globals.css` as
`--clefs-gold-*` and `--clefs-slate-*`, and the legacy `--hc-*` names are aliases
onto them.

| | | |
|---|---|---|
| Gold | `#5B2700` `#885224` `#A7763E` `#C59253` **`#E1AF68`** `#F2C67D` `#FDDA96` `#FFEDA1` | 500 is the primary |
| Slate | `#03060C` `#070C14` `#0B121B` `#0F1720` `#16202D` `#1E2B3A` | 900 is the page ground |

## Design notes

- **Concept:** a key, after *Les Clefs d'Or* — the concierge society whose crossed
  golden keys are the universal badge of the profession. "Clefs" is the generic
  French noun, so the association comes without borrowing their mark.
- **Type:** Playfair Display 500–600 at `0.13–0.16em` tracking for the wordmark;
  Inter 500 at `0.3–0.42em` for the tagline. Display weight is deliberately 500,
  not 700 — a lighter serif reads as luxury, a heavy one as a newspaper.
- **Tagline:** "THE FIVE-STAR AI CONCIERGE", on the social cards and the vertical
  lockup. It is what makes the five stars on the bit legible as a claim rather
  than decoration.
- **Icon shape.** The mark is 1:2, so inside a square it can only ever be about
  40% wide. That tall-monogram-in-a-square is the conventional luxury app icon,
  and the silhouette still reads at 16px even though the five stars close up.
