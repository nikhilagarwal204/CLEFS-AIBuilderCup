/* ═══════════════════════════════════════════════════════════════════════
   Transport for the guest concierge: Gemini Live voice, or HTTP text.

   LivePeer wraps a Gemini Live session behind the small peer/channel
   surface ClefsConcierge.tsx drives: addTrack (the mic), ontrack (the
   model's audio as a MediaStream, so the viseme analyser keeps working),
   createDataChannel (JSON events in and out), close. The channel speaks
   the component's event protocol: the component sends session.update /
   response.create / response.cancel / conversation.item.create, and
   receives transcript, tool-call and audio-buffer lifecycle events
   translated from Gemini Live server messages.

   Two ways to reach Gemini Live, one message protocol:
   - direct: connect(token, model) opens the @google/genai Live API with a
     server-minted ephemeral token (Gemini Developer API);
   - relay: connect(relayToken, model, { relayUrl }) opens a plain
     WebSocket to the API's relay (server: concierge/live_relay.py), which
     holds the Live session on Vertex AI. The first frame is
     {auth: {token}}; then the same Live messages flow (realtimeInput,
     clientContent, toolResponse up; setupComplete, serverContent, toolCall
     down). The server owns the setup: prompt, tools, model and voice.
   Either way the channel opens on setupComplete and closes whenever the
   socket does, before or after it opened (the page then falls back to
   HTTP chat).

   Turns: a typed message (conversation.item.create, role user) is one
   complete turn (USER_TEXT_COMPLETES_TURN). A function_call_output becomes a
   tool response and the model carries on by itself. A response.create sent
   in the same task as either joins it (its instructions ride along, inside
   the tool response as followup_instructions), so a reply is never asked
   for twice; on its own it is a short instruction turn (the greeting cue).

   Mic: getUserMedia -> AudioContext -> 16 kHz PCM16 mono ->
   sendRealtimeInput. Playback: base64 PCM16 24 kHz chunks scheduled on a
   playback AudioContext; "interrupted" stops them.

   Hotel Wi-Fi sometimes blocks the Live endpoint. If the socket is not open
   within LIVE_CONNECT_MS the caller switches to POST .../chat (text only;
   the avatar never enters SPEAKING there).
   ═══════════════════════════════════════════════════════════════════════ */

import { GoogleGenAI, Modality, type LiveServerMessage } from "@google/genai";

export type TransportKind = "live" | "http";

/** How long the Live socket may take to open before we fall back to text. */
export const LIVE_CONNECT_MS = 8_000;
/** Upper bound from arming the probe (covers the server mint too). */
export const LIVE_CEILING_MS = 15_000;

/** A typed message (conversation.item.create, role user) is sent as one
 *  complete turn: the page sends no response.create after it. */
export const USER_TEXT_COMPLETES_TURN = true;

export interface ConnectOptions {
  /** relay_url from a session mint with transport "relay": connect through
   *  the server's Vertex AI Live relay; the token is then relay_token. */
  relayUrl?: string | null;
}

/** The Live socket closed before the session was ready. */
export class LiveClosedError extends Error {
  readonly code: number;
  constructor(code: number, reason?: string) {
    super(reason || `live_closed_${code}`);
    this.name = "LiveClosedError";
    this.code = code;
  }
}

const INPUT_RATE = 16_000;
const OUTPUT_RATE = 24_000;
/** Relay uplink backlog past which mic audio is dropped instead of queued. */
const MAX_BUFFERED_BYTES = 256 * 1024;

type Listener = (ev: { data: string }) => void;

type LiveTurn = { role: "user"; parts: { text: string }[] };
type ToolAnswer = { id: string; name: string; response: Record<string, unknown> };

/** What LivePeer needs from a Live connection: the @google/genai Session
 *  (direct) or the relay socket. */
interface LiveLink {
  sendRealtimeInput(params: { audio: { data: string; mimeType: string } }): void;
  sendClientContent(params: { turns: LiveTurn[]; turnComplete: boolean }): void;
  sendToolResponse(params: { functionResponses: ToolAnswer[] }): void;
  close(): void;
}

interface LinkEvents {
  onmessage: (m: LiveServerMessage) => void;
  onerror: (e: unknown) => void;
  onclose: (e: { code?: number; reason?: string }) => void;
}

/** A conversation item held until the end of the current task, so a
 *  response.create sent right after it can join it. */
type HeldItem =
  | { kind: "text"; text: string }
  | { kind: "tool"; id: string; name: string; response: Record<string, unknown> };

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

/** The server relay to Vertex AI Live: Live messages as JSON text frames. */
class RelayLink implements LiveLink {
  private ws: WebSocket;

  constructor(url: string, token: string, events: LinkEvents) {
    this.ws = new WebSocket(url);
    // The token travels in the first frame, never in the URL (request logs).
    this.ws.onopen = () => this.ws.send(JSON.stringify({ auth: { token } }));
    this.ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data !== "string") return;
      let m: unknown;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m && typeof m === "object") events.onmessage(m as LiveServerMessage);
    };
    this.ws.onerror = (e: Event) => events.onerror(e);
    this.ws.onclose = (e: CloseEvent) => events.onclose({ code: e.code, reason: e.reason });
  }

  private send(msg: Record<string, unknown>) {
    if (this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  sendRealtimeInput(params: { audio: { data: string; mimeType: string } }) {
    // A slow uplink: drop mic audio rather than let it queue up behind.
    if (this.ws.bufferedAmount > MAX_BUFFERED_BYTES) return;
    this.send({ realtimeInput: params });
  }

  sendClientContent(params: { turns: LiveTurn[]; turnComplete: boolean }) {
    this.send({ clientContent: params });
  }

  sendToolResponse(params: { functionResponses: ToolAnswer[] }) {
    this.send({ toolResponse: params });
  }

  close() {
    try { this.ws.close(1000, "client_closed"); } catch { /* already closed */ }
  }
}

/** The JSON event channel the component reads and writes. */
export class LiveChannel {
  readyState: "connecting" | "open" | "closed" = "connecting";
  onopen: (() => void) | null = null;
  onmessage: Listener | null = null;
  onerror: ((err: unknown) => void) | null = null;
  onclose: (() => void) | null = null;
  private peer: LivePeer;

  constructor(peer: LivePeer) {
    this.peer = peer;
  }

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
  private peer: LivePeer;
  constructor(peer: LivePeer) {
    this.peer = peer;
  }
  async replaceTrack(track: MediaStreamTrack | null) {
    this.peer.setMicTrack(track);
  }
}

export class LivePeer {
  connectionState: "new" | "connecting" | "connected" | "failed" | "closed" = "new";
  ontrack: ((e: { streams: MediaStream[] }) => void) | null = null;
  private channel: LiveChannel | null = null;
  private link: LiveLink | null = null;
  private ready = false;
  private readyWaiter: { resolve: () => void; reject: (err: unknown) => void } | null = null;
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
  private guestTalking = false;
  private held: HeldItem | null = null;
  private callNames = new Map<string, string>();
  private closed = false;

  addTrack(track: MediaStreamTrack, _stream?: MediaStream): LiveSender {
    if (track.kind === "audio") this.setMicTrack(track);
    return new LiveSender(this);
  }

  createDataChannel(_label?: string): LiveChannel {
    this.channel = new LiveChannel(this);
    return this.channel;
  }

  /**
   * Open the Live session: directly with the server-minted ephemeral token,
   * or, with opts.relayUrl, through the server's Vertex AI relay with the
   * relay token. Resolves once the session is set up (the channel is then
   * open); rejects if the socket errors or closes first, and the channel
   * closes too.
   */
  async connect(token: string, model: string, opts: ConnectOptions = {}): Promise<void> {
    if (this.closed) return;
    this.connectionState = "connecting";
    const Ctx = window.AudioContext || (window as any).webkitAudioContext;
    this.playCtx = new Ctx({ sampleRate: OUTPUT_RATE });
    this.playDest = this.playCtx.createMediaStreamDestination();
    this.ontrack?.({ streams: [this.playDest.stream] });
    const ready = new Promise<void>((resolve, reject) => { this.readyWaiter = { resolve, reject }; });
    const events: LinkEvents = {
      onmessage: (m) => this.onServerMessage(m),
      onerror: (e) => this.onLinkError(e),
      onclose: (e) => this.onLinkClose(e),
    };
    try {
      const opening: Promise<LiveLink> = opts.relayUrl
        ? Promise.resolve(new RelayLink(opts.relayUrl, token, events))
        : new GoogleGenAI({ apiKey: token, httpOptions: { apiVersion: "v1alpha" } }).live.connect({
          model,
          // The token is locked to the server's config; this only repeats it.
          config: { responseModalities: [Modality.AUDIO], inputAudioTranscription: {}, outputAudioTranscription: {} },
          callbacks: {
            onmessage: events.onmessage,
            onerror: events.onerror,
            onclose: (e: CloseEvent) => events.onclose({ code: e?.code, reason: e?.reason }),
          },
        });
      // A link that opens after this peer was closed is closed at once.
      opening.then((l) => { if (this.closed) l.close(); }, () => {});
      // A socket that fails before it opens never settles the SDK's connect;
      // `ready` rejects instead.
      const link = await Promise.race([opening, ready.then(() => opening)]);
      if (this.closed) return;
      this.link = link;
      await ready;
    } catch (err) {
      if (this.closed) return; // torn down meanwhile: nothing to report
      this.connectionState = "failed";
      this.readyWaiter = null;
      this.link?.close();
      this.link = null;
      this.channel?.close();
      throw err;
    }
    if (this.closed) return;
    this.connectionState = "connected";
    if (this.channel && this.channel.readyState === "connecting") {
      this.channel.readyState = "open";
      this.channel.onopen?.();
    }
    this.startMic();
  }

  setMicTrack(track: MediaStreamTrack | null) {
    this.micTrack = track;
    if (this.ready && this.link) this.startMic();
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
      const link = this.link;
      if (!link || !this.ready || !track.enabled || !this.sendAudio || track.readyState !== "live") return;
      const pcm = downsample(e.inputBuffer.getChannelData(0), ctx.sampleRate);
      try {
        link.sendRealtimeInput({ audio: { data: b64FromPcm16(pcm), mimeType: `audio/pcm;rate=${INPUT_RATE}` } });
      } catch { /* socket closing */ }
    };
    this.micSource.connect(this.micNode);
    this.micNode.connect(ctx.destination);
  }

  // ─── Socket events ───

  private onLinkError(e: unknown) {
    if (!this.ready) {
      // Before setup: connect() rejects (a refused socket reads as a
      // network block to isNetworkBlock) and closes the channel.
      this.readyWaiter?.reject(e);
      this.readyWaiter = null;
      return;
    }
    this.connectionState = "failed";
    this.channel?.emit({ type: "error", error: { message: (e as any)?.message || "live_error" } });
    this.channel?.onerror?.(e);
  }

  private onLinkClose(e: { code?: number; reason?: string }) {
    const code = e?.code ?? 1006;
    if (!this.ready) {
      this.readyWaiter?.reject(new LiveClosedError(code, e?.reason));
      this.readyWaiter = null;
    } else if (code !== 1000 && !this.closed) {
      this.channel?.emit({ type: "error", error: { message: e?.reason || "live_closed", code } });
    }
    this.connectionState = "closed";
    this.link = null;
    this.channel?.close();
  }

  // ─── Client events (component -> Live) ───

  handleClientEvent(ev: any) {
    switch (ev?.type) {
      case "session.update": {
        // Instructions and tools are the server's. Only honour the input
        // side: no turn detection (typed mode) means no mic audio.
        const td = ev.session?.audio?.input?.turn_detection;
        this.sendAudio = td !== null;
        this.channel?.emit({ type: "session.updated", session: ev.session });
        return;
      }
      case "response.create": {
        const raw = ev.response?.instructions;
        const text = typeof raw === "string" ? raw.trim() : "";
        // Right after an item: it joins that turn or tool response.
        if (this.flushHeld(text || null)) return;
        // On its own: an instruction turn (the greeting cue, a follow-up).
        // A bare one has nothing to add: the model already answers a
        // complete turn and carries on after a tool response.
        if (text) {
          this.link?.sendClientContent({
            turns: [{ role: "user", parts: [{ text: `(Instruction for your next reply) ${text}` }] }],
            turnComplete: true,
          });
        }
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
          const id = String(item.call_id || "");
          const name = this.callNames.get(id) || "";
          this.callNames.delete(id);
          this.hold({ kind: "tool", id, name, response });
          return;
        }
        const text = (item.content || []).map((c: any) => c?.text || c?.transcript || "").join(" ").trim();
        if (text) this.hold({ kind: "text", text });
        return;
      }
      default:
        return;
    }
  }

  /** Hold an item until this task ends; a response.create sent in the same
   *  task joins it (flushHeld), otherwise it goes on its own. */
  private hold(item: HeldItem) {
    this.flushHeld(null);
    this.held = item;
    queueMicrotask(() => { this.flushHeld(null); });
  }

  /** Send the held item, with `instructions` from a response.create that
   *  followed it. False when nothing was held. */
  private flushHeld(instructions: string | null): boolean {
    const item = this.held;
    if (!item) return false;
    this.held = null;
    const link = this.link;
    if (!link) return true;
    if (item.kind === "tool") {
      // The prompt tells the model to follow a tool result's followup_instructions.
      const response = instructions ? { ...item.response, followup_instructions: instructions } : item.response;
      link.sendToolResponse({ functionResponses: [{ id: item.id, name: item.name, response }] });
    } else {
      const parts = [{ text: item.text }];
      if (instructions) parts.push({ text: `(Instruction for your next reply) ${instructions}` });
      link.sendClientContent({ turns: [{ role: "user", parts }], turnComplete: USER_TEXT_COMPLETES_TURN || !!instructions });
    }
    return true;
  }

  // ─── Server messages (Live -> component) ───

  private onServerMessage(m: LiveServerMessage) {
    if (m.setupComplete && !this.ready) {
      this.ready = true;
      this.readyWaiter?.resolve();
      this.readyWaiter = null;
    }
    const ch = this.channel;
    if (!ch) return;
    // Voice activity (models that report it): the guest started or stopped
    // speaking, so the page keeps the mic open while they talk.
    const activity = String(m.voiceActivity?.voiceActivityType || "");
    if (activity === "ACTIVITY_START" && !this.guestTalking) {
      this.guestTalking = true;
      ch.emit({ type: "input_audio_buffer.speech_started" });
    } else if (activity === "ACTIVITY_END" && this.guestTalking) {
      this.guestTalking = false;
      ch.emit({ type: "input_audio_buffer.speech_stopped" });
    }
    const sc = m.serverContent;
    if (m.toolCall?.functionCalls?.length) {
      const calls = m.toolCall.functionCalls.map((fc) => ({ id: fc.id || `call_${++this.turnSeq}`, name: fc.name || "", args: fc.args }));
      for (const fc of calls) {
        this.callNames.set(fc.id, fc.name);
        ch.emit({ type: "response.function_call_arguments.done", call_id: fc.id, name: fc.name, arguments: JSON.stringify(fc.args || {}) });
      }
      ch.emit({ type: "response.done", response: { status: "completed", output: calls.map((fc) => ({ type: "function_call", call_id: fc.id, name: fc.name })) } });
    }
    if (!sc) return;
    if (sc.interrupted) {
      this.stopPlayback(true);
      if (!this.guestTalking) {
        this.guestTalking = true;
        ch.emit({ type: "input_audio_buffer.speech_started" });
      }
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
    this.guestTalking = false;
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
    this.held = null;
    this.stopPlayback(false);
    this.micNode?.disconnect();
    this.micSource?.disconnect();
    const link = this.link;
    this.link = null;
    try { link?.close(); } catch { /* ignore */ }
    // A connect() still waiting for setup settles quietly.
    this.readyWaiter?.reject(new LiveClosedError(1000, "closed"));
    this.readyWaiter = null;
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
