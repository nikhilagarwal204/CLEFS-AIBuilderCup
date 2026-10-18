"""
Finance public API (frozen signatures; other modules import only this file).

Owner P&L pulse (04 spec section 6). 04 spec 7.2: every function ships as a
working stub; the Finance lane replaces the bodies, never the signatures
(FROZEN_API).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import money
from clefs.core import time as htime
from clefs.core.hotels import get_hotel


def weekly_pulse(cur: Any, hotel_slug: str, *, week_start: date) -> dict | None:
    """The weekly pulse (NRevPAR, distribution cost, cancellation leakage,
    recovery cost) for the week starting `week_start`: the stored rollup, else
    computed live. None when the hotel has no occupancy or revenue for the
    week (the brief shows no finance block). Money as minor ints plus Decimal
    strings; a figure whose input or denominator is missing is None ("n/a")."""
    from clefs.modules.finance import pulse

    p = pulse.stored_week(cur, hotel_slug, week_start)
    if p is None:
        p = pulse.compute_week(cur, hotel_slug, week_start)
        if p["available_rn"] is None and not p["rooms_revenue_minor"]:
            return None
    return pulse.to_api(p)


def monthly_summary(cur: Any, hotel_slug: str, *, month: date) -> dict | None:
    """The published P&L pulse for the month starting `month`, or None (none
    published). Read only from confirmed, published data:
    {month, currency, normalized, booked_net, total_revenue, rooms_revenue,
    gop, gop_margin_pct, goppar, trevpar, revpar, labour_pct, flow_through_pct,
    flow_through_basis, ebitda, net_profit, verified_profit,
    verified_profit_par, market_index, anomalies[<=3], published_at}
    (money as Decimal strings next to *_minor ints; percents as "32.83")."""
    from clefs.modules.finance import views

    return views.monthly(cur, hotel_slug, month.replace(day=1))


def verified_profit(cur: Any, hotel_slug: str, *, start: date, end: date) -> dict:
    """Clefs-verified profit over the hotel-local dates [start, end] (04 spec
    6.7): the sum of actions.outcome_minor for verified actions that count
    toward verified profit, bucketed by verified_at. Reversed outcomes are
    already 0 (1.3). Only outcomes in the hotel's currency are summed (the
    ledger converts on write, so any other currency is a mislabelled row, and
    the core.verified_value metric skips it the same way).

    -> {start, end, currency, verified_minor, verified_value, actions,
        by_module: {module: minor}, available_rn, verified_par_minor,
        verified_par} (per available room; None without occupancy)."""
    hotel = get_hotel(cur, hotel_slug)
    t0, t1 = htime.local_range(hotel, start, end)
    cur.execute(
        """
        SELECT module, COALESCE(SUM(outcome_minor), 0) AS minor, COUNT(*) AS n
        FROM actions
        WHERE hotel_slug = %s AND status = 'verified' AND counts_toward_verified_profit
          AND currency = %s AND verified_at >= %s AND verified_at < %s
        GROUP BY module ORDER BY module
        """,
        (hotel_slug, hotel.currency, t0, t1),
    )
    rows = cur.fetchall()
    total = sum(int(r["minor"]) for r in rows)
    arn = _arn(cur, hotel_slug, start, end)
    par = None if not arn else int((Decimal(total) / arn).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "currency": hotel.currency,
        "verified_minor": total,
        "verified_value": money.money_str(total, hotel.currency),
        "actions": sum(int(r["n"]) for r in rows),
        "by_module": {r["module"]: int(r["minor"]) for r in rows},
        "available_rn": arn,
        "verified_par_minor": par,
        "verified_par": money.money_str(par, hotel.currency),
    }


def _arn(cur: Any, hotel_slug: str, start: date, end: date) -> int | None:
    """Available room-nights over [start, end]: the month's statistical inputs
    (demo overrides included) for exactly one calendar month, else the sum of
    occupancy_daily. None when nothing is stored."""
    if start.day == 1 and (end + timedelta(days=1)).day == 1 and start.month == end.month:
        from clefs.modules.finance import review

        v = review.stat_inputs(cur, hotel_slug, start).get("available_rn")
        if v:
            return int(v)
    cur.execute("""SELECT COUNT(*) AS n, SUM(rooms_available) AS arn FROM occupancy_daily
                   WHERE hotel_slug = %s AND date BETWEEN %s AND %s""", (hotel_slug, start, end))
    r = cur.fetchone()
    return int(r["arn"]) if r and r["n"] and r["arn"] else None


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It loads this api by name and runs finance's own demo builder, which
# imports, maps, checks and publishes 13 months through the same pipeline an
# owner's upload takes. Nothing else calls this.


def demo_seed_door() -> Any:
    """seed_demo(cur, hotel_slug, rng, anchor) from finance/demo.py."""
    from types import SimpleNamespace

    from clefs.modules.finance import demo

    return SimpleNamespace(seed_demo=demo.seed_demo)
