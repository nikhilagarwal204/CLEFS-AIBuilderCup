"""On-the-books snapshots (02 spec 2.1): `reservations` holds only current
state, so pace needs a nightly photograph of what is booked for each future
night. One row per (snapshot_date, stay_date) over the horizon, zero rows
included (an empty night is a pace fact too).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core.hotels import get_hotel
from clefs.core.time import business_date

HORIZON_DAYS = 365
LIVE = ("CONFIRMED", "IN_HOUSE")

_SNAPSHOT = """
WITH days AS (SELECT d::date AS stay_date FROM generate_series(%(start)s::date, %(end)s::date, '1 day') d),
booked AS (
    SELECT n.stay_date, COALESCE(r.channel_code, 'UNKNOWN') AS channel,
           SUM(n.rooms) AS rooms, SUM(n.rate_minor * n.rooms) AS revenue
    FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id
    WHERE n.hotel_slug = %(hotel)s AND r.status IN ('CONFIRMED', 'IN_HOUSE')
      AND n.stay_date BETWEEN %(start)s AND %(end)s
      AND n.stay_date < COALESCE(r.actual_check_out, r.check_out)
    GROUP BY 1, 2
),
per_day AS (
    SELECT stay_date, SUM(rooms) AS rooms, SUM(revenue) AS revenue,
           jsonb_object_agg(channel, rooms) AS by_channel, jsonb_object_agg(channel, revenue) AS by_revenue
    FROM booked GROUP BY stay_date
)
INSERT INTO otb_snapshots (hotel_slug, snapshot_date, stay_date, rooms_otb, revenue_otb_minor, currency,
                           by_channel, by_channel_revenue)
SELECT %(hotel)s, %(snap)s, days.stay_date, COALESCE(p.rooms, 0), COALESCE(p.revenue, 0), %(ccy)s,
       COALESCE(p.by_channel, '{}'), COALESCE(p.by_revenue, '{}')
FROM days LEFT JOIN per_day p USING (stay_date)
ON CONFLICT (hotel_slug, snapshot_date, stay_date) DO UPDATE SET
    rooms_otb = EXCLUDED.rooms_otb, revenue_otb_minor = EXCLUDED.revenue_otb_minor, currency = EXCLUDED.currency,
    by_channel = EXCLUDED.by_channel, by_channel_revenue = EXCLUDED.by_channel_revenue, created_at = NOW()
"""


def snapshot_otb(
    cur: Any, hotel_slug: str, *, snapshot_date: date | None = None, horizon_days: int = HORIZON_DAYS
) -> int:
    """Photograph what is on the books for [snapshot_date, +horizon]:
    CONFIRMED and IN_HOUSE nights not yet left. Re-running the same day
    replaces that day's photograph. Returns the rows written."""
    hotel = get_hotel(cur, hotel_slug)
    snap = snapshot_date or business_date(hotel)
    cur.execute(_SNAPSHOT, {"hotel": hotel_slug, "snap": snap, "start": snap,
                            "end": snap + timedelta(days=horizon_days), "ccy": hotel.currency})
    return cur.rowcount or 0


def get_otb(cur: Any, hotel_slug: str, *, snapshot_date: date, start: date, end: date) -> list[dict]:
    """One snapshot's rows for stay dates in [start, end]."""
    cur.execute(
        """
        SELECT snapshot_date, stay_date, rooms_otb, revenue_otb_minor, currency, by_channel, by_channel_revenue
        FROM otb_snapshots WHERE hotel_slug = %s AND snapshot_date = %s AND stay_date BETWEEN %s AND %s
        ORDER BY stay_date
        """,
        (hotel_slug, snapshot_date, start, end),
    )
    return [dict(r) for r in cur.fetchall()]


def pace(cur: Any, hotel_slug: str, stay_date: date, *, days_out: int = 90) -> list[dict]:
    """How one night filled up: each snapshot of it up to `days_out` before,
    oldest first ({snapshot_date, days_before, rooms_otb, revenue_otb_minor})."""
    cur.execute(
        """
        SELECT snapshot_date, (stay_date - snapshot_date) AS days_before, rooms_otb, revenue_otb_minor, currency
        FROM otb_snapshots WHERE hotel_slug = %s AND stay_date = %s AND snapshot_date >= %s
        ORDER BY snapshot_date
        """,
        (hotel_slug, stay_date, stay_date - timedelta(days=days_out)),
    )
    return [dict(r) for r in cur.fetchall()]


def latest_snapshot_date(cur: Any, hotel_slug: str) -> date | None:
    cur.execute("SELECT max(snapshot_date) AS d FROM otb_snapshots WHERE hotel_slug = %s", (hotel_slug,))
    return cur.fetchone()["d"]
