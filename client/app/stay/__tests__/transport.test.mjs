// Live transport (05 spec, Voice relay (F)): the relay handshake, the event
// mapping both ways and the close paths, with a fake WebSocket and audio.
// Node's runner, TS stripping:
//   cd client && node --test app/stay/__tests__/transport.test.mjs
import { test } from "node:test";
import assert from "node:assert/strict";

class FakeWS {
  static OPEN = 1;
  static last = null;
  constructor(url) {
    this.url = url;
    this.readyState = 0;
    this.bufferedAmount = 0;
    this.sent = [];
    this.closedWith = null;
    FakeWS.last = this;
  }
  send(data) { this.sent.push(JSON.parse(data)); }
  close(code = 1000, reason = "") {
    if (this.readyState === 3) return;
    this.closedWith = code;
    this.readyState = 3;
    this.onclose?.({ code, reason });
  }
  // The server's side.
  open() { this.readyState = 1; this.onopen?.(); }
  push(obj) { this.onmessage?.({ data: JSON.stringify(obj) }); }
  drop(code, reason = "") { this.readyState = 3; this.onclose?.({ code, reason }); }
  refuse() { this.onerror?.(new Event("error")); this.drop(1006); }
}

class FakeAudioContext {
  constructor() { this.currentTime = 0; this.state = "running"; this.sampleRate = 48000; }
  createMediaStreamDestination() { return { stream: { id: "model-audio" } }; }
  createBuffer(_channels, length, rate) { return { duration: length / rate, copyToChannel() {} }; }
  createBufferSource() { return { buffer: null, onended: null, connect() {}, start() {}, stop() {} }; }
  resume() { return Promise.resolve(); }
  close() { return Promise.resolve(); }
}

globalThis.window = { AudioContext: FakeAudioContext };
globalThis.WebSocket = FakeWS;
const t = await import("../concierge/transport.ts");

const URL_ = "wss://api.example.test/public/concierge/live/s1";
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

function peer() {
  const pc = new t.LivePeer();
  const dc = pc.createDataChannel("events");
  const events = [];
  const seen = { opened: 0, closed: 0 };
  dc.onmessage = (e) => events.push(JSON.parse(e.data));
  dc.onopen = () => { seen.opened++; };
  dc.onclose = () => { seen.closed++; };
  const connecting = pc.connect("relay-token-1", "gemini-3.8-live", { relayUrl: URL_ });
  return { pc, dc, events, seen, connecting, ws: FakeWS.last };
}

async function openPeer() {
  const s = peer();
  s.ws.open();
  s.ws.push({ setupComplete: {} });
  await s.connecting;
  s.ws.sent.length = 0;
  return s;
}

const send = (dc, ev) => dc.send(JSON.stringify(ev));
const userText = (text) => ({ type: "conversation.item.create", item: { type: "message", role: "user", content: [{ type: "input_text", text }] } });
const types = (events) => events.map((e) => e.type);

test("relay: the token goes in the first frame and the channel opens on setupComplete", async () => {
  const s = peer();
  assert.equal(s.ws.url, URL_);
  s.ws.open();
  assert.deepEqual(s.ws.sent, [{ auth: { token: "relay-token-1" } }]);
  assert.equal(s.dc.readyState, "connecting");
  s.ws.push({ setupComplete: {} });
  await s.connecting;
  assert.equal(s.dc.readyState, "open");
  assert.equal(s.seen.opened, 1);
  assert.equal(s.pc.connectionState, "connected");
});

test("a typed message is one complete turn", async () => {
  assert.equal(t.USER_TEXT_COMPLETES_TURN, true);
  const s = await openPeer();
  send(s.dc, userText("Two towels please"));
  await tick();
  assert.deepEqual(s.ws.sent, [{ clientContent: { turns: [{ role: "user", parts: [{ text: "Two towels please" }] }], turnComplete: true } }]);
  // An older page adds a bare response.create: still one turn.
  s.ws.sent.length = 0;
  send(s.dc, userText("And water"));
  send(s.dc, { type: "response.create" });
  await tick();
  assert.deepEqual(s.ws.sent, [{ clientContent: { turns: [{ role: "user", parts: [{ text: "And water" }] }], turnComplete: true } }]);
});

test("instructions sent with an item join its turn; alone they are an instruction turn", async () => {
  const s = await openPeer();
  send(s.dc, userText("Extra towels"));
  send(s.dc, { type: "response.create", response: { instructions: "Say Maria is on her way." } });
  await tick();
  assert.deepEqual(s.ws.sent, [{ clientContent: { turns: [{ role: "user", parts: [
    { text: "Extra towels" }, { text: "(Instruction for your next reply) Say Maria is on her way." }] }], turnComplete: true } }]);
  s.ws.sent.length = 0;
  send(s.dc, { type: "response.create", response: { instructions: "Greet the guest." } });
  assert.deepEqual(s.ws.sent, [{ clientContent: { turns: [{ role: "user", parts: [
    { text: "(Instruction for your next reply) Greet the guest." }] }], turnComplete: true } }]);
});

test("a tool call goes to the page and its output back as a tool response", async () => {
  const s = await openPeer();
  s.ws.push({ toolCall: { functionCalls: [{ id: "call_1", name: "notify_staff", args: { request_type: "towels", quantity: 2 } }] } });
  const call = s.events.find((e) => e.type === "response.function_call_arguments.done");
  assert.deepEqual(call, { type: "response.function_call_arguments.done", call_id: "call_1", name: "notify_staff",
    arguments: JSON.stringify({ request_type: "towels", quantity: 2 }) });
  const output = { status: "created", staff_first_name: "Maria", followup_instructions: "Say Maria is on her way." };
  send(s.dc, { type: "conversation.item.create", item: { type: "function_call_output", call_id: "call_1", output: JSON.stringify(output) } });
  await tick();
  assert.deepEqual(s.ws.sent, [{ toolResponse: { functionResponses: [{ id: "call_1", name: "notify_staff", response: output } ] } }]);
});

test("an older page's follow-up rides inside the tool response, not as a second turn", async () => {
  const s = await openPeer();
  s.ws.push({ toolCall: { functionCalls: [{ id: "call_2", name: "notify_staff", args: {} }] } });
  send(s.dc, { type: "conversation.item.create", item: { type: "function_call_output", call_id: "call_2", output: JSON.stringify({ status: "created" }) } });
  send(s.dc, { type: "response.create", response: { instructions: "Say it is on its way." } });
  await tick();
  assert.deepEqual(s.ws.sent, [{ toolResponse: { functionResponses: [{ id: "call_2", name: "notify_staff",
    response: { status: "created", followup_instructions: "Say it is on its way." } }] } }]);
});

test("speech and transcripts become the page's events", async () => {
  const s = await openPeer();
  s.ws.push({ voiceActivity: { voiceActivityType: "ACTIVITY_START" } });
  s.ws.push({ serverContent: { inputTranscription: { text: "Two towels" } } });
  s.ws.push({ voiceActivity: { voiceActivityType: "ACTIVITY_END" } });
  const pcm = Buffer.from(new Int16Array([0, 1000, -1000, 32767]).buffer).toString("base64");
  s.ws.push({ serverContent: { modelTurn: { parts: [{ inlineData: { data: pcm, mimeType: "audio/pcm;rate=24000" } }] } } });
  s.ws.push({ serverContent: { outputTranscription: { text: "Of course." } } });
  s.ws.push({ serverContent: { turnComplete: true } });
  const got = types(s.events);
  for (const ev of ["input_audio_buffer.speech_started", "input_audio_buffer.speech_stopped",
    "conversation.item.input_audio_transcription.completed", "response.created", "output_audio_buffer.started",
    "response.output_audio_transcript.delta", "response.output_audio_transcript.done", "response.done"]) {
    assert.ok(got.includes(ev), `missing ${ev} in ${got}`);
  }
  assert.equal(s.events.find((e) => e.type === "conversation.item.input_audio_transcription.completed").transcript, "Two towels");
  assert.equal(s.events.find((e) => e.type === "response.output_audio_transcript.done").transcript, "Of course.");
  assert.ok(got.indexOf("input_audio_buffer.speech_started") < got.indexOf("response.created"));
});

test("a relay that refuses the session rejects connect and closes the channel", async () => {
  const s = peer();
  s.ws.open();
  s.ws.drop(4401, "session_invalid");
  await assert.rejects(s.connecting, (err) => err instanceof t.LiveClosedError && err.code === 4401 && !t.isNetworkBlock(err));
  assert.equal(s.dc.readyState, "closed");
  assert.equal(s.seen.closed, 1);
  assert.equal(s.seen.opened, 0);
});

test("a socket the network refuses reads as a network block", async () => {
  const s = peer();
  s.ws.refuse();
  await assert.rejects(s.connecting, (err) => t.isNetworkBlock(err));
  assert.equal(s.dc.readyState, "closed");
});

test("a relay that drops after opening reports an error and closes the channel", async () => {
  const s = await openPeer();
  s.ws.drop(1011, "upstream_closed");
  assert.deepEqual(s.events.at(-1), { type: "error", error: { message: "upstream_closed", code: 1011 } });
  assert.equal(s.dc.readyState, "closed");
  assert.equal(s.pc.connectionState, "closed");
});

test("closing the peer while it connects settles quietly and closes the socket", async () => {
  const s = peer();
  s.pc.close();
  await s.connecting;
  assert.equal(s.ws.closedWith, 1000);
  assert.equal(s.dc.readyState, "closed");
});
