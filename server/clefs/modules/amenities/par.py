"""
Par recompute (02 spec 5.2): par = daily_usage x days_of_supply + safety.

- daily_usage = forecast_occ x std + r x forecast_occ / 100, scaled to the
  location's share of occupied rooms (a floor closet serves its floor).
- forecast_occ = mean on-the-books rooms for the supply period (latest
  otb_snapshots row per stay date) plus pickup, where pickup is the mean of
  (actual occupied - OTB seen 7 days before) over the last 28 days. Without
  OTB it is the trailing 28-day mean occupancy.
- safety = 1.65 x sigma_daily x sqrt(lead_days), sigma_daily from the last
  28 days of issuance (hotel-wide, scaled by the same share).

Pinned rows (a manual par) are left alone; their inputs are still stored in
`computed` so the console can show what the recompute would suggest.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from clefs.core.money import D
from clefs.db import Json
from clefs.modules.amenities import catalog, rates, stock
from clefs.modules.amenities.constants import PAR_HISTORY_DAYS, PAR_Z, RATE_PRIORS
from clefs.modules.amenities.variance import GUESTS_PER_ROOM_FALLBACK

PICKUP_LEAD = 7


def forecast_occupancy(cur: Any, hotel_slug: str, today: date, days: int) -> dict:
    hist0 = today - timedelta(days=PAR_HISTORY_DAYS)
    cur.execute("SELECT date, rooms_occupied, arrivals FROM occupancy_daily WHERE hotel_slug = %s "
                "AND date >= %s AND date < %s", (hotel_slug, hist0, today))
    hist = {r["date"]: r for r in cur.fetchall()}
    trailing = (sum(Decimal(r["rooms_occupied"]) for r in hist.values()) / len(hist)) if hist else Decimal(0)
    arrivals = (sum(Decimal(r["arrivals"]) for r in hist.values()) / len(hist)) if hist else Decimal(0)
    cur.execute(
        """
        SELECT DISTINCT ON (stay_date) stay_date, rooms_otb FROM otb_snapshots
        WHERE hotel_slug = %s AND stay_date >= %s AND stay_date < %s AND snapshot_date <= %s
        ORDER BY stay_date, snapshot_date DESC
        """,
        (hotel_slug, today, today + timedelta(days=days), today),
    )
    otb = [Decimal(r["rooms_otb"]) for r in cur.fetchall()]
    cur.execute(
        "SELECT o.date, o.rooms_occupied - s.rooms_otb AS pickup FROM occupancy_daily o JOIN otb_snapshots s "
        "ON s.hotel_slug = o.hotel_slug AND s.stay_date = o.date AND s.snapshot_date = o.date - %s "
        "WHERE o.hotel_slug = %s AND o.date >= %s AND o.date < %s", (PICKUP_LEAD, hotel_slug, hist0, today))
    pick = [Decimal(r["pickup"]) for r in cur.fetchall()]
    if otb:
        pickup = max(sum(pick) / len(pick), Decimal(0)) if pick else Decimal(0)
        return {"occupied": sum(otb) / len(otb) + pickup, "arrivals": arrivals, "source": "otb_pickup",
                "pickup": pickup}
    return {"occupied": trailing, "arrivals": arrivals, "source": "trailing_28d", "pickup": None}


def floor_share(cur: Any, hotel_slug: str, floor: str | None, today: date) -> Decimal:
    if not floor:
        return Decimal(1)
    cur.execute("SELECT COALESCE(SUM(rooms_occupied) FILTER (WHERE floor = %s), 0) AS f, "
                "COALESCE(SUM(rooms_occupied), 0) AS t FROM occupancy_by_floor_daily WHERE hotel_slug = %s "
                "AND date >= %s AND date < %s", (floor, hotel_slug, today - timedelta(days=PAR_HISTORY_DAYS), today))
    r = cur.fetchone()
    return D(r["f"]) / D(r["t"]) if D(r["t"]) else Decimal(1)


def issued_sigma(cur: Any, hotel_slug: str, item_id: int, today: date) -> Decimal:
    start = today - timedelta(days=PAR_HISTORY_DAYS)
    cur.execute("SELECT t.business_date, COALESCE(SUM(v.issued), 0) AS i FROM amn_daily_totals t "
                "LEFT JOIN amn_variance_daily v ON v.hotel_slug = t.hotel_slug AND v.business_date = t.business_date "
                "AND v.item_id = %s WHERE t.hotel_slug = %s AND t.business_date >= %s AND t.business_date < %s "
                "GROUP BY t.business_date", (item_id, hotel_slug, start, today))
    xs = [D(r["i"]) for r in cur.fetchall()]
    if len(xs) < 2:
        return Decimal(0)
    mu = sum(xs) / len(xs)
    return (sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)).sqrt()


def recompute(cur: Any, hotel: Any, today: date) -> dict:
    slug = hotel.slug
    learned = rates.rates_by_item(cur, slug)
    fc_cache: dict[int, dict] = {}
    updated = pinned = 0
    for p in stock.list_par(cur, slug):
        item = catalog.get_item(cur, slug, p["item_id"])
        loc = stock.get_location(cur, slug, p["location_id"])
        dos, lead = D(p["days_of_supply"]), D(p["lead_days"])
        horizon = max(1, min(14, math.ceil(dos)))
        fc = fc_cache.setdefault(horizon, forecast_occupancy(cur, slug, today, horizon))
        share = floor_share(cur, slug, loc.get("floor"), today) if loc["kind"] in ("FLOOR_CLOSET", "CART") else Decimal(1)
        occ = fc["occupied"] * share
        std = D(item["std_qty"])
        basis = {"PER_ARRIVAL": fc["arrivals"] * share, "PER_GUEST_NIGHT": occ * GUESTS_PER_ROOM_FALLBACK,
                 "PER_OCCUPIED_ROOM_NIGHT": occ}.get(item["basis"], Decimal(0))
        r = learned.get(item["id"]) or (RATE_PRIORS.get(item["category"], Decimal(0))
                                         if item["basis"] == "ON_REQUEST" else Decimal(0))
        daily = basis * std + r * occ / 100
        sigma = issued_sigma(cur, slug, item["id"], today) * share
        safety = PAR_Z * sigma * lead.sqrt()
        par_qty = (daily * dos + safety).quantize(Decimal(1), rounding=ROUND_CEILING)
        computed = {"forecast_occ": str(round(fc["occupied"], 1)), "source": fc["source"], "share": str(round(share, 3)),
                    "std_qty": str(std), "rate_per_100": str(round(r, 2)), "daily_usage": str(round(daily, 2)),
                    "sigma_daily": str(round(sigma, 2)), "safety_stock": str(round(safety, 1)),
                    "suggested_par": str(par_qty), "as_of": today.isoformat()}
        if p["pinned"]:
            cur.execute("UPDATE amn_par_levels SET computed = %s, computed_at = NOW() WHERE location_id = %s "
                        "AND item_id = %s", (Json(computed), p["location_id"], p["item_id"]))
            pinned += 1
            continue
        cur.execute("UPDATE amn_par_levels SET par_qty = %s, safety_stock = %s, computed = %s, computed_at = NOW(), "
                    "updated_at = NOW() WHERE location_id = %s AND item_id = %s",
                    (par_qty, round(safety, 1), Json(computed), p["location_id"], p["item_id"]))
        updated += 1
    return {"updated": updated, "pinned": pinned}
