"""
The phone's MCP server (03 spec 3.3): POST /webhooks/voice/mcp.

The telephony bridge (or any MCP client the call is wired to) calls this with the per-call JWT that the webhook
put in the accept payload (tools[0].authorization). Every request is
authenticated by that token alone; the hotel, the call and the extension
room come from its claims, never from tool arguments.

Transport: MCP Streamable HTTP in stateless JSON-response mode (no
Mcp-Session-Id, no SSE stream: GET is 405), so it runs on serverless
Python. Why not the SDK's session manager: mcp 2.x renamed FastMCP to
MCPServer, and its StreamableHTTPSessionManager needs a task group started
from the app lifespan, which modules cannot hook (clefs/app.py is frozen).
The JSON-RPC surface here is small (initialize, ping, tools/list,
tools/call, notifications) and follows the same wire format; protocol
versions come from mcp.types when the SDK is installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from starlette.concurrency import run_in_threadpool

from clefs import db
from clefs.core.errors import ClefsError
from clefs.modules.voice import calls, executor
from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.mcp")

router = APIRouter(tags=["voice"])
BODY_MAX = 64 * 1024
SERVER_INFO = {"name": "clefs-voice", "title": "Clefs hotel phone tools", "version": "1.0"}
FALLBACK_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


def _versions() -> tuple[str, ...]:
    try:
        from mcp import types as mcp_types

        latest = getattr(mcp_types, "LATEST_PROTOCOL_VERSION", None)
        supported = tuple(getattr(mcp_types, "SUPPORTED_PROTOCOL_VERSIONS", ()) or ())
    except Exception:  # the SDK is optional at runtime
        latest, supported = None, ()
    out = tuple(dict.fromkeys([v for v in (latest, *supported, *FALLBACK_VERSIONS) if isinstance(v, str)]))
    return out or FALLBACK_VERSIONS


PROTOCOL_VERSIONS = _versions()


# ─── Tool definitions ───


def _obj(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


_ROOM = {"type": "string", "description": "The caller's room number, only when the call is not already tied to a room. "
                                          "Ask for it and repeat it back first."}
TOOLS: list[dict] = [
    {
        "name": "create_ticket",
        "description": "File a request for hotel staff (towels, cleaning, maintenance, food, luggage, a wake-up call...). "
                       "Read the items and quantities back to the caller first.",
        "inputSchema": _obj({
            "request_type": {"type": "string", "description": "A short key such as towels, water, make_up_room, ac, "
                                                              "plumbing, luggage, wake_up, front_desk, or other."},
            "description_en": {"type": "string", "description": "What is needed, in English, with quantities and times."},
            "original_text": {"type": "string", "description": "The caller's own words."},
            "language": {"type": "string", "description": "The caller's language code, for example en or ar."},
            "priority": {"type": "string", "enum": ["low", "medium", "high"]},
            "sensitivity": {"type": "string", "enum": ["none", "handoff", "sensitive"],
                            "description": "sensitive for harassment, theft, a lost passport or anything private."},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 20},
            "scheduled_for": {"type": "string", "description": "When, if not now (hotel local time)."},
            "room": _ROOM,
        }, ["request_type", "description_en"]),
    },
    {
        "name": "escalate_to_human",
        "description": "Hand the caller to a person: complaints, billing, legal questions, an emergency, an unknown "
                       "answer, or the caller asking for a human.",
        "inputSchema": _obj({
            "reason": {"type": "string", "enum": ["unknown_answer", "complaint", "refund_or_billing", "legal", "emergency",
                                                  "asked_for_human", "other"]},
            "urgency": {"type": "string", "enum": ["low", "medium", "high"]},
            "summary_en": {"type": "string"},
            "original_text": {"type": "string"},
            "room": _ROOM,
        }, ["reason", "summary_en"]),
    },
    {
        "name": "get_hotel_info",
        "description": "Look up approved hotel information (hours, facilities, policies, local places). Answer only from it.",
        "inputSchema": _obj({"query": {"type": "string"}}, ["query"]),
    },
    {
        "name": "get_request_status",
        "description": "The status of requests already made from this room.",
        "inputSchema": _obj({"request_ref": {"type": "string"}}, []),
    },
    {
        "name": "place_order",
        "description": "Order food or drinks to the room. First call with confirm false to get the priced total, read it "
                       "back, and only after the caller says yes call again with the same lines and confirm true.",
        "inputSchema": _obj({
            "outlet_key": {"type": "string"},
            "lines": {"type": "array", "maxItems": 20, "items": _obj({
                "menu_item_id": {"type": "integer"}, "qty": {"type": "integer", "minimum": 1, "maximum": 20},
            }, ["menu_item_id", "qty"])},
            "notes": {"type": "string"},
            "confirm": {"type": "boolean"},
            "room": _ROOM,
        }, ["lines", "confirm"]),
    },
    {
        "name": "transfer_to_front_desk",
        "description": "Transfer this call to a person at the front desk.",
        "inputSchema": _obj({"reason": {"type": "string"}}, []),
    },
]
TOOL_NAMES = tuple(t["name"] for t in TOOLS)
assert set(TOOL_NAMES) == set(C.MCP_TOOLS)


# ─── JSON-RPC ───


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _ok(msg_id: Any, res: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": res}


def for_model(out: dict) -> dict:
    """What the model sees: the tracking tokens and client hints stay here."""
    return {k: v for k, v in out.items() if k not in ("ui",)}


def call_tool(claims: dict, name: str, arguments: dict) -> dict:
    """tools/call for the call named by the token (sync; threadpool)."""
    with db.db() as cur:
        call = calls.get_call(cur, int(claims["call"]))
        if call is None or call["provider_call_id"] != claims["call_id"] or call["hotel_slug"] != claims["hotel"]:
            raise ClefsError(403, "Token does not match this call", "call_mismatch")
        out = executor.run(cur, call, name, arguments, transport="mcp", claims_room=claims.get("room"))
    return for_model(out)


def handle(claims: dict, msg: Any) -> dict | None:
    """One JSON-RPC message -> its response (None for a notification)."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid Request")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg or method.startswith("notifications/"):
        return None
    if method == "initialize":
        asked = str(params.get("protocolVersion") or "")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _ok(msg_id, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": SERVER_INFO,
                            "instructions": "Hotel phone tools. The room and hotel come from the call, not from arguments."})
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name not in TOOL_NAMES:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "arguments must be an object")
        try:
            out = call_tool(claims, name, args)
        except ClefsError as exc:
            return _error(msg_id, -32001, f"{exc.code}: {exc.detail}")
        failed = out.get("status") in ("error", "call_ended", "unknown_tool")
        return _ok(msg_id, {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                            "structuredContent": out, "isError": failed})
    return _error(msg_id, -32601, f"Method not found: {method}")


# ─── Routes ───


async def _body(request: Request) -> Any:
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > BODY_MAX:
            raise ClefsError(413, "MCP body too large", "payload_too_large")
    try:
        return json.loads(raw or b"null")
    except ValueError:
        return ...


@router.post("/webhooks/voice/mcp")
async def mcp_post(request: Request) -> Response:
    """MCP Streamable HTTP, stateless JSON responses. 401 invalid_call_token
    without a valid per-call JWT (Authorization: Bearer <jwt>)."""
    claims = calls.verify_call_token(request.headers.get("authorization"))
    body = await _body(request)
    if body is ...:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    batch = body if isinstance(body, list) else [body]
    if not batch:
        return JSONResponse(_error(None, -32600, "Invalid Request"), status_code=400)
    replies = []
    for msg in batch:
        reply = await run_in_threadpool(handle, claims, msg)
        if reply is not None:
            replies.append(reply)
    if not replies:
        return Response(status_code=202)
    return JSONResponse(replies if isinstance(body, list) else replies[0])


@router.get("/webhooks/voice/mcp")
async def mcp_get() -> Response:
    """No server-initiated stream in stateless mode (MCP: 405 is allowed)."""
    return Response(status_code=405, headers={"Allow": "POST"})
