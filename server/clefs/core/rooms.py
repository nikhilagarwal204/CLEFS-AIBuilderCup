"""
The room register (hotel_rooms) and printed-QR keys.

Rooms are TEXT ("502", "PH-1") to match staff_requests.room and
guest_rooms.room_number. A hotel with no hotel_rooms rows (every legacy Indian
hotel) is "unregistered": any room number is accepted and the floor is inferred
from the number (502 -> 5, 1204 -> 12).

QR keys: qr_key = base32(HMAC(SECRET_KEY, "slug:room:version"))[:10]. New QR
codes print /stay/{slug}/{room}?k=<qr_key>; bumping qr_key_version (on
checkout, or if a code is photographed and shared) invalidates the old print.
"""

from __future__ import annotations

import hmac
import re
from typing import Any

from clefs.auth.tokens import qr_digest

ROOM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,23}$")
_LEADING_DIGITS = re.compile(r"^(\d+)")

ROOM_COLUMNS = (
    "room_number",
    "floor",
    "floor_inferred",
    "wing",
    "room_type_code",
    "bedrooms",
    "extension",
    "service_state",
    "ooo_reason",
    "qr_key_version",
    "is_active",
)
EDITABLE = frozenset(ROOM_COLUMNS) - {"room_number", "qr_key_version", "floor_inferred"}


def valid_room(room: str) -> bool:
    return bool(ROOM_RE.match(room or ""))


def infer_floor(room_number: str) -> str | None:
    """Digits minus the last two: 502 -> "5", 1204 -> "12", 12 -> None."""
    m = _LEADING_DIGITS.match(room_number or "")
    if not m or len(m.group(1)) < 3:
        return None
    return str(int(m.group(1)[:-2]))


def qr_key(hotel_slug: str, room_number: str, version: int = 1) -> str:
    return qr_digest(f"{hotel_slug}:{room_number}:{version}")


def verify_qr_key(hotel_slug: str, room_number: str, version: int, key: str | None) -> bool:
    if not key:
        return False
    # Bytes, not str: compare_digest raises TypeError on non-ASCII str, and ?k=
    # comes straight from an unauthenticated URL.
    given = key.strip().upper().encode("utf-8", "replace")
    return hmac.compare_digest(qr_key(hotel_slug, room_number, version).encode("ascii"), given)


def stay_path(hotel_slug: str, room_number: str, version: int = 1) -> str:
    return f"/stay/{hotel_slug}/{room_number}?k={qr_key(hotel_slug, room_number, version)}"


def has_register(cur: Any, hotel_slug: str) -> bool:
    cur.execute("SELECT EXISTS (SELECT 1 FROM hotel_rooms WHERE hotel_slug = %s) AS e", (hotel_slug,))
    return bool(cur.fetchone()["e"])


def get_room(cur: Any, hotel_slug: str, room_number: str) -> dict | None:
    cur.execute(
        f"SELECT {', '.join(ROOM_COLUMNS)} FROM hotel_rooms WHERE hotel_slug = %s AND room_number = %s",
        (hotel_slug, room_number),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def list_rooms(cur: Any, hotel_slug: str, *, active_only: bool = True) -> list[dict]:
    # NUMERIC, not BIGINT: ROOM_RE allows 24 digits, and a BIGINT cast of a
    # 20-digit room number would fail the whole listing.
    cur.execute(
        f"""
        SELECT {', '.join(ROOM_COLUMNS)} FROM hotel_rooms
        WHERE hotel_slug = %s AND (is_active OR NOT %s)
        ORDER BY NULLIF(regexp_replace(room_number, '\\D.*$', ''), '')::NUMERIC NULLS LAST, room_number
        """,
        (hotel_slug, active_only),
    )
    return [dict(r) for r in cur.fetchall()]


def floor_for(cur: Any, hotel_slug: str, room_number: str) -> str | None:
    room = get_room(cur, hotel_slug, room_number)
    if room and room["floor"]:
        return room["floor"]
    return infer_floor(room_number)


def upsert_rooms(cur: Any, hotel_slug: str, rooms: list[dict]) -> int:
    """Insert or update rooms by number (a partial update: omitted fields keep
    their stored value).

    Floor: a new room without one gets the inferred floor (flagged). An update
    that omits `floor` keeps the stored floor, so toggling is_active or
    service_state never resets an explicit "Penthouse" or "12A" (floor drives
    device scoping and routing). Sending floor as "" or None asks for the
    inferred value again."""
    n = 0
    for r in rooms:
        number = str(r.get("room_number") or "").strip()
        if not valid_room(number):
            raise ValueError(f"invalid room number {number!r}")
        data = {k: v for k, v in r.items() if k in EDITABLE}
        floor_supplied = "floor" in data
        floor = data.pop("floor", None) or None
        inferred = False
        if floor is None:
            floor = infer_floor(number)
            inferred = floor is not None
        code = str(data.get("room_type_code") or "").strip()
        if code:
            # hotel_rooms.room_type_code references room_types; a code the
            # hotel has not described yet is created with its code as name
            # (renamed later through PUT /room-types), so a room import or
            # the room editor never fails on it.
            data["room_type_code"] = code
            cur.execute(
                "INSERT INTO room_types (hotel_slug, code, name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (hotel_slug, code, code),
            )
        elif "room_type_code" in data:
            data["room_type_code"] = None
        cols = ["hotel_slug", "room_number", "floor", "floor_inferred"] + list(data)
        vals = [hotel_slug, number, floor, inferred] + list(data.values())
        update_cols = list(data) + (["floor", "floor_inferred"] if floor_supplied else [])
        updates = "".join(f"{c} = EXCLUDED.{c}, " for c in update_cols) + "updated_at = NOW()"
        cur.execute(
            f"INSERT INTO hotel_rooms ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (hotel_slug, room_number) DO UPDATE SET {updates}",
            vals,
        )
        n += 1
    return n


# ─── Room types ───

ROOM_TYPE_COLUMNS = ("code", "name", "bedrooms", "max_occupancy", "room_count", "is_active")


def list_room_types(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        f"SELECT {', '.join(ROOM_TYPE_COLUMNS)} FROM room_types WHERE hotel_slug = %s ORDER BY code", (hotel_slug,)
    )
    return [dict(r) for r in cur.fetchall()]


def upsert_room_types(cur: Any, hotel_slug: str, items: list[dict]) -> int:
    """Insert or update room types by code (partial: omitted fields keep
    their stored value). Never deletes: rooms and reservations point at them;
    `is_active: false` retires one."""
    n = 0
    for t in items:
        code = str(t.get("code") or "").strip()
        if not code:
            raise ValueError("room type code is required")
        supplied = {k: v for k, v in t.items() if k in ROOM_TYPE_COLUMNS and k != "code"}
        data = {"name": code, **supplied}  # a new type is named by its code until renamed
        cols = ["hotel_slug", "code"] + list(data)
        conflict = ("DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in supplied)) if supplied else "DO NOTHING"
        cur.execute(
            f"INSERT INTO room_types ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
            f"ON CONFLICT (hotel_slug, code) {conflict}",
            [hotel_slug, code, *data.values()],
        )
        n += 1
    return n


def rotate_qr(cur: Any, hotel_slug: str, room_number: str) -> int:
    """Invalidate every printed QR for the room; returns the new version."""
    cur.execute(
        "UPDATE hotel_rooms SET qr_key_version = qr_key_version + 1, updated_at = NOW() "
        "WHERE hotel_slug = %s AND room_number = %s RETURNING qr_key_version",
        (hotel_slug, room_number),
    )
    row = cur.fetchone()
    if row is None:
        raise LookupError(room_number)
    return int(row["qr_key_version"])
