"""
Guest voice relay: Gemini Live on Vertex AI, held by the server (05 spec,
Voice relay (F)).

Vertex AI's Live API has no ephemeral tokens and a browser must never hold a
Google credential, so when sessions.live_via_vertex() is on, a live session
mint answers transport "relay" with a relay_url and a single-use relay_token.
The browser opens a WebSocket here and speaks the Live API's own JSON
messages. This route opens the Vertex AI Live session as the service identity
(on Cloud Run: no key), with the instructions, voice and tools the server
built for the session (sessions.vertex_live_config), and pipes messages both
ways until either side closes.

Wire protocol (JSON text frames):
  browser -> relay  first {"auth": {"token": "<relay_token>"}}, within
                    AUTH_TIMEOUT_S. Then Live client messages: realtimeInput
                    (audio as base64 PCM16 16 kHz, audioStreamEnd, text),
                    clientContent (the guest's turns: role user, text parts)
                    and toolResponse (answers to this session's own tool
                    calls). setup and anything else is dropped: the server
                    owns the setup, so a page cannot change the prompt, the
                    tools or the model.
  relay -> browser  {"setupComplete": {}} once Vertex AI is ready, then each
                    Live server message (serverContent, toolCall,
                    toolCallCancellation, voiceActivity, usageMetadata) in the
                    Live API's camelCase, audio as standard base64. Resumption
                    handles and goAway stay here: when Vertex AI ends a
                    connection, the relay resumes the same Live session on a
                    new one and the page never notices.
  close codes       1000 finished, or no frame from the page for
                    IDLE_TIMEOUT_S; 4400 a bad first frame; 4401 a wrong,
                    expired or used token, or a session that ended; 4403 a
                    foreign Origin (refused at the handshake); 4408 the
                    session's time is up; 1011 Vertex AI could not be reached
                    or dropped the session.

Limits: one relay per session (the token is single use and opens within
NEW_SESSION_WINDOW_S). A relay lasts MAX_SESSION_S at most (the ephemeral
token's 30 minutes) and never past the session's own expiry. Logs carry the
session id, hotel, model, location, duration and counts; never audio, text,
instructions or tokens.

Settings (env): LIVE_VERTEX_MODEL (sessions.relay_model), LIVE_VERTEX_LOCATION
(default us-central1: Vertex AI serves Live from regional endpoints, not
"global"), GOOGLE_CLOUD_PROJECT. Tests install a fake Live session with
set_live_connector() and never reach the network.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import contextlib
import enum
import json
import logging
import threading
import time
from typing import Any, AsyncContextManager, Callable

from fastapi import APIRouter, WebSocket

from clefs import db
from clefs.core import clock
from clefs.modules.concierge import constants as C
from clefs.modules.concierge import sessions
from clefs.settings import env, get_settings

log = logging.getLogger("clefs.concierge.live_relay")

router = APIRouter(tags=["concierge-public"])

DEFAULT_LOCATION = "us-central1"
AUTH_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 15.0
IDLE_TIMEOUT_S = 600.0
MAX_SESSION_S = float(C.CLIENT_SECRET_TTL_S)
MAX_RESUMES = 6
RESUME_MIN_LEFT_S = 5.0
MAX_FRAME_CHARS = 512 * 1024
MAX_AUDIO_BYTES = 256 * 1024
MAX_TEXT_CHARS = 4000
MAX_PARTS = 8
MAX_TOOL_RESPONSE_CHARS = 64 * 1024
QUEUE_LIMIT = 32

CLOSE_DONE = 1000
CLOSE_UPSTREAM = 1011
CLOSE_BAD_FRAME = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_ORIGIN = 4403
CLOSE_TIME_UP = 4408


# ─── The Vertex AI side ───

LiveConnector = Callable[[str, dict], AsyncContextManager[Any]]
_connector: LiveConnector | None = None


def set_live_connector(fn: LiveConnector | None) -> None:
    """Tests: fn(model, config) -> an async context manager yielding a Live
    session (send_realtime_input, send_client_content, send_tool_response,
    receive), the shape of client.aio.live.connect."""
    global _connector
    _connector = fn


def available() -> bool:
    """Whether a relay can reach Gemini (the mint answers 409 ai_disabled
    otherwise)."""
    return _connector is not None or bool(get_settings().AI_ENABLED)


def vertex_location() -> str:
    return (env("LIVE_VERTEX_LOCATION") or DEFAULT_LOCATION).strip()


_cred_lock = threading.Lock()
_client_lock = threading.Lock()
_credentials: Any = None
_adc_project: str | None = None
_clients: dict[tuple[str | None, str], Any] = {}


def _fresh_credentials() -> tuple[Any, str | None]:
    """Application Default Credentials (on Cloud Run, the service identity),
    refreshed here, in a worker thread, so the SDK's connect finds a valid
    token and never refreshes on the event loop."""
    import google.auth
    from google.auth.transport.requests import Request

    global _credentials, _adc_project
    with _cred_lock:
        if _credentials is None:
            _credentials, _adc_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if not (_credentials.token and _credentials.valid):
            _credentials.refresh(Request())
        return _credentials, _adc_project


def _vertex_client(credentials: Any, adc_project: str | None) -> Any:
    """One Vertex AI client per (project, Live location). Explicit
    credentials also keep the SDK off any API key in the environment."""
    from google import genai

    key = (get_settings().GOOGLE_CLOUD_PROJECT or adc_project, vertex_location())
    with _client_lock:
        client = _clients.get(key)
        if client is None:
            client = genai.Client(vertexai=True, project=key[0], location=key[1], credentials=credentials)
            _clients[key] = client
    return client


@contextlib.asynccontextmanager
async def _vertex_connect(model: str, config: dict):
    credentials, project = await asyncio.to_thread(_fresh_credentials)
    async with _vertex_client(credentials, project).aio.live.connect(model=model, config=config) as live:
        yield live


def _connect(model: str, config: dict) -> AsyncContextManager[Any]:
    return (_connector or _vertex_connect)(model, config)


# ─── The route ───


@router.websocket(sessions.RELAY_PATH)
async def live_relay(websocket: WebSocket, session_id: str) -> None:
    """The page's Live session, relayed to Vertex AI (module docstring)."""
    sid = "".join(ch for ch in session_id[:40] if ch.isalnum() or ch == "-")  # for logs only
    if not _origin_ok(websocket):
        log.info("live relay refused: foreign origin (session %s)", sid)
        await websocket.close(code=CLOSE_ORIGIN)
        return
    await websocket.accept()
    token, code = await _auth_frame(websocket)
    if token is None:
        if code:
            await _close(websocket, code, "auth_required")
        return
    try:
        ctx = await asyncio.to_thread(_authorize, session_id, token)
    except Exception:
        log.exception("live relay: could not open session %s", sid)
        await _close(websocket, CLOSE_UPSTREAM, "relay_failed")
        return
    if ctx is None:
        log.info("live relay refused: invalid, expired or used token (session %s)", sid)
        await _close(websocket, CLOSE_UNAUTHORIZED, "session_invalid")
        return
    code, reason = await _Relay(websocket, ctx).run()
    await _close(websocket, code, reason)


def _origin_ok(websocket: WebSocket) -> bool:
    """The API's own CSRF origin rule (WEB_ORIGINS, PUBLIC_API_ORIGIN, the
    CORS regex). Browsers always send Origin on a WebSocket; a client
    without one is not a browser and still needs the token."""
    from clefs.app import origin_allowed

    return origin_allowed(websocket.headers, get_settings())


async def _auth_frame(websocket: WebSocket) -> tuple[str | None, int]:
    """(token, 0), or (None, close code); (None, 0) when the page left."""
    try:
        message = await asyncio.wait_for(websocket.receive(), timeout=AUTH_TIMEOUT_S)
    except asyncio.TimeoutError:
        return None, CLOSE_BAD_FRAME
    if message.get("type") != "websocket.receive":
        return None, 0
    try:
        token = json.loads(_frame_text(message) or "")["auth"]["token"]
    except (TypeError, ValueError, KeyError):
        return None, CLOSE_BAD_FRAME
    if not isinstance(token, str) or not token or len(token) > 2048:
        return None, CLOSE_UNAUTHORIZED
    return token, 0


def _authorize(session_id: str, token: str) -> dict | None:
    """The session's relay context, or None for a wrong, expired or used
    token or a session that is over. Claims the single use and rebuilds the
    Live config the mint would have locked into an ephemeral token."""
    claims = sessions.verify_relay_token(token, session_id)
    if claims is None:
        return None
    with db.db() as cur:
        row = sessions.get_session(cur, session_id)
        now = clock.now()
        if (
            row is None or row["hotel_slug"] != claims.get("h") or row["transport"] != "live"
            or row["ended_at"] is not None or row["expires_at"] <= now
        ):
            return None
        if not sessions.claim_relay(cur, str(row["id"])):
            return None
        spec = sessions.spec_for_session(cur, row)
        cur.execute("UPDATE cc_sessions SET last_seen_at = %s WHERE id = %s", (now, row["id"]))
    return {
        "session_id": str(row["id"]),
        "hotel_slug": row["hotel_slug"],
        "model": row["model"] or sessions.relay_model(),
        "config": sessions.vertex_live_config(spec, mode=row["mode"]),
        "max_s": max(0.0, min(MAX_SESSION_S, (row["expires_at"] - now).total_seconds())),
        "prompt_changed": spec.prompt_version != row["prompt_version"],
    }


async def _close(websocket: WebSocket, code: int, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.close(code=code, reason=reason)


# ─── One relayed session ───


class _BrowserGone(Exception):
    """The page's socket is closed; nothing more can be sent to it."""


class _Relay:
    """Pipes one page socket and one Vertex AI Live session. The page side
    and the Vertex side run as two tasks; the first to finish (or the time
    limit) ends the relay."""

    def __init__(self, websocket: WebSocket, ctx: dict) -> None:
        self.ws = websocket
        self.sid = ctx["session_id"]
        self.hotel = ctx["hotel_slug"]
        self.model = ctx["model"]
        self.config = ctx["config"]
        self.prompt_changed = ctx.get("prompt_changed", False)
        self.deadline = time.monotonic() + float(ctx["max_s"])
        self.live: Any = None
        self.ready = False
        self.handle: str | None = None
        self.go_away = False
        self.browser_gone = False
        # Page-side call id -> (Vertex AI's id or None, function name).
        self.pending: dict[str, tuple[str | None, str]] = {}
        self.queue: list[tuple[str, dict]] = []
        self.resumes = 0
        self.tokens = 0
        self.counts: collections.Counter[str] = collections.Counter()

    def _left(self) -> float:
        return self.deadline - time.monotonic()

    async def run(self) -> tuple[int, str]:
        started = time.monotonic()
        reason = "cancelled"  # the server is shutting down
        page = asyncio.create_task(self._from_page())
        vertex = asyncio.create_task(self._vertex())
        try:
            try:
                done, _ = await asyncio.wait({page, vertex}, timeout=max(0.0, self._left()), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (page, vertex):
                    task.cancel()
                # asyncio.wait, not gather: if this task is itself being
                # cancelled (server shutdown), a cancelled gather raises a
                # CancelledError of its own that the server's cancel scope
                # does not take for the one it asked for.
                await asyncio.wait({page, vertex})
            if not done:
                code, reason = CLOSE_TIME_UP, "time_up"
            elif page in done:
                code, reason = CLOSE_DONE, _outcome(page)
            else:
                reason = _outcome(vertex)
                code = CLOSE_DONE if reason == "browser_closed" else CLOSE_UPSTREAM
            for task in (page, vertex):
                if task not in done:
                    _outcome(task, quiet=True)
            return code, reason
        finally:
            c = self.counts
            log.info(
                "live relay end: session %s reason %s after %.1fs, %d resumes; from page %d audio, %d text, %d tool "
                "responses, %d dropped, %d setup ignored; to page %d messages, %d audio, %d tool calls; %d tokens",
                self.sid, reason, time.monotonic() - started, self.resumes, c["audio_in"], c["text_in"],
                c["tool_responses"], c["dropped"], c["setup_ignored"], c["out"], c["audio_out"], c["tool_calls"], self.tokens,
            )

    # ─── Page -> Vertex AI ───

    async def _from_page(self) -> str:
        while True:
            try:
                message = await asyncio.wait_for(self.ws.receive(), timeout=IDLE_TIMEOUT_S)
            except asyncio.TimeoutError:
                return "idle"
            if message.get("type") != "websocket.receive":
                self.browser_gone = True
                return "browser_closed"
            raw = _frame_text(message)
            try:
                frame = json.loads(raw) if raw and len(raw) <= MAX_FRAME_CHARS else None
            except ValueError:
                frame = None
            if not isinstance(frame, dict):
                self.counts["dropped"] += 1
                continue
            for method, kwargs in self._client_ops(frame):
                await self._send_vertex(method, kwargs)

    def _client_ops(self, frame: dict) -> list[tuple[str, dict]]:
        """A page frame -> the Live session calls it allows (method, kwargs)."""
        ops: list[tuple[str, dict]] = []
        if "setup" in frame:
            self.counts["setup_ignored"] += 1
        realtime = _field(frame, "realtimeInput", "realtime_input")
        if isinstance(realtime, dict):
            ops += self._realtime_ops(realtime)
        content = _field(frame, "clientContent", "client_content")
        if isinstance(content, dict):
            ops += self._content_ops(content)
        tool = _field(frame, "toolResponse", "tool_response")
        if isinstance(tool, dict):
            ops += self._tool_ops(tool)
        return ops

    def _realtime_ops(self, realtime: dict) -> list[tuple[str, dict]]:
        ops: list[tuple[str, dict]] = []
        audio = realtime.get("audio")
        if isinstance(audio, dict):
            pcm = _b64decode(audio.get("data"))
            mime = str(_field(audio, "mimeType", "mime_type") or "audio/pcm;rate=16000")
            if pcm and len(pcm) <= MAX_AUDIO_BYTES and mime.startswith("audio/pcm"):
                ops.append(("send_realtime_input", {"audio": {"data": pcm, "mime_type": mime}}))
                self.counts["audio_in"] += 1
            else:
                self.counts["dropped"] += 1
        if _field(realtime, "audioStreamEnd", "audio_stream_end") is True:
            ops.append(("send_realtime_input", {"audio_stream_end": True}))
        text = realtime.get("text")
        if isinstance(text, str) and text.strip():
            ops.append(("send_realtime_input", {"text": text[:MAX_TEXT_CHARS]}))
            self.counts["text_in"] += 1
        return ops

    def _content_ops(self, content: dict) -> list[tuple[str, dict]]:
        """The guest's own turns only: role user, text parts. A page cannot
        put words in the model's mouth or send it files."""
        turns = content.get("turns")
        if isinstance(turns, dict):
            turns = [turns]
        kept: list[dict] = []
        for turn in (turns if isinstance(turns, list) else [])[:MAX_PARTS]:
            if not isinstance(turn, dict) or (turn.get("role") or "user") != "user":
                self.counts["dropped"] += 1
                continue
            parts = [
                {"text": p["text"][:MAX_TEXT_CHARS]}
                for p in (turn.get("parts") or [])[:MAX_PARTS]
                if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()
            ]
            if parts:
                kept.append({"role": "user", "parts": parts})
        complete = _field(content, "turnComplete", "turn_complete") is True
        if not kept and not complete:
            return []
        self.counts["text_in"] += len(kept)
        kwargs: dict[str, Any] = {"turn_complete": complete}
        if kept:
            kwargs["turns"] = kept
        return [("send_client_content", kwargs)]

    def _tool_ops(self, tool: dict) -> list[tuple[str, dict]]:
        """Answers to calls this session's model made, by their ids; the
        function name is the model's, never the page's."""
        out: list[dict] = []
        responses = _field(tool, "functionResponses", "function_responses")
        if isinstance(responses, dict):
            responses = [responses]
        for item in responses if isinstance(responses, list) else []:
            known = self.pending.pop(str(item.get("id") or ""), None) if isinstance(item, dict) else None
            response = item.get("response") if known else None
            if not isinstance(response, dict) or len(json.dumps(response, default=str)) > MAX_TOOL_RESPONSE_CHARS:
                self.counts["dropped"] += 1
                continue
            vertex_id, name = known
            answer: dict[str, Any] = {"name": name, "response": response}
            if vertex_id:
                answer["id"] = vertex_id
            out.append(answer)
        if not out:
            return []
        self.counts["tool_responses"] += len(out)
        return [("send_tool_response", {"function_responses": out})]

    async def _send_vertex(self, method: str, kwargs: dict) -> None:
        live = self.live
        if live is None:
            # Vertex AI is reconnecting: audio from that gap is stale, the
            # guest's text and tool answers wait for the new connection.
            if method != "send_realtime_input" and len(self.queue) < QUEUE_LIMIT:
                self.queue.append((method, kwargs))
            else:
                self.counts["dropped"] += 1
            return
        try:
            await getattr(live, method)(**kwargs)
        except Exception as exc:  # the connection is closing: _vertex() notices and resumes
            log.debug("live relay: send to Vertex AI failed on session %s: %s", self.sid, type(exc).__name__)
            # A typed turn is kept for the resumed connection; audio is stale
            # and a tool answer belongs to the old connection's call.
            if method == "send_client_content" and len(self.queue) < QUEUE_LIMIT:
                self.queue.append((method, kwargs))
            else:
                self.counts["dropped"] += 1

    # ─── Vertex AI -> page ───

    async def _vertex(self) -> str:
        """Connect, and reconnect with the resumption handle when Vertex AI
        ends a connection (goAway, a dropped socket) while the page is still
        there. Returns why the relay should end."""
        while True:
            config = dict(self.config)
            if self.handle:
                config["session_resumption"] = {"handle": self.handle}
            ended, connected = "closed", False
            try:
                async with contextlib.AsyncExitStack() as stack:
                    live = await asyncio.wait_for(stack.enter_async_context(_connect(self.model, config)), CONNECT_TIMEOUT_S)
                    connected, self.go_away = True, False
                    if not self.ready:
                        self.ready = True
                        await self._to_page({"setupComplete": {}})
                        log.info(
                            "live relay start: session %s hotel %s model %s location %s%s", self.sid, self.hotel,
                            self.model, vertex_location(), " (prompt rebuilt from newer knowledge)" if self.prompt_changed else "",
                        )
                    # Messages held during a reconnect go first. self.live is
                    # set only once the queue is empty, so the page side
                    # never sends on this connection at the same time.
                    while self.queue:
                        method, kwargs = self.queue.pop(0)
                        await getattr(live, method)(**kwargs)
                    self.live = live
                    ended = await self._from_vertex(live)
            except asyncio.CancelledError:
                raise
            except _BrowserGone:
                return "browser_closed"
            except Exception as exc:
                ended = "error"
                # Before setup nothing of the guest's has been sent, so the
                # API's reason (model or config) is safe to log; after it,
                # only the type and code.
                detail = " ".join(str(exc).split())[:200] if not self.ready else ""
                log.warning(
                    "live relay: Vertex AI %s on session %s: %s %s %s", "connection ended" if connected else "connect failed",
                    self.sid, type(exc).__name__, getattr(exc, "code", "") or "", detail,
                )
            finally:
                self.live = None
            if self.browser_gone:
                return "browser_closed"
            if not self.ready:
                return "upstream_failed"
            if not connected:
                # A handle Vertex AI would not resume from: stop, do not loop.
                self.handle = None
            if not self.handle or self.resumes >= MAX_RESUMES or self._left() < RESUME_MIN_LEFT_S:
                return "upstream_closed"
            self.resumes += 1
            # Calls from the old connection cannot be answered on the new one.
            self.pending.clear()
            log.info("live relay: resuming session %s after %s (resume %d)", self.sid, ended, self.resumes)

    async def _from_vertex(self, live: Any) -> str:
        while True:
            turn_done = False
            async for msg in live.receive():
                out = self._server_message(msg)
                done = _turn_complete(out or {})
                if done and self.go_away:
                    # Moving to a new connection after this turn: what the
                    # page sends from now on waits for it.
                    self.live = None
                if out:
                    await self._to_page(out)
                if done:
                    turn_done = True
                    if self.go_away:
                        return "go_away"
            if not turn_done:
                # receive() ends after a complete turn; ending without one
                # means the connection closed.
                return "closed"

    def _server_message(self, msg: Any) -> dict | None:
        """A Live server message -> what the page gets (or None). Keeps the
        resumption handle and goAway, tracks tool call ids, counts."""
        if isinstance(msg, dict):
            data = _plain(msg)
        else:
            data = _plain(msg.model_dump(mode="python", by_alias=True, exclude_none=True))
        update = data.pop("sessionResumptionUpdate", None)
        if isinstance(update, dict) and update.get("resumable") and update.get("newHandle"):
            self.handle = str(update["newHandle"])
        if data.pop("goAway", None) is not None:
            self.go_away = True
        usage = data.get("usageMetadata")
        if isinstance(usage, dict):
            self.tokens += int(usage.get("totalTokenCount") or 0)
        call = data.get("toolCall")
        if isinstance(call, dict):
            for fc in call.get("functionCalls") or []:
                self.counts["tool_calls"] += 1
                vertex_id = fc.get("id")
                page_id = str(vertex_id or f"relay_call_{self.counts['tool_calls']}")
                fc["id"] = page_id
                self.pending[page_id] = (vertex_id, str(fc.get("name") or ""))
        cancelled = data.get("toolCallCancellation")
        if isinstance(cancelled, dict):
            for call_id in cancelled.get("ids") or []:
                self.pending.pop(str(call_id), None)
        parts = ((data.get("serverContent") or {}).get("modelTurn") or {}).get("parts") or []
        self.counts["audio_out"] += sum(1 for p in parts if isinstance(p, dict) and p.get("inlineData"))
        return data or None

    async def _to_page(self, obj: dict) -> None:
        try:
            await self.ws.send_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str))
        except Exception as exc:
            self.browser_gone = True
            raise _BrowserGone() from exc
        self.counts["out"] += 1


# ─── Helpers ───


def _outcome(task: asyncio.Task, *, quiet: bool = False) -> str:
    """A finished task's result, its exception retrieved (and logged by type)."""
    if task.cancelled():
        return "cancelled"
    exc = task.exception()
    if exc is not None:
        if not quiet:
            log.warning("live relay: %s", type(exc).__name__)
        return "error"
    return str(task.result())


def _field(obj: dict, camel: str, snake: str) -> Any:
    """A Live message field by its JSON (camelCase) or proto (snake) name."""
    return obj[camel] if camel in obj else obj.get(snake)


def _frame_text(message: dict) -> str | None:
    text = message.get("text")
    if text is None and message.get("bytes") is not None:
        text = message["bytes"].decode("utf-8", errors="replace")
    return text


def _b64decode(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value.replace("-", "+").replace("_", "/"), validate=False)
    except (binascii.Error, ValueError):
        return None


def _plain(value: Any) -> Any:
    """JSON-ready: bytes as standard base64 (the browser's atob() rejects
    the URL-safe alphabet pydantic's JSON mode would write), enums as values."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, enum.Enum):
        return value.value
    return value


def _turn_complete(data: dict) -> bool:
    return bool((data.get("serverContent") or {}).get("turnComplete"))
