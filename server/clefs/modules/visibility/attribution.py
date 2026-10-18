"""
Google attribution (04 spec 5.8, visibility.attribution_monthly, day 2 07:00).

Counts direct reservations in core/stays whose D2 `utm` says
utm_source=google AND (utm_campaign=gbp_listing OR the configured
free-booking-link pattern), booked in the month (hotel time; check-in when
the booking time is unknown), cancellations excluded.

Commission avoided = the sum over those stays of what the reference OTA
would have charged: profit_guard.api.expected_commission with
core.stays.reservation_breakdown, on the check-in date. With no reference
OTA (the Profit Guard stub) it is NULL and the line omits it.

The line reads "Google sent N website clicks -> M direct bookings -> AED X
commission avoided", captioned "bookings that lost the tag are not counted".
This is attribution, NOT ledger-verified profit.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core import clock
from clefs.modules.visibility import xmod

CAPTION = "Bookings that lost the tag are not counted. Attribution, not ledger-verified profit."
GBP_CAMPAIGN = "gbp_listing"


def month_end(month: date) -> date:
    return (month.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def google_bookings(cur: Any, hotel: Any, month: date, pattern: str | None) -> list[dict]:
    cur.execute(
        """
        SELECT id, check_in, room_revenue_minor, currency
        FROM reservations
        WHERE hotel_slug = %(h)s AND utm IS NOT NULL AND status <> 'CANCELLED'
          AND lower(utm->>'utm_source') = 'google'
          AND (utm->>'utm_campaign' = %(camp)s OR (%(pat)s::text IS NOT NULL AND utm->>'utm_campaign' LIKE %(pat)s))
          AND COALESCE((booked_at AT TIME ZONE %(tz)s)::date, check_in) BETWEEN %(m0)s AND %(m1)s
        ORDER BY id
        """,
        {"h": hotel.slug, "camp": GBP_CAMPAIGN, "pat": pattern or None, "tz": hotel.timezone,
         "m0": month, "m1": month_end(month)},
    )
    return [dict(r) for r in cur.fetchall()]


def website_clicks(cur: Any, hotel_slug: str, month: date) -> int | None:
    """From the retained derived KPI row; the raw daily rows as a fallback
    while they have not expired."""
    cur.execute("SELECT website_clicks FROM vis_gbp_kpis_monthly WHERE hotel_slug = %s AND month = %s",
                (hotel_slug, month))
    r = cur.fetchone()
    if r and r["website_clicks"] is not None:
        return int(r["website_clicks"])
    cur.execute("SELECT SUM(value) AS v, COUNT(*) AS n FROM vis_gbp_daily WHERE hotel_slug = %s "
                "AND metric = 'WEBSITE_CLICKS' AND date BETWEEN %s AND %s", (hotel_slug, month, month_end(month)))
    r = cur.fetchone()
    return int(r["v"]) if r and r["n"] else None


def commission_avoided(cur: Any, hotel: Any, bookings: list[dict], month: date) -> tuple[int | None, str | None]:
    from clefs.core import stays
    from clefs.modules.profit_guard import api as pg_api

    ref = xmod.call(cur, None, pg_api.reference_ota, hotel.slug, as_of=month_end(month))
    if not ref:
        return None, None
    ota_code = ref[0]
    total = 0
    for b in bookings:
        breakdown = stays.reservation_breakdown(cur, b["id"])
        res = xmod.call(cur, None, pg_api.expected_commission, hotel.slug, ota_code=ota_code, breakdown=breakdown,
                        on=b["check_in"])
        if res is None:
            return None, ota_code
        total += int(res.get("commission_minor") or 0)
    return total, ota_code


def line(row: dict) -> str:
    from clefs.core.money import fmt_money

    clicks = row["gbp_website_clicks"]
    parts = [f"Google sent {clicks:,} website clicks" if clicks is not None else "Google",
             f"{row['direct_bookings']} direct booking{'s' if row['direct_bookings'] != 1 else ''}"]
    if row["commission_avoided_minor"] is not None:
        parts.append(f"{fmt_money(row['commission_avoided_minor'], row['currency'], minor=True)} commission avoided")
    return " → ".join(parts)


def compute(cur: Any, hotel: Any, month: date) -> dict:
    """Write vis_attribution_monthly for the month and raise the monthly
    vis.google_attribution signal when there is anything to say."""
    from clefs.modules.visibility import store, vsignals

    month = month.replace(day=1)
    profile = store.get_profile(cur, hotel.slug)
    bookings = google_bookings(cur, hotel, month, profile.get("free_booking_link_pattern"))
    clicks = website_clicks(cur, hotel.slug, month)
    avoided, ref = commission_avoided(cur, hotel, bookings, month)
    revenue = sum(int(b["room_revenue_minor"] or 0) for b in bookings if b["currency"] == hotel.currency)
    row = {"month": month, "gbp_website_clicks": clicks, "direct_bookings": len(bookings),
           "direct_room_revenue_minor": revenue, "commission_avoided_minor": avoided, "reference_ota": ref,
           "currency": hotel.currency}
    cur.execute(
        """
        INSERT INTO vis_attribution_monthly (hotel_slug, month, gbp_website_clicks, direct_bookings,
            direct_room_revenue_minor, commission_avoided_minor, reference_ota, currency, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, month) DO UPDATE SET gbp_website_clicks = EXCLUDED.gbp_website_clicks,
            direct_bookings = EXCLUDED.direct_bookings, direct_room_revenue_minor = EXCLUDED.direct_room_revenue_minor,
            commission_avoided_minor = EXCLUDED.commission_avoided_minor, reference_ota = EXCLUDED.reference_ota,
            currency = EXCLUDED.currency, computed_at = EXCLUDED.computed_at
        """,
        (hotel.slug, month, clicks, len(bookings), revenue, avoided, ref, hotel.currency, clock.now()),
    )
    if bookings or clicks:
        vsignals.google_attribution(cur, hotel.slug, {**row, "month": month.isoformat()}, line(row))
    return {**row, "month": month.isoformat(), "line": line(row)}


def get_month(cur: Any, hotel_slug: str, month: date) -> dict | None:
    cur.execute("SELECT month, gbp_website_clicks, direct_bookings, direct_room_revenue_minor, "
                "commission_avoided_minor, reference_ota, currency, computed_at FROM vis_attribution_monthly "
                "WHERE hotel_slug = %s AND month = %s", (hotel_slug, month))
    r = cur.fetchone()
    if not r:
        return None
    row = dict(r)
    return {**row, "month": row["month"].isoformat(), "computed_at": row["computed_at"].isoformat(),
            "line": line(row), "label": CAPTION}


def recent(cur: Any, hotel_slug: str, *, months: int = 6) -> list[dict]:
    cur.execute("SELECT month FROM vis_attribution_monthly WHERE hotel_slug = %s ORDER BY month DESC LIMIT %s",
                (hotel_slug, months))
    return [get_month(cur, hotel_slug, r["month"]) for r in cur.fetchall()]
