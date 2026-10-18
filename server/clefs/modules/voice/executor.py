"""
Phone tool execution, shared by the MCP server and the sideband worker
(03 spec 3.3-3.4).

The room never comes from the model when the call has one:
- extension-mapped calls use the room bound to the call (and the JWT);
  a `room` argument is dropped;
- spoken-room calls: the first request must carry a room that exists in the
  register; it is confirmed onto the call row (room_confirmed_by 'spoken')
  and every later tool call uses it, whatever the model passes. Requests
  from a spoken room are unverified, so staff confirm at the door.

Tools run through the concierge executor (one executor for every surface,
03 spec 0.3) via concierge.api.run_tool, on the concierge session bound when
the call was answered. The voice-local executor (fallback.py) is only a
guarded fallback for a call the concierge cannot serve: the module switched
off for the hotel or missing from the build, or no session bound. Every
call is written to vc_tool_calls first: a retried call returns the stored
answer (and run_tool dedupes again on the same key in cc_tool_calls).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from typing import Any

from clefs.core import clock
from clefs.core import hotels
from clefs.core import rooms as rooms_mod
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.concierge import api as concierge_api
from clefs.modules.voice import calls, lines, realtime_ops
from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.executor")

ROOM_TOOLS = frozenset({"create_ticket", "notify_staff", "place_order", "escalate_to_human"})
ROOM_REQUIRED = frozenset({"create_ticket", "notify_staff", "place_order"})
COUNTED = {"created", "escalated", "placed", "callback_requested"}


def result(status: str, followup: str, **data: Any) -> dict:
    out = {"status": status, **{k: v for k, v in data.items() if v is not None}, "followup_instructions": followup}
    return out


def _clean(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str))


# ─── Room binding ───


def resolve_room(cur: Any, call: dict, name: str, args: dict, *, claims_room: str | None = None) -> tuple[dict, dict | None]:
    """(args to execute with, or a refusal result). Mutates nothing but the
    call row's sticky spoken room."""
    args = dict(args or {})
    if name not in ROOM_TOOLS:
        args.pop("room", None)
        return args, None
    bound = claims_room or (call.get("room") if call.get("room_confirmed_by") == "extension" else None)
    if bound:
        args.pop("room", None)            # the model cannot redirect a bound call
        return args, None
    if call.get("room") and call.get("room_confirmed_by") == "spoken":
        args["room"] = call["room"]       # sticky for the rest of the call
        return args, None
    spoken = _clean(args.get("room"), 24)
    if spoken and _room_exists(cur, call["hotel_slug"], spoken):
        cur.execute(
            "UPDATE vc_calls SET room = %s, room_confirmed_by = 'spoken' WHERE id = %s AND room IS NULL",
            (spoken, call["id"]),
        )
        call["room"], call["room_confirmed_by"] = spoken, "spoken"
        args["room"] = spoken
        return args, None
    args.pop("room", None)
    if name in ROOM_REQUIRED:
        what = "is not a room in this hotel" if spoken else "is missing"
        return args, result("room_required", f"The room number {what}. Ask the caller for their room number, repeat it "
                            "back to confirm, then call again with room set to it.")
    return args, None                     # an escalation is never blocked on the room


def _room_exists(cur: Any, hotel_slug: str, room: str) -> bool:
    if rooms_mod.has_register(cur, hotel_slug):
        return rooms_mod.get_room(cur, hotel_slug, room) is not None
    return rooms_mod.valid_room(room)


# ─── Ledger (idempotency) ───


def dedupe_key(name: str, args: dict, tool_call_id: str | None, *, transport: str) -> str:
    """The idempotency key. Sideband calls carry the Gemini function call id. MCP
    tools/call ids restart per connection, so an MCP side-effect call is
    keyed on its content within a short bucket (a retry lands in the same
    bucket; the same request minutes later is a new request). Read-only
    tools are never deduped."""
    if tool_call_id and transport == "sideband":
        return _clean(tool_call_id, 120)
    if name not in C.SIDE_EFFECT_TOOLS:
        return f"ro_{uuid.uuid4().hex}"
    bucket = int(clock.now().timestamp()) // C.MCP_DEDUPE_BUCKET_S
    digest = hashlib.sha256(f"{name}|{json.dumps(args, sort_keys=True, default=str)}|{bucket}".encode()).hexdigest()
    return f"mcp_{digest[:32]}"


def _claim(cur: Any, call: dict, key: str, name: str, args: dict, transport: str) -> dict | None:
    """Insert the ledger row; a duplicate waits on the unique index and gets
    the first call's stored answer."""
    cur.execute(
        """
        INSERT INTO vc_tool_calls (hotel_slug, call_row_id, tool_call_id, name, transport, arguments)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (call_row_id, tool_call_id) DO NOTHING RETURNING id
        """,
        (call["hotel_slug"], call["id"], key, name, transport, Json(_jsonable(args))),
    )
    if cur.fetchone() is not None:
        return None
    cur.execute("SELECT result FROM vc_tool_calls WHERE call_row_id = %s AND tool_call_id = %s", (call["id"], key))
    row = cur.fetchone() or {}
    stored = dict(row.get("result") or {}) or result("in_progress", "Wait for the earlier answer to this request.")
    return {**stored, "deduped": True}


def _store(cur: Any, call: dict, key: str, out: dict, status: str) -> None:
    cur.execute(
        "UPDATE vc_tool_calls SET result = %s, status = %s WHERE call_row_id = %s AND tool_call_id = %s",
        (Json(_jsonable(out)), status, call["id"], key),
    )


# ─── Entry point ───


def run(cur: Any, call: dict, name: str, args: dict | None, *, tool_call_id: str | None = None, transport: str = "mcp",
        claims_room: str | None = None) -> dict:
    """Run one phone tool for a vc_calls row. Always returns a speakable
    result dict (status + followup_instructions), never raises mid-call."""
    if not calls.is_live(call):
        return result("call_ended", "Apologise and suggest the caller rings the front desk directly.")
    if name not in C.MCP_TOOLS and name not in C.CONCIERGE_TOOL:
        return result("unknown_tool", "That tool does not exist. Carry on without it.")
    args, refusal = resolve_room(cur, call, name, args or {}, claims_room=claims_room)
    if refusal is not None:
        return refusal
    key = dedupe_key(name, args, tool_call_id, transport=transport)
    earlier = _claim(cur, call, key, name, args, transport)
    if earlier is not None:
        return earlier
    cur.execute("SAVEPOINT vc_tool")
    try:
        out = transfer(cur, call, args) if name == "transfer_to_front_desk" else _dispatch(cur, call, name, args, key)
        status = "ok"
    except ClefsError as exc:
        cur.execute("ROLLBACK TO SAVEPOINT vc_tool")
        out, status = _error_result(exc), "refused"
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT vc_tool")
        log.exception("voice tool %s failed on call %s", name, call.get("id"))
        out, status = result("error", "Apologise that something went wrong and offer to transfer the caller to the "
                             "front desk."), "failed"
    else:
        cur.execute("RELEASE SAVEPOINT vc_tool")
    out = {k: v for k, v in _jsonable(out).items() if v is not None}
    _store(cur, call, key, out, status)
    _count(cur, call, name, args, out)
    return out


def _error_result(exc: ClefsError) -> dict:
    if exc.status == 429:
        return result("rate_limited", "Apologise: too many requests came from this room just now. Offer to transfer "
                      "the caller to the front desk if it is urgent.", code=exc.code)
    return result("error", "Apologise that this could not be done right now and offer to transfer the caller to the "
                  "front desk.", code=exc.code)


def _count(cur: Any, call: dict, name: str, args: dict, out: dict) -> None:
    if out.get("status") not in COUNTED or out.get("deduped"):
        return
    emergency = name == "escalate_to_human" and _clean(args.get("reason"), 30) == "emergency"
    cur.execute(
        "UPDATE vc_calls SET requests_created = requests_created + 1, emergencies = emergencies + %s WHERE id = %s",
        (1 if emergency else 0, call["id"]),
    )


# ─── Dispatch ───


def concierge_available(cur: Any, call: dict) -> bool:
    """Whether this call runs on the concierge executor: it has a bound
    concierge session and the concierge module is loaded and on for the
    hotel. False only when the concierge is disabled (switched off for the
    hotel, or absent from a trimmed build) or could not bind a session when
    the call was answered; then the guarded fallback serves the call."""
    if not call.get("session_id"):
        return False
    return hotels.module_enabled(cur, call["hotel_slug"], "concierge")


def _dispatch(cur: Any, call: dict, name: str, args: dict, key: str) -> dict:
    target = C.CONCIERGE_TOOL.get(name, name)
    if concierge_available(cur, call):
        cargs = dict(args)
        if target == "notify_staff":
            cargs.setdefault("label", _clean(args.get("request_type"), 100) or None)
        # The one executor (03 spec 0.3): the session row carries the room
        # bound at answer time; `key` makes a retried call a stored replay.
        return concierge_api.run_tool(cur, call["hotel_slug"], session_ctx={"session_id": call["session_id"], "call_id": key},
                                      name=target, args=cargs)
    from clefs.modules.voice import fallback

    log.info("voice: concierge unavailable for call %s, using the fallback executor", call.get("id"))
    return fallback.execute(cur, call, target, args)


# ─── transfer_to_front_desk ───


def transfer(cur: Any, call: dict, args: dict) -> dict:
    """SIP REFER to the line's front desk. sip:/sips: only (never tel:); a
    refused or failed REFER files a callback request and tells the caller."""
    line = lines.line_by_id(cur, call.get("voice_line_id")) or {}
    target = (line.get("front_desk_uri") or "").strip()
    reason = _clean(args.get("reason"), 200) or "caller asked for the front desk"
    if not lines.valid_refer_target(target):
        return _callback(cur, call, reason, why="no_transfer_target")
    try:
        realtime_ops.refer(call["provider_call_id"], target)
    except realtime_ops.RealtimeOpError as exc:
        log.warning("voice: REFER failed on call %s: %s", call["id"], exc)
        return _callback(cur, call, reason, why="refer_failed")
    cur.execute("UPDATE vc_calls SET transferred = TRUE, transfer_target = %s WHERE id = %s", (target, call["id"]))
    return result("transferred", "Tell the caller you are connecting them to the front desk now, then stop talking.")


def _callback(cur: Any, call: dict, reason: str, *, why: str) -> dict:
    room = call.get("room")
    if not room:
        return result("transfer_failed", "Apologise: you cannot connect the call right now. Ask the caller to dial the "
                      "front desk directly (the operator).", reason=why)
    from clefs.modules.service import api as service_api

    cur.execute("SAVEPOINT vc_callback")
    try:
        row = service_api.create_request(
            cur, call["hotel_slug"], room=room, type="front_desk", source="sip",
            description=f"Phone AI: please call room {room} back ({reason}). The transfer could not be completed.",
            priority_hint="high", language="en",
        )
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT vc_callback")
        log.warning("voice: callback request failed on call %s: %s", call["id"], exc)
        return result("transfer_failed", "Apologise: you cannot connect the call right now. Ask the caller to dial the "
                      "front desk directly.", reason=why)
    cur.execute("RELEASE SAVEPOINT vc_callback")
    return result("callback_requested", "Tell the caller you could not connect them, and that the front desk will call "
                  "them back in the room shortly.", reason=why, request_ref=str(row.get("id")))
