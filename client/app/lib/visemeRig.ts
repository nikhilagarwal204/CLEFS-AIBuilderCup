/**
 * Audio-driven viseme rig.
 *
 * The avatar's mouth is a sprite composited over a single locked portrait. The
 * sprite is chosen from the live audio envelope, so the mouth cannot drift out
 * of sync with the voice: it is not following a timeline, it is following the
 * sound that is playing this frame.
 *
 * This is not phoneme-accurate - audio alone cannot tell "f" from "b" - but two
 * features carry most of what a viewer reads as lip-sync:
 *
 *   openness  loudness (RMS of the waveform) -> how far the jaw drops
 *   spread    spectral centroid              -> "ee" stretches wide, "oo" rounds
 *
 * Everything here is plain CPU work on an analyser that already exists, so it
 * adds no latency and no per-minute cost. See VISEMES.md for the asset spec.
 */

export type VisemeId =
  | "rest"
  | "closed"
  | "slight"
  | "mid"
  | "open"
  | "wide"
  | "spread"
  | "round";

export type VisemeFrame = {
  id: VisemeId;
  /** Where this shape sits on the openness ladder, 0 (shut) to 1 (widest). */
  openness: number;
  /**
   * Lip shape at that openness. 0.5 is neutral; >0.5 is stretched wide (front
   * vowels), <0.5 is rounded (back vowels). Shapes that only make sense at one
   * width - "round", "spread" - sit at the extremes and are only reachable when
   * the spectral signal is decisive.
   */
  spread: number;
  /** Index into the sprite sheet, left to right. */
  index: number;
};

export type VisemeManifest = {
  version: 1;
  /** Locked portrait. Every viseme composites onto this exact image. */
  base: string;
  /** Horizontal strip of mouth sprites, all the same size. */
  sheet: string;
  /** Size of one sprite cell in the sheet. */
  frame: { w: number; h: number };
  /** Natural size of the base portrait, so the rig can scale to any canvas. */
  baseSize: { w: number; h: number };
  /** Top-left of the mouth region in base-image pixel coordinates. */
  anchor: { x: number; y: number };
  visemes: VisemeFrame[];
};

/** Analyser settings the rig needs. Smoothing must stay off - see the note. */
export const VISEME_FFT_SIZE = 1024;

/**
 * Ceiling on jaw openness. The ladder's widest shape sits at 0.92, so 0.80
 * means the loudest moment of a line is roughly a 45/55 blend of "open" and
 * "wide" - never the full gape on its own.
 */
export const MAX_OPENNESS = 0.8;

export type MouthFeatures = {
  /** Waveform RMS, 0..1. */
  rms: number;
  /**
   * Spectral centroid in Hz, left raw on purpose. Absolute centroid depends on
   * the speaker's timbre, so the selector centres it per voice rather than
   * against a constant that would only ever suit one of them.
   */
  centroidHz: number;
};

/**
 * Pull the two features from a live analyser.
 *
 * `smoothingTimeConstant` on the analyser must be 0. Its exponential averaging
 * lags the envelope by well over 100ms, which is precisely the delay that makes
 * a mouth look dubbed rather than spoken.
 */
export function readMouthFeatures(
  analyser: AnalyserNode,
  timeDomain: Uint8Array,
  freq: Uint8Array,
  sampleRate: number,
): MouthFeatures {
  analyser.getByteTimeDomainData(timeDomain);
  let sum = 0;
  for (let i = 0; i < timeDomain.length; i++) {
    const s = (timeDomain[i] - 128) / 128;
    sum += s * s;
  }
  const rms = Math.sqrt(sum / timeDomain.length);

  analyser.getByteFrequencyData(freq);
  // Centroid over the band that carries vowel identity. Below ~180Hz is pitch
  // and room rumble; above ~4kHz is sibilance that would drag every "s" wide.
  const nyquist = sampleRate / 2;
  const binHz = nyquist / freq.length;
  const lo = Math.max(1, Math.floor(180 / binHz));
  const hi = Math.min(freq.length - 1, Math.ceil(4000 / binHz));
  let num = 0;
  let den = 0;
  for (let i = lo; i <= hi; i++) {
    const mag = freq[i] / 255;
    num += mag * (i * binHz);
    den += mag;
  }
  // With no energy there is no meaningful centroid. Reporting 0 lets the
  // selector skip the frame rather than let silence drag its running centre.
  const centroidHz = den > 1e-6 ? num / den : 0;
  return { rms, centroidHz };
}

function clamp01(v: number) {
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

function smoothstep(t: number) {
  const x = clamp01(t);
  return x * x * (3 - 2 * x);
}

export type VisemeBlend = {
  /** Neutral shape at or below the current openness. */
  lo: VisemeFrame;
  /** Neutral shape at or above it. */
  hi: VisemeFrame;
  /** 0 = fully `lo`, 1 = fully `hi`. */
  t: number;
  /** "spread" or "round", when the spectral signal calls for one. */
  shaped: VisemeFrame | null;
  /** How strongly the shaped overlay is applied, 0..1. */
  shapedW: number;
};

export type VisemeRigOptions = {
  /**
   * Quietest RMS that should ever count as a fully open jaw. Acts as a floor on
   * the adaptive peak so a hushed voice, or a moment of room tone, cannot rescale
   * itself into a shouting mouth.
   */
  minFullScaleRms?: number;
  /** Ceiling on the same peak, so one clipped syllable does not deaden the rig. */
  maxFullScaleRms?: number;
  /** Rise time toward a more open mouth. Fast: jaws drop quickly. */
  attackMs?: number;
  /** Fall time toward a more closed mouth. Slower, or the mouth chatters. */
  releaseMs?: number;
  /** How decisive the spectral signal must be before a shaped viseme is used. */
  spreadBias?: number;
  /**
   * Hz either side of this voice's running centre that maps to a fully
   * stretched or fully rounded mouth.
   */
  centroidSpanHz?: number;
};

/**
 * Turns a stream of audio features into a viseme index, with the smoothing that
 * keeps a mouth from flickering at frame rate.
 *
 * Deliberately holds no canvas or DOM reference: it is pure state, so it can be
 * unit-tested and replayed against recorded audio offline.
 */
export class VisemeSelector {
  private openness = 0;
  private spread = 0.5;
  private lastAt = 0;
  /** Running loudness peak; the jaw is scaled against this, not a constant. */
  private peakRms: number;
  /** Running centroid for this voice; the neutral mouth shape sits here. */
  private centroidMean = 0;
  private centroidCount = 0;
  private readonly opts: Required<VisemeRigOptions>;

  constructor(
    private readonly visemes: VisemeFrame[],
    opts: VisemeRigOptions = {},
  ) {
    this.opts = {
      minFullScaleRms: opts.minFullScaleRms ?? 0.035,
      maxFullScaleRms: opts.maxFullScaleRms ?? 0.30,
      attackMs: opts.attackMs ?? 28,
      releaseMs: opts.releaseMs ?? 70,
      spreadBias: opts.spreadBias ?? 0.16,
      centroidSpanHz: opts.centroidSpanHz ?? 900,
    };
    this.peakRms = this.opts.minFullScaleRms;
  }

  reset() {
    this.openness = 0;
    this.spread = 0.5;
    this.lastAt = 0;
    this.peakRms = this.opts.minFullScaleRms;
    this.centroidMean = 0;
    this.centroidCount = 0;
  }

  /**
   * Advance the rig. `active` false closes the mouth smoothly rather than
   * snapping it shut, which is what a real jaw does at the end of a word.
   */
  update(f: MouthFeatures, active: boolean, nowMs: number): VisemeFrame {
    const dt = this.lastAt === 0 ? 16 : Math.min(64, nowMs - this.lastAt);
    this.lastAt = nowMs;

    if (active) {
      // Track the loudest recent speech and scale the jaw against that. A fixed
      // full-scale constant only ever suits the one voice it was measured on;
      // this lands the same on a loud line and a quiet one. The slow decay lets
      // it come back down after a shout without chasing every syllable.
      this.peakRms = Math.min(
        this.opts.maxFullScaleRms,
        Math.max(f.rms, this.peakRms * Math.exp(-dt / 4000), this.opts.minFullScaleRms),
      );
      // Only vowels carry lip shape, so the centre is measured on loud frames
      // only. Letting quiet onsets and consonants in seeds it low, and every
      // vowel afterwards then reads as "wide".
      const energy = clamp01(f.rms / this.peakRms);
      if (f.centroidHz > 0 && energy > 0.25) {
        this.centroidCount++;
        // True running mean while warming up, easing into an EMA once settled.
        // A plain EMA would spend seconds trailing the voice it is supposed to
        // be centred on - fatal in a clip only a few syllables long.
        const w = Math.max(1 / this.centroidCount, 1 - Math.exp(-dt / 2500));
        this.centroidMean += (f.centroidHz - this.centroidMean) * w;
      }
    }

    // How far the jaw drops for a given loudness. Two things this must get
    // right, both learned the hard way on a live avatar:
    //  - Ordinary speech should sit around the middle of the ladder, not shut.
    //    A gentle power curve lifts quiet syllables without flattening loud ones.
    //  - The full gape must be rare. sqrt() with the peak mapped to 1.0 put the
    //    widest shape on the loudest ~5% of every sentence, which on a real
    //    face reads as the avatar shouting at the guest. Capping at
    //    MAX_OPENNESS means the peak of a line lands as a blend leaning toward
    //    "wide", never the shape at full strength.
    const targetOpen = active
      ? MAX_OPENNESS * Math.pow(clamp01(f.rms / this.peakRms), 0.8)
      : 0;

    const tau = targetOpen > this.openness ? this.opts.attackMs : this.opts.releaseMs;
    const k = 1 - Math.exp(-dt / tau);
    this.openness += (targetOpen - this.openness) * k;

    // Spread is measured against this voice's own centre, so "wide" means wide
    // for this speaker rather than wide against some absolute frequency.
    const targetSpread =
      active && f.centroidHz > 0 && this.centroidMean > 0
        ? clamp01(0.5 + (f.centroidHz - this.centroidMean) / this.opts.centroidSpanHz)
        : 0.5;
    // Slower than openness: vowel colour changes less often than amplitude, and
    // a twitchy width reads as a glitch rather than as speech.
    this.spread += (targetSpread - this.spread) * (1 - Math.exp(-dt / (active ? 110 : 200)));

    return this.pick();
  }

  /** Current smoothed state, for tests and debugging overlays. */
  get state() {
    return { openness: this.openness, spread: this.spread };
  }

  /**
   * Continuous output: the two neutral shapes that bracket the current
   * openness, the fraction between them, and an optional shaped overlay.
   *
   * This is what makes the rig watchable. Snapping between eight photos of a
   * face, even smoothed, still teleports the mouth from one shape to the next,
   * and a viewer reads that instantly as broken. Blending the two adjacent
   * shapes by the fractional openness turns the same eight images into
   * continuous motion - every frame is a small step from the last, which is the
   * property the looping video had and the discrete rig did not.
   */
  blend(): VisemeBlend {
    const ladder = this.ladder();
    const o = this.openness;
    let lo = ladder[0];
    let hi = ladder[ladder.length - 1];
    for (let i = 0; i < ladder.length - 1; i++) {
      if (o >= ladder[i].openness && o <= ladder[i + 1].openness) {
        lo = ladder[i];
        hi = ladder[i + 1];
        break;
      }
    }
    const span = hi.openness - lo.openness;
    const t = span > 1e-6 ? clamp01((o - lo.openness) / span) : 0;

    // Shaped overlay fades in with the spectral signal rather than switching,
    // and only once the mouth is open enough for lip shape to be visible.
    const dev = this.spread - 0.5;
    const shaped =
      Math.abs(dev) > 0.05
        ? this.visemes.find((v) => (dev > 0 ? v.spread > 0.7 : v.spread < 0.3)) ?? null
        : null;
    const decisiveness = clamp01((Math.abs(dev) - this.opts.spreadBias) / (0.42 - this.opts.spreadBias));
    const openGate = clamp01((o - 0.18) / 0.22);
    const shapedW = shaped ? smoothstep(decisiveness) * openGate : 0;

    return { lo, hi, t, shaped, shapedW };
  }

  private ladderCache: VisemeFrame[] | null = null;
  private ladder(): VisemeFrame[] {
    if (!this.ladderCache) {
      this.ladderCache = this.visemes
        .filter((v) => Math.abs(v.spread - 0.5) <= 0.2)
        .sort((a, b) => a.openness - b.openness);
      if (this.ladderCache.length === 0) this.ladderCache = [this.visemes[0]];
    }
    return this.ladderCache;
  }

  private pick(): VisemeFrame {
    const o = this.openness;
    const s = this.spread;
    // Shaped visemes are only worth using when the spectrum is actually
    // decisive and the mouth is open enough for the shape to be visible.
    const decisive = Math.abs(s - 0.5) > this.opts.spreadBias && o > 0.25;

    let best = this.visemes[0];
    let bestCost = Infinity;
    for (const v of this.visemes) {
      const shaped = Math.abs(v.spread - 0.5) > 0.2;
      if (shaped && !decisive) continue;
      // Openness dominates: getting the jaw wrong is far more visible than
      // getting the lip shape wrong.
      const cost =
        (v.openness - o) ** 2 * 3 + (decisive ? (v.spread - s) ** 2 : 0);
      if (cost < bestCost) {
        bestCost = cost;
        best = v;
      }
    }
    return best;
  }
}

/**
 * Draws base + mouth sprite into a canvas, scaled to whatever size the canvas
 * happens to be. Keeping the compositing here means the React component only
 * has to hand over a viseme index.
 */
export class VisemeRenderer {
  private lastIndex = -1;

  constructor(
    private readonly ctx: CanvasRenderingContext2D,
    private readonly manifest: VisemeManifest,
    private readonly baseImg: CanvasImageSource,
    private readonly sheetImg: CanvasImageSource,
  ) {}

  /** Force the next draw even if the viseme has not changed (after a resize). */
  invalidate() {
    this.lastIndex = -1;
  }

  /** Single discrete shape. Kept for the resting frame and for tests. */
  draw(index: number, force = false) {
    if (index === this.lastIndex && !force) return;
    this.lastIndex = index;
    const g = this.geometry();
    this.ctx.clearRect(0, 0, g.cw, g.ch);
    this.ctx.drawImage(this.baseImg, g.ox, g.oy, g.dw, g.dh);
    this.sprite(index, 1, g);
  }

  /**
   * Continuous shape: `lo` fully, `hi` faded in by `t`, then an optional
   * shaped overlay. Three draws of a ~270px sprite; nothing a phone notices.
   */
  drawBlend(b: VisemeBlend) {
    this.lastIndex = -1;
    const g = this.geometry();
    this.ctx.clearRect(0, 0, g.cw, g.ch);
    this.ctx.drawImage(this.baseImg, g.ox, g.oy, g.dw, g.dh);
    this.sprite(b.lo.index, 1, g);
    if (b.t > 0.001 && b.hi.index !== b.lo.index) this.sprite(b.hi.index, b.t, g);
    if (b.shaped && b.shapedW > 0.001) this.sprite(b.shaped.index, b.shapedW, g);
  }

  private geometry() {
    const { ctx, manifest: m } = this;
    const cw = ctx.canvas.width;
    const ch = ctx.canvas.height;
    // Cover-fit, matching the object-fit: cover the video used, so swapping the
    // rig in does not reframe the avatar.
    const scale = Math.max(cw / m.baseSize.w, ch / m.baseSize.h);
    const dw = m.baseSize.w * scale;
    const dh = m.baseSize.h * scale;
    return { cw, ch, scale, dw, dh, ox: (cw - dw) / 2, oy: (ch - dh) / 2 };
  }

  private sprite(index: number, alpha: number, g: ReturnType<VisemeRenderer["geometry"]>) {
    const { ctx, manifest: m } = this;
    ctx.globalAlpha = alpha;
    ctx.drawImage(
      this.sheetImg,
      index * m.frame.w, 0, m.frame.w, m.frame.h,
      g.ox + m.anchor.x * g.scale,
      g.oy + m.anchor.y * g.scale,
      m.frame.w * g.scale,
      m.frame.h * g.scale,
    );
    ctx.globalAlpha = 1;
  }
}

/** Loads the manifest and its two images. Rejects if anything is missing. */
export async function loadVisemeRig(manifestUrl: string): Promise<{
  manifest: VisemeManifest;
  base: HTMLImageElement;
  sheet: HTMLImageElement;
}> {
  const res = await fetch(manifestUrl);
  if (!res.ok) throw new Error(`viseme manifest ${res.status}`);
  const manifest: VisemeManifest = await res.json();
  const dir = manifestUrl.slice(0, manifestUrl.lastIndexOf("/") + 1);
  const [base, sheet] = await Promise.all([
    loadImage(dir + manifest.base),
    loadImage(dir + manifest.sheet),
  ]);
  return { manifest, base, sheet };
}

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error(`viseme image failed: ${src}`));
    img.src = src;
  });
}
