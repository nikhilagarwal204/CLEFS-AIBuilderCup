# Avatar viseme assets

The concierge avatar's mouth is a sprite composited over one locked portrait,
chosen each frame from the live audio envelope. Because the sound drives the
mouth directly, it cannot drift out of sync: there is no timeline to slip
against, and it stops the moment the audio does.

This replaces the looping `avataar_female_noaudio.mp4`. That clip is an AI
talking head whose face identity morphs continuously through the loop — face
width, brow, nose and skin tone all shift — which is a large part of why it
reads as toyish at size, and why visemes cannot be harvested from it. At the
identity threshold where the face holds steady, its mouth ladder has a hole
spanning half the openness range.

## What to produce

Nine PNGs, all the **same pixel dimensions**, in one folder:

```
base.png     the locked portrait, mouth at rest
rest.png  closed.png  slight.png  mid.png
open.png  wide.png    spread.png  round.png
```

The critical constraint: **every file must be identical to `base.png` except
inside the mouth**. Same head position, same lighting, same skin tone, same
eyes, same hair, same background. Generate one portrait you are happy with,
then inpaint *only* the mouth region eight times. Identity is then locked by
construction rather than by luck — which is exactly what the existing video
fails to do.

Do not re-run a text-to-image prompt eight times. Even with a fixed seed the
face will drift, and you will be back where the video is.

Portrait guidance: head and shoulders, face roughly centred, mouth near the
middle third. ~720×1140 or larger. The runtime cover-fits it to a circle, so
leave a little margin around the head.

## The eight shapes

The rig drives two axes. **Openness** is how far the jaw drops; **spread** is
lip width, where neutral is 0.5, higher is stretched, lower is rounded.

| File | Openness | Spread | What it should look like |
|---|---|---|---|
| `rest.png` | 0.00 | neutral | Lips together, relaxed, faint pleasant smile. This is the idle face — it shows whenever nobody is speaking, so it must be the most flattering of the set. |
| `closed.png` | 0.08 | neutral | Lips together but engaged, as at the start of "m", "b", "p". |
| `slight.png` | 0.26 | neutral | Barely parted, a sliver of teeth. |
| `mid.png` | 0.48 | neutral | Clearly open, upper teeth visible. |
| `open.png` | 0.70 | neutral | Well open, some lower teeth or tongue. |
| `wide.png` | 0.92 | neutral | Widest jaw drop, as in "ah". Do not caricature — this shows on loud syllables and must still look composed. |
| `spread.png` | 0.60 | wide | Open *and* stretched horizontally, as in "ee". Corners pulled out. |
| `round.png` | 0.55 | rounded | Open *and* pursed, as in "oo"/"oh". Corners drawn in, lips forward. |

Two notes on art direction, since this is shown to five-star properties:

- Keep the teeth consistent. A tooth that changes shape between shapes will
  flicker at speaking rate and is the most common giveaway.
- Keep the tongue subtle. It reads as cartoonish at small sizes.

## Packing them

```
node scripts/pack-visemes.mjs <src-dir> [out-dir]     # default out: public/avatar
```

The script works out which pixels actually changed across the eight variants,
crops to that region plus a blending margin, feathers the sprite edges so the
seam is invisible, packs them into one horizontal WebP strip, and writes
`visemes.json` with the anchor the runtime needs. Nothing has to be aligned by
hand. It errors out if any variant differs in size from the base, or if a
variant is identical to it.

Expect the whole set — portrait plus all eight mouths — to come to well under
200 KB.

Output:

```
public/avatar/avatar-base.webp
public/avatar/avatar-visemes.webp
public/avatar/visemes.json
```

## How it is consumed

`app/lib/visemeRig.ts` loads the manifest and drives it from the same analyser
that already gates the avatar's speaking state.

**The rig is off by default.** The looping video is the production avatar. To
evaluate the rig, set `NEXT_PUBLIC_AVATAR_RIG=viseme` at build time; anything
else (including unset) renders the video. The assets stay deployed either way,
so switching is a redeploy of the flag, not of the artwork.

### Why it blends instead of switching

The first version picked the nearest of the eight shapes each frame and drew
it. With good artwork that still looked wrong, and the reason is worth keeping:
a photoreal face cannot hard-swap. Even smoothed, the mouth teleports from one
shape to the next, and a viewer reads that instantly as broken — 32 hard swaps
in a 4-second greeting, one every 134ms. The looping video, with no lip-sync at
all, looked better simply because every frame was a small step from the last.

So the renderer now treats openness as a *position on the ladder* and draws the
two bracketing shapes with a fractional crossfade (`VisemeSelector.blend()` →
`VisemeRenderer.drawBlend()`). The same eight images become continuous motion.
The `spread`/`round` shapes are faded in as an overlay by how decisive the
spectral signal is, never switched to.

Two related limits that are deliberate:

- **`MAX_OPENNESS = 0.8`.** The loudest moment of a line lands as a blend leaning
  toward `wide`, never `wide` on its own. Mapping the loudest 5% of every
  sentence to the full gape had the avatar shouting at the guest.
- The analyser's `smoothingTimeConstant` must stay at `0`. Its default of 0.8
  lags the envelope by well over 100ms, which is the difference between a mouth
  that looks spoken and one that looks dubbed.

Loudness and vowel colour are both **self-calibrating** — the rig learns the
peak level and centre frequency of whatever voice it is given, over the first
few syllables. Changing the TTS voice needs no retuning.

Validated against `public/assistant_greeting.mp3` with the deployed artwork:
peak openness 0.795, `wide` never at full strength, all shaped overlays
reachable, mouth closed 176ms after the last audible sample.
