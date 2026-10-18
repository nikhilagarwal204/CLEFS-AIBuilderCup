"""
Amenity reports (02 spec 5.2, 5.3): the grouped variance report, CPOR,
minibar unbilled, the neutral per-staff view, item trends and the overview
the console's first screen reads. Everything reads the rollup tables
(amn_variance_daily, amn_daily_totals) or the issuance log; nothing here
writes.

Money leaves as Decimal strings in the hotel currency next to *_minor ints.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core.money import D, money_str
from clefs.modules.amenities import catalog
from clefs.modules.amenities.constants import (
    COUNTER_ROTATION_SHARE,
    EXCLUDED_REASONS,
    STAFF_MIN_DAYS,
    STAFF_MIN_PEOPLE,
)

q = catalog.qty_str
GROUPS = ("item", "floor", "shift", "staff")
STAFF_NOTE = ("Issued per occupied room served, on the floors and days each person issued to. Differences often "
              "come from floors, room mix and guest profile; use this to plan rotation and counts, never to assign "
              "blame.")


def _m(minor: Any, ccy: str) -> str | None:
    return money_str(int(D(minor or 0).to_integral_value()), ccy)


def _ratio(num: Decimal, den: Decimal) -> str | None:
    return str(round(num / den * 100, 1)) if den else None


# ─── Grouped variance report ───


def report(cur: Any, hotel: Any, start: date, end: date, group: str = "item") -> dict:
    ccy = hotel.currency
    if group == "staff":
        return staff_view(cur, hotel, start, end)
    if group == "shift":
        return _shift_report(cur, hotel, start, end)
    key = "v.item_id, i.key, i.name_en, i.category" if group == "item" else "v.floor"
    cur.execute(
        f"""
        SELECT {key}, SUM(v.expected_total) AS expected, SUM(v.requested) AS requested, SUM(v.issued) AS issued,
               SUM(v.issued_excluded) AS excluded, SUM(v.variance_units) AS variance,
               SUM(v.issued_minor) AS issued_minor, SUM(v.expected_minor) AS expected_minor,
               SUM(v.variance_minor) AS variance_minor
        FROM amn_variance_daily v JOIN amn_items i ON i.id = v.item_id
        WHERE v.hotel_slug = %s AND v.business_date BETWEEN %s AND %s
        GROUP BY {key} ORDER BY SUM(v.variance_minor) DESC
        """,
        (hotel.slug, start, end),
    )
    rows = []
    for r in cur.fetchall():
        row = {"expected": q(r["expected"]), "requested": q(r["requested"]), "issued": q(r["issued"]),
               "issued_excluded": q(r["excluded"]), "variance_units": q(round(D(r["variance"]), 1)),
               "variance_pct": _ratio(D(r["variance"]), D(r["expected"])),
               "issued_value": _m(r["issued_minor"], ccy), "expected_value": _m(r["expected_minor"], ccy),
               "variance_value": _m(r["variance_minor"], ccy), "variance_minor": int(r["variance_minor"])}
        if group == "item":
            row.update(item_id=r["item_id"], key=r["key"], name=r["name_en"], category=r["category"])
        else:
            row.update(floor=r["floor"] or None)
        rows.append(row)
    cov = coverage(cur, hotel.slug, start, end)
    return {"group": group, "start": start.isoformat(), "end": end.isoformat(), "currency": ccy, "rows": rows,
            **cov}


def coverage(cur: Any, slug: str, start: date, end: date) -> dict:
    cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(orn), 0) AS orn FROM amn_daily_totals WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s", (slug, start, end))
    r = cur.fetchone()
    days = (end - start).days + 1
    return {"days": days, "days_covered": int(r["n"]), "days_missing_occupancy": days - int(r["n"]),
            "orn": int(r["orn"])}


def _shift_report(cur: Any, hotel: Any, start: date, end: date) -> dict:
    ccy = hotel.currency
    cur.execute(
        """
        SELECT COALESCE(shift, 'UNKNOWN') AS shift, SUM(qty) AS issued,
               SUM(qty * COALESCE(unit_cost_minor, 0)) AS issued_minor, COUNT(*) AS lines
        FROM amn_issuance WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s AND voided_at IS NULL
          AND reason <> ALL(%s)
        GROUP BY 1 ORDER BY 1
        """,
        (hotel.slug, start, end, list(EXCLUDED_REASONS)),
    )
    rows = cur.fetchall()
    total = sum((D(r["issued_minor"]) for r in rows), Decimal(0))
    return {"group": "shift", "start": start.isoformat(), "end": end.isoformat(), "currency": ccy,
            "rows": [{"shift": r["shift"], "issued": q(r["issued"]), "lines": int(r["lines"]),
                      "issued_value": _m(r["issued_minor"], ccy), "share_pct": _ratio(D(r["issued_minor"]), total)}
                     for r in rows],
            "note": "Expected use is a per-day figure, so shifts are compared on what they issued, not on variance.",
            **coverage(cur, hotel.slug, start, end)}


# ─── CPOR ───
# CPOR = net issued money / occupied room-nights, next to the expected CPOR
# (what the standards and logged requests would have cost). Days without
# occupancy are outside both sums.


def cpor(cur: Any, hotel: Any, start: date, end: date) -> dict:
    ccy = hotel.currency
    cur.execute("SELECT business_date, orn, issued_minor, expected_minor FROM amn_daily_totals WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s ORDER BY business_date", (hotel.slug, start, end))
    days = cur.fetchall()
    orn = sum(int(d["orn"]) for d in days)
    iss = sum(int(d["issued_minor"]) for d in days)
    exp = sum(int(d["expected_minor"]) for d in days)

    def per(minor: int, n: int) -> str | None:
        return _m(Decimal(minor) / n, ccy) if n else None

    return {
        "start": start.isoformat(), "end": end.isoformat(), "currency": ccy, "orn": orn,
        "cpor": per(iss, orn), "expected_cpor": per(exp, orn), "issued_value": _m(iss, ccy),
        "expected_value": _m(exp, ccy), "gap": per(iss - exp, orn),
        "series": [{"date": d["business_date"].isoformat(), "orn": int(d["orn"]),
                    "cpor": per(int(d["issued_minor"]), int(d["orn"])),
                    "expected_cpor": per(int(d["expected_minor"]), int(d["orn"]))} for d in days],
        **{k: v for k, v in coverage(cur, hotel.slug, start, end).items() if k != "orn"},
    }


# ─── Minibar ───
# Unbilled = refills issued - MINIBAR quantity on the folio (reversals net).
# Folio lines carry no item, so the value uses the refills' own average
# price (cost when an item has no price). Without any MINIBAR folio charge in
# 90 days the hotel does not import folios: the figure is not shown then.


def minibar(cur: Any, hotel: Any, start: date, end: date) -> dict:
    ccy = hotel.currency
    cur.execute(
        """
        SELECT COALESCE(SUM(s.qty), 0) AS units,
               COALESCE(SUM(s.qty * COALESCE(i.price_minor, s.unit_cost_minor, 0)), 0) AS value_minor
        FROM amn_issuance s JOIN amn_items i ON i.id = s.item_id
        WHERE s.hotel_slug = %s AND i.category = 'MINIBAR' AND s.business_date BETWEEN %s AND %s
          AND s.voided_at IS NULL AND s.reason <> ALL(%s)
        """,
        (hotel.slug, start, end, list(EXCLUDED_REASONS)),
    )
    r = cur.fetchone()
    refills, refill_value = D(r["units"]), D(r["value_minor"])
    cur.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN is_reversal THEN -ABS(qty) ELSE qty END), 0) AS units,
               COALESCE(SUM(amount_minor), 0) AS amount_minor,
               (SELECT COUNT(*) FROM folio_charges WHERE hotel_slug = %s AND code = 'MINIBAR'
                  AND posted_date BETWEEN %s AND %s) AS recent
        FROM folio_charges WHERE hotel_slug = %s AND code = 'MINIBAR' AND posted_date BETWEEN %s AND %s
        """,
        (hotel.slug, end - timedelta(days=89), end, hotel.slug, start, end),
    )
    f = cur.fetchone()
    has_folio = int(f["recent"]) > 0
    billed = D(f["units"])
    unbilled = max(refills - billed, Decimal(0))
    avg_price = refill_value / refills if refills > 0 else Decimal(0)
    unbilled_minor = int((unbilled * avg_price).to_integral_value())
    return {
        "start": start.isoformat(), "end": end.isoformat(), "currency": ccy, "has_folio": has_folio,
        "refills": q(refills), "billed": q(billed), "unbilled": q(unbilled) if has_folio else None,
        "unbilled_share_pct": _ratio(unbilled, refills) if has_folio else None,
        "unbilled_value": _m(unbilled_minor, ccy) if has_folio else None,
        "unbilled_minor": unbilled_minor if has_folio else None, "billed_value": _m(f["amount_minor"], ccy),
    }


# ─── Staff view ───
# Shown only with 14+ days of issuance and 3+ issuers. Each issuer gets
# "issued per occupied room served": units over the occupied rooms on the
# (day, floor) pairs they issued to. A counter-rotation note appears when one
# issuer accounts for more than 60% of the above-expected money, allocated
# per (day, floor) by each issuer's share of what was issued there.


def staff_view(cur: Any, hotel: Any, start: date, end: date) -> dict:
    ccy, slug = hotel.currency, hotel.slug
    base = {"group": "staff", "start": start.isoformat(), "end": end.isoformat(), "currency": ccy,
            "note": STAFF_NOTE}
    cur.execute(
        """
        SELECT staff_name, business_date, COALESCE(to_floor, '') AS floor, item_id, SUM(qty) AS units,
               SUM(qty * COALESCE(unit_cost_minor, 0)) AS minor
        FROM amn_issuance WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s AND voided_at IS NULL
          AND reason <> ALL(%s) AND staff_name IS NOT NULL AND staff_name <> ''
        GROUP BY 1, 2, 3, 4
        """,
        (slug, start, end, list(EXCLUDED_REASONS)),
    )
    lines = cur.fetchall()
    people = {r["staff_name"] for r in lines}
    days = {r["business_date"] for r in lines}
    if len(days) < STAFF_MIN_DAYS or len(people) < STAFF_MIN_PEOPLE:
        return {**base, "available": False, "rows": [],
                "reason": f"Needs at least {STAFF_MIN_DAYS} days of issuance and {STAFF_MIN_PEOPLE} people issuing "
                          f"(have {len(days)} days, {len(people)} people)."}
    cur.execute("SELECT date, floor, rooms_occupied FROM occupancy_by_floor_daily WHERE hotel_slug = %s "
                "AND date BETWEEN %s AND %s", (slug, start, end))
    floor_occ = {(r["date"], str(r["floor"])): int(r["rooms_occupied"]) for r in cur.fetchall()}
    cur.execute("SELECT business_date, orn FROM amn_daily_totals WHERE hotel_slug = %s AND business_date "
                "BETWEEN %s AND %s", (slug, start, end))
    day_orn = {r["business_date"]: int(r["orn"]) for r in cur.fetchall()}
    # Above-expected money per (day, floor, item): items are kept apart so an
    # unissued item's shortfall never hides another item's excess.
    cur.execute("SELECT business_date, floor, item_id, variance_minor FROM amn_variance_daily WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s AND variance_minor > 0", (slug, start, end))
    pos_var = {(r["business_date"], r["floor"], r["item_id"]): D(r["variance_minor"]) for r in cur.fetchall()}
    cell_total: dict[tuple, Decimal] = defaultdict(Decimal)
    for r in lines:
        cell_total[(r["business_date"], r["floor"], r["item_id"])] += D(r["minor"])
    agg: dict[str, dict] = defaultdict(lambda: {"units": Decimal(0), "minor": Decimal(0), "served": set(),
                                                "days": set(), "floors": set(), "var": Decimal(0)})
    for r in lines:
        a = agg[r["staff_name"]]
        cell = (r["business_date"], r["floor"], r["item_id"])
        a["units"] += D(r["units"])
        a["minor"] += D(r["minor"])
        a["days"].add(r["business_date"])
        if r["floor"]:
            a["floors"].add(r["floor"])
        a["served"].add((r["business_date"], r["floor"]))
        if cell in pos_var and cell_total[cell] > 0:
            a["var"] += pos_var[cell] * D(r["minor"]) / cell_total[cell]
    for a in agg.values():
        # Rooms served: the occupied rooms of each (day, floor) issued to, once
        # per pair however many items went there; floor unknown = the hotel.
        a["rooms"] = sum(floor_occ.get((d, f), 0) if f else day_orn.get(d, 0) for d, f in a["served"])
    total_var = sum((a["var"] for a in agg.values()), Decimal(0))
    rows = []
    for name, a in sorted(agg.items()):
        rows.append({"staff_name": name, "issued": q(a["units"]), "issued_value": _m(a["minor"], ccy),
                     "days": len(a["days"]), "floors": sorted(a["floors"]), "rooms_served": a["rooms"],
                     "issued_per_room": str(round(a["units"] / a["rooms"], 2)) if a["rooms"] else None,
                     "share_of_above_expected_pct": _ratio(a["var"], total_var) if total_var > 0 else None})
    top = max(agg.values(), key=lambda a: a["var"]) if agg else None
    rotation = bool(top and total_var > 0 and top["var"] / total_var > COUNTER_ROTATION_SHARE)
    return {**base, "available": True, "rows": rows, "counter_rotation": rotation,
            "counter_rotation_note": ("Most of the above-expected use sits with one issuer's floors and days. "
                                      "Rotate who issues and who counts, and never let anyone count their own "
                                      "floor.") if rotation else None}


# ─── Item trend and overview ───


def item_trend(cur: Any, hotel: Any, item: dict, start: date, end: date) -> dict:
    ccy, slug = hotel.currency, hotel.slug
    cur.execute(
        """
        SELECT t.business_date AS d, COALESCE(SUM(v.expected_total), 0) AS expected,
               COALESCE(SUM(v.requested), 0) AS requested, COALESCE(SUM(v.issued), 0) AS issued,
               COALESCE(SUM(v.variance_units), 0) AS variance, COALESCE(SUM(v.variance_minor), 0) AS vm
        FROM amn_daily_totals t LEFT JOIN amn_variance_daily v ON v.hotel_slug = t.hotel_slug
             AND v.business_date = t.business_date AND v.item_id = %s
        WHERE t.hotel_slug = %s AND t.business_date BETWEEN %s AND %s GROUP BY t.business_date ORDER BY 1
        """,
        (item["id"], slug, start, end),
    )
    series = [{"date": r["d"].isoformat(), "expected": q(round(D(r["expected"]), 1)), "requested": q(r["requested"]),
               "issued": q(r["issued"]), "variance": q(round(D(r["variance"]), 1)), "variance_value": _m(r["vm"], ccy)}
              for r in cur.fetchall()]
    cur.execute(
        "SELECT floor, SUM(expected_total) AS e, SUM(issued) AS i, SUM(variance_minor) AS vm FROM amn_variance_daily "
        "WHERE hotel_slug = %s AND item_id = %s AND business_date BETWEEN %s AND %s GROUP BY floor ORDER BY floor",
        (slug, item["id"], start, end))
    floors = [{"floor": r["floor"] or None, "expected": q(round(D(r["e"]), 1)), "issued": q(r["i"]),
               "variance_value": _m(r["vm"], ccy)} for r in cur.fetchall()]
    cur.execute(
        "SELECT COALESCE(shift, 'UNKNOWN') AS shift, SUM(qty) AS units FROM amn_issuance WHERE hotel_slug = %s "
        "AND item_id = %s AND business_date BETWEEN %s AND %s AND voided_at IS NULL AND reason <> ALL(%s) "
        "GROUP BY 1 ORDER BY 1", (slug, item["id"], start, end, list(EXCLUDED_REASONS)))
    shifts = [{"shift": r["shift"], "issued": q(r["units"])} for r in cur.fetchall()]
    return {"item": catalog.item_out(item), "start": start.isoformat(), "end": end.isoformat(), "currency": ccy,
            "series": series, "by_floor": floors, "by_shift": shifts}


def overview(cur: Any, hotel: Any, end: date) -> dict:
    """The console's first screen: 7- and 30-day variance, CPOR, top leaking
    items and requested vs issued vs expected per item."""
    ccy, slug = hotel.currency, hotel.slug
    s7, s30 = end - timedelta(days=6), end - timedelta(days=29)
    cur.execute(
        """
        SELECT COALESCE(SUM(variance_minor) FILTER (WHERE business_date >= %s), 0) AS v7,
               COALESCE(SUM(variance_minor), 0) AS v30
        FROM amn_variance_daily WHERE hotel_slug = %s AND business_date BETWEEN %s AND %s
        """,
        (s7, slug, s30, end),
    )
    v = cur.fetchone()
    items30 = report(cur, hotel, s30, end, "item")
    top = [r for r in items30["rows"] if r["variance_minor"] > 0][:5]
    cur.execute("SELECT COUNT(*) FILTER (WHERE needs_review) AS review, COUNT(*) AS n FROM amn_items "
                "WHERE hotel_slug = %s AND active", (slug,))
    cat = cur.fetchone()
    return {
        "end": end.isoformat(), "currency": ccy,
        "variance_7d": _m(v["v7"], ccy), "variance_7d_minor": int(v["v7"]),
        "variance_30d": _m(v["v30"], ccy), "variance_30d_minor": int(v["v30"]),
        "cpor_30d": {k: cpor(cur, hotel, s30, end)[k] for k in ("cpor", "expected_cpor", "gap", "orn")},
        "top_leaking": top,
        "chart": [{"item_id": r["item_id"], "name": r["name"], "requested": r["requested"], "issued": r["issued"],
                   "expected": r["expected"]} for r in items30["rows"][:8]],
        "catalog": {"active": int(cat["n"]), "needs_review": int(cat["review"])},
        "days_covered_30d": items30["days_covered"], "days_missing_occupancy_30d": items30["days_missing_occupancy"],
    }
