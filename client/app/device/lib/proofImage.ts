/* ═══════════════════════════════════════════════════════════════════════
   Completion photo, made small before it leaves the phone (05 spec,
   decision 9): at most 1280 px on the long edge, JPEG at quality 0.8,
   redrawn through a canvas. The redraw also drops EXIF (GPS, device,
   time), because a canvas export carries pixels only.

   The size math is pure (tested in worker.test.mjs). downsizeImage needs a
   browser: createImageBitmap honours the EXIF orientation so a portrait
   shot stays upright after the metadata is gone; an <img> decode is the
   fallback for engines without it.
   ═══════════════════════════════════════════════════════════════════════ */

export const PROOF_MAX_EDGE = 1280;
export const PROOF_JPEG_QUALITY = 0.8;
export const PROOF_MIME = "image/jpeg";
/** The proof route refuses bigger uploads (05 spec: max 3 MB). */
export const PROOF_MAX_BYTES = 3 * 1024 * 1024;
/** A second, lower quality for the rare photo still over the limit. */
export const PROOF_RETRY_QUALITY = 0.6;
/** Formats the route takes as they are, when the phone cannot redraw them. */
export const PROOF_PASSTHROUGH_TYPES = ["image/jpeg", "image/png"] as const;

export interface EncodePlan {
  width: number;
  height: number;
  /** <= 1: never upscale. */
  scale: number;
  mime: typeof PROOF_MIME;
  quality: number;
}

/** Fit width x height inside a maxEdge square, keeping the aspect ratio and
    never upscaling. Each side stays at least 1 px. */
export function fitWithin(width: number, height: number, maxEdge: number = PROOF_MAX_EDGE): { width: number; height: number; scale: number } {
  if (!(width > 0) || !(height > 0)) throw new Error("The photo has no size");
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
    scale,
  };
}

/** How a photo of this size is redrawn and encoded. */
export function proofEncodePlan(width: number, height: number): EncodePlan {
  return { ...fitWithin(width, height, PROOF_MAX_EDGE), mime: PROOF_MIME, quality: PROOF_JPEG_QUALITY };
}

/** Whether the original file may go up unchanged when it cannot be redrawn. */
export function canSendAsIs(file: { type: string; size: number }): boolean {
  return (PROOF_PASSTHROUGH_TYPES as readonly string[]).includes(file.type) && file.size > 0 && file.size <= PROOF_MAX_BYTES;
}

// ─── Browser part ────────────────────────────────────────────────────────

interface Decoded { source: CanvasImageSource; width: number; height: number; release: () => void }

async function decode(file: Blob): Promise<Decoded> {
  if (typeof createImageBitmap === "function") {
    try {
      const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
      return { source: bmp, width: bmp.width, height: bmp.height, release: () => bmp.close() };
    } catch { /* an engine that rejects the option or the format: try <img> */ }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = new Image();
    img.decoding = "async";
    await new Promise<void>((resolve, reject) => {
      img.onload = () => resolve();
      img.onerror = () => reject(new Error("The photo could not be decoded"));
      img.src = url;
    });
    return { source: img, width: img.naturalWidth, height: img.naturalHeight, release: () => URL.revokeObjectURL(url) };
  } catch (err) {
    URL.revokeObjectURL(url);
    throw err;
  }
}

function toBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob(b => (b ? resolve(b) : reject(new Error("The photo could not be encoded"))), PROOF_MIME, quality);
  });
}

/**
 * The photo as a small JPEG with no metadata. Throws when the browser can
 * neither decode nor encode it; the caller may then send the original if
 * canSendAsIs() allows.
 */
export async function downsizeImage(file: Blob): Promise<Blob> {
  const img = await decode(file);
  try {
    const plan = proofEncodePlan(img.width, img.height);
    const canvas = document.createElement("canvas");
    canvas.width = plan.width;
    canvas.height = plan.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("No canvas on this device");
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    // JPEG has no transparency: a PNG screenshot gets white, not black.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, plan.width, plan.height);
    ctx.drawImage(img.source, 0, 0, plan.width, plan.height);
    let out = await toBlob(canvas, plan.quality);
    if (out.size > PROOF_MAX_BYTES) out = await toBlob(canvas, PROOF_RETRY_QUALITY);
    canvas.width = canvas.height = 0; // free the backing store at once (iOS caps total canvas memory)
    return out;
  } finally {
    img.release();
  }
}
