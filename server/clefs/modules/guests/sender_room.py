"""
The room of a WhatsApp sender (guests.api.room_for_sender).

Why this exists: a concierge WhatsApp chat has no room QR behind it. The
only thing we know is the sender's number (as its per-hotel HMAC). When
that number is on the reservation of a guest who is in the house right now,
the concierge may treat the chat as coming from that room (a verified
room, like a scanned in-room QR); otherwise the guest types a room and
every request carries it as unverified, for a door check.

A verified room lets a chat order food or file requests to that door, so
every rule here leans toward None:
- only stays that are in the house NOW: IN_HOUSE, or a roster stay
  (GuestModal / desk capture, no PMS status) whose dates cover today.
  A CONFIRMED arrival is never enough: before check-in the room may still
  hold the departing guest;
- a stay that a later in-house stay in the same room has overtaken (a
  check-out the PMS never sent) does not count;
- more than one distinct room (one number on a family's two rooms) is
  ambiguous and returns None, so the guest says which room.

Sources, both read (the snapshot can lag core by one stay event):
- core reservations + reservation_contacts, through stays_port (guarded:
  a database without core/stays simply knows nothing);
- the guests snapshot: gst_stays.contact_hmacs (roster stays) and the
  stay's gst_contact_points (own captures and PMS mirrors), purged points
  excluded.

Only HMACs are compared; no contact value is read or returned.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from clefs.core.hotels import get_hotel
from clefs.core.time import local_date
from clefs.modules.guests import stays_port

log = logging.getLogger("clefs.guests.sender_room")

# A stay counts as "in the house today" when it is IN_HOUSE, or a roster
# stay (no status) whose dates cover the day. %(today)s is the hotel-local
# date. Used for the candidate and for the "overtaken by a later stay"
# check, so both read the same rule.
_IN_HOUSE_SQL = (
    "({a}.status = 'IN_HOUSE' OR ({a}.status IS NULL AND ({a}.check_in IS NULL OR {a}.check_in <= %(today)s)"
    " AND ({a}.check_out IS NULL OR {a}.check_out >= %(today)s)))"
)


# ─── Snapshot (gst_stays) ───


def snapshot_rooms(cur: Any, hotel_slug: str, hmacs: list[bytes], *, today: date) -> list[str]:
    """Rooms of in-house snapshot stays that carry one of `hmacs`."""
    live_s = _IN_HOUSE_SQL.format(a="s")
    live_o = _IN_HOUSE_SQL.format(a="o")
    cur.execute(
        f"""
        SELECT DISTINCT s.room FROM gst_stays s
        WHERE s.hotel_slug = %(slug)s AND s.room IS NOT NULL AND s.room <> ''
          AND s.checked_out_at IS NULL AND {live_s}
          AND (s.contact_hmacs && %(hs)s::bytea[]
               OR s.reservation_id IN (SELECT p.reservation_id FROM gst_contact_points p
                                       WHERE p.hotel_slug = %(slug)s AND p.value_hmac = ANY(%(hs)s)
                                         AND p.status <> 'purged' AND p.reservation_id IS NOT NULL))
          AND NOT EXISTS (SELECT 1 FROM gst_stays o
                          WHERE o.hotel_slug = s.hotel_slug AND o.room = s.room AND o.id <> s.id
                            AND o.checked_out_at IS NULL AND {live_o}
                            AND o.check_in > s.check_in)
        """,
        {"slug": hotel_slug, "hs": hmacs, "today": today},
    )
    return [str(r["room"]) for r in cur.fetchall()]


# ─── Resolve ───


def room_for_sender(cur: Any, hotel_slug: str, sender_hmac: bytes | None) -> str | None:
    """The one room the sender is staying in now, or None (unknown sender,
    not in the house, or more than one room)."""
    if not sender_hmac:
        return None
    hs = [bytes(sender_hmac)]
    today = local_date(get_hotel(cur, hotel_slug))
    rooms: set[str] = set(snapshot_rooms(cur, hotel_slug, hs, today=today))
    # core/stays through the port; a test backend without the method (or a
    # database without core/stays) contributes nothing.
    core_fn = getattr(stays_port.backend(), "sender_rooms", None)
    if core_fn is not None:
        rooms |= {str(r) for r in core_fn(cur, hotel_slug, hs, today=today) if r}
    rooms = {r.strip() for r in rooms if r and r.strip()}
    if len(rooms) != 1:
        if len(rooms) > 1:
            log.info("room_for_sender: %d rooms match one sender in %s; asking the guest", len(rooms), hotel_slug)
        return None
    return rooms.pop()
