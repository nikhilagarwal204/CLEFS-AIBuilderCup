"""Reservations: the PMS's current state of each stay (02 spec 2.1, 04 spec D1).

`upsert_reservation` is the single write path (ingest commit, the demo seed,
a channel-manager feed, manual entry). It normalises the matching keys,
derives nights and fees, links back-to-back stays into a chain, stores
contacts sealed, and emits one stay event per change in the same
transaction, so an event exists exactly when its change committed.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.stay_events import (
    CANCELLED,
    CHECKED_IN,
    CHECKED_OUT,
    CREATED,
    DATES_CHANGED,
    NO_SHOW,
)
from clefs.core.stays import contacts as contacts_mod
from clefs.core.stays import events, nights as nights_mod
from clefs.core.stays.normalize import normalize_name, normalize_ota_res_id
from clefs.core.time import business_date

STATUSES = ("CONFIRMED", "CANCELLED", "NO_SHOW", "IN_HOUSE", "CHECKED_OUT")
SOURCE_SYSTEMS = ("PMS", "CM", "MANUAL", "DEMO")
ARRIVED = ("IN_HOUSE", "CHECKED_OUT")
# Columns a caller may set. Derived ones (nights, *_norm, stay_chain_id,
# rate_basis) are computed here and never taken from input.
WRITABLE = (
    "channel_code", "ota_res_id", "guest_name", "guest_country", "guest_language", "booked_at", "check_in",
    "check_out", "actual_check_out", "rooms_count", "room_type_code", "room_number", "rate_plan_code", "status",
    "cancelled_at", "checked_in_at", "checked_out_at", "cancel_fee_minor", "no_show_fee_minor", "collect_model",
    "payment_method", "gross_minor", "room_revenue_minor", "currency", "segment", "adults", "children", "utm",
    "promo_code", "booking_engine_ref",
)
# Channel kinds for a code first seen in an import (channels is FK'd).
_CHANNEL_KINDS = {
    "BOOKING": "ota", "AGODA": "ota", "EXPEDIA": "ota", "DIRECT_WEB": "direct", "DIRECT_PHONE": "direct",
    "WALKIN": "walk_in", "CORPORATE": "corporate", "GDS": "gds",
}
CHAIN_HOPS = 200

# ─── Reads ───


def get_reservation(cur: Any, reservation_id: int) -> dict | None:
    cur.execute("SELECT * FROM reservations WHERE id = %s", (reservation_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def find_reservation(cur: Any, hotel_slug: str, pms_res_id: str, *, source_system: str = "PMS") -> dict | None:
    cur.execute(
        "SELECT * FROM reservations WHERE hotel_slug = %s AND source_system = %s AND pms_res_id = %s",
        (hotel_slug, source_system, str(pms_res_id)),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def ensure_channel(cur: Any, hotel_slug: str, code: str | None) -> str | None:
    """Upper-case the code and create the channels row on first sight, so an
    import never fails on the (hotel_slug, channel_code) foreign key."""
    if not code:
        return None
    c = str(code).strip().upper().replace(" ", "_")
    kind = _CHANNEL_KINDS.get(c, "other")
    collect = "OTA_COLLECT" if c == "EXPEDIA" else ("HOTEL_COLLECT" if kind != "ota" else None)
    cur.execute(
        """
        INSERT INTO channels (hotel_slug, code, name, kind, default_collect_model) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, code) DO NOTHING
        """,
        (hotel_slug, c, c.replace("_", " ").title(), kind, collect),
    )
    return c


# ─── Stay chains (04 spec D1) ───
# The back-to-back chain of one guest in one room with no gap. The same
# guest is the same normalised name or a shared contact hmac; the same room
# is the same room number, or (neither assigned yet) the same room type.

_PREV = """
SELECT p.id, p.stay_chain_id FROM reservations p
WHERE p.hotel_slug = %(hotel)s AND p.id <> %(id)s
  AND COALESCE(p.actual_check_out, p.check_out) = %(check_in)s
  AND p.status NOT IN ('CANCELLED', 'NO_SHOW')
  AND ((p.room_number IS NOT NULL AND p.room_number = %(room)s)
       OR (p.room_number IS NULL AND %(room)s::text IS NULL AND p.room_type_code IS NOT DISTINCT FROM %(rtype)s))
  AND ((p.guest_name_norm IS NOT NULL AND p.guest_name_norm = %(name)s)
       OR EXISTS (SELECT 1 FROM reservation_contacts a JOIN reservation_contacts b ON a.value_hmac = b.value_hmac
                  WHERE a.reservation_id = p.id AND b.reservation_id = %(id)s AND a.value_hmac IS NOT NULL))
ORDER BY p.check_in, p.id
LIMIT 1
"""


def _chain_of(cur: Any, row: dict) -> int | None:
    if row["status"] in nights_mod.NOT_STAYING:
        return None
    cur.execute(_PREV, {"hotel": row["hotel_slug"], "id": row["id"], "check_in": row["check_in"],
                        "room": row.get("room_number"), "rtype": row.get("room_type_code"),
                        "name": row.get("guest_name_norm")})
    prev = cur.fetchone()
    if prev is None:
        return int(row["id"])
    return int(prev["stay_chain_id"] or prev["id"])


def relink_chain(cur: Any, reservation_id: int) -> list[int]:
    """Recompute stay_chain_id for a reservation, then walk forward: every
    stay that starts when a visited one ends, or shared a visited one's old
    chain, is recomputed in turn (bounded by CHAIN_HOPS). Returns the ids
    whose chain changed (their TD night index may have moved)."""
    first = get_reservation(cur, reservation_id)
    if first is None:
        return []
    queue, seen, changed = [first], {int(first["id"])}, []
    while queue and len(seen) <= CHAIN_HOPS:
        r = queue.pop(0)
        old = r["stay_chain_id"]
        new = _chain_of(cur, r)
        if new != old:
            cur.execute("UPDATE reservations SET stay_chain_id = %s WHERE id = %s", (new, r["id"]))
            changed.append(int(r["id"]))
        cur.execute(
            """
            SELECT * FROM reservations WHERE hotel_slug = %s AND id <> ALL(%s) AND check_in >= %s
              AND (check_in = %s OR stay_chain_id = ANY(%s))
            ORDER BY check_in, id
            """,
            (r["hotel_slug"], list(seen), r["check_in"], r["actual_check_out"] or r["check_out"],
             [c for c in (old, int(r["id"])) if c]),
        )
        for nxt in cur.fetchall():
            if int(nxt["id"]) not in seen:
                seen.add(int(nxt["id"]))
                queue.append(dict(nxt))
    return changed


def prior_chain_nights(cur: Any, row: dict) -> int:
    """Nights of the same chain before this stay (the TD 30-night cap counts
    across a back-to-back chain)."""
    chain = row.get("stay_chain_id")
    if not chain:
        return 0
    cur.execute(
        """
        SELECT count(DISTINCT n.stay_date) AS n FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
        WHERE r.hotel_slug = %s AND COALESCE(r.stay_chain_id, r.id) = %s AND r.id <> %s
          AND r.status NOT IN ('CANCELLED', 'NO_SHOW') AND n.stay_date < %s
        """,
        (row["hotel_slug"], chain, row["id"], row["check_in"]),
    )
    return int(cur.fetchone()["n"] or 0)


# ─── Upsert ───


def _as_date(v: Any, field: str) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise ClefsError(400, f"{field} is not a date", "invalid_reservation") from exc


def _as_instant(v: Any, field: str, tz: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=tz)
    if isinstance(v, date):
        return datetime.combine(v, time(12), tzinfo=tz)
    try:
        dt = datetime.fromisoformat(str(v))
    except ValueError as exc:
        raise ClefsError(400, f"{field} is not a timestamp", "invalid_reservation") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=tz)


def _default_instant(d: date, tz: Any, now: datetime) -> datetime:
    """Local noon of a stay date, never later than now: the instant used for
    a check-in or check-out the PMS reported without a time."""
    return min(datetime.combine(d, time(12), tzinfo=tz), now)


def _clean(hotel: Any, data: dict, old: dict | None) -> dict:
    """Validated, normalised column values from caller input (only the keys
    given; on an insert the required ones must be there)."""
    tz = hotel.tz
    vals = {k: data[k] for k in WRITABLE if k in data}
    for k in ("check_in", "check_out", "actual_check_out"):
        if k in vals:
            vals[k] = _as_date(vals[k], k)
    for k in ("booked_at", "cancelled_at", "checked_in_at", "checked_out_at"):
        if k in vals:
            vals[k] = _as_instant(vals[k], k, tz)
    if "status" in vals:
        vals["status"] = str(vals["status"] or "CONFIRMED").upper()
        if vals["status"] not in STATUSES:
            raise ClefsError(400, f"unknown status {vals['status']!r}", "invalid_reservation")
    if "currency" in vals:
        vals["currency"] = (vals["currency"] or hotel.currency).upper()
    merged = (old or {}) | vals
    if merged.get("check_in") is None or merged.get("check_out") is None:
        raise ClefsError(400, "check_in and check_out are required", "invalid_reservation")
    if merged["check_out"] < merged["check_in"]:
        raise ClefsError(400, "check_out is before check_in", "invalid_reservation")
    return vals


def _status_events(old: str | None, new: str) -> list[str]:
    """The frozen events a status move implies, in order. A jump from
    CONFIRMED straight to CHECKED_OUT (an import missed the stay) still
    tells consumers the guest arrived. Reinstating a cancelled booking has
    no frozen event and emits none."""
    if old == new:
        return []
    if new == "CANCELLED":
        return [CANCELLED]
    if new == "NO_SHOW":
        return [NO_SHOW]
    out = []
    if new in ARRIVED and old not in ARRIVED:
        out.append(CHECKED_IN)
    if new == "CHECKED_OUT":
        out.append(CHECKED_OUT)
    return out


def _write_row(cur: Any, hotel_slug: str, source_system: str, pms_res_id: str, old: dict | None, vals: dict,
               import_batch_id: int | None) -> dict:
    """INSERT or UPDATE (only changed columns); returns the stored row."""
    if old is None:
        cols = ["hotel_slug", "source_system", "pms_res_id", *vals.keys()]
        params = [hotel_slug, source_system, pms_res_id, *vals.values()]
        if import_batch_id is not None:
            cols.append("import_batch_id")
            params.append(import_batch_id)
        cur.execute(
            f"INSERT INTO reservations ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) RETURNING *",
            [_adapt(v) for v in params],
        )
        return dict(cur.fetchone())
    diff = {k: v for k, v in vals.items() if old.get(k) != v}
    if import_batch_id is not None:
        diff["import_batch_id"] = import_batch_id
    if not diff:
        return old
    sets = ", ".join(f"{k} = %s" for k in diff)
    cur.execute(
        f"UPDATE reservations SET {sets}, updated_at = NOW() WHERE id = %s RETURNING *",
        [*(_adapt(v) for v in diff.values()), old["id"]],
    )
    return dict(cur.fetchone())


def _adapt(v: Any) -> Any:
    if isinstance(v, dict):
        from psycopg2.extras import Json

        return Json(v)
    return v


def _events(old: dict | None, row: dict, *, now: datetime, occurred_at: datetime | None) -> list[tuple]:
    """[(event, changes, occurred_at)] for the move from old to row."""
    out: list[tuple] = []
    when = occurred_at or now
    if old is None:
        out.append((CREATED, {"status": (None, row["status"]), "check_in": (None, row["check_in"]),
                              "check_out": (None, row["check_out"]), "channel_code": (None, row["channel_code"])},
                    row["booked_at"]))
    elif (old["check_in"], old["check_out"]) != (row["check_in"], row["check_out"]):
        out.append((DATES_CHANGED, {"check_in": (old["check_in"], row["check_in"]),
                                    "check_out": (old["check_out"], row["check_out"]),
                                    "nights": (old["nights"], row["nights"])}, when))
    prev = old["status"] if old else None
    for ev in _status_events(prev, row["status"]):
        if ev == CANCELLED:
            out.append((ev, {"status": (prev, "CANCELLED"),
                             "cancelled_at": (old and old["cancelled_at"], row["cancelled_at"])},
                        row["cancelled_at"] or when))
        elif ev == NO_SHOW:
            out.append((ev, {"status": (prev, "NO_SHOW")}, when))
        elif ev == CHECKED_IN:
            out.append((ev, {"status": (prev, "IN_HOUSE"),
                             "checked_in_at": (old and old["checked_in_at"], row["checked_in_at"])},
                        row["checked_in_at"] or when))
            prev = "IN_HOUSE"
        else:
            out.append((ev, {"status": (prev, "CHECKED_OUT"),
                             "checked_out_at": (old and old["checked_out_at"], row["checked_out_at"]),
                             "actual_check_out": (old and old["actual_check_out"], row["actual_check_out"])},
                        row["checked_out_at"] or when))
    # created carries booked_at when known, else the earliest instant that
    # follows it; then every instant is clamped so the order holds.
    if out and out[0][0] == CREATED and out[0][2] is None:
        later = [e[2] for e in out[1:] if e[2] is not None]
        out[0] = (CREATED, out[0][1], min([when, *later]))
    return out


def _emit_all(cur: Any, row: dict, evs: list[tuple]) -> list[str]:
    cur.execute("SELECT max(occurred_at) AS m FROM stay_events WHERE reservation_id = %s", (row["id"],))
    floor = cur.fetchone()["m"]
    for name, changes, at in evs:
        if floor is not None and at < floor:
            at = floor
        events.emit(cur, row["hotel_slug"], row["id"], name, changes, occurred_at=at)
        floor = at
    return [e[0] for e in evs]


# Changes that move nights, fees, `stayed` or the chain.
_NIGHT_FIELDS = ("check_in", "check_out", "actual_check_out", "status", "rooms_count", "room_type_code",
                 "room_number", "gross_minor", "room_revenue_minor", "currency")
_CHAIN_FIELDS = ("check_in", "check_out", "actual_check_out", "status", "room_number", "room_type_code",
                 "guest_name_norm")


def _kept_nightly(cur: Any, row: dict) -> list[dict]:
    """A nightly-rate stay whose dates moved with no new nights: keep each
    remaining night's rate, and price an added night like the nearest one."""
    cur.execute(
        """
        SELECT stay_date, room_seq, rooms, rate_minor, room_number, room_type_code FROM reservation_nights
        WHERE reservation_id = %s ORDER BY stay_date, room_seq
        """,
        (row["id"],),
    )
    have = [dict(r) for r in cur.fetchall()]
    if not have:
        return []
    by_date: dict[date, list[dict]] = {}
    for r in have:
        by_date.setdefault(r["stay_date"], []).append(r)
    out: list[dict] = []
    d = row["check_in"]
    while d < row["check_out"]:
        near = by_date.get(d) or by_date[min(by_date, key=lambda x: abs((x - d).days))]
        out.extend({**r, "stay_date": d, "room_number": row["room_number"] or r["room_number"]} for r in near)
        d += timedelta(days=1)
    return out


def rebuild_nights(cur: Any, row: dict, nights: list[dict] | None = None, *, today: date | None = None) -> dict:
    """Rewrite one reservation's nights (fees, TD cap across its chain,
    `stayed`) and store the derived rate_basis and room revenue."""
    if nights is None and row.get("rate_basis") == "nightly":
        nights = _kept_nightly(cur, row) or None
    out = nights_mod.write_nights(cur, row, nights, prior_chain_nights=prior_chain_nights(cur, row), today=today)
    rev = out["room_revenue_minor"] if out["rate_basis"] != "none" else row.get("room_revenue_minor")
    cur.execute(
        "UPDATE reservations SET rate_basis = %s, room_revenue_minor = %s WHERE id = %s",
        (out["rate_basis"], rev, row["id"]),
    )
    return out


def upsert_reservation(
    cur: Any,
    hotel_slug: str,
    data: dict,
    *,
    source_system: str = "PMS",
    nights: list[dict] | None = None,
    contacts: list[dict] | None = None,
    import_batch_id: int | None = None,
    occurred_at: datetime | None = None,
) -> dict:
    """Create or update one reservation by (hotel, source_system, pms_res_id).

    `data` holds WRITABLE columns (dates as date or ISO text, instants
    tz-aware or hotel-local). `nights` ([{stay_date, rate_minor, room_seq?,
    rooms?, room_number?, sc_minor?...}]) gives PMS nightly rates; without
    it nights come from room_revenue_minor, else gross_minor (decomposed),
    else a zero rate. `contacts` ([{kind, value}]) replaces the stored set.
    Returns {id, created, events, changed, stay_chain_id, nights}."""
    hotel = get_hotel(cur, hotel_slug)
    if source_system not in SOURCE_SYSTEMS:
        raise ClefsError(400, f"unknown source_system {source_system!r}", "invalid_reservation")
    pms_res_id = str(data.get("pms_res_id") or "").strip()
    if not pms_res_id:
        raise ClefsError(400, "pms_res_id is required", "invalid_reservation")
    now = clock.now()
    cur.execute(
        "SELECT * FROM reservations WHERE hotel_slug = %s AND source_system = %s AND pms_res_id = %s FOR UPDATE",
        (hotel_slug, source_system, pms_res_id),
    )
    found = cur.fetchone()
    old = dict(found) if found else None
    vals = _clean(hotel, data, old)
    if "channel_code" in vals:
        vals["channel_code"] = ensure_channel(cur, hotel_slug, vals["channel_code"])
    if old is None:
        vals.setdefault("status", "CONFIRMED")
        vals.setdefault("currency", hotel.currency)
        vals.setdefault("rooms_count", 1)
    merged = (old or {}) | vals
    tz, status = hotel.tz, merged["status"]
    if status == "CANCELLED" and merged.get("cancelled_at") is None:
        vals["cancelled_at"] = occurred_at or now
    if status in ARRIVED and merged.get("checked_in_at") is None:
        vals["checked_in_at"] = _default_instant(merged["check_in"], tz, now)
    if status == "CHECKED_OUT":
        out_at = vals.get("checked_out_at") if "checked_out_at" in data else None
        if merged.get("actual_check_out") is None and out_at is not None:
            left = out_at.astimezone(tz).date()
            if merged["check_in"] <= left < merged["check_out"]:
                vals["actual_check_out"] = left
        if merged.get("checked_out_at") is None:
            vals["checked_out_at"] = _default_instant(vals.get("actual_check_out") or merged["check_out"], tz, now)
    merged = (old or {}) | vals
    vals["nights"] = (merged["check_out"] - merged["check_in"]).days
    vals["ota_res_id_norm"] = normalize_ota_res_id(merged.get("channel_code"), merged.get("ota_res_id"))
    vals["guest_name_norm"] = normalize_name(merged.get("guest_name"))
    row = _write_row(cur, hotel_slug, source_system, pms_res_id, old, vals, import_batch_id)
    changed = sorted(k for k in vals if old is None or old.get(k) != row.get(k))
    emitted = _emit_all(cur, row, _events(old, row, now=now, occurred_at=occurred_at))
    if contacts is not None:
        contacts_mod.set_contacts(cur, hotel_slug, row["id"], contacts, import_batch_id=import_batch_id,
                                  occurred_at=occurred_at)
    if old is not None and any(k in changed for k in ("check_out", "actual_check_out")):
        contacts_mod.refresh_lifetimes(cur, row["id"])
    relinked: list[int] = []
    if old is None or contacts is not None or any(k in changed for k in _CHAIN_FIELDS):
        relinked = relink_chain(cur, row["id"])
        row = get_reservation(cur, row["id"]) or row
    today = business_date(hotel)
    out = None
    if old is None or nights is not None or row["id"] in relinked or any(k in changed for k in _NIGHT_FIELDS):
        out = rebuild_nights(cur, row, nights, today=today)
    for other in relinked:
        if other != row["id"]:
            o = get_reservation(cur, other)
            if o is not None:
                rebuild_nights(cur, o, today=today)
    return {"id": int(row["id"]), "created": old is None, "events": emitted, "changed": changed,
            "stay_chain_id": row["stay_chain_id"], "nights": out}
