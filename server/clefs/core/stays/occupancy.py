"""occupancy_daily and occupancy_by_floor_daily (02 spec 2.1; Canon: replaces
hotel_daily_stats), plus baseline_exclusions.

occupancy_daily is derived from stayed reservation_nights. A row's source
ranks MANUAL > PMS = DEMO > DERIVED: a derivation never overwrites a PMS
figure or an owner's manual override, and a PMS figure never overwrites a
manual one. rooms_available keeps out-of-order rooms in (ARN, 04 spec A10);
rooms_ooo is recorded beside it.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.audit import audit
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.time import business_date

RANK = {"DERIVED": 1, "PMS": 2, "DEMO": 2, "MANUAL": 3}
MAX_RANGE_DAYS = 400

# ─── Derivation ───


def rooms_in_register(cur: Any, hotel_slug: str) -> tuple[int, int]:
    """(active rooms, out-of-order now) from hotel_rooms; falls back to
    hotels.rooms_count when the hotel has no room register."""
    cur.execute(
        """
        SELECT count(*) AS n, count(*) FILTER (WHERE service_state = 'ooo') AS ooo
        FROM hotel_rooms WHERE hotel_slug = %s AND is_active
        """,
        (hotel_slug,),
    )
    row = cur.fetchone()
    if row["n"]:
        return int(row["n"]), int(row["ooo"])
    cur.execute("SELECT rooms_count FROM hotels WHERE slug = %s", (hotel_slug,))
    h = cur.fetchone()
    return int((h and h.get("rooms_count")) or 0), 0


_DERIVE = """
WITH days AS (SELECT d::date AS date FROM generate_series(%(start)s::date, %(end)s::date, '1 day') d),
occ AS (
    SELECT n.stay_date AS date, SUM(n.rooms) AS occupied, SUM(n.rate_minor * n.rooms) AS revenue
    FROM reservation_nights n WHERE n.hotel_slug = %(hotel)s AND n.stayed
      AND n.stay_date BETWEEN %(start)s AND %(end)s
    GROUP BY n.stay_date
),
arr AS (
    SELECT r.check_in AS date, SUM(r.rooms_count) AS n FROM reservations r
    WHERE r.hotel_slug = %(hotel)s AND r.status IN ('IN_HOUSE', 'CHECKED_OUT')
      AND r.check_in BETWEEN %(start)s AND %(end)s GROUP BY r.check_in
),
dep AS (
    SELECT COALESCE(r.actual_check_out, r.check_out) AS date, SUM(r.rooms_count) AS n FROM reservations r
    WHERE r.hotel_slug = %(hotel)s AND r.status = 'CHECKED_OUT'
      AND COALESCE(r.actual_check_out, r.check_out) BETWEEN %(start)s AND %(end)s
    GROUP BY COALESCE(r.actual_check_out, r.check_out)
)
INSERT INTO occupancy_daily AS o (hotel_slug, date, rooms_available, rooms_ooo, rooms_occupied, arrivals,
                                  departures, room_revenue_minor, currency, source, is_complete, updated_at)
SELECT %(hotel)s, days.date, %(available)s,
       CASE WHEN days.date = %(today)s THEN %(ooo)s ELSE 0 END,
       COALESCE(occ.occupied, 0), COALESCE(arr.n, 0), COALESCE(dep.n, 0), COALESCE(occ.revenue, 0),
       %(ccy)s, 'DERIVED', days.date < %(today)s, NOW()
FROM days LEFT JOIN occ USING (date) LEFT JOIN arr USING (date) LEFT JOIN dep USING (date)
ON CONFLICT (hotel_slug, date) DO UPDATE SET
    rooms_occupied = EXCLUDED.rooms_occupied, arrivals = EXCLUDED.arrivals, departures = EXCLUDED.departures,
    room_revenue_minor = EXCLUDED.room_revenue_minor, currency = EXCLUDED.currency,
    is_complete = EXCLUDED.is_complete, updated_at = NOW(),
    -- Only today's register is known: history keeps the ooo it was stamped with.
    rooms_ooo = CASE WHEN o.date = %(today)s THEN EXCLUDED.rooms_ooo ELSE o.rooms_ooo END,
    rooms_available = CASE WHEN o.date >= %(today)s THEN EXCLUDED.rooms_available ELSE o.rooms_available END
WHERE o.source = 'DERIVED'
"""

_BY_FLOOR = """
WITH src AS (
    SELECT n.stay_date AS date, COALESCE(hr.floor, '?') AS floor, SUM(n.rooms) AS occupied
    FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
    LEFT JOIN hotel_rooms hr ON hr.hotel_slug = n.hotel_slug
         AND hr.room_number = COALESCE(n.room_number, r.room_number)
    WHERE n.hotel_slug = %(hotel)s AND n.stayed AND n.stay_date BETWEEN %(start)s AND %(end)s
    GROUP BY 1, 2
)
INSERT INTO occupancy_by_floor_daily (hotel_slug, date, floor, rooms_occupied, updated_at)
SELECT %(hotel)s, date, floor, occupied, NOW() FROM src
"""


def derive_occupancy(cur: Any, hotel_slug: str, start: date, end: date, *, today: date | None = None) -> dict:
    """Recompute occupancy_daily (DERIVED rows only) and the by-floor table
    for [start, end] from stayed nights. Rooms with no known floor count
    under floor "?". Returns {days, by_floor_rows}."""
    if end < start:
        raise ValueError("end is before start")
    if (end - start).days > MAX_RANGE_DAYS:
        raise ValueError(f"at most {MAX_RANGE_DAYS} days per call")
    hotel = get_hotel(cur, hotel_slug)
    today = today or business_date(hotel)
    available, ooo = rooms_in_register(cur, hotel_slug)
    params = {"hotel": hotel_slug, "start": start, "end": end, "today": today, "available": available,
              "ooo": ooo, "ccy": hotel.currency}
    cur.execute(_DERIVE, params)
    # Two statements: a DELETE in a CTE is invisible to the INSERT beside it.
    cur.execute("DELETE FROM occupancy_by_floor_daily WHERE hotel_slug = %(hotel)s AND date BETWEEN %(start)s AND %(end)s",
                params)
    cur.execute(_BY_FLOOR, params)
    by_floor = cur.rowcount or 0
    return {"days": (end - start).days + 1, "by_floor_rows": by_floor}


# ─── Overrides (manual wins) ───

_SET_COLS = ("rooms_available", "rooms_ooo", "rooms_occupied", "arrivals", "departures", "room_revenue_minor")


def set_occupancy(
    cur: Any,
    hotel_slug: str,
    day: date,
    values: dict,
    *,
    source: str = "MANUAL",
    actor: Any = None,
    import_batch_id: int | None = None,
) -> dict | None:
    """Write one day's figures as MANUAL (an owner's correction), PMS (a
    night-audit import) or DEMO. Missing fields keep the current (or
    derived) value. A lower-ranked source never overwrites a higher one:
    returns None when refused. MANUAL writes are audited."""
    if source not in ("MANUAL", "PMS", "DEMO"):
        raise ValueError(f"unknown occupancy source {source!r}")
    bad = [k for k in values if k not in _SET_COLS]
    if bad:
        raise ClefsError(400, f"unknown occupancy fields {bad}", "invalid_occupancy")
    if any(values.get(k) is not None and int(values[k]) < 0 for k in _SET_COLS):
        raise ClefsError(400, "occupancy figures cannot be negative", "invalid_occupancy")
    hotel = get_hotel(cur, hotel_slug)
    derive_occupancy(cur, hotel_slug, day, day)  # a base row to override
    cur.execute("SELECT * FROM occupancy_daily WHERE hotel_slug = %s AND date = %s FOR UPDATE", (hotel_slug, day))
    before = dict(cur.fetchone())
    if RANK[before["source"]] > RANK[source]:
        return None
    after = {k: int(values[k]) if values.get(k) is not None else before[k] for k in _SET_COLS}
    if after["rooms_occupied"] > after["rooms_available"] > 0:
        raise ClefsError(400, "rooms_occupied is above rooms_available", "invalid_occupancy")
    uid = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        f"""
        UPDATE occupancy_daily SET {", ".join(f"{k} = %s" for k in _SET_COLS)}, source = %s, currency = %s,
            is_complete = %s, import_batch_id = COALESCE(%s, import_batch_id), updated_by = %s, updated_at = NOW()
        WHERE hotel_slug = %s AND date = %s RETURNING *
        """,
        [*after.values(), source, hotel.currency, day < business_date(hotel), import_batch_id, uid, hotel_slug, day],
    )
    row = dict(cur.fetchone())
    if source == "MANUAL":
        audit(cur, actor, "stays.occupancy_override", "occupancy_daily", day.isoformat(),
              before={k: before[k] for k in (*_SET_COLS, "source")}, after={**after, "source": source},
              hotel_slug=hotel_slug)
    return _shape(row)


def clear_override(cur: Any, hotel_slug: str, day: date, *, actor: Any = None) -> dict:
    """Drop a MANUAL (or PMS) figure and go back to the derived one."""
    cur.execute(
        "DELETE FROM occupancy_daily WHERE hotel_slug = %s AND date = %s AND source <> 'DERIVED' RETURNING source",
        (hotel_slug, day),
    )
    gone = cur.fetchone()
    derive_occupancy(cur, hotel_slug, day, day)
    if gone is not None:
        audit(cur, actor, "stays.occupancy_override_clear", "occupancy_daily", day.isoformat(),
              before={"source": gone["source"]}, hotel_slug=hotel_slug)
    return get_occupancy(cur, hotel_slug, day, day)[0]


# ─── Reads ───


def _ratio(num: int, den: int) -> str | None:
    if not den:
        return None
    return str((Decimal(num) / Decimal(den)).quantize(Decimal("0.0001"), ROUND_HALF_UP))


def _shape(row: dict) -> dict:
    """A row plus occupancy (0..1), ADR and RevPAR in minor units (half up)."""
    occ, avail, rev = int(row["rooms_occupied"]), int(row["rooms_available"]), int(row["room_revenue_minor"])
    adr = int((Decimal(rev) / occ).quantize(Decimal(1), ROUND_HALF_UP)) if occ else None
    revpar = int((Decimal(rev) / avail).quantize(Decimal(1), ROUND_HALF_UP)) if avail else None
    out = {k: row[k] for k in ("date", "rooms_available", "rooms_ooo", "rooms_occupied", "arrivals", "departures",
                               "room_revenue_minor", "currency", "source", "is_complete")}
    return out | {"occupancy": _ratio(occ, avail), "adr_minor": adr, "revpar_minor": revpar}


def get_occupancy(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    """Stored days in [start, end] (derive first for fresh figures)."""
    cur.execute(
        "SELECT * FROM occupancy_daily WHERE hotel_slug = %s AND date BETWEEN %s AND %s ORDER BY date",
        (hotel_slug, start, end),
    )
    return [_shape(dict(r)) for r in cur.fetchall()]


def occupancy_by_floor(cur: Any, hotel_slug: str, start: date, end: date) -> list[dict]:
    cur.execute(
        """
        SELECT date, floor, rooms_occupied FROM occupancy_by_floor_daily
        WHERE hotel_slug = %s AND date BETWEEN %s AND %s ORDER BY date, floor
        """,
        (hotel_slug, start, end),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Baseline exclusions ───


def baseline_exclusions(cur: Any, hotel_slug: str) -> list[dict]:
    """Ranges seasonality baselines skip: the global rows plus the hotel's own."""
    cur.execute(
        """
        SELECT id, hotel_slug, from_date, to_date, reason FROM baseline_exclusions
        WHERE hotel_slug IS NULL OR hotel_slug = %s ORDER BY from_date, id
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def is_excluded(cur: Any, hotel_slug: str, day: date) -> bool:
    cur.execute(
        """
        SELECT 1 FROM baseline_exclusions WHERE (hotel_slug IS NULL OR hotel_slug = %s)
          AND %s BETWEEN from_date AND to_date LIMIT 1
        """,
        (hotel_slug, day),
    )
    return cur.fetchone() is not None


def add_exclusion(cur: Any, hotel_slug: str, from_date: date, to_date: date, reason: str, *, actor: Any = None) -> dict:
    """A hotel's own excluded range (a renovation, a closure). Global rows
    come from migrations only."""
    if to_date < from_date:
        raise ClefsError(400, "to_date is before from_date", "invalid_range")
    if not (reason or "").strip():
        raise ClefsError(400, "a reason is required", "invalid_range")
    uid = getattr(getattr(actor, "principal", actor), "user_id", None)
    cur.execute(
        """
        INSERT INTO baseline_exclusions (hotel_slug, from_date, to_date, reason, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT ((COALESCE(hotel_slug, '')), from_date, to_date) DO UPDATE SET reason = EXCLUDED.reason
        RETURNING id, hotel_slug, from_date, to_date, reason
        """,
        (hotel_slug, from_date, to_date, reason.strip()[:200], uid),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "stays.baseline_exclusion_add", "baseline_exclusion", row["id"], after=row, hotel_slug=hotel_slug)
    return row


def remove_exclusion(cur: Any, hotel_slug: str, exclusion_id: int, *, actor: Any = None) -> bool:
    """Only a hotel's own rows can be removed (never a global one)."""
    cur.execute(
        "DELETE FROM baseline_exclusions WHERE id = %s AND hotel_slug = %s RETURNING id, from_date, to_date, reason",
        (exclusion_id, hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        return False
    audit(cur, actor, "stays.baseline_exclusion_remove", "baseline_exclusion", exclusion_id, before=dict(row),
          hotel_slug=hotel_slug)
    return True
