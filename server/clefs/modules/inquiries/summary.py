"""
The brief's inquiry block (04 spec 4.11, 7.3; Canon 18 daily cadence):
unanswered inquiries past their SLA and the money they put at risk, always
labelled "est.".
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core.hotels import find_hotel, module_enabled

UNANSWERED_LINE_AFTER = timedelta(hours=2)


def compact_money(minor: int, currency: str) -> str:
    """AED 2.5k / AED 850 / AED 1.2m: the brief's short form."""
    major = money.from_minor(minor, currency)
    for size, suffix in ((Decimal(1_000_000), "m"), (Decimal(1_000), "k")):
        if abs(major) >= size:
            v = (major / size).quantize(Decimal("0.1"))
            text = f"{v}".rstrip("0").rstrip(".")
            return f"{currency} {text}{suffix}"
    return f"{currency} {major.quantize(Decimal(1))}"


def unanswered(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(
        """
        SELECT id, ref, source, segment, created_at, first_response_due_at, money_at_risk_minor, currency
        FROM inq_inquiries
        WHERE hotel_slug = %s AND stage = 'new' AND first_response_at IS NULL AND sla_breached
          AND NOT is_test AND duplicate_of IS NULL
        ORDER BY first_response_due_at NULLS LAST, id
        """,
        (hotel_slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def brief_summary(cur: Any, hotel_slug: str, *, day: date) -> dict:
    hotel = find_hotel(cur, hotel_slug)
    if hotel is None or not module_enabled(cur, hotel_slug, "inquiries"):
        return {}
    rows = unanswered(cur, hotel_slug)
    if not rows:
        return {}
    now = clock.now()
    ccy = hotel.currency
    at_risk = sum(int(r["money_at_risk_minor"] or 0) for r in rows if r["currency"] == ccy)
    over_2h = [r for r in rows if now - r["created_at"] >= UNANSWERED_LINE_AFTER]
    shown = over_2h or rows
    label = "> 2 h" if over_2h else "past SLA"
    line = f"{len(shown)} unanswered {label}"
    if at_risk:
        line += f", {compact_money(at_risk, ccy)} at risk (est.)"
    return {
        "day": day.isoformat(),
        "unanswered": len(rows),
        "unanswered_over_2h": len(over_2h),
        "money_at_risk_minor": at_risk,
        "money_at_risk_value": money.money_str(at_risk, ccy),
        "currency": ccy,
        "estimated": True,
        "line": line,
        "items": [
            {"id": r["id"], "ref": r["ref"], "source": r["source"], "segment": r["segment"],
             "due_at": r["first_response_due_at"].isoformat() if r["first_response_due_at"] else None,
             "money_at_risk_minor": r["money_at_risk_minor"]}
            for r in rows[:10]
        ],
    }
