"""
In-room phone AI constants (03 spec 3). One place for the limits and the
vocabulary the webhook, the MCP server, the worker and the console share.
"""

from __future__ import annotations

import os

# ─── Webhook ───

# A SIP 486 (Busy Here) lets the PBX fail over to the operator, so every
# refusal (unidentified caller, disabled line, over capacity, after hours)
# uses it rather than 403/603.
REJECT_CODE = 486
LINE_HEADER = "x-clefs-line"
WEBHOOK_BODY_MAX = 256 * 1024
SIGNATURE_HEADER = "x-clefs-signature"      # sha256=<hex HMAC-SHA256 of "<webhook id>.<timestamp>.<raw body>">
TIMESTAMP_HEADER = "x-clefs-timestamp"      # unix seconds, signed; outside SIGNATURE_TOLERANCE_S is a replay
SIGNATURE_TOLERANCE_S = 300
WEBHOOK_ID_HEADER = "x-clefs-webhook-id"    # dedupe key (falls back to the event id)
ACCEPT_TIMEOUT_S = 3.0          # 03 spec 3.2 step 4: accept inline within 3 s
RECEIPT_RETENTION_DAYS = 30
EXTENSION_CALLS_PER_HOUR = 10   # per extension per line (03 spec 3.2 step 3)
LIVE_STATUSES = ("accepting", "accepted", "active")

# SIP headers kept on the call row. Everything else (tokens a bridge might
# add, auth headers) is dropped, and the line header's value never stored.
KEPT_HEADERS = ("from", "to", "via", "contact", "call-id", "user-agent", "p-asserted-identity", "remote-party-id", "diversion")

# ─── Lines ───

PBX_KINDS = ("grandstream_ucm", "3cx", "asterisk", "freeswitch", "fxo_gateway", "bridge", "other")
TOOL_TRANSPORTS = ("mcp", "sideband")
CALLER_ID_MODES = ("extension", "none")
MAX_CALL_S_LIMIT = 7200
# The SIP domain of the telephony bridge the PBX trunk dials (Gemini Live has
# no SIP endpoint; the bridge turns the call into a media WebSocket).
BRIDGE_SIP_HOST = os.environ.get("VOICE_SIP_HOST") or "sip.voice.clefs.app"
EXTENSION_KINDS = ("room", "location", "staff", "other")
CSV_MAX_ROWS = 5000

# ─── Tools ───

# The tools the phone model may call over MCP (03 spec 3.2 allowed_tools),
# plus escalate_to_human: the concierge prompt's disclosure and policy
# blocks tell the model to use it for "I want a person" and complaints, so a
# phone without it would be told to call a tool it does not have.
MCP_TOOLS = (
    "create_ticket",
    "escalate_to_human",
    "get_hotel_info",
    "get_request_status",
    "place_order",
    "transfer_to_front_desk",
)
# Phone tool name -> the concierge executor's tool name (one executor for
# every surface, 03 spec 0.3).
CONCIERGE_TOOL = {
    "create_ticket": "notify_staff",
    "escalate_to_human": "escalate_to_human",
    "get_hotel_info": "search_hotel_info",
    "get_request_status": "get_request_status",
    "place_order": "place_order",
    "get_menu": "get_menu",
    "notify_staff": "notify_staff",
    "search_hotel_info": "search_hotel_info",
}
# Tools whose effect must not repeat when a model or the MCP client retries.
SIDE_EFFECT_TOOLS = frozenset({"create_ticket", "notify_staff", "escalate_to_human", "place_order", "transfer_to_front_desk"})
# MCP retries carry no stable call id, so an identical side-effect call
# within this bucket is treated as the same call (Service also merges a
# same-room, same-type request inside its own window).
MCP_DEDUPE_BUCKET_S = 120

# ─── Gemini Live ───

DEFAULT_VOICE = "Kore"
GEMINI_VOICES = ("Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr")
# Older line/session voice names -> the closest Gemini prebuilt voice.
VOICE_ALIASES = {
    "alloy": "Kore", "marin": "Kore", "cedar": "Charon", "shimmer": "Aoede", "ash": "Fenrir",
    "ballad": "Leda", "coral": "Zephyr", "echo": "Puck", "sage": "Orus", "verse": "Puck",
}
INPUT_AUDIO_MIME = "audio/pcm;rate=16000"   # caller audio into Gemini Live: PCM16 16 kHz mono
OUTPUT_AUDIO_RATE = 24000                   # Gemini Live speaks PCM16 24 kHz mono


def gemini_voice(name: str | None) -> str:
    """A Gemini prebuilt voice for a configured voice name (any case)."""
    raw = (name or "").strip()
    for v in GEMINI_VOICES:
        if raw.lower() == v.lower():
            return v
    return VOICE_ALIASES.get(raw.lower(), DEFAULT_VOICE)


# ─── JWT ───

JWT_AUDIENCE = "clefs-voice-mcp"
JWT_ISSUER = "clefs"
JWT_GRACE_S = 120                # beyond max_call_s, for a call that runs to the limit

# ─── Worker ───

LEASE_S = 60
LEASE_RENEW_S = 20
POLL_S = 2.0
MAX_CALLS_PER_WORKER = 20

# ─── Emergency numbers ───
# The numbers a caller is told to dial are the hotel country's, from
# concierge.api.emergency_numbers (the list the web assistant and the guest
# EmergencyCard use: JP 119 fire and ambulance, 110 police; AE 999 police,
# 998 ambulance, 997 civil defence, 996 coast guard). A call reads out the
# life-safety services below, in the country's order (most used first);
# utility lines (the UAE's 991) and search and rescue are left out of a
# spoken list. A country with no list gets NO_NUMBERS instead of a number
# that may be wrong there.
DIAL_KINDS: dict[str, str] = {
    "emergency": "any emergency",
    "fire_ambulance": "fire or an ambulance",
    "ambulance": "an ambulance",
    "fire": "fire",
    "civil_defence": "fire or rescue",
    "police": "the police",
    "coast_guard": "the coast guard",
}
NO_NUMBERS = "the local emergency services"
