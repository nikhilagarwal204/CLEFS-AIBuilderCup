"""
Legacy concierge routes (conversation logs, hotel locations), moved out of the
monolith with their paths and JSON shapes frozen by the golden tests, plus the
new public per-room endpoint that replaces the guest page's roster download.

Access after the move (foundation spec A):
  POST /conversation-logs   public append (the guest page logs each turn), rate-limited
  GET  /conversation-logs   members only (transcripts are guest PII) or legacy open access
  GET  /locations[/{loc}]   public (QR codes in restaurants and the spa read them)
  POST/DELETE /locations    members (concierge.manage) or legacy open access

GET /public/stay/{slug}/{room}?k=<qr_key> returns ONE room's minimal context.
Personal fields come back only with a valid key for that room's current
qr_key_version, or for legacy QR codes (no key) when the hotel's concierge
setting legacy_qr_personalization is true.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from clefs import db, ratelimit
from clefs.auth.deps import HotelCtx, require_legacy_or
from clefs.core import clock
from clefs.core import rooms as rooms_mod
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import PUBLIC_STAY_FIELDS, find_hotel, module_settings_row
from clefs.modules.guests import api as guests_api

router = APIRouter(tags=["concierge-legacy"])

LOCATION_COLUMNS = "id, hotel_slug, slug, name, type, custom_instructions, created_at"
LOG_COLUMNS = "id, hotel_slug, room_number, session_id, role, message, timestamp"

LOG_LIMIT_PER_ROOM = 300  # turns per 10 minutes; a chatty guest never gets near it
# room_number is chosen by the (public) caller, so two ceilings sit above the
# per-room one: the whole hotel, and one source address across all hotels.
LOG_LIMIT_PER_HOTEL = 2000
LOG_LIMIT_PER_IP = 600
# Stored text is bounded (truncated, since the guest page logs fire-and-forget).
LOG_MESSAGE_MAX = 8000
LOG_FIELD_MAX = 128
LOCATION_READS_PER_IP = 240
STAY_READS_PER_IP_ROOM = 60
WINDOW_S = 600


class ConversationLogEntry(BaseModel):
    session_id: str
    room_number: str
    role: str  # "user" or "assistant"
    message: str
    timestamp: int  # epoch ms


class LocationBody(BaseModel):
    slug: str
    name: str
    type: str = "public"  # "public" | "restaurant" | "spa" | etc.
    custom_instructions: Optional[str] = None


def _ip() -> str:
    return request_info().ip or "unknown"


# ─── Locations ───


@router.get("/hotels/{slug}/locations")
def list_locations(slug: str) -> list[dict]:
    with db.db() as cur:
        cur.execute(f"SELECT {LOCATION_COLUMNS} FROM hotel_locations WHERE hotel_slug = %s ORDER BY created_at", (slug,))
        return [dict(r) for r in cur.fetchall()]


@router.get("/hotels/{slug}/locations/{location_slug}")
def get_location(slug: str, location_slug: str) -> dict:
    with db.db() as cur:
        if not ratelimit.hit(cur, f"loc:ip:{_ip()}", limit=LOCATION_READS_PER_IP, window_s=WINDOW_S):
            raise too_many()
        cur.execute(
            f"SELECT {LOCATION_COLUMNS} FROM hotel_locations WHERE hotel_slug = %s AND slug = %s",
            (slug, location_slug),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Location '{location_slug}' not found for hotel '{slug}'")
    return dict(row)


@router.post("/hotels/{slug}/locations")
def upsert_location(slug: str, body: LocationBody, ctx: HotelCtx = Depends(require_legacy_or("concierge.manage"))) -> dict:
    with db.db() as cur:
        cur.execute(
            f"""
            INSERT INTO hotel_locations (hotel_slug, slug, name, type, custom_instructions)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, slug) DO UPDATE SET
                name = EXCLUDED.name,
                type = EXCLUDED.type,
                custom_instructions = EXCLUDED.custom_instructions
            RETURNING {LOCATION_COLUMNS}
            """,
            (slug, body.slug, body.name, body.type, body.custom_instructions),
        )
        return dict(cur.fetchone())


@router.delete("/hotels/{slug}/locations/{location_slug}")
def delete_location(slug: str, location_slug: str, ctx: HotelCtx = Depends(require_legacy_or("concierge.manage"))) -> dict:
    with db.db() as cur:
        cur.execute("DELETE FROM hotel_locations WHERE hotel_slug = %s AND slug = %s", (slug, location_slug))
    return {"status": "deleted"}


# ─── Conversation logs ───


@router.post("/hotels/{slug}/conversation-logs")
def log_conversation(slug: str, body: ConversationLogEntry) -> dict:
    room = body.room_number[:LOG_FIELD_MAX]
    ip = _ip()
    with db.db() as cur:
        if find_hotel(cur, slug) is None:
            raise HTTPException(status_code=404, detail=f"Hotel '{slug}' not found")
        allowed = (
            ratelimit.hit(cur, f"convlog:ip:{ip}", limit=LOG_LIMIT_PER_IP, window_s=WINDOW_S)
            and ratelimit.hit(cur, f"convlog:hotel:{slug}", limit=LOG_LIMIT_PER_HOTEL, window_s=WINDOW_S)
            and ratelimit.hit(cur, f"convlog:{slug}:{room}:{ip}", limit=LOG_LIMIT_PER_ROOM, window_s=WINDOW_S)
        )
        if not allowed:
            raise too_many()
        cur.execute(
            """
            INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                slug,
                room,
                body.session_id[:LOG_FIELD_MAX],
                body.role[:20],
                body.message[:LOG_MESSAGE_MAX],
                # epoch ms; an out-of-range value would overflow the BIGINT column
                body.timestamp if 0 < body.timestamp < 2**53 else clock.now_ms(),
            ),
        )
    return {"ok": True}


@router.get("/hotels/{slug}/conversation-logs")
def get_conversation_logs(
    slug: str,
    room_number: Optional[str] = None,
    session_id: Optional[str] = None,
    ctx: HotelCtx = Depends(require_legacy_or("concierge.view")),
) -> list[dict]:
    filters = ["hotel_slug = %s"]
    params: list = [slug]
    if room_number:
        filters.append("room_number = %s")
        params.append(room_number)
    if session_id:
        filters.append("session_id = %s")
        params.append(session_id)
    with db.db() as cur:
        cur.execute(
            f"SELECT {LOG_COLUMNS} FROM conversation_logs WHERE {' AND '.join(filters)} ORDER BY timestamp ASC",
            params,
        )
        return [dict(r) for r in cur.fetchall()]


# ─── Public per-room stay context ───


@router.get("/public/stay/{slug}/{room}")
def public_stay(slug: str, room: str, k: Optional[str] = None) -> dict:
    """What the guest page needs for one room, and nothing about other rooms.

    `room` is the guest context for this room (the GuestRoom shape the guest
    page already used, cut down to guests.api's allowlist) or null when not
    personalized; client/app/lib/guestData.ts getRoomData reads exactly that.
    key_status: "valid" (k matches), "invalid" (k given but wrong or rotated),
    "absent" (legacy printed QR).
    """
    if not rooms_mod.valid_room(room):
        raise ClefsError(404, "Room not found", "room_not_found")
    with db.db() as cur:
        hotel = find_hotel(cur, slug)
        if hotel is None:
            raise ClefsError(404, f"Hotel '{slug}' not found", "hotel_not_found")
        if not ratelimit.hit(cur, f"stay:{slug}:{room}:{_ip()}", limit=STAY_READS_PER_IP_ROOM, window_s=WINDOW_S):
            raise too_many()
        registered = rooms_mod.get_room(cur, slug, room)
        if registered is None and rooms_mod.has_register(cur, slug):
            raise ClefsError(404, "Room not found", "room_not_found")
        if registered is not None and not registered["is_active"]:
            raise ClefsError(404, "Room not found", "room_not_found")
        version = registered["qr_key_version"] if registered else 1

        if k:
            key_status = "valid" if rooms_mod.verify_qr_key(slug, room, version, k) else "invalid"
        else:
            key_status = "absent"
        personalize = key_status == "valid"
        if key_status == "absent":
            row = module_settings_row(cur, slug, "concierge")
            personalize = bool(((row or {}).get("settings") or {}).get("legacy_qr_personalization"))

        guest = guests_api.guest_context_for_room(cur, slug, room) if personalize else None
        floor = (registered or {}).get("floor") or rooms_mod.infer_floor(room)

    return {
        "hotel": hotel.project(PUBLIC_STAY_FIELDS),
        "room_number": room,
        "floor": floor,
        "personalized": guest is not None,
        "key_status": key_status,
        "room": guest,
    }
