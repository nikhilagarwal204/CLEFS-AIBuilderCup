"""
Hotel-local instants and daily windows for journeys (Canon 3: every bucket
and window in the hotel's timezone, through clefs.core.time).

Windows are half-open [start, end): in a 09:00-18:00 marketing window 09:00
and 17:59 are inside, 08:59 and 18:00 are outside (the tests pin all four,
Asia/Dubai).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from clefs.core.time import tz_of


def at_local(hotel: Any, d: date, t: time) -> datetime:
    """The aware instant for local date `d` at local time `t`."""
    return datetime.combine(d, t, tzinfo=tz_of(hotel))


def local(hotel: Any, ts: datetime) -> datetime:
    return ts.astimezone(tz_of(hotel))


def in_window(hotel: Any, ts: datetime, t0: time, t1: time) -> bool:
    lt = local(hotel, ts).time()
    return t0 <= lt < t1


def next_window_start(hotel: Any, ts: datetime, t0: time, t1: time) -> datetime:
    """`ts` itself when inside [t0, t1), else the next local t0."""
    lt = local(hotel, ts)
    if t0 <= lt.time() < t1:
        return ts
    d = lt.date() if lt.time() < t0 else lt.date() + timedelta(days=1)
    return at_local(hotel, d, t0)


def day_bounds(hotel: Any, ts: datetime) -> tuple[datetime, datetime]:
    d = local(hotel, ts).date()
    start = at_local(hotel, d, time(0))
    return start, at_local(hotel, d + timedelta(days=1), time(0))
