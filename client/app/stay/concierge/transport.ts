/* ═══════════════════════════════════════════════════════════════════════
   Transport for the guest concierge: Gemini Live voice, or HTTP text.

   LivePeer wraps the @google/genai Live API (a WebSocket) behind the small
   peer/channel surface ClefsConcierge.tsx drives: addTrack (the mic),
   ontrack (the model's audio as a MediaStream, so the viseme analyser keeps
   working), createDataChannel (JSON events in and out), close. The channel
   speaks the component's event protocol: the component sends
   session.update / response.create / response.cancel /
   conversation.item.create, and receives transcript, tool-call and
   audio-buffer lifecycle events translated from Gemini server messages.

   Mic: getUserMedia -> AudioContext -> 16 kHz PCM16 mono ->
   session.sendRealtimeInput. Playback: base64 PCM16 24 kHz chunks
   scheduled on a playback AudioContext; "interrupted" stops them.

   Hotel Wi-Fi sometimes blocks the Live endpoint. If the socket is not open
   within LIVE_CONNECT_MS the caller switches to POST .../chat (text only;
   the avatar never enters SPEAKING there).
   ═══════════════════════════════════════════════════════════════════════ */

import { GoogleGenAI, Modality, type LiveServerMessage, type Session } from "@google/genai";

export type TransportKind = "live" | "http";

/** How long the Live socket may take to open before we fall back to text. */
export const LIVE_CONNECT_MS = 8_000;
/** Upper bound from arming the probe (covers the server mint too). */
export const LIVE_CEILING_MS = 15_000;

const INPUT_RATE = 16_000;
const OUTPUT_RATE = 24_000;

type Listener = (ev: { data: string }) => void;

function b64FromPcm16(pcm: Int16Array): string {
  const bytes = new Uint8Array(pcm.buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

function pcm16FromB64(b64: string): Float32Array {
  const bin = atob(b64);
  const n = bin.length >> 1;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let v = bin.charCodeAt(2 * i) | (bin.charCodeAt(2 * i + 1) << 8);
    if (v >= 0x8000) v -= 0x10000;
    out[i] = v / 0x8000;
  }
  return out;
}

function downsample(input: Float32Array, fromRate: number): Int16Array {
  const ratio = fromRate / INPUT_RATE;
  const len = Math.floor(input.length / ratio);
  const out = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const s = Math.max(-1, Math.min(1, input[Math.floor(i * ratio)] || 0));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

/** The JSON event channel the component reads and writes. */
export class LiveChannel {
  readyState: "connecting" | "open" | "closed" = "connecting";
  onopen: (() => void) | null = null;
  onmessage: Listener | null = null;
  onerror: ((err: unknown) => void) | null = null;
  onclose: (() => void) | null = null;

  constructor(private peer: LivePeer) {}

  send(raw: string) {
    let ev: any;
    try { ev = JSON.parse(raw); } catch { return; }
    this.peer.handleClientEvent(ev);
  }

  emit(ev: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(ev) });
  }

  close() {
    if (this.readyState === "closed") return;
    this.readyState = "closed";
    this.onclose?.();
  }
}

/** Mic sender: replaceTrack swaps the captured track (pre-warm -> real mic). */
export class LiveSender {
  constructor(private peer: LivePeer) {}
  async replaceTrack(track: MediaStreamTrack | null) {
    this.peer.setMicTrack(track);
  }
}

export class LivePeer {
  connectionState: "new" | "connecting" | "connected" | "failed" | "closed" = "new";
  ontrack: ((e: { streams: MediaStream[] }) => void) | null = null;
  private channel: LiveChannel | null = null;
  private session: Session | null = null;
  private micTrack: MediaStreamTrack | null = null;
  private micCtx: AudioContext | null = null;
  private micNode: ScriptProcessorNode | null = null;
  private micSource: MediaStreamAudioSourceNode | null = null;
  private playCtx: AudioContext | null = null;
  private playDest: MediaStreamAudioDestinationNode | null = null;
  private playing = new Set<AudioBufferSourceNode>();
  private playHead = 0;
  private speaking = false;
  private stopTimer: ReturnType<typeof setTimeout> | null = null;
  private outText = "";
  private inText = "";
  private turnSeq = 0;
  private sendAudio = true;
  private closed = false;

  addTrack(track: MediaStreamTrack, _stream?: MediaStream): LiveSender {
    if (track.kind === "audio") this.setMicTrack(track);
    return new LiveSender(this);
  }

  createDataChannel(_label?: string): LiveChannel {
    this.channel = new LiveChannel(this);
    return this.channel;
  }

  /** Open the Live session with the server-minted ephemeral token. */
  async connect(token: string, model: string): Promise<void> {
    this.connectionState = "connecting";
    const ai = new GoogleGenAI({ apiKey: token, httpOptions: { apiVersion: "v1alpha" } });
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    this.playCtx = new Ctx({ sampleRate: OUTPUT_RATE });
    this.playDest = this.playCtx.createMediaStreamDestination();
    this.ontrack?.({ streams: [this.playDest.stream] });
    this.session = await ai.live.connect({
      model,
      // The token is locked to the server's config; this only repeats it.
      config: { responseModalities: [Modality.AUDIO], inputAudioTranscription: {}, outputAudioTranscription: {} },
      callbacks: {
        onopen: () => {
          this.connectionState = "connected";
          if (this.channel) {
            this.channel.readyState = "open";
            this.channel.onopen?.();
          }
        },
        onmessage: (m: LiveServerMessage) => this.onServerMessage(m),
        onerror: (e: unknown) => {
          this.connectionState = "failed";
          this.channel?.emit({ type: "error", error: { message: (e as any)?.message || "live_error" } });
          this.channel?.onerror?.(e);
        },
        onclose: () => {
          this.connectionState = "closed";
          this.channel?.close();
        },
      },
    });
    this.startMic();
  }

  setMicTrack(track: MediaStreamTrack | null) {
    this.micTrack = track;
    if (this.session) this.startMic();
  }

  private startMic() {
    this.micNode?.disconnect();
    this.micSource?.disconnect();
    this.micNode = null;
    this.micSource = null;
    if (!this.micTrack || this.closed) return;
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    if (!this.micCtx) this.micCtx = new Ctx();
    const ctx = this.micCtx!;
    const track = this.micTrack;
    this.micSource = ctx.createMediaStreamSource(new MediaStream([track]));
    this.micNode = ctx.createScriptProcessor(4096, 1, 1);
    this.micNode.onaudioprocess = (e) => {
      if (!this.session || !track.enabled || !this.sendAudio || track.readyState !== "live") return;
      const pcm = downsample(e.inputBuffer.getChannelData(0), ctx.sampleRate);
      try {
        this.session.sendRealtimeInput({ audio: { data: b64FromPcm16(pcm), mimeType: `audio/pcm;rate=${INPUT_RATE}` } });
      } catch { /* socket closing */ }
    };
    this.micSource.connect(this.micNode);
    this.micNode.connect(ctx.destination);
  }

  // ─── Client events (component -> Live) ───

  handleClientEvent(ev: any) {
    const s = this.session;
    switch (ev?.type) {
      case "session.update": {
        // Instructions and tools are locked in the token. Only honour the
        // input side: no turn detection (typed mode) means no mic audio.
        const td = ev.session?.audio?.input?.turn_detection;
        this.sendAudio = td !== null;
        this.channel?.emit({ type: "session.updated", session: ev.session });
        return;
      }
      case "response.create": {
        const text = ev.response?.instructions;
        s?.sendClientContent({ turns: [{ role: "user", parts: [{ text: text ? `(Instruction for your next reply) ${text}` : "Please continue." }] }], turnComplete: true });
        return;
      }
      case "response.cancel":
        this.stopPlayback(true);
        return;
      case "conversation.item.create": {
        const item = ev.item || {};
        if (item.type === "function_call_output") {
          let output: unknown = item.output;
          try { output = typeof item.output === "string" ? JSON.parse(item.output) : item.output; } catch { /* keep string */ }
          const response = output && typeof output === "object" && !Array.isArray(output) ? (output as Record<string, unknown>) : { output };
          s?.sendToolResponse({ functionResponses: [{ id: item.call_id, name: this.callNames.get(item.call_id) || "", response }] });
          return;
        }
        const text = (item.content || []).map((c: any) => c?.text || c?.transcript || "").join(" ").trim();
        if (text) s?.sendClientContent({ turns: [{ role: "user", parts: [{ text }] }], turnComplete: false });
        return;
      }
      default:
        return;
    }
  }

  private callNames = new Map<string, string>();

  // ─── Server messages (Live -> component) ───

  private onServerMessage(m: LiveServerMessage) {
    const ch = this.channel;
    if (!ch) return;
    const sc = m.serverContent;
    if (m.toolCall?.functionCalls?.length) {
      for (const fc of m.toolCall.functionCalls) {
        const callId = fc.id || `call_${++this.turnSeq}`;
        this.callNames.set(callId, fc.name || "");
        ch.emit({ type: "response.function_call_arguments.done", call_id: callId, name: fc.name, arguments: JSON.stringify(fc.args || {}) });
      }
      ch.emit({ type: "response.done", response: { status: "completed", output: m.toolCall.functionCalls.map((fc) => ({ type: "function_call", call_id: fc.id, name: fc.name })) } });
    }
    if (!sc) return;
    if (sc.interrupted) {
      this.stopPlayback(true);
      ch.emit({ type: "input_audio_buffer.speech_started" });
    }
    if (sc.inputTranscription?.text) {
      this.inText += sc.inputTranscription.text;
    }
    if (sc.modelTurn?.parts?.length) {
      if (this.inText.trim()) this.flushInput();
      if (!this.outText) ch.emit({ type: "response.created" });
      for (const p of sc.modelTurn.parts) {
        if (p.inlineData?.data) this.play(p.inlineData.data);
      }
    }
    if (sc.outputTranscription?.text) {
      if (this.inText.trim()) this.flushInput();
      if (!this.outText) ch.emit({ type: "response.created" });
      this.outText += sc.outputTranscription.text;
      ch.emit({ type: "response.output_audio_transcript.delta", delta: sc.outputTranscription.text, item_id: `turn_${this.turnSeq}` });
    }
    if (sc.turnComplete || sc.generationComplete) {
      if (this.inText.trim()) this.flushInput();
      if (sc.turnComplete) {
        const transcript = this.outText;
        this.outText = "";
        if (transcript) ch.emit({ type: "response.output_audio_transcript.done", transcript, item_id: `turn_${this.turnSeq}` });
        ch.emit({ type: "response.done", response: { status: "completed", output: [] } });
        this.turnSeq++;
      }
    }
  }

  private flushInput() {
    const transcript = this.inText.trim();
    this.inText = "";
    this.channel?.emit({ type: "input_audio_buffer.speech_stopped" });
    this.channel?.emit({ type: "conversation.item.input_audio_transcription.completed", transcript, item_id: `in_${this.turnSeq}` });
  }

  private play(b64: string) {
    const ctx = this.playCtx;
    if (!ctx || !this.playDest) return;
    const samples = pcm16FromB64(b64);
    if (!samples.length) return;
    const buf = ctx.createBuffer(1, samples.length, OUTPUT_RATE);
    buf.copyToChannel(samples, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    // The component's <audio> element plays this stream (and its analyser
    // drives the visemes), so the chunk goes only to the stream node.
    src.connect(this.playDest);
    const start = Math.max(ctx.currentTime + 0.02, this.playHead);
    src.start(start);
    this.playHead = start + buf.duration;
    this.playing.add(src);
    if (this.stopTimer) { clearTimeout(this.stopTimer); this.stopTimer = null; }
    if (!this.speaking) {
      this.speaking = true;
      this.channel?.emit({ type: "output_audio_buffer.started" });
    }
    src.onended = () => {
      this.playing.delete(src);
      if (this.playing.size === 0 && this.speaking) {
        // A short grace so back-to-back chunks do not flap the mouth.
        this.stopTimer = setTimeout(() => {
          if (this.playing.size === 0 && this.speaking) {
            this.speaking = false;
            this.channel?.emit({ type: "output_audio_buffer.stopped" });
          }
        }, 250);
      }
    };
    if (ctx.state === "suspended") ctx.resume().catch(() => {});
  }

  /** Stop scheduled model audio now (barge-in, or the user's stop button). */
  stopPlayback(notify: boolean) {
    for (const src of this.playing) { try { src.stop(); } catch { /* already stopped */ } }
    this.playing.clear();
    this.playHead = 0;
    if (this.stopTimer) { clearTimeout(this.stopTimer); this.stopTimer = null; }
    if (this.speaking) {
      this.speaking = false;
      if (notify) this.channel?.emit({ type: "output_audio_buffer.cleared" });
    }
  }

  close() {
    if (this.closed) return;
    this.closed = true;
    this.stopPlayback(false);
    this.micNode?.disconnect();
    this.micSource?.disconnect();
    try { this.session?.close(); } catch { /* ignore */ }
    this.session = null;
    this.micCtx?.close().catch(() => {});
    this.playCtx?.close().catch(() => {});
    this.connectionState = "closed";
    this.channel?.close();
  }
}

/**
 * Call `onFail` once if the Live channel has not opened in time.
 * Returns a cancel function; call it when the connection is up or torn down.
 */
export function armConnectProbe(getDc: () => LiveChannel | null, onFail: (reason: "live_timeout") => void): () => void {
  const timer = setTimeout(() => {
    if (getDc()?.readyState !== "open") onFail("live_timeout");
  }, LIVE_CEILING_MS);
  return () => clearTimeout(timer);
}

/** True when a failed Live connect looks like the network refused it. */
export function isNetworkBlock(err: unknown): boolean {
  return err instanceof TypeError || (err instanceof Event && err.type === "error");
}
