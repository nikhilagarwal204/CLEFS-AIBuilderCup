"""
Legacy guest-roster routes (guest_rooms JSONB per room), moved out of the
monolith with paths and shapes frozen by the golden tests.

Access changed with the move: the whole roster is guest PII, so every route is
members-only (guests.view / guests.manage) or legacy open access. The guest QR
page no longer downloads the roster; it calls GET /public/stay/{slug}/{room}.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from clefs import db
from clefs.auth.deps import HotelCtx, require_legacy_or
from clefs.db import Json
from clefs.legacy.seed_data import DEMO_GUESTS

router = APIRouter(tags=["guests-legacy"])


class GuestRoomBody(BaseModel):
    data: dict[str, Any]


_UPSERT = """
    INSERT INTO guest_rooms (hotel_slug, room_number, data, updated_at)
    VALUES (%s, %s, %s, NOW())
    ON CONFLICT (hotel_slug, room_number) DO UPDATE SET
        data = EXCLUDED.data,
        updated_at = NOW()
"""


@router.get("/hotels/{slug}/guests")
def get_all_guests(slug: str, ctx: HotelCtx = Depends(require_legacy_or("guests.view"))) -> dict:
    with db.db() as cur:
        cur.execute("SELECT room_number, data FROM guest_rooms WHERE hotel_slug = %s", (slug,))
        return {row["room_number"]: row["data"] for row in cur.fetchall()}


@router.put("/hotels/{slug}/guests/{room_number}")
def upsert_guest(slug: str, room_number: str, body: GuestRoomBody, ctx: HotelCtx = Depends(require_legacy_or("guests.manage"))) -> dict:
    data = {**body.data, "roomNumber": room_number}
    with db.db() as cur:
        cur.execute(_UPSERT, (slug, room_number, Json(data)))
    return data


@router.delete("/hotels/{slug}/guests/{room_number}")
def delete_guest(slug: str, room_number: str, ctx: HotelCtx = Depends(require_legacy_or("guests.manage"))) -> dict:
    with db.db() as cur:
        cur.execute("DELETE FROM guest_rooms WHERE hotel_slug = %s AND room_number = %s", (slug, room_number))
    return {"ok": True}


@router.post("/hotels/{slug}/guests/seed")
def seed_guests(slug: str, ctx: HotelCtx = Depends(require_legacy_or("guests.manage"))) -> dict:
    with db.db() as cur:
        for room_number, data in DEMO_GUESTS.items():
            cur.execute(_UPSERT, (slug, room_number, Json(data)))
    return DEMO_GUESTS
