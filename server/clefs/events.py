"""
Realtime events (SSE).

    publish(slug, "service.request_created", {"id": 12}, cur=cur)   # delivered on commit
    publish(slug, "requests_changed", {})                           # legacy name, now

Backends (EVENTS_BACKEND):
  notify  pg_notify on channel clefs_events. With `cur` it rides the caller's
          transaction, so Postgres delivers it only if that commits. Each API
          process runs one LISTEN thread (unpooled URL) and fans out to its SSE
          subscribers, so events cross uvicorn workers and the worker process.
  local   in-process fan-out (the old behaviour). Used on Vercel, where
          instances cannot share a LISTEN connection and clients fall back to
          polling. With `cur`, delivery waits for db() to commit.

Payloads must stay under MAX_PAYLOAD_BYTES (NOTIFY's limit is 8000 bytes);
larger ones are replaced by {"id": ..., "truncated": true} and clients refetch.

`audience` = {roles, departments, floors, device_ids, user_ids} is matched per
subscriber on the server. Anonymous (legacy open access) subscribers receive
only audience-less events, which is what every legacy event is.

Event names are "<module>.<event>". The legacy names requests_changed,
new_clefs_request and new_aria_request stay as-is for deployed dashboards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import select
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from clefs import db
from clefs.settings import get_settings

log = logging.getLogger("clefs.events")

CHANNEL = "clefs_events"
MAX_PAYLOAD_BYTES = 7 * 1024
QUEUE_SIZE = 50
KEEPALIVE_S = 25
# Tickets are checked only at connect, so a stream is closed after this long;
# EventSource reconnects and the client re-mints a ticket.
STREAM_MAX_S = 30 * 60

LEGACY_EVENTS = ("requests_changed", "new_clefs_request", "new_aria_request")


# ─── Subscribers ───


@dataclass(eq=False)
class Subscriber:
    hotel_slug: str
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop
    kind: str = "anonymous"  # user | device | anonymous
    user_id: int | None = None
    device_id: int | None = None
    role: str | None = None
    departments: frozenset[str] = field(default_factory=frozenset)
    floors: frozenset[str] = field(default_factory=frozenset)


_subs: dict[str, list[Subscriber]] = defaultdict(list)
_subs_lock = threading.Lock()


def subscribe(sub: Subscriber) -> None:
    with _subs_lock:
        _subs[sub.hotel_slug].append(sub)


def unsubscribe(sub: Subscriber) -> None:
    with _subs_lock:
        try:
            _subs[sub.hotel_slug].remove(sub)
        except ValueError:
            pass


def subscriber_count(hotel_slug: str | None = None) -> int:
    with _subs_lock:
        if hotel_slug is not None:
            return len(_subs.get(hotel_slug, []))
        return sum(len(v) for v in _subs.values())


def matches(sub: Subscriber, audience: dict | None) -> bool:
    if not audience:
        return True
    if sub.kind == "anonymous":
        return False
    device_ids = audience.get("device_ids")
    if device_ids:
        return sub.device_id in set(device_ids)
    user_ids = audience.get("user_ids")
    if user_ids and sub.user_id not in set(user_ids):
        return False
    roles = audience.get("roles")
    if roles and (sub.role or "") not in set(roles):
        return False
    # Empty subscriber scope means "all departments / floors".
    depts = audience.get("departments")
    if depts and sub.departments and not (sub.departments & set(depts)):
        return False
    floors = audience.get("floors")
    if floors and sub.floors and not (sub.floors & {str(f) for f in floors}):
        return False
    return True


def _offer(q: asyncio.Queue, msg: str) -> None:
    try:
        q.put_nowait(msg)
    except asyncio.QueueFull:
        pass  # a stalled client misses events; it refetches on reconnect


def _fanout(payload: dict) -> None:
    slug = payload.get("h")
    event = payload.get("e")
    if not slug or not event:
        return
    msg = f"event: {event}\ndata: {json.dumps(payload.get('d', {}), default=str)}\n\n"
    audience = payload.get("a")
    with _subs_lock:
        targets = [s for s in _subs.get(slug, []) if matches(s, audience)]
    for s in targets:
        try:
            s.loop.call_soon_threadsafe(_offer, s.queue, msg)
        except RuntimeError:
            unsubscribe(s)  # its loop is closed


# ─── Publish ───


def _encode(hotel_slug: str, event: str, data: dict, audience: dict | None) -> tuple[dict, str]:
    payload = {"h": hotel_slug, "e": event, "d": data, "a": audience}
    text = json.dumps(payload, default=str)
    if len(text.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        slim = {"truncated": True}
        if isinstance(data, dict) and "id" in data:
            slim["id"] = data["id"]
        payload = {"h": hotel_slug, "e": event, "d": slim, "a": audience}
        text = json.dumps(payload, default=str)
        log.warning("event %s for %s exceeded %d bytes; sent ids only", event, hotel_slug, MAX_PAYLOAD_BYTES)
    return payload, text


def publish(hotel_slug: str, event: str, data: dict, *, audience: dict | None = None, cur: Any = None) -> None:
    payload, text = _encode(hotel_slug, event, data or {}, audience)
    if get_settings().EVENTS_BACKEND == "local":
        if cur is not None:
            db.on_commit(cur, lambda: _fanout(payload))
        else:
            _fanout(payload)
        return
    try:
        if cur is not None:
            cur.execute("SELECT pg_notify(%s, %s)", (CHANNEL, text))
        else:
            with db.db() as c:
                c.execute("SELECT pg_notify(%s, %s)", (CHANNEL, text))
    except Exception:
        if cur is not None:
            raise  # the caller's transaction is now aborted; let it roll back
        log.exception("publish %s failed", event)


# ─── LISTEN thread (notify backend) ───

_listener: threading.Thread | None = None
_stop = threading.Event()
# Set while a LISTEN connection is up (tests and /internal/status read it).
listener_ready = threading.Event()


def _listen_forever() -> None:
    backoff = 1.0
    while not _stop.is_set():
        conn = None
        try:
            conn = db.raw_connection(unpooled=True)
            conn.autocommit = True
            with conn.cursor() as c:
                c.execute(f"LISTEN {CHANNEL}")
            listener_ready.set()
            backoff = 1.0
            while not _stop.is_set():
                if select.select([conn], [], [], 1.0) == ([], [], []):
                    continue
                conn.poll()
                while conn.notifies:
                    n = conn.notifies.pop(0)
                    try:
                        _fanout(json.loads(n.payload))
                    except Exception:
                        log.exception("bad event payload")
        except Exception:
            listener_ready.clear()
            log.exception("events listener lost its connection; retrying in %.0fs", backoff)
            _stop.wait(backoff)
            backoff = min(backoff * 2, 30.0)
        finally:
            listener_ready.clear()
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


def start_listener() -> None:
    global _listener
    if get_settings().EVENTS_BACKEND != "notify":
        return
    if _listener is not None and _listener.is_alive():
        return
    _stop.clear()
    _listener = threading.Thread(target=_listen_forever, name="clefs-events-listener", daemon=True)
    _listener.start()


def stop_listener(timeout_s: float = 3.0) -> None:
    global _listener
    _stop.set()
    if _listener is not None:
        _listener.join(timeout_s)
        _listener = None


# ─── Stream route ───

router = APIRouter(tags=["events"])


def _authorize_stream(slug: str, ticket: str | None) -> dict:
    """Subscriber attributes from a stream ticket, or anonymous for a legacy
    open-access hotel. Runs in the threadpool (DB + HMAC)."""
    from clefs.auth import tokens
    from clefs.core.errors import ClefsError
    from clefs.core.hotels import get_hotel

    if ticket:
        claims = tokens.verify_ticket("stream", ticket)
        if claims is None:
            raise ClefsError(401, "Stream ticket expired or invalid", "invalid_ticket")
        if claims.get("h") != slug:
            raise ClefsError(403, "Ticket is for another hotel", "forbidden")
        return claims
    with db.db() as cur:
        hotel = get_hotel(cur, slug)
    if not hotel.legacy_open_access:
        raise ClefsError(401, "A stream ticket is required", "stream_ticket_required")
    return {"k": "anonymous", "h": slug}


@router.get("/hotels/{slug}/stream")
async def stream_events(slug: str, request: Request, t: str | None = None) -> StreamingResponse:
    """SSE endpoint. `t` is a ticket from POST /auth/stream-ticket; legacy
    open-access hotels may connect without one (old dashboards do)."""
    claims = await run_in_threadpool(_authorize_stream, slug, t)
    sub = Subscriber(
        hotel_slug=slug,
        queue=asyncio.Queue(maxsize=QUEUE_SIZE),
        loop=asyncio.get_running_loop(),
        kind=claims.get("k", "anonymous"),
        user_id=claims.get("u"),
        device_id=claims.get("d"),
        role=claims.get("r"),
        departments=frozenset(claims.get("dep") or ()),
        floors=frozenset(str(f) for f in (claims.get("fl") or ())),
    )
    subscribe(sub)

    async def generator():
        deadline = time.monotonic() + STREAM_MAX_S
        try:
            yield ": keepalive\n\n"
            while (left := deadline - time.monotonic()) > 0:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=min(KEEPALIVE_S, left))
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"  # stops proxies closing an idle connection
        finally:
            unsubscribe(sub)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
