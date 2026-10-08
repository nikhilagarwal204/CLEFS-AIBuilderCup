"""
The daily variance rollup (02 spec 5.2).

Expected use for item i, floor f, day d: E = basis_count(d, f) x std_i +
requested(d, f, i). basis_count is occupied rooms (occupancy_by_floor_daily,
else the hotel total on floor ''), arrivals (hotel total) or guests
(stayed reservation_nights, else 2 per occupied room). Requests are
amn_request_lines joined to service_requests_v1 for the floor and time.

Variance V = issued - E in units and V x unit_cost(d) in money (cost incl.
excise, from the cost history). Issued is net of returns and leaves out VIP
and DAMAGE lines, which are explained use. A business date without an
occupancy_daily row is not written at all: a missing day is excluded, never
zero-filled, so a gap in the PMS feed never looks like a leak or a saving.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import time as ctime
from clefs.core.money import D
from clefs.modules.amenities import catalog
from clefs.modules.amenities.constants import EXCLUDED_REASONS

GUESTS_PER_ROOM_FALLBACK = Decimal("2")


# ─── Loaders ───


def occupancy(cur: Any, hotel_slug: str, start: date, end: date) -> dict[date, dict]:
    """{date: {orn, arrivals, floors: {floor: occupied}}} for days that have
    an occupancy_daily row; other days are absent (excluded)."""
    cur.execute("SELECT date, rooms_occupied, arrivals FROM occupancy_daily WHERE hotel_slug = %s "
                "AND date BETWEEN %s AND %s", (hotel_slug, start, end))
    out = {r["date"]: {"orn": int(r["rooms_occupied"]), "arrivals": int(r["arrivals"]), "floors": {}}
           for r in cur.fetchall()}
    cur.execute("SELECT date, floor, rooms_occupied FROM occupancy_by_floor_daily WHERE hotel_slug = %s "
                "AND date BETWEEN %s AND %s", (hotel_slug, start, end))
    for r in cur.fetchall():
        if r["date"] in out:
            out[r["date"]]["floors"][str(r["floor"])] = int(r["rooms_occupied"])
    return out


def guest_nights(cur: Any, hotel_slug: str, start: date, end: date) -> dict[date, Decimal]:
    """{stay_date: guests} over STAYED nights only. WHY n.stayed: write_nights
    keeps every booked night and marks the ones after an early departure
    stayed = FALSE; counting them would inflate expected use, shrink the
    variance and hide leakage (and shrink the verified 'saved' figure). This
    is the same rule occupancy_daily, TD liability and channel economics use."""
    cur.execute(
        """
        SELECT n.stay_date, SUM((COALESCE(r.adults, 2) + COALESCE(r.children, 0)) * n.rooms) AS guests
        FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
        WHERE n.hotel_slug = %s AND n.stay_date BETWEEN %s AND %s AND n.stayed
          AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
        GROUP BY n.stay_date
        """,
        (hotel_slug, start, end),
    )
    return {r["stay_date"]: D(r["guests"]) for r in cur.fetchall()}


def issued(cur: Any, hotel_slug: str, start: date, end: date) -> dict[tuple, list[Decimal]]:
    """{(date, item_id, floor): [counted, excluded]} net of returns."""
    cur.execute(
        """
        SELECT business_date, item_id, COALESCE(to_floor, '') AS floor,
               COALESCE(SUM(qty) FILTER (WHERE reason <> ALL(%s)), 0) AS counted,
               COALESCE(SUM(qty) FILTER (WHERE reason = ANY(%s)), 0) AS excluded
        FROM amn_issuance
        WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s AND voided_at IS NULL
        GROUP BY 1, 2, 3
        """,
        (list(EXCLUDED_REASONS), list(EXCLUDED_REASONS), hotel_slug, start, end),
    )
    return {(r["business_date"], r["item_id"], r["floor"]): [D(r["counted"]), D(r["excluded"])]
            for r in cur.fetchall()}


def requested(cur: Any, hotel: Any, start: date, end: date) -> dict[tuple, Decimal]:
    """{(business_date, item_id, floor): units} from request lines. The
    business date of a request is its creation instant in hotel time."""
    t0 = ctime.business_day_bounds(hotel, start)[0]
    t1 = ctime.business_day_bounds(hotel, end)[1]
    cur.execute(
        """
        SELECT v.created_at, COALESCE(v.floor, '') AS floor, l.item_id, l.qty
        FROM amn_request_lines l JOIN service_requests_v1 v ON v.id = l.request_id AND v.hotel_slug = l.hotel_slug
        WHERE l.hotel_slug = %s AND v.created_at >= %s AND v.created_at < %s
          AND COALESCE(v.status, '') NOT IN ('cancelled', 'canceled')
        """,
        (hotel.slug, t0, t1),
    )
    out: dict[tuple, Decimal] = defaultdict(Decimal)
    for r in cur.fetchall():
        out[(ctime.business_date(hotel, r["created_at"]), r["item_id"], str(r["floor"]))] += D(r["qty"])
    return out


# ─── Rollup ───


def _basis(item: dict, occ_day: dict, guests: Decimal | None) -> dict[str, Decimal]:
    """{floor: basis_count} for one item and day."""
    b = item["basis"]
    if b == "PER_OCCUPIED_ROOM_NIGHT":
        if occ_day["floors"]:
            return {f: Decimal(n) for f, n in occ_day["floors"].items()}
        return {"": Decimal(occ_day["orn"])}
    if b == "PER_ARRIVAL":
        return {"": Decimal(occ_day["arrivals"])}
    if b == "PER_GUEST_NIGHT":
        return {"": guests if guests is not None else Decimal(occ_day["orn"]) * GUESTS_PER_ROOM_FALLBACK}
    return {}


def _minor(units: Decimal, cost: int) -> int:
    return int((units * cost).to_integral_value())


def rollup(cur: Any, hotel: Any, start: date, end: date) -> dict:
    """Recompute amn_variance_daily and amn_daily_totals for [start, end].
    Idempotent: the range is deleted and rewritten in the caller's
    transaction."""
    slug, ccy = hotel.slug, hotel.currency
    occ = occupancy(cur, slug, start, end)
    guests = guest_nights(cur, slug, start, end)
    iss = issued(cur, slug, start, end)
    req = requested(cur, hotel, start, end)
    items = {i["id"]: i for i in catalog.list_items(cur, slug)}
    book = catalog.CostBook(cur, slug)
    cur.execute("DELETE FROM amn_variance_daily WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s",
                (slug, start, end))
    cur.execute("DELETE FROM amn_daily_totals WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s",
                (slug, start, end))
    iss_by_day: dict[date, list] = defaultdict(list)
    for (dd, iid, f), v in iss.items():
        iss_by_day[dd].append((iid, f, v))
    req_by_day: dict[date, list] = defaultdict(list)
    for (dd, iid, f), units in req.items():
        req_by_day[dd].append((iid, f, units))
    rows, totals = [], []
    for d in sorted(occ):
        o = occ[d]
        cells: dict[tuple[int, str], dict] = defaultdict(lambda: {"basis": Decimal(0), "std": Decimal(0),
                                                                  "req": Decimal(0), "iss": Decimal(0),
                                                                  "exc": Decimal(0)})
        for iid, it in items.items():
            if not it["active"]:
                continue
            for f, n in _basis(it, o, guests.get(d)).items():
                c = cells[(iid, f)]
                c["basis"] += n
                c["std"] += n * D(it["std_qty"])
        for iid, f, (counted, excluded) in iss_by_day.get(d, ()):
            if iid in items:
                cells[(iid, f)]["iss"] += counted
                cells[(iid, f)]["exc"] += excluded
        for iid, f, units in req_by_day.get(d, ()):
            if iid in items:
                cells[(iid, f)]["req"] += units
        day_iss = day_exp = 0
        day_req = day_units = Decimal(0)
        for (iid, f), c in cells.items():
            exp = c["std"] + c["req"]
            if not (exp or c["iss"] or c["exc"]):
                continue
            cost = book.on(iid, d)
            var = c["iss"] - exp
            rows.append((slug, d, iid, f, c["basis"], c["std"], c["req"], exp, c["iss"], c["exc"], var, cost,
                         _minor(c["iss"], cost), _minor(exp, cost), _minor(var, cost), ccy))
            day_iss += _minor(c["iss"], cost)
            day_exp += _minor(exp, cost)
            day_req += c["req"]
            day_units += c["iss"]
        totals.append((slug, d, o["orn"], o["arrivals"], day_units, day_iss, day_exp, int(day_req), ccy))
    if rows:
        cur.executemany(
            "INSERT INTO amn_variance_daily (hotel_slug, business_date, item_id, floor, basis_count, expected_std, "
            "requested, expected_total, issued, issued_excluded, variance_units, unit_cost_minor, issued_minor, "
            "expected_minor, variance_minor, currency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s)", rows)
    if totals:
        cur.executemany(
            "INSERT INTO amn_daily_totals (hotel_slug, business_date, orn, arrivals, issued_units, issued_minor, "
            "expected_minor, requests, currency) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", totals)
    missing = (end - start).days + 1 - len(occ)
    return {"days": len(occ), "days_missing_occupancy": missing, "rows": len(rows)}


# ─── Windows ───
# Floor closets hold 1-2 days of stock, so one day's issuance says little: a
# Monday restock of two days is a spike on Monday and a dip on Tuesday. Alerts
# therefore judge 7-day sums, hotel-wide per item. Window k covers the 7
# business dates ending 7k days before `end` (k = 0 is the current week).


def item_windows(cur: Any, hotel_slug: str, end: date, *, count: int) -> dict[int, dict[int, dict]]:
    """{item_id: {k: {expected, issued, variance, variance_minor, expected_minor,
    issued_minor, requested}}} for windows k = 0..count-1."""
    start = end - timedelta(days=7 * count - 1)
    cur.execute(
        """
        SELECT item_id, (%s::date - business_date) / 7 AS k,
               SUM(expected_total) AS expected, SUM(issued) AS issued, SUM(variance_units) AS variance,
               SUM(variance_minor) AS variance_minor, SUM(expected_minor) AS expected_minor,
               SUM(issued_minor) AS issued_minor, SUM(requested) AS requested
        FROM amn_variance_daily WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s
        GROUP BY 1, 2
        """,
        (end, hotel_slug, start, end),
    )
    out: dict[int, dict[int, dict]] = defaultdict(dict)
    for r in cur.fetchall():
        out[r["item_id"]][int(r["k"])] = {k: D(r[k]) for k in ("expected", "issued", "variance", "variance_minor",
                                                               "expected_minor", "issued_minor", "requested")}
    return out


def window_coverage(cur: Any, hotel_slug: str, end: date, *, count: int) -> dict[int, int]:
    """{k: business dates with occupancy} per window."""
    start = end - timedelta(days=7 * count - 1)
    cur.execute("SELECT (%s::date - business_date) / 7 AS k, COUNT(*) AS n FROM amn_daily_totals "
                "WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s GROUP BY 1", (end, hotel_slug, start, end))
    return {int(r["k"]): int(r["n"]) for r in cur.fetchall()}


def covered_days(cur: Any, hotel_slug: str, start: date, end: date) -> int:
    """Business dates in [start, end] that have occupancy (a rollup row)."""
    cur.execute("SELECT COUNT(*) AS n FROM amn_daily_totals WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s",
                (hotel_slug, start, end))
    return int(cur.fetchone()["n"])


def scoped_sum(cur: Any, hotel_slug: str, item_id: int, start: date, end: date, *, floor: str | None = None) -> dict:
    """{expected, issued, variance, variance_minor} for one item over [start,
    end], on one floor when `floor` is given, else hotel-wide. Zeros when the
    range has no rows."""
    cur.execute(
        "SELECT COALESCE(SUM(expected_total), 0) AS expected, COALESCE(SUM(issued), 0) AS issued, "
        "COALESCE(SUM(variance_units), 0) AS variance, COALESCE(SUM(variance_minor), 0) AS variance_minor, "
        "COUNT(*) AS n FROM amn_variance_daily WHERE hotel_slug = %s AND item_id = %s "
        "AND business_date BETWEEN %s AND %s AND (%s::text IS NULL OR floor = %s::text)",
        (hotel_slug, item_id, start, end, floor, floor),
    )
    r = cur.fetchone()
    return {"expected": D(r["expected"]), "issued": D(r["issued"]), "variance": D(r["variance"]),
            "variance_minor": int(r["variance_minor"]), "rows": int(r["n"])}


def daily_series(cur: Any, hotel_slug: str, item_id: int, start: date, end: date) -> list[dict]:
    """[{date, orn, issued, expected}] hotel-wide per covered day (0 for days
    the item had no row but occupancy existed)."""
    cur.execute(
        """
        SELECT t.business_date AS d, t.orn, COALESCE(SUM(v.issued), 0) AS issued,
               COALESCE(SUM(v.expected_total), 0) AS expected
        FROM amn_daily_totals t
        LEFT JOIN amn_variance_daily v ON v.hotel_slug = t.hotel_slug AND v.business_date = t.business_date
                                      AND v.item_id = %s
        WHERE t.hotel_slug = %s AND t.business_date BETWEEN %s AND %s
        GROUP BY t.business_date, t.orn ORDER BY t.business_date
        """,
        (item_id, hotel_slug, start, end),
    )
    return [{"date": r["d"], "orn": int(r["orn"]), "issued": D(r["issued"]), "expected": D(r["expected"])}
            for r in cur.fetchall()]


def occupancy_residual(series: list[dict], last_days: int = 7) -> dict | None:
    """OLS issued ~ a + b * occupied over the series; the residual sum of the
    last `last_days` against 2 sigma x sqrt(n). None with too little data."""
    if len(series) < 21:
        return None
    xs = [Decimal(p["orn"]) for p in series]
    ys = [p["issued"] for p in series]
    n = Decimal(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx else Decimal(0)
    a = my - b * mx
    res = [y - (a + b * x) for x, y in zip(xs, ys)]
    sigma = (sum(r * r for r in res) / (n - 2)).sqrt() if n > 2 else Decimal(0)
    recent = sum(res[-last_days:])
    bound = 2 * sigma * Decimal(last_days).sqrt()
    return {"alpha": round(a, 3), "beta": round(b, 4), "sigma": round(sigma, 3), "recent_residual": round(recent, 1),
            "bound": round(bound, 1), "unexplained": bool(sigma and recent > bound)}
