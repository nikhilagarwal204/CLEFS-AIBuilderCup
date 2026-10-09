/* ═══════════════════════════════════════════════════════════════════════
   Hold-to-record voice notes for "Reply to guest" (05 spec: the reply route
   takes {audio_b64, mime}; Gemini transcribes it and the server translates
   it for the guest).

   MediaRecorder records whatever the browser can: WebM/Opus on Chrome and
   Android, Ogg/Opus on Firefox, MP4/AAC on Safari and iOS. The mime sent is
   the bare type ("audio/webm"), without codec parameters.

   The mime choice and the base64 step are pure (tested in worker.test.mjs);
   VoiceRecorder touches the microphone only when called.
   ═══════════════════════════════════════════════════════════════════════ */

export const VOICE_MAX_MS = 30_000;
/** A tap, not a message: shorter recordings are dropped with a hint. */
export const VOICE_MIN_MS = 700;

export const AUDIO_MIME_PREFERENCE = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4;codecs=mp4a.40.2",
  "audio/mp4",
  "audio/aac",
] as const;

/** The first type the recorder supports, or null to let it choose. */
export function pickAudioMime(isSupported: ((mime: string) => boolean) | null | undefined): string | null {
  if (!isSupported) return null;
  for (const m of AUDIO_MIME_PREFERENCE) {
    try { if (isSupported(m)) return m; } catch { /* a throwing probe means "no" */ }
  }
  return null;
}

/** "audio/webm;codecs=opus" -> "audio/webm"; "" when unknown. */
export function baseMime(mime: string | null | undefined): string {
  return (mime ?? "").split(";")[0].trim().toLowerCase();
}

/** Standard base64 of raw bytes, chunked so a long note never overflows the stack. */
export function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK) {
    bin += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(bin);
}

export async function blobToBase64(blob: Blob): Promise<string> {
  return bytesToBase64(new Uint8Array(await blob.arrayBuffer()));
}

// ─── Recorder (browser only) ─────────────────────────────────────────────

export interface VoiceNote {
  blob: Blob;
  /** Bare mime type of the recording. */
  mime: string;
  ms: number;
}

export type MicProblem = "unsupported" | "blocked" | "failed";

export class MicError extends Error {
  // A plain field, not a constructor parameter property: Node's type
  // stripping (node --test) only removes erasable syntax.
  problem: MicProblem;

  constructor(problem: MicProblem, message?: string) {
    super(message ?? problem);
    this.name = "MicError";
    this.problem = problem;
  }
}

export class VoiceRecorder {
  private stream: MediaStream | null = null;
  private rec: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private startedAt = 0;

  static supported(): boolean {
    return typeof window !== "undefined" && typeof window.MediaRecorder !== "undefined"
      && typeof navigator !== "undefined" && Boolean(navigator.mediaDevices?.getUserMedia);
  }

  get isOpen(): boolean { return this.stream != null; }
  get isRecording(): boolean { return this.rec?.state === "recording"; }

  /** Ask for the microphone (the permission prompt appears on the first call). */
  async open(): Promise<void> {
    if (this.stream) return;
    if (!VoiceRecorder.supported()) throw new MicError("unsupported");
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    } catch (err) {
      const name = (err as Error)?.name;
      if (name === "NotAllowedError" || name === "SecurityError") throw new MicError("blocked");
      if (name === "NotFoundError" || name === "OverconstrainedError") throw new MicError("unsupported");
      throw new MicError("failed", (err as Error)?.message);
    }
  }

  start(): void {
    if (!this.stream) throw new MicError("failed", "The microphone is not open");
    const wanted = pickAudioMime(m => MediaRecorder.isTypeSupported(m));
    this.chunks = [];
    this.rec = wanted ? new MediaRecorder(this.stream, { mimeType: wanted }) : new MediaRecorder(this.stream);
    this.rec.ondataavailable = e => { if (e.data && e.data.size) this.chunks.push(e.data); };
    this.rec.start();
    this.startedAt = Date.now();
  }

  /** Stop and hand back the note (null when nothing was recording). Releases the microphone. */
  stop(): Promise<VoiceNote | null> {
    const rec = this.rec;
    if (!rec || rec.state === "inactive") { this.release(); return Promise.resolve(null); }
    return new Promise(resolve => {
      rec.onstop = () => {
        const mime = baseMime(rec.mimeType) || baseMime(this.chunks[0]?.type) || "audio/webm";
        const note = { blob: new Blob(this.chunks, { type: mime }), mime, ms: Date.now() - this.startedAt };
        this.release();
        resolve(note);
      };
      try { rec.stop(); } catch { this.release(); resolve(null); }
    });
  }

  /** Drop any recording and turn the microphone off (the browser's mic light goes out). */
  release(): void {
    try { if (this.rec && this.rec.state !== "inactive") this.rec.stop(); } catch { /* already stopped */ }
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = null;
    this.rec = null;
    this.chunks = [];
  }
}
