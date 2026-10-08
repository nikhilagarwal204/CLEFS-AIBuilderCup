"""
On-request rates, shrunk toward a category prior (02 spec 5.2).

r = (req + prior x n0/100) / (ORN + n0) x 100, n0 = 200 pseudo room-nights.
A new hotel with 40 ORN and one dental request reads close to the prior
(6 per 100 ORN), not 2.5; after a few thousand room-nights its own logs win.
No published request rate exists for UAE hotels (research: ops-benchmarks
section 2), which is why the hotel learns its own.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core.money import D
from clefs.modules.amenities import catalog
from clefs.modules.amenities.constants import RATE_N0, RATE_PRIORS, RATE_WINDOW_DAYS


def shrunk_rate(requests: Decimal, orn: int, prior: Decimal, n0: int = RATE_N0) -> Decimal:
    return (D(requests) + prior * Decimal(n0) / 100) / (Decimal(orn) + n0) * 100


def learn(cur: Any, hotel: Any, end: date, *, days: int = RATE_WINDOW_DAYS) -> dict:
    """Recompute amn_item_rates for every ON_REQUEST item from the rollup."""
    slug = hotel.slug
    start = end - timedelta(days=days - 1)
    cur.execute("SELECT COALESCE(SUM(orn), 0) AS orn FROM amn_daily_totals WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s", (slug, start, end))
    orn = int(cur.fetchone()["orn"])
    cur.execute("SELECT item_id, SUM(requested) AS req FROM amn_variance_daily WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s GROUP BY item_id", (slug, start, end))
    req = {r["item_id"]: D(r["req"]) for r in cur.fetchall()}
    n = 0
    for it in catalog.list_items(cur, slug, active_only=True):
        if it["basis"] != "ON_REQUEST":
            continue
        prior = RATE_PRIORS.get(it["category"], RATE_PRIORS["OTHER"])
        r = req.get(it["id"], Decimal(0))
        rate = shrunk_rate(r, orn, prior)
        raw = (r / orn * 100) if orn else None
        cur.execute(
            """
            INSERT INTO amn_item_rates (hotel_slug, item_id, window_days, requests, orn, prior_per_100, n0,
                                        rate_per_100, raw_per_100)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, item_id) DO UPDATE SET window_days = EXCLUDED.window_days,
                requests = EXCLUDED.requests, orn = EXCLUDED.orn, prior_per_100 = EXCLUDED.prior_per_100,
                n0 = EXCLUDED.n0, rate_per_100 = EXCLUDED.rate_per_100, raw_per_100 = EXCLUDED.raw_per_100,
                computed_at = NOW()
            """,
            (slug, it["id"], days, r, orn, prior, RATE_N0, round(rate, 3), round(raw, 3) if raw is not None else None),
        )
        n += 1
    return {"items": n, "orn": orn}


def rates_by_item(cur: Any, hotel_slug: str) -> dict[int, Decimal]:
    cur.execute("SELECT item_id, rate_per_100 FROM amn_item_rates WHERE hotel_slug = %s", (hotel_slug,))
    return {r["item_id"]: D(r["rate_per_100"]) for r in cur.fetchall()}


def list_rates(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        "SELECT r.*, i.key, i.name_en, i.category FROM amn_item_rates r JOIN amn_items i ON i.id = r.item_id "
        "WHERE r.hotel_slug = %s ORDER BY i.sort, i.name_en", (hotel_slug,))
    q = catalog.qty_str
    return [{"item_id": r["item_id"], "key": r["key"], "name": r["name_en"], "category": r["category"],
             "window_days": r["window_days"], "requests": q(r["requests"]), "orn": r["orn"],
             "prior_per_100": q(r["prior_per_100"]), "n0": r["n0"], "rate_per_100": q(r["rate_per_100"]),
             "raw_per_100": q(r["raw_per_100"]) if r["raw_per_100"] is not None else None,
             "computed_at": r["computed_at"].isoformat()} for r in cur.fetchall()]
