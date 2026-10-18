"""
End-of-pilot ROI report data (03 spec 5.5). The console renders it at
/app/[hotelSlug]/trust/pilots/[id]/report with print CSS; this module only
computes figures, each with the metric query that reproduces it.

The headline is "Clefs-verified profit": the actions ledger's verified
outcomes that count toward verified profit (finance.api.verified_profit),
per available room-night and per room. Order and upsell revenue are shown
beside it as revenue through Clefs, never added to it: a verified upsell
outcome may already be in the ledger, and profit is not revenue. The ROI
uses the ledger figure only.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core import time as htime
from clefs.core.hotels import Hotel, get_hotel
from clefs.modules.finance import api as finance_api
from clefs.modules.service import api as service_api
from clefs.modules.trust.pilots import store

REVENUE_METRICS = (("upsell.order_revenue", "Order revenue"), ("upsell.upsell_revenue", "Upsell revenue"))


def _q(metric: str, start: date, end: date) -> dict:
    # The body for POST /hotels/{slug}/metrics/query: every figure links here.
    return {"metric": metric, "start": start.isoformat(), "end": end.isoformat(), "grain": "total"}


def _money(minor: int | None, ccy: str) -> str | None:
    return money.money_str(minor, ccy)


def available_room_nights(cur: Any, hotel: Hotel, start: date, end: date) -> tuple[int, int, int]:
    """(room-nights available, days with occupancy data, days in range).
    occupancy_daily wins; a day without a row counts the hotel's room total
    (rooms_count), which the caveats then name."""
    days = (end - start).days + 1
    if days <= 0:
        return 0, 0, 0
    cur.execute(
        "SELECT COALESCE(SUM(rooms_available), 0) AS n, COUNT(*) AS d FROM occupancy_daily "
        "WHERE hotel_slug = %s AND date BETWEEN %s AND %s",
        (hotel.slug, start, end),
    )
    row = cur.fetchone()
    covered = int(row["d"])
    missing = days - covered
    return int(row["n"]) + missing * int(hotel.rooms_count or 0), covered, days


def _per(minor: int, n: int) -> int | None:
    if n <= 0:
        return None
    return int((Decimal(minor) / Decimal(n)).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _recovery_cost(cur: Any, hotel_slug: str, start: date, end: date) -> int:
    # Service's api reads its own svc_ tables, which exist only where service
    # is loaded (a trust-only deploy or test run has none): no service, no
    # recovery gestures, so 0 rather than a failed report.
    from clefs.registry import enabled_modules

    if "service" not in enabled_modules():
        return 0
    return int(service_api.recovery_cost(cur, hotel_slug, start=start, end=end) or 0)


def _recovery_avoided(cur: Any, hotel: Hotel, pilot: dict, start: date, end: date) -> dict:
    """Service-recovery cost per day in the pilot against the baseline
    period; the saving (never negative) over the pilot's days."""
    b0, b1 = pilot.get("baseline_start"), pilot.get("baseline_end")
    spent = _recovery_cost(cur, hotel.slug, start, end)
    out = {"pilot_cost_minor": spent, "pilot_cost": _money(spent, hotel.currency), "baseline_cost_minor": None,
           "avoided_minor": None, "avoided": None}
    if not b0 or not b1:
        return out
    base = _recovery_cost(cur, hotel.slug, b0, b1)
    bdays, pdays = (b1 - b0).days + 1, (end - start).days + 1
    avoided = max(0, int(Decimal(base) / bdays * pdays - spent)) if bdays > 0 else 0
    out.update({"baseline_cost_minor": base, "avoided_minor": avoided, "avoided": _money(avoided, hotel.currency)})
    return out


def _roi(pilot: dict, verified_minor: int, days: int, ccy: str) -> dict:
    """Conversion ROI: the verified run-rate against the conversion price
    (terms.billing_period month|year, default month)."""
    price = pilot.get("conversion_price_minor")
    out = {"price_minor": price, "price": _money(price, ccy), "billing_period": None, "annual_price_minor": None,
           "annualised_verified_minor": None, "roi_multiple": None, "payback_days": None}
    if days <= 0:
        return out
    per_day = Decimal(verified_minor) / days
    out["annualised_verified_minor"] = int((per_day * 365).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if not price:
        return out
    period = (pilot.get("terms") or {}).get("billing_period") or "month"
    annual = int(price) * (12 if period == "month" else 1)
    out.update({"billing_period": period, "annual_price_minor": annual,
                "roi_multiple": str((Decimal(out["annualised_verified_minor"]) / annual).quantize(Decimal("0.01")))})
    if per_day > 0:
        out["payback_days"] = int((Decimal(annual) / per_day).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return out


def build(cur: Any, hotel_slug: str, pilot_id: int) -> dict:
    """Everything the report page prints, in the hotel's currency."""
    hotel = get_hotel(cur, hotel_slug)
    pilot = store.get_pilot(cur, hotel_slug, pilot_id)
    today = htime.local_date(hotel)
    start = pilot["start_date"]
    end = min(pilot["end_date"], today)
    ccy = hotel.currency
    final = pilot["status"] in ("completed", "converted") and pilot.get("outcome", {}).get("criteria") is not None
    ev = pilot["outcome"] if final else store.evaluate(hotel, pilot, until=end)
    ledger = finance_api.verified_profit(cur, hotel_slug, start=start, end=end) if end >= start else \
        {"verified_minor": 0, "actions": 0, "by_module": {}}
    verified = int(ledger.get("verified_minor") or 0)
    arn, covered, days = available_room_nights(cur, hotel, start, end)
    rooms = int(hotel.rooms_count or 0)
    revenue = []
    for key, label in REVENUE_METRICS:
        v = store.metric_value(hotel, key, start, end) if end >= start else None
        revenue.append({"metric_key": key, "label": label, "value": None if v is None else str(v), "query": _q(key, start, end)})
    caveats = ["Only outcomes verified inside the pilot dates count; anything before the pilot (legacy data) is excluded."]
    if covered < days:
        caveats.append(f"Occupancy data covers {covered} of {days} days; the other days count all {rooms} rooms as available.")
    if not pilot.get("baseline_start"):
        caveats.append("No baseline period was set, so improvement criteria and recovery savings cannot be measured.")
    missing = [c["label"] for c in ev.get("criteria", []) if c["status"] == "no_data"]
    if missing:
        caveats.append(f"No data yet for: {', '.join(missing)}.")
    if pilot["status"] == "active":
        caveats.append("The pilot is still running: figures are to date and will change.")
    return {
        "pilot": pilot,
        "hotel": {"slug": hotel.slug, "name": hotel.name, "city": hotel.city, "rooms_count": rooms, "currency": ccy},
        "scope": {"modules": pilot["modules"], "start_date": start.isoformat(), "end_date": pilot["end_date"].isoformat(),
                  "reported_through": end.isoformat(), "days": days,
                  "baseline_start": pilot["baseline_start"].isoformat() if pilot.get("baseline_start") else None,
                  "baseline_end": pilot["baseline_end"].isoformat() if pilot.get("baseline_end") else None},
        "criteria": ev.get("criteria", []),
        "score": ev.get("score"),
        "all_met": ev.get("all_met", False),
        "final": final,
        "verified": {
            "label": "Clefs-verified profit",
            "minor": verified, "value": _money(verified, ccy), "actions": int(ledger.get("actions") or 0),
            "by_module": {m: {"minor": v, "value": _money(v, ccy)} for m, v in (ledger.get("by_module") or {}).items()},
            "available_room_nights": arn,
            "per_available_room_night_minor": _per(verified, arn),
            "per_available_room_night": _money(_per(verified, arn), ccy),
            "per_room_minor": _per(verified, rooms),
            "per_room": _money(_per(verified, rooms), ccy),
            "query": _q("core.verified_value", start, end),
        },
        "revenue_through_clefs": revenue,
        "recovery": _recovery_avoided(cur, hotel, pilot, start, end),
        "roi": _roi(pilot, verified, days, ccy),
        "caveats": caveats,
        "generated_at": clock.now().isoformat(),
    }
