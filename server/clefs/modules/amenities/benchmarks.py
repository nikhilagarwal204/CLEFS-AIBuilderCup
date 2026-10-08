"""
Cross-hotel amenity benchmarks (02 spec 5.1-5.3; 00-PLAN R2).

Units issued per 100 occupied room-nights, by item category, per cohort
(country-emirate-star, "AE-DXB-HOTEL_4") and month. Privacy rules:
- published only with hotels_count >= 3 and no single hotel above 50% of the
  cohort's issued volume;
- below 5 hotels only a band is stored (p25 floored and p75 ceiled to a
  multiple of 5), never the percentiles, so no hotel's own figure can be
  worked back out of three or four data points;
- the rows carry no hotel_slug: a cohort aggregate belongs to nobody.

The monthly job runs at system scope (per_hotel=False) over every hotel with
rollup data for the month.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from clefs.core.money import D
from clefs.modules.amenities.constants import BENCH_EXACT_FROM, BENCH_MAX_SHARE, BENCH_MIN_HOTELS

EMIRATE_CODES = {"dubai": "DXB", "abu_dhabi": "AUH", "abudhabi": "AUH", "sharjah": "SHJ", "ajman": "AJM",
                 "ras_al_khaimah": "RAK", "fujairah": "FUJ", "umm_al_quwain": "UAQ"}
BAND_STEP = Decimal(5)


def cohort_key(hotel: Any) -> str:
    country = (getattr(hotel, "country", None) or "XX").upper()
    em = (getattr(hotel, "emirate", None) or "").lower().replace(" ", "_").replace("-", "_")
    region = EMIRATE_CODES.get(em, em[:3].upper() or "ALL")
    star = getattr(hotel, "star_rating", None)
    return f"{country}-{region}-HOTEL_{int(star) if star else 'NA'}"


def percentile(xs: list[Decimal], p: Decimal) -> Decimal:
    """Linear interpolation between closest ranks (xs sorted)."""
    if len(xs) == 1:
        return xs[0]
    k = (Decimal(len(xs)) - 1) * p
    lo = int(k.to_integral_value(rounding=ROUND_FLOOR))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def summarize(values: list[Decimal], volumes: list[Decimal]) -> dict:
    """The publication decision and figures for one cohort/category/month."""
    n = len(values)
    total = sum(volumes, Decimal(0))
    max_share = (max(volumes) / total) if total > 0 else Decimal(1)
    out: dict[str, Any] = {"hotels_count": n, "max_share": round(max_share, 4), "published": False,
                           "suppressed_reason": None, "p25": None, "p50": None, "p75": None, "band_low": None,
                           "band_high": None}
    if n < BENCH_MIN_HOTELS:
        out["suppressed_reason"] = "needs_3_hotels"
        return out
    if max_share > BENCH_MAX_SHARE:
        out["suppressed_reason"] = "one_hotel_dominates"
        return out
    xs = sorted(values)
    p25, p50, p75 = (percentile(xs, Decimal(p)) for p in ("0.25", "0.5", "0.75"))
    low = (p25 / BAND_STEP).to_integral_value(rounding=ROUND_FLOOR) * BAND_STEP
    high = (p75 / BAND_STEP).to_integral_value(rounding=ROUND_CEILING) * BAND_STEP
    if high <= low:
        high = low + BAND_STEP
    out.update(published=True, band_low=low, band_high=high)
    if n >= BENCH_EXACT_FROM:
        out.update(p25=round(p25, 2), p50=round(p50, 2), p75=round(p75, 2))
    return out


def rollup(cur: Any, month: date) -> dict:
    """Recompute every cohort for `month` (any date inside it)."""
    m0 = month.replace(day=1)
    m1 = (m0 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    cur.execute(
        """
        SELECT h.slug, h.country, h.emirate, h.star_rating, t.orn
        FROM (SELECT hotel_slug, SUM(orn) AS orn FROM amn_daily_totals WHERE business_date BETWEEN %s AND %s
              GROUP BY hotel_slug HAVING SUM(orn) > 0) t
        JOIN hotels h ON h.slug = t.hotel_slug
        WHERE COALESCE(h.status, '') NOT IN ('archived', 'demo')
        """,
        (m0, m1),
    )
    hotels = {r["slug"]: r for r in cur.fetchall()}
    if not hotels:
        return {"cohorts": 0}
    cur.execute(
        """
        SELECT v.hotel_slug, i.category, SUM(v.issued) AS issued
        FROM amn_variance_daily v JOIN amn_items i ON i.id = v.item_id
        WHERE v.business_date BETWEEN %s AND %s AND v.hotel_slug = ANY(%s)
        GROUP BY 1, 2
        """,
        (m0, m1, list(hotels)),
    )
    cells: dict[tuple[str, str], list[tuple[Decimal, Decimal]]] = defaultdict(list)
    for r in cur.fetchall():
        h = hotels[r["hotel_slug"]]
        issued = D(r["issued"])
        if issued <= 0:
            continue
        per100 = issued / D(h["orn"]) * 100
        cells[(cohort_key(_Obj(h)), r["category"])].append((per100, issued))
    cur.execute("DELETE FROM amn_benchmarks WHERE month = %s", (m0,))
    for (ck, cat), vals in cells.items():
        s = summarize([v for v, _ in vals], [w for _, w in vals])
        cur.execute(
            """
            INSERT INTO amn_benchmarks (cohort_key, item_category, month, hotels_count, max_share, published,
                                        suppressed_reason, p25, p50, p75, band_low, band_high)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ck, cat, m0, s["hotels_count"], s["max_share"], s["published"], s["suppressed_reason"], s["p25"],
             s["p50"], s["p75"], s["band_low"], s["band_high"]),
        )
    return {"cohorts": len(cells), "month": m0.isoformat()}


class _Obj:
    def __init__(self, d: dict) -> None:
        self.country, self.emirate, self.star_rating = d.get("country"), d.get("emirate"), d.get("star_rating")


# ─── Hotel view ───


def for_hotel(cur: Any, hotel: Any) -> dict:
    """The hotel's cohort rows for the latest month with any row, next to its
    own per-100-ORN figure. Unpublished rows show only why."""
    ck = cohort_key(hotel)
    cur.execute("SELECT MAX(month) AS m FROM amn_benchmarks WHERE cohort_key = %s", (ck,))
    m = cur.fetchone()["m"]
    base = {"cohort_key": ck, "month": m.isoformat() if m else None, "min_hotels": BENCH_MIN_HOTELS,
            "exact_from": BENCH_EXACT_FROM, "rows": []}
    if not m:
        return {**base, "available": False, "reason": "needs_3_hotels"}
    m1 = (m + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    cur.execute("SELECT COALESCE(SUM(orn), 0) AS orn FROM amn_daily_totals WHERE hotel_slug = %s "
                "AND business_date BETWEEN %s AND %s", (hotel.slug, m, m1))
    orn = D(cur.fetchone()["orn"])
    cur.execute("SELECT i.category, SUM(v.issued) AS issued FROM amn_variance_daily v JOIN amn_items i "
                "ON i.id = v.item_id WHERE v.hotel_slug = %s AND v.business_date BETWEEN %s AND %s GROUP BY 1",
                (hotel.slug, m, m1))
    own = {r["category"]: (D(r["issued"]) / orn * 100 if orn else None) for r in cur.fetchall()}
    cur.execute("SELECT * FROM amn_benchmarks WHERE cohort_key = %s AND month = %s ORDER BY item_category", (ck, m))
    rows = []
    for r in cur.fetchall():
        s = (lambda x: str(x) if x is not None else None)
        rows.append({"category": r["item_category"], "hotels_count": r["hotels_count"], "published": r["published"],
                     "suppressed_reason": r["suppressed_reason"], "p25": s(r["p25"]), "p50": s(r["p50"]),
                     "p75": s(r["p75"]), "band_low": s(r["band_low"]), "band_high": s(r["band_high"]),
                     "yours_per_100_orn": str(round(own[r["item_category"]], 1))
                     if own.get(r["item_category"]) is not None else None})
    return {**base, "available": any(r["published"] for r in rows), "rows": rows,
            "reason": None if any(r["published"] for r in rows) else "needs_3_hotels"}
