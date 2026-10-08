"""
The one place the guests module reads core/stays (04 spec D1).

Why a port: core/stays (reservations, reservation_contacts, contact_value,
reservation_breakdown, the stay-event outbox) is built by the F-domain lane
in the same wave as this module. Everything else in guests works from its own
snapshot (gst_stays, gst_contact_points), refreshed through this port when a
stay event arrives or the nightly repair runs, so:

- the planner, guards, metrics and api.py never join a core table whose
  columns this lane cannot see yet (metric SQL is EXPLAINed by the contract
  suite, so it must only name tables that exist);
- a database where core/stays is absent still migrates and runs (every read
  here checks to_regclass first and degrades to "nothing known");
- tests swap the backend (set_backend) for an in-memory one.

The SQL backend reads `SELECT *` and picks known keys, so a column the domain
lane names differently is simply missing (None), never an error. Reads run
inside a savepoint: a surprise in core's schema cannot abort the caller's
transaction.
"""

from __future__ import annotations

import importlib
import logging
from datetime import date, datetime
from typing import Any, Protocol

log = logging.getLogger("clefs.guests.stays")

OTA_CHANNELS = frozenset({"BOOKING", "AGODA", "EXPEDIA"})
# 04 spec 3.4: a contact on a DIRECT_WEB, DIRECT_PHONE, WALKIN or CORPORATE
# reservation is pms_direct (the guest or their company booked with us).
DIRECT_CHANNELS = frozenset({"DIRECT_WEB", "DIRECT_PHONE", "WALKIN", "CORPORATE"})
STATUSES = frozenset({"CONFIRMED", "IN_HOUSE", "CHECKED_OUT", "CANCELLED", "NO_SHOW"})
# reservations_since reads in keyset pages of this many ids.
PAGE_SIZE = 1000


class StaysBackend(Protocol):
    def reservation(self, cur: Any, hotel_slug: str, reservation_id: int) -> dict | None: ...

    def contacts(self, cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]: ...

    def contact_value(self, cur: Any, reservation_contact_id: int) -> str | None: ...

    def reservation_breakdown(self, cur: Any, reservation_id: int) -> Any: ...

    def reservations_since(self, cur: Any, hotel_slug: str, *, check_out_from: date) -> list[int]: ...

    def known_sender(self, cur: Any, hotel_slug: str, hmacs: list[bytes], *, today: date) -> bool: ...


# ─── Normalised shapes ───
# reservation(): {id, status, check_in, check_out, channel_code, segment,
#   booked_at, checked_in_at, checked_out_at, guest_language, stay_chain_id,
#   booking_ref, guest_name, promo_code, utm, room, rooms_count,
#   room_revenue_minor, currency}
# contacts(): [{id, kind, value_hmac, provenance, alias_domain, usable_until, purged_at}]


def _first(row: dict, *keys: str) -> Any:
    for k in keys:
        if row.get(k) is not None:
            return row[k]
    return None


def normalise_reservation(row: dict) -> dict:
    status = (row.get("status") or "").upper() or None
    return {
        "id": row.get("id"),
        "status": status if status in STATUSES else None,
        "check_in": row.get("check_in"),
        "check_out": _first(row, "check_out"),
        "channel_code": (row.get("channel_code") or row.get("channel") or None),
        "segment": row.get("segment"),
        "booked_at": row.get("booked_at"),
        "checked_in_at": row.get("checked_in_at"),
        "checked_out_at": row.get("checked_out_at"),
        "guest_language": _first(row, "guest_language", "language"),
        "stay_chain_id": row.get("stay_chain_id"),
        "booking_ref": _first(row, "pms_res_id", "ota_res_id", "booking_ref"),
        "guest_name": row.get("guest_name"),
        "promo_code": row.get("promo_code"),
        "utm": row.get("utm") or {},
        "room": _first(row, "room_number", "room"),
        "rooms_count": row.get("rooms_count"),
        "room_revenue_minor": _first(row, "room_revenue_minor", "room_revenue_fils", "gross_minor", "gross_fils"),
        "currency": (row.get("currency") or None),
    }


class SqlStaysBackend:
    """core/stays over SQL. Every read is guarded: missing table -> nothing."""

    name = "sql"

    def _exists(self, cur: Any, table: str) -> bool:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"public.{table}",))
        return bool(cur.fetchone()["ok"])

    def _guarded(self, cur: Any, sql: str, params: tuple, *, many: bool) -> Any:
        cur.execute("SAVEPOINT gst_stays_port")
        try:
            cur.execute(sql, params)
            out = [dict(r) for r in cur.fetchall()] if many else cur.fetchone()
            cur.execute("RELEASE SAVEPOINT gst_stays_port")
            return out
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT gst_stays_port")
            cur.execute("RELEASE SAVEPOINT gst_stays_port")
            log.exception("core/stays read failed; treating it as unknown")
            return [] if many else None

    def reservation(self, cur: Any, hotel_slug: str, reservation_id: int) -> dict | None:
        if not self._exists(cur, "reservations"):
            return None
        row = self._guarded(cur, "SELECT * FROM reservations WHERE id = %s AND hotel_slug = %s", (reservation_id, hotel_slug), many=False)
        if row is None:
            return None
        out = normalise_reservation(dict(row))
        if out["room"] is None and self._exists(cur, "reservation_nights"):
            night = self._guarded(
                cur,
                "SELECT room_number FROM reservation_nights WHERE reservation_id = %s AND room_number IS NOT NULL "
                "ORDER BY stay_date DESC LIMIT 1",
                (reservation_id,),
                many=False,
            )
            if night is not None:
                out["room"] = night.get("room_number")
        return out

    def contacts(self, cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]:
        if not self._exists(cur, "reservation_contacts"):
            return []
        rows = self._guarded(
            cur,
            "SELECT * FROM reservation_contacts WHERE hotel_slug = %s AND reservation_id = %s ORDER BY id",
            (hotel_slug, reservation_id),
            many=True,
        )
        return [
            {
                "id": r.get("id"),
                "kind": r.get("kind"),
                "value_hmac": bytes(r["value_hmac"]) if r.get("value_hmac") is not None else None,
                "provenance": r.get("provenance"),
                "alias_domain": r.get("alias_domain"),
                "usable_until": r.get("usable_until"),
                "purged_at": r.get("purged_at"),
            }
            for r in rows
        ]

    def contact_value(self, cur: Any, reservation_contact_id: int) -> str | None:
        stays = _core_stays()
        fn = getattr(stays, "contact_value", None) if stays else None
        if fn is None:
            return None
        return fn(cur, reservation_contact_id)

    def reservation_breakdown(self, cur: Any, reservation_id: int) -> Any:
        stays = _core_stays()
        fn = getattr(stays, "reservation_breakdown", None) if stays else None
        if fn is None:
            return None
        try:
            return fn(cur, reservation_id)
        except Exception:
            log.exception("reservation_breakdown(%s) failed", reservation_id)
            return None

    def reservations_since(self, cur: Any, hotel_slug: str, *, check_out_from: date) -> list[int]:
        """Every reservation id checking out on or after `check_out_from`.

        Keyset pages of PAGE_SIZE (id > last id) instead of one LIMIT: a
        hotel with more rows than a single page must not silently lose its
        newest reservations, which are exactly the ones the nightly repair
        and the stay linker care about."""
        if not self._exists(cur, "reservations"):
            return []
        out: list[int] = []
        last = 0
        while True:
            rows = self._guarded(
                cur,
                "SELECT id FROM reservations WHERE hotel_slug = %s AND check_out >= %s AND id > %s ORDER BY id LIMIT %s",
                (hotel_slug, check_out_from, last, PAGE_SIZE),
                many=True,
            )
            out.extend(int(r["id"]) for r in rows)
            if len(rows) < PAGE_SIZE:
                return out
            last = out[-1]

    def known_sender(self, cur: Any, hotel_slug: str, hmacs: list[bytes], *, today: date) -> bool:
        if not hmacs or not (self._exists(cur, "reservation_contacts") and self._exists(cur, "reservations")):
            return False
        row = self._guarded(
            cur,
            """
            SELECT 1 AS hit FROM reservation_contacts c JOIN reservations r ON r.id = c.reservation_id
            WHERE c.hotel_slug = %s AND c.value_hmac = ANY(%s)
              AND (r.status = 'IN_HOUSE' OR (r.status = 'CONFIRMED' AND r.check_in <= %s AND r.check_out >= %s))
            LIMIT 1
            """,
            (hotel_slug, [bytes(h) for h in hmacs], today.fromordinal(today.toordinal() + 1), today),
            many=False,
        )
        return row is not None

    def sender_rooms(self, cur: Any, hotel_slug: str, hmacs: list[bytes], *, today: date) -> list[str]:
        """Rooms of IN_HOUSE core reservations whose (unpurged) contacts
        carry one of `hmacs` (guests.api.room_for_sender). A reservation
        that a later IN_HOUSE one in the same room has overtaken (a missed
        check-out) is left out: its guest is not the one in the room now.
        Optional on the protocol: callers reach it through getattr, so an
        in-memory test backend without it simply knows nothing. `today` is
        unused (IN_HOUSE is the only live status) but kept so the snapshot
        and core reads take the same arguments."""
        if not hmacs or not (self._exists(cur, "reservation_contacts") and self._exists(cur, "reservations")):
            return []
        rows = self._guarded(
            cur,
            """
            SELECT DISTINCT r.room_number AS room FROM reservation_contacts c JOIN reservations r ON r.id = c.reservation_id
            WHERE c.hotel_slug = %s AND r.hotel_slug = c.hotel_slug AND c.value_hmac = ANY(%s) AND c.purged_at IS NULL
              AND r.status = 'IN_HOUSE' AND r.room_number IS NOT NULL AND r.room_number <> ''
              AND NOT EXISTS (SELECT 1 FROM reservations o
                              WHERE o.hotel_slug = r.hotel_slug AND o.room_number = r.room_number AND o.id <> r.id
                                AND o.status = 'IN_HOUSE' AND o.check_in > r.check_in)
            """,
            (hotel_slug, [bytes(h) for h in hmacs]),
            many=True,
        )
        return [str(r["room"]) for r in rows]


def _core_stays() -> Any:
    """clefs.core.stays when the domain lane has shipped it, else None."""
    try:
        return importlib.import_module("clefs.core.stays")
    except ModuleNotFoundError as exc:
        if exc.name not in ("clefs.core.stays",):
            raise
        return None


_backend: StaysBackend = SqlStaysBackend()


def backend() -> StaysBackend:
    return _backend


def set_backend(b: StaysBackend | None) -> StaysBackend:
    """Tests (and the demo seed) swap in an in-memory core/stays."""
    global _backend
    _backend = b if b is not None else SqlStaysBackend()
    return _backend


def reset_backend() -> None:
    set_backend(None)


# ─── Convenience ───


def is_ota(channel_code: str | None) -> bool:
    return (channel_code or "").upper() in OTA_CHANNELS


def is_direct(channel_code: str | None) -> bool:
    return (channel_code or "").upper() in DIRECT_CHANNELS


def as_date(v: Any) -> date | None:
    if v is None or isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def as_datetime(v: Any) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None
