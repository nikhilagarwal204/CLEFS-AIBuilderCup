"""
POST /webhooks/voice/incoming (03 spec 3.2).

The hotel's telephony bridge (the SIP trunk terminates on a SIP-to-WebSocket
media bridge, since Google Gemini Live has no SIP endpoint) posts call
events here; an accepted call's audio is then streamed over a WebSocket
that the sideband worker bridges to a Gemini Live session.

1. Verify X-Clefs-Signature (HMAC-SHA256 over the raw body with
   VOICE_WEBHOOK_SECRET) and dedupe on X-Clefs-Webhook-Id (vc_webhook_receipts).
2. call.incoming: identify the line (X-Clefs-Line secret, else an
   allowed source on a line of the dialled project); anything unidentified,
   disabled, over capacity or after hours is rejected with SIP 486 so the
   PBX fails over to the operator.
3. Map the From extension through the allowlist to a room, only when the
   call carried the line secret (else, and for an unmapped extension, the
   model asks for the room: room_confirmed_by 'spoken', requests
   unverified). Source hosts are caller-written, so a source-identified
   call's From extension is never a verified room.
4. Build the phone session (concierge.api) and accept inline: the Gemini
   Live session config (stored on the call row for the worker), plus the MCP
   server and a per-call JWT on MCP lines.

Three short phases: decide and write the call row (one transaction), call
the bridge (no transaction held open), record the outcome (one transaction).
A retried delivery of the same webhook id, or a second delivery of the same
call id, is answered 200 without touching the bridge again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from clefs import db
from clefs.core import clock, hotels
from clefs.core.errors import ClefsError, bad_request
from clefs.db import Json
from clefs.settings import get_settings
from clefs.modules.concierge import api as concierge_api
from clefs.modules.voice import calls, lines, realtime_ops
from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.webhook")

router = APIRouter(tags=["voice"])
NOTIFY_CHANNEL = "voice_call_accepted"
INCOMING_TYPES = ("call.incoming", "call.initiated")


@dataclass
class Decision:
    action: str                      # accept | reject | none
    call_id: str | None = None
    call_row_id: int | None = None
    body: dict = field(default_factory=dict)
    reason: str | None = None
    response: dict = field(default_factory=dict)


# ─── Receipts ───


def claim_receipt(cur: Any, webhook_id: str, event: dict) -> bool:
    """True the first time this webhook-id arrives."""
    data = event.get("data") or {}
    cur.execute(
        """
        INSERT INTO vc_webhook_receipts (webhook_id, source, event_type, call_id, received_at)
        VALUES (%s, 'bridge', %s, %s, %s) ON CONFLICT (webhook_id) DO NOTHING RETURNING webhook_id
        """,
        (webhook_id, str(event.get("type") or "")[:80], (data.get("call_id") or None), clock.now()),
    )
    return cur.fetchone() is not None


def note_receipt(cur: Any, webhook_id: str, outcome: str, *, line: dict | None = None, project: str | None = None,
                 detail: dict | None = None) -> None:
    sets = ["outcome = %s", "detail = detail || %s"]
    params: list[Any] = [outcome, Json(detail or {})]
    if line is not None:
        sets += ["hotel_slug = %s", "voice_line_id = %s"]
        params += [line["hotel_slug"], line["id"]]
    if project:
        sets.append("project_id = %s")
        params.append(project)
    cur.execute(f"UPDATE vc_webhook_receipts SET {', '.join(sets)} WHERE webhook_id = %s", (*params, webhook_id))


# ─── Incoming call: decide ───


def _insert_call(cur: Any, *, webhook_id: str, call_id: str, line: dict | None, ext: str | None,
                 headers: dict[str, list[str]]) -> dict:
    cur.execute(
        """
        INSERT INTO vc_calls (hotel_slug, voice_line_id, provider_call_id, webhook_id, status, from_user,
                              tool_transport, sip_headers_redacted, started_at)
        VALUES (%s, %s, %s, %s, 'incoming', %s, %s, %s, %s)
        ON CONFLICT (provider_call_id) DO NOTHING RETURNING *
        """,
        (
            line["hotel_slug"] if line else None, line["id"] if line else None, call_id, webhook_id, ext,
            (line or {}).get("tool_transport"), Json(lines.redacted_headers(headers)), clock.now(),
        ),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _reject(cur: Any, webhook_id: str, call: dict, reason: str, *, line: dict | None, project: str | None) -> Decision:
    if call:
        cur.execute(
            "UPDATE vc_calls SET status = 'rejected', reject_code = %s, reject_reason = %s, ended_at = %s, end_reason = 'rejected' WHERE id = %s",
            (C.REJECT_CODE, reason, clock.now(), call["id"]),
        )
    note_receipt(cur, webhook_id, "rejected", line=line, project=project, detail={"reason": reason, "code": C.REJECT_CODE})
    return Decision("reject", call_id=call.get("provider_call_id"), call_row_id=call.get("id"), reason=reason,
                    response={"ok": True, "action": "rejected", "reason": reason})


class _NotLive(Exception):
    """concierge.api.open_session refused the phone mint (concierge_not_live)."""


def _open_session(cur: Any, spec: Any, call: dict, room: str | None) -> dict:
    """The call's concierge session. The not-live refusal is re-raised as
    _NotLive so _savepoint passes it through (rolled back) and the call is
    rejected, instead of being answered with no session."""
    try:
        return concierge_api.open_session(
            cur, spec, key_status="extension" if room else "spoken", call_ref=call["provider_call_id"], transport="sip",
            # The phone says in its first sentence that the call is recorded
            # (03 spec 3.5); a caller who stays on the line has been told.
            consents={"recording": True, "ai_processing": True, "transfer_abroad": True},
        )
    except ClefsError as exc:
        if exc.code == "concierge_not_live":
            raise _NotLive() from exc
        raise


def _savepoint(cur: Any, name: str, fn):
    """Another lane's api inside a savepoint: its failure must not abort the
    call decision (it becomes a 486 or a call without a concierge session).
    _NotLive is rolled back and re-raised: the caller turns it into a 486."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn()
    except _NotLive:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        raise
    except Exception as exc:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        log.warning("voice: %s failed: %s", name, exc)
        return None
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return out


def decide_incoming(cur: Any, webhook_id: str, event: dict) -> Decision:
    data = event.get("data") or {}
    call_id = str(data.get("call_id") or "").strip()[:200]
    headers = lines.header_map(data.get("sip_headers"))
    project = lines.to_project(headers)
    if not call_id:
        note_receipt(cur, webhook_id, "invalid_event", project=project, detail={"reason": "missing_call_id"})
        return Decision("none", response={"ok": True, "action": "ignored", "reason": "missing_call_id"})
    existing = calls.call_by_provider_id(cur, call_id)
    if existing:
        note_receipt(cur, webhook_id, "duplicate_call", project=project, detail={"call": existing["id"]})
        return Decision("none", response={"ok": True, "action": "duplicate", "status": existing["status"]})
    line, how = lines.identify_line(cur, headers)
    ext = lines.normalize_extension(lines.from_user(headers)) or (lines.from_user(headers) or "")[:64] or None
    call = _insert_call(cur, webhook_id=webhook_id, call_id=call_id, line=line, ext=ext, headers=headers)
    if not call:  # a concurrent delivery of the same call won the insert
        note_receipt(cur, webhook_id, "duplicate_call", project=project)
        return Decision("none", response={"ok": True, "action": "duplicate"})
    if line is None:
        return _reject(cur, webhook_id, call, how, line=None, project=project)
    hotel = hotels.find_hotel(cur, line["hotel_slug"])
    if hotel is None or hotel.status == "archived":
        return _reject(cur, webhook_id, call, "hotel_unavailable", line=line, project=project)
    reason = lines.admit(cur, line, hotel, voice_enabled=get_settings().VOICE_ENABLED,
                         module_on=hotels.module_enabled(cur, hotel.slug, "voice"))
    if reason is None and not concierge_api.is_live(cur, hotel.slug):
        # The concierge has not passed its readiness gate (concierge live.py):
        # a 486 fails the call over to the operator instead of an untested AI.
        reason = "concierge_not_live"
    if reason is None and not lines.extension_rate_ok(cur, line, ext, trusted=lines.extension_trusted(how)):
        reason = "extension_rate_limited"
    if reason:
        return _reject(cur, webhook_id, call, reason, line=line, project=project)
    return _prepare_accept(cur, webhook_id, call, line, hotel, ext=ext, how=how, project=project)


def _prepare_accept(cur: Any, webhook_id: str, call: dict, line: dict, hotel: Any, *, ext: str | None, how: str,
                    project: str | None) -> Decision:
    mapping = lines.map_extension(cur, line, ext)
    # A mapped extension binds a room (verified, in the JWT, sticky against
    # model arguments) or a location only when the PBX proved itself with
    # the line secret. A source-identified call's From is whatever the
    # caller typed: From sip:1204@pbx.hotel.ae must not become room 1204.
    trusted = lines.extension_trusted(how)
    bound = mapping if trusted else None
    room = bound["room"] if bound and bound["kind"] == "room" else None
    location = bound["location_slug"] if bound and bound["kind"] == "location" else None
    language = line.get("default_language") or "en"
    spec = _savepoint(cur, "vc_build_session", lambda: concierge_api.build_surface_session(
        hotel.slug, surface="phone", room=room, location=location, language=language, verified=bool(room), cur=cur,
    ))
    if spec is None:
        return _reject(cur, webhook_id, call, "session_unavailable", line=line, project=project)
    transport = line.get("tool_transport") or "mcp"
    block_args = dict(hotel_name=hotel.name, language=language, outside_line_prefix=line.get("outside_line_prefix"),
                      transport=transport)
    block = calls.phone_block(room=room, room_from_extension=bool(room), **block_args)
    template = calls.phone_block(room="{room}" if room else None, room_from_extension=bool(room), **block_args)
    instructions = f"{spec.instructions}\n\n{block}"
    version = calls.phone_prompt_version(spec.prompt_version, template, transport)
    try:
        session = _savepoint(cur, "vc_open_session", lambda: _open_session(cur, spec, call, room))
    except _NotLive:
        # The concierge refused the phone mint (switched off between the
        # is_live check above and here): same 486 to the operator.
        return _reject(cur, webhook_id, call, "concierge_not_live", line=line, project=project)
    cur.execute(
        """
        UPDATE vc_calls SET status = 'accepting', room = %s, location_slug = %s, extension_matched = %s,
               room_confirmed_by = %s, tool_transport = %s, session_id = %s, language = %s, prompt_version = %s, model = %s
        WHERE id = %s RETURNING *
        """,
        (room, location, bool(mapping), "extension" if room else "none", transport,
         (session or {}).get("session_id"), language, version, line.get("model") or spec.model, call["id"]),
    )
    call = dict(cur.fetchone())
    token = calls.mint_call_token(call, max_call_s=int(line["max_call_s"])) if transport == "mcp" else None
    body = calls.accept_body(spec, instructions=instructions, line=line, token=token)
    cur.execute("UPDATE vc_calls SET model = %s, session_config = %s WHERE id = %s",
                (body["model"], Json({k: v for k, v in body.items() if k != "mcp"}), call["id"]))
    note_receipt(cur, webhook_id, "accepting", line=line, project=project,
                 detail={"identified_by": how, "extension_matched": bool(mapping), "extension_trusted": trusted,
                         "room": room, "transport": transport})
    return Decision("accept", call_id=call["provider_call_id"], call_row_id=call["id"], body=body,
                    response={"ok": True, "action": "accepted", "call": call["id"]})


# ─── Other events ───


def handle_other(cur: Any, webhook_id: str, event: dict) -> Decision:
    """Call lifecycle events other than incoming: any event whose type names
    an end (call.ended, completed, hangup) closes the call row, so the call
    log gets an end time when the bridge reports one (Canon 15 build-time
    check)."""
    etype = str(event.get("type") or "")
    call_id = (event.get("data") or {}).get("call_id")
    call = calls.call_by_provider_id(cur, str(call_id)) if call_id else None
    if call and any(w in etype for w in ("ended", "completed", "hangup", "hung_up", "terminated")):
        if call.get("ended_at") is None:
            calls.mark_ended(cur, call, reason=etype[:60])
        note_receipt(cur, webhook_id, "call_ended", line=calls_line(cur, call), detail={"call": call["id"]})
        return Decision("none", response={"ok": True, "action": "recorded"})
    note_receipt(cur, webhook_id, "ignored", line=calls_line(cur, call) if call else None)
    return Decision("none", response={"ok": True, "action": "ignored"})


def calls_line(cur: Any, call: dict | None) -> dict | None:
    return lines.line_by_id(cur, call.get("voice_line_id")) if call else None


# ─── Execute and record ───


def _finish_accept(d: Decision, webhook_id: str) -> dict:
    try:
        realtime_ops.accept(d.call_id, d.body)
        ok, error = True, None
    except realtime_ops.RealtimeOpError as exc:
        ok, error = False, str(exc)[:300]
    if not ok:
        # The call would ring out anyway; a 486 fails it over sooner.
        try:
            realtime_ops.reject(d.call_id, C.REJECT_CODE)
        except realtime_ops.RealtimeOpError:
            pass
    with db.db() as cur:
        now = clock.now()
        if ok:
            cur.execute("UPDATE vc_calls SET status = 'accepted', accepted_at = %s WHERE id = %s AND status = 'accepting'",
                        (now, d.call_row_id))
            # The sideband worker wakes on this (it also polls).
            cur.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, str(d.call_row_id)))
            note_receipt(cur, webhook_id, "accepted")
        else:
            cur.execute(
                "UPDATE vc_calls SET status = 'failed', ended_at = %s, end_reason = 'accept_failed' WHERE id = %s",
                (now, d.call_row_id),
            )
            note_receipt(cur, webhook_id, "accept_failed", detail={"error": error})
    if not ok:
        return {"ok": True, "action": "accept_failed"}
    return d.response


def _finish_reject(d: Decision, webhook_id: str) -> dict:
    try:
        realtime_ops.reject(d.call_id, C.REJECT_CODE)
        sent = True
    except realtime_ops.RealtimeOpError as exc:
        log.warning("voice: reject of %s failed: %s", d.call_id, exc)
        sent = False
    if not sent:
        with db.db() as cur:
            note_receipt(cur, webhook_id, "rejected", detail={"reject_sent": False})
    return d.response


def handle_event(webhook_id: str, event: dict) -> dict:
    """The whole webhook after verification (sync; runs in the threadpool)."""
    with db.db() as cur:
        if not claim_receipt(cur, webhook_id, event):
            return {"ok": True, "action": "duplicate"}
        if event.get("type") in INCOMING_TYPES:
            d = decide_incoming(cur, webhook_id, event)
        else:
            d = handle_other(cur, webhook_id, event)
    if d.action == "accept":
        return _finish_accept(d, webhook_id)
    if d.action == "reject" and d.call_id:
        return _finish_reject(d, webhook_id)
    return d.response


# ─── Route ───


async def _read_body(request: Request) -> bytes:
    size = request.headers.get("content-length")
    if size and size.isdigit() and int(size) > C.WEBHOOK_BODY_MAX:
        raise ClefsError(413, "Webhook body too large", "payload_too_large")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > C.WEBHOOK_BODY_MAX:
            raise ClefsError(413, "Webhook body too large", "payload_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/voice/incoming")
async def voice_incoming_webhook(request: Request) -> dict:
    """The telephony bridge's call webhooks. 400 invalid_signature for a bad
    or missing signature; 503 not_configured without VOICE_WEBHOOK_SECRET.
    Every verified event gets 200 (a 5xx would make the bridge retry a
    decision already taken)."""
    secret = get_settings().VOICE_WEBHOOK_SECRET
    if not secret:
        raise ClefsError(503, "VOICE_WEBHOOK_SECRET is not set", "not_configured")
    body = await _read_body(request)
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        event = realtime_ops.unwrap(body, headers, secret)
    except realtime_ops.InvalidSignature:
        raise bad_request("Invalid webhook signature", "invalid_signature") from None
    webhook_id = str(headers.get(C.WEBHOOK_ID_HEADER) or event.get("id") or "")[:200]
    if not webhook_id:
        raise bad_request("webhook id missing", "invalid_signature")
    return await run_in_threadpool(handle_event, webhook_id, event)
