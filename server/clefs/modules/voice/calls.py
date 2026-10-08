"""
Phone calls: the per-call JWT, the phone prompt variant and the accept
payload (03 spec 3.2 step 4, 3.3, 3.5), plus the vc_calls helpers the
webhook, the MCP server and the worker share.

The session itself comes from concierge.api.build_surface_session(surface=
'phone'): one server-built prompt for every surface. Voice appends only the
phone block (opening line, room confirmation, read-backs, the emergency dial
sequence) and the transport-specific tool wiring.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import timedelta
from typing import Any

import jwt

from clefs.core import clock
from clefs.core.errors import unauthorized
from clefs.settings import env, get_settings
from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.calls")

MCP_PATH = "/webhooks/voice/mcp"
LANGUAGE_NAMES = {"en": "English", "ar": "Arabic", "hi": "Hindi", "ur": "Urdu", "ru": "Russian", "fr": "French",
                  "de": "German", "zh": "Chinese", "tl": "Filipino", "ml": "Malayalam"}


# ─── Per-call JWT (MCP authorization) ───


def _jwt_key() -> bytes:
    """A key derived from SECRET_KEY for this one purpose, so a token minted
    here is useless anywhere else that also signs with SECRET_KEY."""
    return hmac.new(get_settings().SECRET_KEY.encode(), b"clefs-voice-mcp-v1", hashlib.sha256).digest()


def mint_call_token(call: dict, *, max_call_s: int) -> str:
    """{hotel, call (vc_calls.id), call_id (bridge), room|null, exp}. The
    room is the extension-mapped room only: a spoken room is never in the
    token, it lives on the call row once confirmed (sticky)."""
    now = clock.now()
    claims = {
        "iss": C.JWT_ISSUER,
        "aud": C.JWT_AUDIENCE,
        "hotel": call["hotel_slug"],
        "call": int(call["id"]),
        "call_id": call["provider_call_id"],
        "room": call.get("room") if call.get("room_confirmed_by") == "extension" else None,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=int(max_call_s) + C.JWT_GRACE_S)).timestamp()),
    }
    return jwt.encode(claims, _jwt_key(), algorithm="HS256")


def verify_call_token(token: str | None) -> dict:
    """Claims of a valid token, else 401 invalid_call_token. Expiry is
    checked against clefs.core.clock (frozen in tests), not the wall clock."""
    raw = (token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    if not raw:
        raise unauthorized("Call token required", "invalid_call_token")
    try:
        claims = jwt.decode(
            raw, _jwt_key(), algorithms=["HS256"], audience=C.JWT_AUDIENCE, issuer=C.JWT_ISSUER,
            options={"verify_exp": False, "require": ["exp", "hotel", "call", "call_id"]},
        )
    except jwt.PyJWTError:
        raise unauthorized("Call token invalid", "invalid_call_token") from None
    if int(claims["exp"]) <= int(clock.now().timestamp()):
        raise unauthorized("Call token expired", "invalid_call_token")
    return claims


# ─── Call rows ───


def get_call(cur: Any, call_row_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT * FROM vc_calls WHERE id = %s" + (" FOR UPDATE" if lock else ""), (int(call_row_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def call_by_provider_id(cur: Any, call_id: str) -> dict | None:
    cur.execute("SELECT * FROM vc_calls WHERE provider_call_id = %s", (call_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def is_live(call: dict) -> bool:
    return call.get("status") in C.LIVE_STATUSES and call.get("ended_at") is None


def mark_ended(cur: Any, call: dict, *, reason: str, at=None) -> dict:
    """Record the end once (the worker's socket close, a hangup, a call-end
    event, whichever comes first)."""
    at = at or clock.now()
    cur.execute(
        """
        UPDATE vc_calls SET status = 'ended', ended_at = COALESCE(ended_at, %s), end_reason = COALESCE(end_reason, %s),
               duration_s = COALESCE(duration_s, GREATEST(0, EXTRACT(EPOCH FROM (%s - COALESCE(accepted_at, started_at)))::int)),
               worker_lease_until = NULL
        WHERE id = %s RETURNING *
        """,
        (at, reason, at, call["id"]),
    )
    return dict(cur.fetchone())


# ─── Phone prompt variant (03 spec 3.5) ───

TRANSFER_TOOL = {
    "type": "function",
    "name": "transfer_to_front_desk",
    "description": "Transfer this phone call to a person at the front desk. Use when the caller asks for a person, "
                   "after an emergency escalation if they want it, or when you cannot help.",
    "parameters": {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Why, in a few English words."}},
        "required": [],
        "additionalProperties": False,
    },
}


def _dial_sequence(prefix: str | None) -> str:
    n = C.EMERGENCY_NUMBERS
    if prefix:
        return (f"dial {prefix} for an outside line, then {n['police']} for police, {n['ambulance']} for an ambulance "
                f"or {n['fire']} for fire")
    return (f"dial the outside line, then {n['police']} for police, {n['ambulance']} for an ambulance "
            f"or {n['fire']} for fire")


def phone_block(*, hotel_name: str, language: str, room: str | None, room_from_extension: bool,
                outside_line_prefix: str | None, transport: str) -> str:
    """The rules the web prompt does not carry. No URLs, spelled-out numbers
    and read-backs because the caller only hears the answer."""
    lang_name = LANGUAGE_NAMES.get((language or "en").split("-")[0], "English")
    lines = [
        "PHONE CALL:",
        f'Open in {lang_name}: "Hello, this is the {hotel_name} assistant. I am an AI and this call is recorded. '
        'How can I help?" Switch to the caller\'s language as soon as they speak another one.',
    ]
    if room and room_from_extension:
        lines.append(f'The call comes from the phone in room {room}. Before the first request, confirm: "That is for '
                     f'room {room}, correct?" If the caller says another room, ask them to call from that room or '
                     "connect them to the front desk.")
    else:
        lines.append("The phone did not tell us the room. Ask for the room number and repeat it back before filing "
                     "anything, then pass it as room on the first request; later requests use the same room.")
    lines += [
        "Read back items and quantities before you file a request or place an order.",
        "Never read out web addresses, links or email addresses. Say numbers as words, for example five hundred and four.",
    ]
    if transport == "mcp":
        lines.append("On this call your tools are named: create_ticket (files a request: use it wherever these rules say "
                     "notify_staff), get_hotel_info (answers from approved hotel information: use it wherever they say "
                     "search_hotel_info), escalate_to_human, get_request_status, place_order and transfer_to_front_desk.")
    else:
        lines.append("transfer_to_front_desk connects the caller to a person at the front desk.")
    lines += [
        f"EMERGENCY (fire, medical, violence, someone in danger): 1. tell the caller to {_dial_sequence(outside_line_prefix)}. "
        "2. Escalate at once (escalate_to_human with reason emergency). 3. Offer to transfer them to the front desk.",
    ]
    return "\n".join(lines)


def phone_prompt_version(base_version: str, block_template: str, transport: str) -> str:
    return hashlib.sha256(f"{base_version}|{transport}|{block_template}".encode()).hexdigest()[:16]


# ─── Accept payload ───


def mcp_server_url() -> str:
    return get_settings().PUBLIC_API_ORIGIN.rstrip("/") + MCP_PATH


def _function_declaration(tool: dict) -> dict | None:
    """A function tool (flat {"type": "function", name, description,
    parameters} or nested {"function": {...}}) as a Gemini function
    declaration."""
    fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
    name = fn.get("name")
    if not name:
        return None
    decl = {"name": name, "description": fn.get("description") or ""}
    if fn.get("parameters"):
        decl["parameters"] = fn["parameters"]
    return decl


def live_config(*, instructions: str, voice: str | None, tools: list[dict]) -> dict:
    """The Gemini Live connect config (client.aio.live.connect(config=...)):
    audio out, native input/output transcription, the phone tools."""
    decls = [d for d in (_function_declaration(t) for t in tools if isinstance(t, dict)) if d]
    config: dict[str, Any] = {
        "response_modalities": ["AUDIO"],
        "system_instruction": instructions,
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": C.gemini_voice(voice)}}},
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }
    if decls:
        config["tools"] = [{"function_declarations": decls}]
    return config


def accept_body(spec: Any, *, instructions: str, line: dict, token: str | None) -> dict:
    """The body of POST {bridge}/calls/{id}/accept, also stored on the call
    row for the worker. The bridge streams the call's audio over the media
    WebSocket; the worker opens the Gemini Live session with `live_config`
    and runs every function call through the shared executor. MCP lines also
    carry our MCP server and the per-call JWT, for a bridge that runs the
    phone tools itself."""
    s = get_settings()
    transport = line.get("tool_transport") or "mcp"
    tools = [t for t in (spec.tools or []) if isinstance(t, dict)] + [TRANSFER_TOOL]
    body: dict[str, Any] = {
        "type": "gemini_live",
        "model": line.get("model") or env("VOICE_REALTIME_MODEL") or s.REALTIME_MODEL,
        "instructions": instructions,
        "voice": C.gemini_voice(line.get("voice") or spec.voice),
        "audio": {"input": C.INPUT_AUDIO_MIME, "output": f"audio/pcm;rate={C.OUTPUT_AUDIO_RATE}"},
        "tools": tools,
    }
    if transport == "mcp":
        body["mcp"] = {
            "server_label": "clefs",
            "server_url": mcp_server_url(),
            "authorization": token,
            "allowed_tools": list(C.MCP_TOOLS),
        }
    return body
