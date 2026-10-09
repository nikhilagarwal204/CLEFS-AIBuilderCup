"""
The phone's sideband worker (03 spec 3.4) and the voice jobs.

`voice_sideband` is a Module.worker_services entry: `python -m clefs.worker`
supervises it (restart with backoff). It never runs on Vercel. For every
accepted call it takes a lease on vc_calls.worker_lease_until, opens the
telephony bridge's media WebSocket for the call ({VOICE_BRIDGE_URL}/calls/
{id}/media) and a Google Gemini Live session (google-genai,
client.aio.live.connect with the config stored at accept), relays caller
audio (PCM16 16 kHz) to Gemini and Gemini's audio (PCM16 24 kHz) back, and:
  1. writes transcripts to conversation_logs (source 'phone');
  2. runs Service's safety classifier on caller turns and, for an
     emergency, injects a response.create that reads the dial sequence out;
  3. executes function tools on sideband lines (executor.run);
  4. records the end and the duration when the socket closes.
DTMF events, when the bridge sends them, are logged as system turns.

The per-event logic lives in SidebandCall.handle(event) -> outgoing events,
with no socket in it, so tests and the harness drive it directly.

Jobs: voice.lease_reaper (5 min: expired leases, stuck and stale calls) and
voice.receipts_purge (daily: 30-day webhook receipt retention).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import timedelta
from typing import Any

from clefs import db, jobs
from clefs.core import clock
from clefs.settings import get_settings
from clefs.modules.service import api as service_api
from clefs.modules.voice import calls, executor
from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.worker")

WORKER_ID = f"{socket.gethostname()[:40]}:{os.getpid()}"
STUCK_ACCEPTING_S = 120        # an accept that never reported back
STALE_GRACE_S = 300            # beyond max_call_s: nobody reported the end
IDLE_SLEEP_S = 30.0            # voice off or no API key: check again later
EMERGENCY_LEVELS = frozenset({"emergency"})


# ─── Jobs ───


def register() -> None:
    """Called by module.py at import (jobs.load_all imports the manifest)."""

    @jobs.scheduled("voice.lease_reaper", jobs.every(minutes=5), per_hotel=False, timeout_s=60)
    def _reaper(ctx: jobs.JobCtx) -> dict:
        return reap(ctx.cur)

    @jobs.scheduled("voice.receipts_purge", jobs.daily_at("03:30", tz="utc"), per_hotel=False, timeout_s=120)
    def _purge(ctx: jobs.JobCtx) -> dict:
        return purge_receipts(ctx.cur)


def reap(cur: Any) -> dict:
    """Clear expired leases (a crashed worker's calls go back to the pool),
    fail accepts that never reported back, and mark calls whose end nobody
    reported as stale. A stale call keeps ended_at NULL on purpose: without
    a worker or a call-end event the end time is unknown (03 spec 3.4)."""
    now = clock.now()
    cur.execute(
        """
        UPDATE vc_calls SET worker_id = NULL, worker_lease_until = NULL
        WHERE status IN ('accepted', 'active') AND worker_lease_until IS NOT NULL AND worker_lease_until < %s
        """,
        (now,),
    )
    leases = cur.rowcount
    cur.execute(
        """
        UPDATE vc_calls SET status = 'failed', ended_at = %s, end_reason = 'accept_unconfirmed'
        WHERE status = 'accepting' AND started_at < %s
        """,
        (now, now - timedelta(seconds=STUCK_ACCEPTING_S)),
    )
    stuck = cur.rowcount
    cur.execute(
        """
        UPDATE vc_calls c SET status = 'stale', end_reason = COALESCE(c.end_reason, 'end_unknown'),
               worker_id = NULL, worker_lease_until = NULL
        FROM vc_lines l
        WHERE l.id = c.voice_line_id AND c.status IN ('accepted', 'active') AND c.ended_at IS NULL
          AND c.started_at < %s - make_interval(secs => l.max_call_s + %s)
        """,
        (now, STALE_GRACE_S),
    )
    return {"leases_cleared": leases, "stuck_failed": stuck, "stale": cur.rowcount}


def purge_receipts(cur: Any) -> dict:
    """30-day retention for receipts, and for the call rows of calls no line
    claimed (anyone can dial the project's SIP address; those rows carry no
    hotel and exist only for the setup panel's diagnostics)."""
    cutoff = clock.now() - timedelta(days=C.RECEIPT_RETENTION_DAYS)
    cur.execute("DELETE FROM vc_webhook_receipts WHERE received_at < %s", (cutoff,))
    deleted = cur.rowcount
    cur.execute("DELETE FROM vc_calls WHERE hotel_slug IS NULL AND started_at < %s", (cutoff,))
    return {"deleted": deleted, "orphan_calls": cur.rowcount}


# ─── Leases ───


def claim_calls(cur: Any, worker_id: str, limit: int) -> list[dict]:
    """Up to `limit` accepted calls with no live lease, leased to this
    worker. SKIP LOCKED: two workers never take the same call."""
    if limit <= 0:
        return []
    now = clock.now()
    cur.execute(
        """
        UPDATE vc_calls SET worker_id = %s, worker_lease_until = %s
        WHERE id IN (
            SELECT id FROM vc_calls
            WHERE status IN ('accepted', 'active') AND ended_at IS NULL
              AND (worker_lease_until IS NULL OR worker_lease_until < %s)
            ORDER BY id LIMIT %s FOR UPDATE SKIP LOCKED
        ) RETURNING *
        """,
        (worker_id, now + timedelta(seconds=C.LEASE_S), now, int(limit)),
    )
    return [dict(r) for r in cur.fetchall()]


def renew_lease(cur: Any, call_row_id: int, worker_id: str) -> bool:
    """False when the lease was lost (reaped, or the call ended elsewhere)."""
    cur.execute(
        """
        UPDATE vc_calls SET worker_lease_until = %s
        WHERE id = %s AND worker_id = %s AND ended_at IS NULL RETURNING id
        """,
        (clock.now() + timedelta(seconds=C.LEASE_S), int(call_row_id), worker_id),
    )
    return cur.fetchone() is not None


def release_call(cur: Any, call_row_id: int, *, reason: str) -> dict | None:
    """Socket closed: record the end once (03 spec 3.4 item 4)."""
    call = calls.get_call(cur, call_row_id, lock=True)
    if call is None:
        return None
    if call.get("ended_at") is not None:
        cur.execute("UPDATE vc_calls SET worker_lease_until = NULL WHERE id = %s", (call["id"],))
        return call
    return calls.mark_ended(cur, call, reason=reason)


# ─── One call's events (no socket here) ───


def _text(value: Any, limit: int = 4000) -> str:
    return " ".join(str(value or "").split())[:limit]


class SidebandCall:
    """The state of one leased call. handle(cur, event) returns the client
    events to send back on the call's socket (function outputs, an injected
    emergency response). It never raises for a malformed event."""

    def __init__(self, call: dict, *, use_llm_safety: bool = True) -> None:
        self.call = dict(call)
        self.use_llm_safety = use_llm_safety
        self.active = call.get("status") == "active"
        self.emergency_sent = False
        self.done_tools: set[str] = set()
        self.turns = 0

    @property
    def row_id(self) -> int:
        return int(self.call["id"])

    def refresh(self, cur: Any) -> None:
        """Pick up a spoken room the executor confirmed onto the row."""
        fresh = calls.get_call(cur, self.row_id)
        if fresh:
            self.call = fresh

    # ─── Transcripts ───

    def log_turn(self, cur: Any, role: str, text: str, *, safety_level: str | None = None) -> None:
        """conversation_logs (source 'phone'). The legacy columns are NOT
        NULL: an unknown room is '' and a call without a concierge session
        is keyed by its bridge call id, so the transcript still groups."""
        text = _text(text)
        if not text:
            return
        now = clock.now()
        cur.execute(
            """
            INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp,
                                           source, call_id, language, prompt_version, safety_level, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, 'phone', %s, %s, %s, %s, %s)
            """,
            (
                self.call["hotel_slug"], str(self.call.get("room") or ""),
                str(self.call.get("session_id") or f"vc:{self.call['provider_call_id']}"), role, text,
                int(now.timestamp() * 1000), self.call["provider_call_id"], self.call.get("language"),
                self.call.get("prompt_version"), safety_level, now,
            ),
        )
        self.turns += 1

    # ─── Dispatch ───

    def handle(self, cur: Any, event: dict) -> list[dict]:
        etype = str((event or {}).get("type") or "")
        try:
            if not self.active and etype == "session.started":
                self._mark_active(cur)
            if etype == "input_transcription.done":
                return self._caller_turn(cur, event)
            if etype == "output_transcription.done":
                self.log_turn(cur, "assistant", event.get("transcript"))
                return []
            if etype == "tool_call":
                return self._function_call(cur, event)
            if "dtmf" in etype:
                digit = event.get("event") or event.get("digit") or event.get("dtmf") or ""
                self.log_turn(cur, "system", f"[keypad {_text(digit, 8)}]")
                return []
            if etype == "error":
                log.warning("voice sideband: call %s error event: %s", self.row_id, _text(event.get("error"), 300))
        except Exception:
            log.exception("voice sideband: event %s failed on call %s", etype, self.row_id)
        return []

    def _mark_active(self, cur: Any) -> None:
        cur.execute("UPDATE vc_calls SET status = 'active' WHERE id = %s AND status = 'accepted'", (self.row_id,))
        self.active = True

    # ─── Caller turns and safety ───

    def _caller_turn(self, cur: Any, event: dict) -> list[dict]:
        text = _text(event.get("transcript"))
        if not text:
            return []
        verdict = service_api.classify_safety(
            text, language=self.call.get("language"), hotel_slug=self.call["hotel_slug"],
            use_llm=self.use_llm_safety, cur=cur,
        ) or {}
        level = str(verdict.get("level") or "none")
        self.log_turn(cur, "user", text, safety_level=level)
        if level not in EMERGENCY_LEVELS or self.emergency_sent:
            return []
        return self._emergency(cur, text, verdict)

    def _emergency(self, cur: Any, text: str, verdict: dict) -> list[dict]:
        """The server files the L1 escalation itself (an emergency never
        waits on the model choosing a tool), once per call, then makes the
        model speak: dial sequence first, then the transfer offer."""
        self.refresh(cur)
        out = executor.run(
            cur, self.call, "escalate_to_human",
            {"reason": "emergency", "summary_en": f"Caller said: {text[:300]}", "original_text": text[:300]},
            tool_call_id=f"safety_{self.call['provider_call_id']}", transport="sideband",
        )
        self.emergency_sent = True
        line_prefix = self._outside_prefix(cur)
        filed = out.get("status") in ("escalated", "created", "merged") or out.get("deduped")
        instructions = (
            "EMERGENCY. Speak now, calmly, in the caller's language. "
            f"1. Tell them to {calls._dial_sequence(line_prefix)}. "
            + ("2. Say hotel staff have been alerted and help is coming; do not call escalate_to_human again. "
               if filed else "2. Call escalate_to_human with reason emergency now. ")
            + "3. Offer to transfer them to the front desk."
        )
        kind = verdict.get("kind")
        log.warning("voice sideband: emergency (%s) on call %s", kind or "unspecified", self.row_id)
        return [{"type": "inject", "instructions": instructions}]

    def _outside_prefix(self, cur: Any) -> str | None:
        cur.execute("SELECT outside_line_prefix FROM vc_lines WHERE id = %s", (self.call.get("voice_line_id"),))
        row = cur.fetchone()
        return row["outside_line_prefix"] if row else None

    # ─── Function tools (sideband lines) ───

    def _function_call(self, cur: Any, event: dict) -> list[dict]:
        """Gemini Live delivers every function call on the session, on MCP and
        sideband lines alike; it runs through the shared executor, keyed on
        the Gemini function call id so a replayed event returns the stored answer."""
        call_id = _text(event.get("call_id"), 120)
        name = _text(event.get("name"), 64)
        if not call_id or not name or call_id in self.done_tools:
            return []
        try:
            args = json.loads(event.get("arguments") or "{}")
        except ValueError:
            args = None
        if not isinstance(args, dict):
            out = executor.result("invalid_arguments", "The tool arguments were not valid JSON. Try again.")
        else:
            self.refresh(cur)
            out = executor.run(cur, self.call, name, args, tool_call_id=call_id, transport="sideband")
        self.done_tools.add(call_id)
        return [{"type": "tool_response", "call_id": call_id, "name": name,
                 "output": json.loads(json.dumps(out, ensure_ascii=False, default=str))}]


# ─── Sockets: the bridge's media stream and the Gemini Live session ───

CONNECT_ATTEMPTS = 3
_connect_failures: dict[int, int] = {}


def realtime_ws_url(call_id: str) -> str:
    """The bridge's media WebSocket for a call: {VOICE_BRIDGE_URL}/calls/{id}/media
    (https -> wss). The harness points VOICE_BRIDGE_URL at its stub."""
    from urllib.parse import quote

    base = (os.environ.get("VOICE_BRIDGE_URL") or "https://bridge.invalid/v1").rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return f"{base}/calls/{quote(call_id, safe='')}/media"


def _connect(call_id: str):
    from websockets.asyncio.client import connect

    token = os.environ.get("VOICE_BRIDGE_TOKEN") or ""
    return connect(realtime_ws_url(call_id), additional_headers={"Authorization": f"Bearer {token}"} if token else {},
                   open_timeout=10, max_size=4 * 1024 * 1024)


def _live_connect(call: dict):
    """client.aio.live.connect for the call (async context manager)."""
    from google import genai

    cfg = dict(call.get("session_config") or {})
    config = calls.live_config(instructions=str(cfg.get("instructions") or ""), voice=cfg.get("voice"),
                               tools=list(cfg.get("tools") or []))
    model = cfg.get("model") or call.get("model") or get_settings().REALTIME_MODEL
    client = genai.Client(vertexai=False, api_key=get_settings().GEMINI_API_KEY)  # not the env's Vertex switch
    return client.aio.live.connect(model=model, config=config)


def _in_tx(fn, *args, **kw):
    with db.db() as cur:
        return fn(cur, *args, **kw)


def _handle_tx(session: SidebandCall, event: dict) -> list[dict]:
    with db.db() as cur:
        return session.handle(cur, event)


def _drop_lease(cur: Any, call_row_id: int, worker_id: str) -> None:
    """Shutdown or a connect failure: hand the call back to the pool."""
    cur.execute("UPDATE vc_calls SET worker_id = NULL, worker_lease_until = NULL WHERE id = %s AND worker_id = %s",
                (int(call_row_id), worker_id))


async def _renew_loop(call_row_id: int, worker_id: str, ws: Any) -> None:
    while True:
        await asyncio.sleep(C.LEASE_RENEW_S)
        if not await asyncio.to_thread(_in_tx, renew_lease, call_row_id, worker_id):
            log.info("voice sideband: lease lost on call %s; closing", call_row_id)
            await ws.close()
            return


def live_events(msg: Any, buf: dict[str, list[str]]) -> tuple[list[dict], list[bytes]]:
    """One Gemini Live server message -> (normalized events, PCM16 24 kHz
    audio chunks). Transcription arrives in chunks; buf collects them until
    the turn completes (or the caller barges in)."""
    events: list[dict] = []
    audio: list[bytes] = []
    sc = getattr(msg, "server_content", None)
    if sc is not None:
        for part in (getattr(getattr(sc, "model_turn", None), "parts", None) or []):
            data = getattr(getattr(part, "inline_data", None), "data", None)
            if data:
                audio.append(data)
        for key, attr in (("input", "input_transcription"), ("output", "output_transcription")):
            text = getattr(getattr(sc, attr, None), "text", None)
            if text:
                buf.setdefault(key, []).append(text)
        if getattr(sc, "interrupted", False):
            events.append({"type": "interrupted"})
        if getattr(sc, "interrupted", False) or getattr(sc, "turn_complete", False) or getattr(sc, "generation_complete", False):
            for key in ("input", "output"):
                if buf.get(key):
                    events.append({"type": f"{key}_transcription.done", "transcript": "".join(buf.pop(key))})
    tc = getattr(msg, "tool_call", None)
    for fc in (getattr(tc, "function_calls", None) or []):
        if buf.get("input"):
            events.append({"type": "input_transcription.done", "transcript": "".join(buf.pop("input"))})
        events.append({"type": "tool_call", "call_id": getattr(fc, "id", None) or "", "name": getattr(fc, "name", ""),
                       "arguments": json.dumps(getattr(fc, "args", None) or {}, default=str)})
    return events, audio


GREETING_CUE = "The caller's line has just connected. Greet them now, as your instructions say."


async def _send_live(live: Any, msg: dict) -> None:
    """Apply one SidebandCall output to the Gemini Live session."""
    from google.genai import types

    if msg.get("type") == "tool_response":
        await live.send_tool_response(function_responses=[types.FunctionResponse(
            id=msg.get("call_id"), name=msg.get("name"), response=msg.get("output") or {})])
    elif msg.get("type") == "inject":
        await live.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=str(msg.get("instructions") or ""))]),
            turn_complete=True)


async def _caller_to_live(ws: Any, live: Any, session: SidebandCall) -> None:
    """Bridge frames -> Gemini: {"event": "media", "media": {"payload": b64 PCM16 16 kHz}},
    {"event": "dtmf", "dtmf": {"digit": "5"}}, {"event": "stop"}."""
    import base64

    from google.genai import types

    async for raw in ws:
        try:
            frame = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(frame, dict):
            continue
        kind = frame.get("event")
        if kind == "media":
            try:
                pcm = base64.b64decode((frame.get("media") or {}).get("payload") or "")
            except ValueError:
                continue
            if pcm:
                await live.send_realtime_input(audio=types.Blob(data=pcm, mime_type=C.INPUT_AUDIO_MIME))
        elif kind == "dtmf":
            digit = (frame.get("dtmf") or {}).get("digit") or ""
            await asyncio.to_thread(_handle_tx, session, {"type": "dtmf", "digit": digit})
        elif kind == "stop":
            return


async def _live_to_caller(ws: Any, live: Any, session: SidebandCall) -> None:
    """Gemini -> bridge: audio as {"event": "media"}, a barge-in as {"event": "clear"};
    transcripts, tool calls and the emergency path through SidebandCall."""
    import base64

    buf: dict[str, list[str]] = {}
    while True:
        async for msg in live.receive():
            events, audio = live_events(msg, buf)
            for chunk in audio:
                await ws.send(json.dumps({"event": "media", "media": {"payload": base64.b64encode(chunk).decode()}}))
            for event in events:
                if event["type"] == "interrupted":
                    await ws.send(json.dumps({"event": "clear"}))
                    continue
                for out in await asyncio.to_thread(_handle_tx, session, event):
                    await _send_live(live, out)


async def run_call(call: dict, *, connect=None, live=None, worker_id: str = WORKER_ID, use_llm_safety: bool = True) -> str:
    """Serve one leased call until the bridge's media socket closes. Returns
    the end reason recorded (or 'retry' when a socket never opened and the
    lease was handed back). connect(call_id) opens the media socket and
    live(call) the Gemini Live session (tests pass fakes)."""
    session = SidebandCall(call, use_llm_safety=use_llm_safety)
    opened = False
    try:
        async with (connect or _connect)(call["provider_call_id"]) as ws, (live or _live_connect)(call) as gem:
            opened = True
            _connect_failures.pop(session.row_id, None)
            await asyncio.to_thread(_handle_tx, session, {"type": "session.started"})
            # Gemini Live speaks only after a turn: cue the greeting so the
            # caller does not hear silence when the line connects.
            await _send_live(gem, {"type": "inject", "instructions": GREETING_CUE})
            renew = asyncio.create_task(_renew_loop(session.row_id, worker_id, ws))
            speaker = asyncio.create_task(_live_to_caller(ws, gem, session))
            try:
                await _caller_to_live(ws, gem, session)
            finally:
                renew.cancel()
                speaker.cancel()
                await asyncio.gather(speaker, return_exceptions=True)
        reason = "socket_closed"
    except asyncio.CancelledError:
        await asyncio.to_thread(_in_tx, _drop_lease, session.row_id, worker_id)
        raise
    except Exception as exc:
        log.warning("voice sideband: call %s socket failed: %s", session.row_id, type(exc).__name__)
        if not opened:
            failures = _connect_failures.get(session.row_id, 0) + 1
            _connect_failures[session.row_id] = failures
            if failures < CONNECT_ATTEMPTS:
                await asyncio.to_thread(_in_tx, _drop_lease, session.row_id, worker_id)
                return "retry"
            _connect_failures.pop(session.row_id, None)
            reason = "sideband_unreachable"
        else:
            reason = "socket_error"
    await asyncio.to_thread(_in_tx, release_call, session.row_id, reason=reason)
    return reason


# ─── The worker service ───


async def voice_sideband() -> None:
    """Module.worker_services entry. Polls for accepted calls every POLL_S
    (the webhook's NOTIFY voice_call_accepted is advisory; polling alone
    picks every call up within 2 s) and runs each on its own task."""
    running: dict[int, asyncio.Task] = {}
    try:
        while True:
            s = get_settings()
            if not (s.VOICE_ENABLED and s.GEMINI_API_KEY):
                await asyncio.sleep(IDLE_SLEEP_S)
                continue
            running = {k: t for k, t in running.items() if not t.done()}
            free = C.MAX_CALLS_PER_WORKER - len(running)
            if free > 0:
                # A database blip must not cancel the calls already on
                # sockets (a crash would, through the supervisor restart).
                try:
                    claimed = await asyncio.to_thread(_in_tx, claim_calls, WORKER_ID, free)
                except Exception:
                    log.exception("voice sideband: claim failed; retrying")
                    claimed = []
                for call in claimed:
                    running[int(call["id"])] = asyncio.create_task(run_call(call))
            await asyncio.sleep(C.POLL_S)
    finally:
        for t in running.values():
            t.cancel()
        if running:
            await asyncio.gather(*running.values(), return_exceptions=True)
