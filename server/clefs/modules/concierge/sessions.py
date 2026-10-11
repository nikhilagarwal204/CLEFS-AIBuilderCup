"""
Concierge sessions: target resolution, consent, rate limits and minting
(03 spec 2.2 and 2.3).

A session is the security boundary. The room is bound here, from the printed
QR key (?k=) or a stay token minted from one, and every tool call reads it
from cc_sessions, never from model arguments. The browser gets a bearer
session token (only its sha256 is stored) and, for voice, either a
single-use Gemini Live ephemeral token locked to the server-built
instructions and tools (Gemini Developer API), or, with LIVE_VIA_VERTEX, a
relay URL and a single-use relay token: the server then holds the Live
session on Vertex AI with those instructions and tools (live_relay.py).
Clients cannot send instructions or tools: an old cached client that posts
{session} gets 409 client_outdated ("please refresh").

Limited mode: a legacy QR code without a key (or a rotated key) still works,
but the session is unverified: no personal context, and requests say
"confirm the room at the door".
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from clefs import ratelimit
from clefs.auth import tokens
from clefs.core import clock
from clefs.core import consent as consent_mod
from clefs.core import rooms as rooms_mod
from clefs.core import time as hotel_time
from clefs.core.errors import ClefsError, bad_request, not_found, too_many, unauthorized
from clefs.core.hotels import Hotel
from clefs.db import Json
from clefs.modules.concierge import constants as C
from clefs.settings import env, get_settings

log = logging.getLogger("clefs.concierge.sessions")

STAY_TICKET_PURPOSE = "cc_stay"
SESSION_COLUMNS = (
    "id, hotel_slug, surface, channel, room, location_slug, verified, key_status, language, mode, transport, "
    "consent_ref, consents, store_transcripts, prompt_version, model, call_ref, turn_count, tool_calls, "
    "requests_created, handoffs, unknowns, emergencies, orders, created_at, expires_at, last_seen_at, ended_at, end_reason"
)


# ─── Hashing ───


def ip_hash(ip: str | None) -> bytes | None:
    """Keyed hash of the caller's address: rate limits and abuse review work,
    and no raw IP is stored (PDPL)."""
    if not ip:
        return None
    key = hashlib.sha256(("cc-ip:" + get_settings().SECRET_KEY).encode()).digest()
    return hmac.new(key, ip.encode(), hashlib.sha256).digest()


def guest_subject_ref(guest_ref: str) -> str:
    """consents.subject_ref for a guest device: a hash of the random
    guest_ref the browser keeps, never a raw identifier."""
    return "ccg:" + hashlib.sha256(("cc-guest:" + guest_ref).encode()).hexdigest()[:40]


# ─── Target resolution ───


class Target:
    """What a QR scan resolved to."""

    def __init__(self, *, surface: str, room: str | None, location: dict | None, verified: bool, key_status: str, qr_version: int = 1):
        self.surface = surface
        self.room = room
        self.location = location
        self.verified = verified
        self.key_status = key_status
        self.qr_version = qr_version


def mint_stay_token(hotel_slug: str, room: str, qr_version: int) -> str:
    """A signed "this device scanned a valid QR for this room" ticket
    (12 h). Rotating the room's QR key version invalidates it."""
    return tokens.mint_ticket(STAY_TICKET_PURPOSE, {"h": hotel_slug, "r": room, "v": qr_version}, ttl_s=C.STAY_TOKEN_TTL_S)


def _stay_token_ok(token: str | None, hotel_slug: str, room: str, qr_version: int) -> bool:
    if not token:
        return False
    claims = tokens.verify_ticket(STAY_TICKET_PURPOSE, token)
    return bool(claims) and claims.get("h") == hotel_slug and claims.get("r") == room and claims.get("v") == qr_version


def resolve_target(
    cur: Any, hotel: Hotel, *, room: str | None = None, location: str | None = None, k: str | None = None, stay_token: str | None = None
) -> Target:
    """Room or venue, and whether this device proved it scanned that room's
    current QR. 404 for a room the register does not have (or an inactive
    one) and for an unknown venue, so a guessed URL learns nothing more."""
    if bool(room) == bool(location):
        raise bad_request("give exactly one of room or location", "invalid_target")
    if location:
        cur.execute(
            "SELECT slug, name, type, custom_instructions FROM hotel_locations WHERE hotel_slug = %s AND slug = %s",
            (hotel.slug, location),
        )
        row = cur.fetchone()
        if row is None:
            raise not_found("Location not found", "location_not_found")
        return Target(surface="web_location", room=None, location=dict(row), verified=False, key_status="absent")
    if not rooms_mod.valid_room(room or ""):
        raise not_found("Room not found", "room_not_found")
    registered = rooms_mod.get_room(cur, hotel.slug, room)
    if registered is None and rooms_mod.has_register(cur, hotel.slug):
        raise not_found("Room not found", "room_not_found")
    if registered is not None and not registered["is_active"]:
        raise not_found("Room not found", "room_not_found")
    version = int(registered["qr_key_version"]) if registered else 1
    if k and rooms_mod.verify_qr_key(hotel.slug, room, version, k):
        return Target(surface="web_room", room=room, location=None, verified=True, key_status="valid", qr_version=version)
    if _stay_token_ok(stay_token, hotel.slug, room, version):
        return Target(surface="web_room", room=room, location=None, verified=True, key_status="stay_token", qr_version=version)
    return Target(surface="web_room", room=room, location=None, verified=False, key_status="invalid" if k else "absent", qr_version=version)


# ─── Consent (03 spec 2.3, Canon PDPL) ───


def required_consents(mode: str) -> tuple[str, ...]:
    """ai_processing and transfer_abroad before the AI runs at all; voice
    also needs recording (a text-only guest may decline it)."""
    return C.VOICE_REQUIRED_CONSENTS if mode == "voice" else C.REQUIRED_CONSENTS


def sheet_text(cur: Any, hotel_slug: str, purpose: str, language: str) -> dict | None:
    """The wording the concierge sheet shows for a purpose: the hotel's own
    current text first, else the platform's concierge version. Pinned to
    CONSENT_TEXT_VERSION so another module's newer platform wording for the
    same purpose never replaces what this sheet shows and records."""
    cur.execute(
        """
        SELECT id, hotel_slug, purpose, version, language, body FROM consent_texts
        WHERE purpose = %s AND language = %s AND retired_at IS NULL
          AND (hotel_slug = %s OR (hotel_slug IS NULL AND version = %s))
        ORDER BY (hotel_slug IS NULL), created_at DESC, id DESC LIMIT 1
        """,
        (purpose, language, hotel_slug, C.CONSENT_TEXT_VERSION),
    )
    row = cur.fetchone()
    if row is None and language != "en":
        return sheet_text(cur, hotel_slug, purpose, "en")
    return dict(row) if row else None


def record_grants(cur: Any, hotel_slug: str, subject_ref: str, grants: dict[str, bool], *, language: str, evidence: dict) -> None:
    """Append the guest's choices from the consent sheet, each linked to the
    exact wording the sheet showed (sheet_text) in that language."""
    lang = (language or "en").split("-")[0]
    for purpose, granted in grants.items():
        if purpose not in C.CONSENT_PURPOSES:
            raise bad_request(f"unknown consent purpose {purpose!r}", "invalid_consent_purpose")
        text = sheet_text(cur, hotel_slug, purpose, lang)
        consent_mod.record_consent(
            cur,
            hotel_slug,
            subject_ref,
            purpose,
            bool(granted),
            source="concierge_consent_sheet",
            evidence=evidence,
            text_version=text["version"] if text else None,
            language=text["language"] if text else "en",
        )


def consent_flags(cur: Any, hotel_slug: str, subject_ref: str) -> dict[str, bool]:
    state = consent_mod.consent_state(cur, hotel_slug, subject_ref)
    return {p: bool((state.get(p) or {}).get("granted")) for p in C.CONSENT_PURPOSES}


def consent_texts(cur: Any, hotel_slug: str, language: str | None) -> dict[str, dict]:
    """{purpose: {version, language, text, required, required_for_voice}}
    for the consent sheet, in the guest's language when written (every
    guest language at concierge-2), else English. "{hotel}" becomes the
    hotel's name."""
    from clefs.core.hotels import get_hotel

    hotel = get_hotel(cur, hotel_slug)
    lang = (language or "en").split("-")[0]
    out: dict[str, dict] = {}
    for purpose in C.CONSENT_PURPOSES:
        row = sheet_text(cur, hotel_slug, purpose, lang)
        if row is None:
            continue
        out[purpose] = {
            "version": row["version"],
            "language": row["language"],
            "text": row["body"].replace("{hotel}", hotel.name),
            "required": purpose in C.REQUIRED_CONSENTS,
            "required_for_voice": purpose in C.VOICE_REQUIRED_CONSENTS,
        }
    return out


# ─── Rate limits (03 spec 2.2) ───


def check_mint_limits(cur: Any, hotel: Hotel, *, ip_digest: bytes | None, room: str | None, location: str | None, daily_cap: int) -> None:
    """Per IP hash 30/h, per room (or venue) 10/10 min, per hotel a daily
    cap in the hotel's local day. Any one exceeded is a 429."""
    target = f"room:{room}" if room else f"loc:{location}"
    checks = [
        (f"cc:mint:{hotel.slug}:{target}", C.MINT_PER_ROOM_LIMIT, C.MINT_PER_ROOM_WINDOW_S, "Too many sessions for this room, try again in a few minutes"),
        (f"cc:mint:day:{hotel.slug}:{hotel_time.local_date(hotel).isoformat()}", daily_cap, 86_400, "The assistant is busy, please call the front desk"),
    ]
    if ip_digest:
        check_ip_mint_limit(cur, ip_digest)
    for key, limit, window, message in checks:
        if limit <= 0 or not ratelimit.hit(cur, key, limit=limit, window_s=window):
            raise too_many(message, "session_rate_limited")


def check_ip_mint_limit(cur: Any, ip_digest: bytes | None) -> None:
    """The per-address mint cap on its own, so start_session can run it
    before resolve_target: an address over its cap gets a 429 and never
    learns whether the room it named exists (404 vs 200)."""
    if ip_digest and not ratelimit.hit(cur, f"cc:mint:ip:{ip_digest.hex()[:32]}", limit=C.MINT_PER_IP_LIMIT,
                                       window_s=C.MINT_PER_IP_WINDOW_S):
        raise too_many("Too many sessions, try again later", "session_rate_limited")


# ─── Gemini Live ephemeral token ───
# google-genai auth_tokens.create (v1alpha) with the whole Live config locked
# in live_connect_constraints: the browser only ever holds a single-use
# ephemeral token, never the API key, and the instructions, tools, model and
# voice are the server's. Tests swap the transport.

MintTransport = Callable[[dict], dict]
_transport: MintTransport | None = None

GEMINI_VOICES = ("Kore", "Puck", "Charon", "Aoede", "Fenrir", "Leda", "Orus", "Zephyr")
# Legacy voice names a hotel's concierge.voice setting may still hold.
_VOICE_MAP = {
    "alloy": "Kore", "marin": "Kore", "coral": "Leda", "shimmer": "Aoede", "nova": "Leda", "fable": "Aoede",
    "cedar": "Charon", "onyx": "Charon", "ash": "Orus", "ballad": "Fenrir", "echo": "Puck", "verse": "Puck",
    "sage": "Zephyr",
}


def set_mint_transport(fn: MintTransport | None) -> None:
    """Tests: fn(token_config) -> {value, expires_at}, or raise."""
    global _transport
    _transport = fn


def gemini_voice(voice: str | None) -> str:
    """A Gemini prebuilt voice for the hotel's setting (Kore by default)."""
    v = (voice or "").strip()
    for name in GEMINI_VOICES:
        if v.lower() == name.lower():
            return name
    return _VOICE_MAP.get(v.lower(), C.DEFAULT_VOICE)


def function_declarations(tools: list[dict] | None) -> list[dict]:
    """The server's tool schemas as Gemini function declarations."""
    out: list[dict] = []
    for t in tools or []:
        fn = t.get("function") if isinstance(t.get("function"), dict) else t
        if not fn.get("name"):
            continue
        decl: dict[str, Any] = {"name": fn["name"], "description": fn.get("description") or ""}
        if fn.get("parameters"):
            decl["parameters"] = fn["parameters"]
        out.append(decl)
    return out


def live_connect_config(spec: Any, *, mode: str) -> dict:
    """The Live session config the token is locked to. Native audio models
    answer in AUDIO only; typed turns read the output transcription."""
    config: dict[str, Any] = {
        "session_resumption": {},
        "response_modalities": ["AUDIO"],
        "system_instruction": spec.instructions,
        "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": gemini_voice(spec.voice)}}},
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }
    decls = function_declarations(spec.tools)
    if decls:
        config["tools"] = [{"function_declarations": decls}]
    return config


def live_token_config(spec: Any, *, mode: str) -> dict:
    now = clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return {
        "uses": 1,
        "expire_time": now + timedelta(seconds=C.CLIENT_SECRET_TTL_S),
        "new_session_expire_time": now + timedelta(seconds=C.NEW_SESSION_WINDOW_S),
        "live_connect_constraints": {"model": spec.model, "config": live_connect_config(spec, mode=mode)},
    }


def _gemini_mint(config: dict) -> dict:
    from google import genai

    # Ephemeral Live tokens exist only on the Gemini Developer API. vertexai=False
    # keeps this client there when GOOGLE_GENAI_USE_VERTEXAI (read by the SDK
    # from the environment) sends the rest of the AI through Vertex AI.
    client = genai.Client(vertexai=False, api_key=get_settings().GEMINI_API_KEY, http_options={"api_version": "v1alpha"})
    token = client.auth_tokens.create(config=config)
    return {"value": token.name, "expires_at": config["expire_time"].isoformat()}


def mint_client_secret(spec: Any, *, mode: str) -> dict:
    """{value, expires_at, model} of a Gemini Live ephemeral token. 409
    ai_disabled when the AI is switched off or has no key (the page then
    offers the front desk)."""
    config = live_token_config(spec, mode=mode)
    if _transport is not None:
        data = _transport(config)
    else:
        s = get_settings()
        if not s.AI_ENABLED or not s.GEMINI_API_KEY:
            raise ClefsError(409, "The assistant is not available right now", "ai_disabled")
        try:
            data = _gemini_mint(config)
        except ClefsError:
            raise
        except Exception as exc:  # network, auth, quota
            # Never log the config (instructions) or any key: the type only.
            log.warning("live token mint error: %s", type(exc).__name__)
            raise ClefsError(502, "Could not start the assistant, please try again", "live_mint_failed") from exc
    value = (data or {}).get("value")
    if not value:
        raise ClefsError(502, "Could not start the assistant, please try again", "live_mint_failed")
    return {"value": value, "expires_at": data.get("expires_at"), "model": spec.model}


# ─── Vertex AI Live relay (05 spec, Voice relay (F)) ───
# Vertex AI has no ephemeral tokens and the browser never holds a Google
# credential, so with LIVE_VIA_VERTEX a live mint returns a relay URL and a
# single-use relay token instead. live_relay.py checks the token, rebuilds
# this session's spec (spec_for_session) and holds the Vertex AI Live session
# as the service identity. The relay needs no new table: the token is
# signed, its single use is a rate-limit row, and the spec is rebuilt from
# the cc_sessions row seconds after the mint.

RELAY_TICKET_PURPOSE = "cc_live_relay"
RELAY_PATH = "/public/concierge/live/{session_id}"
# A Live model Vertex AI serves from a regional endpoint (not "global"):
# gemini-3.8-live answers in us-central1; gemini-live-2.5-flash-native-audio
# also works there and in europe-west4. LIVE_VERTEX_MODEL overrides it.
DEFAULT_LIVE_VERTEX_MODEL = "gemini-3.8-live"


def live_via_vertex() -> bool:
    """LIVE_VIA_VERTEX: guest voice through the server's Vertex AI Live relay
    instead of browser ephemeral tokens. Default: on when Vertex AI is
    (GOOGLE_GENAI_USE_VERTEXAI), since its Live API has no ephemeral tokens."""
    raw = env("LIVE_VIA_VERTEX")
    if raw is None:
        return bool(get_settings().GOOGLE_GENAI_USE_VERTEXAI)
    return raw.strip().lower() in ("1", "true", "yes", "on")


def relay_model() -> str:
    """The Vertex AI Live model id (LIVE_VERTEX_MODEL); REALTIME_MODEL keeps
    naming the Developer API model of the ephemeral-token path."""
    return (env("LIVE_VERTEX_MODEL") or DEFAULT_LIVE_VERTEX_MODEL).strip()


def relay_url(session_id: str) -> str:
    """wss://<api>/public/concierge/live/{id}: the API's own origin
    (PUBLIC_API_ORIGIN, or LIVE_RELAY_ORIGIN for a relay deployed apart),
    because the web app's same-origin proxy cannot carry a WebSocket."""
    base = (env("LIVE_RELAY_ORIGIN") or get_settings().PUBLIC_API_ORIGIN or "").strip().rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://"):]
    return base + RELAY_PATH.format(session_id=session_id)


def mint_relay_token(session_id: str, hotel_slug: str) -> tuple[str, str]:
    """(token, expires_at ISO): a signed ticket for one session that must
    open its relay within NEW_SESSION_WINDOW_S, like the ephemeral token's
    new-session window. Tickets run on wall time, so the expiry does too."""
    ttl = C.NEW_SESSION_WINDOW_S
    token = tokens.mint_ticket(RELAY_TICKET_PURPOSE, {"s": session_id, "h": hotel_slug}, ttl_s=ttl)
    return token, datetime.fromtimestamp(int(time.time()) + ttl, tz=timezone.utc).isoformat()


def verify_relay_token(token: str | None, session_id: str) -> dict | None:
    """The token's claims when it is genuine, unexpired and for this session."""
    claims = tokens.verify_ticket(RELAY_TICKET_PURPOSE, token) if token else None
    if not claims or claims.get("s") != str(session_id):
        return None
    return claims


def claim_relay(cur: Any, session_id: str) -> bool:
    """Single use, like the ephemeral token (uses=1): True only the first
    time this session's relay opens, on whichever instance it lands."""
    return ratelimit.hit(cur, f"cc:relay:{session_id}", limit=1, window_s=C.SESSION_TTL_S)


def spec_for_session(cur: Any, row: dict) -> Any:
    """The SessionSpec a live session was minted with, rebuilt from its row
    with the same arguments start_session used (surface, room, venue,
    language, verification and the verified guest's context)."""
    from clefs.modules.concierge import api

    guest = guest_context(cur, row["hotel_slug"], row["room"]) if row["verified"] and row["room"] else None
    return api.build_surface_session(
        row["hotel_slug"], surface=row["surface"], room=row["room"], location=row["location_slug"],
        language=row["language"], verified=bool(row["verified"]), guest=guest, cur=cur,
    )


def vertex_live_config(spec: Any, *, mode: str | None) -> dict:
    """live_connect_config for Vertex AI Live: the same instructions, voice,
    tools and transcription, plus a sliding context window so a conversation
    can outlast the Live API's audio session limit. The relay adds the
    resumption handle when it reconnects."""
    config = live_connect_config(spec, mode=mode or "voice")
    config["context_window_compression"] = {"sliding_window": {}}
    return config


def _require_relay() -> None:
    """409 ai_disabled (the page offers the front desk) when the relay
    cannot reach Gemini: the AI is switched off and no test connector is in."""
    from clefs.modules.concierge import live_relay

    if not live_relay.available():
        raise ClefsError(409, "The assistant is not available right now", "ai_disabled")


# ─── Session rows ───


def create_session(
    cur: Any,
    spec: Any,
    *,
    key_status: str,
    mode: str | None = None,
    transport: str = "live",
    consent_ref: str | None = None,
    consents: dict | None = None,
    ip_digest: bytes | None = None,
    call_ref: str | None = None,
    with_token: bool = True,
    location_slug: str | None = None,
) -> tuple[dict, str | None]:
    """Insert a cc_sessions row for a built spec. Returns (row, bearer token
    or None). Transcripts are stored only with the recording consent."""
    token = tokens.new_token() if with_token else None
    now = clock.now()
    flags = consents or {}
    cur.execute(
        f"""
        INSERT INTO cc_sessions (id, hotel_slug, surface, channel, room, location_slug, verified, key_status, language, mode,
                                 transport, token_hash, consent_ref, consents, store_transcripts, prompt_version, model,
                                 call_ref, ip_hash, created_at, expires_at, last_seen_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {SESSION_COLUMNS}
        """,
        (
            str(uuid.uuid4()),
            spec.hotel_slug,
            spec.surface,
            C.SURFACE_SOURCE.get(spec.surface, spec.channel),
            spec.room,
            location_slug or spec.location,
            bool(spec.verified),
            key_status,
            spec.language,
            mode,
            transport,
            tokens.hash_token(token) if token else None,
            consent_ref,
            Json(flags),
            bool(flags.get("recording")),
            spec.prompt_version,
            spec.model,
            call_ref,
            ip_digest,
            now,
            now + timedelta(seconds=C.SESSION_TTL_S),
            now,
        ),
    )
    return dict(cur.fetchone()), token


def get_session(cur: Any, session_id: str) -> dict | None:
    try:
        sid = str(uuid.UUID(str(session_id)))
    except ValueError:
        return None
    cur.execute(f"SELECT {SESSION_COLUMNS}, token_hash FROM cc_sessions WHERE id = %s", (sid,))
    row = cur.fetchone()
    return dict(row) if row else None


def authenticate(cur: Any, session_id: str, token: str | None, *, allow_ended: bool = False) -> dict:
    """The session for a bearer token (the tool, turn and chat routes use
    this). 401 for a wrong, expired or ended session."""
    row = get_session(cur, session_id) if token else None
    if row is None or row.get("token_hash") is None:
        raise unauthorized("Session not found or expired", "session_invalid")
    if not hmac.compare_digest(bytes(row["token_hash"]), tokens.hash_token(token)):
        raise unauthorized("Session not found or expired", "session_invalid")
    if row["expires_at"] <= clock.now() or (row["ended_at"] is not None and not allow_ended):
        raise unauthorized("Session expired, please start again", "session_expired")
    cur.execute("UPDATE cc_sessions SET last_seen_at = %s WHERE id = %s", (clock.now(), row["id"]))
    row.pop("token_hash", None)
    return row


def bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" and value.strip() else None


def end_session(cur: Any, session: dict, *, reason: str, withdraw: bool = False) -> dict:
    """End a session; withdraw=True (the Privacy button) also withdraws every
    consent this guest granted and stops transcript storage."""
    cur.execute(
        "UPDATE cc_sessions SET ended_at = COALESCE(ended_at, %s), end_reason = COALESCE(end_reason, %s), "
        "store_transcripts = store_transcripts AND NOT %s WHERE id = %s",
        (clock.now(), reason[:40], withdraw, session["id"]),
    )
    withdrawn: list[str] = []
    if withdraw and session.get("consent_ref"):
        for purpose, granted in consent_flags(cur, session["hotel_slug"], session["consent_ref"]).items():
            if granted:
                consent_mod.withdraw(
                    cur, session["hotel_slug"], session["consent_ref"], purpose, source="concierge_privacy_button",
                    evidence={"session_id": str(session["id"])},
                )
                withdrawn.append(purpose)
        cur.execute(
            "INSERT INTO cc_session_events (session_id, hotel_slug, kind, ref) VALUES (%s, %s, 'consent_withdrawn', %s)",
            (session["id"], session["hotel_slug"], Json({"purposes": withdrawn})),
        )
    return {"ok": True, "session_id": str(session["id"]), "withdrawn": withdrawn}


def withdraw_for_guest_ref(cur: Any, hotel_slug: str, guest_ref: str) -> dict:
    """The Privacy button when this device has no live session (a reload,
    an expired token): withdraw every consent the device's guest_ref
    granted and stop transcripts on its open sessions. The guest_ref is the
    random token the server gave only to this device, so holding it is the
    proof; the call can only ever take consent away."""
    subject = guest_subject_ref(guest_ref[:200])
    withdrawn: list[str] = []
    for purpose, granted in consent_flags(cur, hotel_slug, subject).items():
        if granted:
            consent_mod.withdraw(cur, hotel_slug, subject, purpose, source="concierge_privacy_button",
                                 evidence={"session_id": None, "via": "guest_ref"})
            withdrawn.append(purpose)
    cur.execute(
        "UPDATE cc_sessions SET store_transcripts = FALSE, ended_at = COALESCE(ended_at, %s), "
        "end_reason = COALESCE(end_reason, 'privacy_withdrawn') WHERE hotel_slug = %s AND consent_ref = %s AND ended_at IS NULL",
        (clock.now(), hotel_slug, subject),
    )
    return {"ok": True, "withdrawn": withdrawn}


def guest_context(cur: Any, hotel_slug: str, room: str) -> dict | None:
    """guests.api.guest_context_for_room inside a savepoint: the Guests
    lane's reads (gst_ tables) must never fail the guest's session; no
    context just means an unpersonalised greeting."""
    from clefs.modules.concierge.prompt import _savepoint
    from clefs.modules.guests import api as guests_api

    return _savepoint(cur, "cc_guest_ctx", lambda: guests_api.guest_context_for_room(cur, hotel_slug, room), None)


# ─── Mint (POST /public/concierge/sessions) ───


def start_session(
    cur: Any,
    hotel: Hotel,
    *,
    room: str | None,
    location: str | None,
    k: str | None,
    stay_token: str | None,
    language: str | None,
    mode: str,
    transport: str,
    guest_ref: str | None,
    grants: dict[str, bool] | None,
    ip: str | None,
) -> dict:
    """Resolve the target, check consent, rate-limit, build the prompt, mint
    the key and store the session. Returns the response body.

    A live mint with LIVE_VIA_VERTEX answers transport "relay" with relay_url,
    relay_token and relay_expires_at (and client_secret null); model is then
    the Vertex AI Live model. Otherwise client_secret is the ephemeral token."""
    from clefs.modules.concierge import api, kb

    if mode not in C.SESSION_MODES:
        raise bad_request(f"mode must be one of {', '.join(C.SESSION_MODES)}", "invalid_mode")
    if transport not in ("live", "http"):
        raise bad_request("transport must be live or http", "invalid_transport")
    if mode == "prewarm" and grants:
        # Prewarm runs without a gesture: it may only use consent already given.
        raise bad_request("prewarm cannot carry consent choices", "prewarm_with_consent")
    lang = language if (language or "").split("-")[0] in C.GUEST_LANGUAGES else None
    digest = ip_hash(ip)
    check_ip_mint_limit(cur, digest)
    # The readiness gate (live.py): no guest conversation starts until the
    # hotel's concierge has been switched live.
    from clefs.modules.concierge import live

    live.require_live(cur, hotel.slug)
    target = resolve_target(cur, hotel, room=room, location=location, k=k, stay_token=stay_token)

    new_ref = False
    if not guest_ref:
        guest_ref, new_ref = tokens.new_token(18), True
    subject = guest_subject_ref(guest_ref[:200])
    if grants:
        record_grants(cur, hotel.slug, subject, grants, language=lang or "en", evidence={"room": target.room, "location": location, "mode": mode})
    flags = consent_flags(cur, hotel.slug, subject)
    missing = [p for p in required_consents(mode) if not flags.get(p)]
    if missing:
        raise ClefsError(409, "Consent is needed before the assistant can start", "consent_required")

    cfg = kb.settings(cur, hotel.slug)
    check_mint_limits(cur, hotel, ip_digest=None, room=target.room, location=location, daily_cap=cfg.daily_session_cap)

    guest = guest_context(cur, hotel.slug, target.room) if target.verified and target.room else None
    surface = "text_fallback" if transport == "http" else target.surface
    spec = api.build_surface_session(
        hotel.slug, surface=surface, room=target.room, location=location, language=lang, verified=target.verified, guest=guest, cur=cur
    )
    relay = transport == "live" and live_via_vertex()
    if relay:
        _require_relay()
        spec = spec.model_copy(update={"model": relay_model()})
        secret = None
    else:
        secret = None if transport == "http" else mint_client_secret(spec, mode=mode)
    # The row keeps transport "live" for a relayed session: the guest's
    # channel is still Gemini Live, only the socket's far end moved.
    row, token = create_session(
        cur, spec, key_status=target.key_status, mode=mode, transport=transport, consent_ref=subject, consents=flags,
        ip_digest=digest, location_slug=location,
    )
    out = {
        "session_id": str(row["id"]),
        "session_token": token,
        "expires_at": row["expires_at"],
        "client_secret": secret,
        "greeting_text": spec.greeting_text,
        "prompt_version": spec.prompt_version,
        "surface": spec.surface,
        "verified": spec.verified,
        "key_status": target.key_status,
        "language": spec.language,
        "model": spec.model,
        "transport": transport,
        "consents": flags,
        "guest_ref": guest_ref,
    }
    if relay:
        relay_token, relay_expires_at = mint_relay_token(str(row["id"]), hotel.slug)
        out.update(
            transport="relay", relay_url=relay_url(str(row["id"])), relay_token=relay_token,
            relay_expires_at=relay_expires_at,
        )
    if target.verified and target.room:
        out["stay_token"] = mint_stay_token(hotel.slug, target.room, target.qr_version)
    return out


# ─── Housekeeping ───


def gc_sessions(cur: Any, *, now=None) -> int:
    """Close sessions past expires_at (their bearer token already fails)."""
    cur.execute(
        "UPDATE cc_sessions SET ended_at = expires_at, end_reason = 'expired' WHERE ended_at IS NULL AND expires_at <= %s",
        (now or clock.now(),),
    )
    return cur.rowcount


def _register_jobs() -> None:
    from clefs import jobs

    @jobs.scheduled("concierge.session_gc", jobs.every(minutes=30), per_hotel=False, timeout_s=60)
    def session_gc(ctx: Any) -> dict:
        return {"closed": gc_sessions(ctx.cur)}


_register_jobs()
