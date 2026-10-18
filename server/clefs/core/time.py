"""
Hotel-local time: every day bucket, "today" and weekend test goes through here.

Canon 3: legacy tables keep epoch-ms, new tables use TIMESTAMPTZ, and ALL
bucketing uses the hotel's timezone. Asia/Dubai has no DST, but Indian and
future non-Gulf hotels may, so bounds are computed with zoneinfo, never by
adding a fixed offset.

Functions accept anything with `timezone`, `weekend_days` and
`business_day_cutoff` attributes (core.hotels.Hotel in practice).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from clefs.core import clock

DEFAULT_CUTOFF = time(4, 0)


class HotelLike(Protocol):
    timezone: str
    weekend_days: list[int]
    business_day_cutoff: time


_TZ_CACHE: dict[str, ZoneInfo] = {}


def tz_of(h: Any) -> ZoneInfo:
    name = h if isinstance(h, str) else h.timezone
    z = _TZ_CACHE.get(name)
    if z is None:
        z = _TZ_CACHE[name] = ZoneInfo(name)
    return z


def hotel_now(h: Any) -> datetime:
    """Now, as an aware datetime in the hotel's timezone."""
    return clock.now().astimezone(tz_of(h))


def local_date(h: Any, ts: datetime | None = None) -> date:
    """Calendar date in the hotel's timezone (midnight boundary)."""
    return (ts or clock.now()).astimezone(tz_of(h)).date()


def business_date(h: Any, ts: datetime | None = None) -> date:
    """The hotel's business date: the calendar date, except that the night
    audit rolls it at business_day_cutoff (default 04:00), so 01:30 on the 5th
    still belongs to the 4th."""
    local = (ts or clock.now()).astimezone(tz_of(h))
    cutoff = getattr(h, "business_day_cutoff", None) or DEFAULT_CUTOFF
    if local.time() < cutoff:
        return local.date() - timedelta(days=1)
    return local.date()


def local_day_bounds(h: Any, d: date) -> tuple[datetime, datetime]:
    """[start, end) of calendar day d in the hotel's timezone, as UTC instants."""
    z = tz_of(h)
    start = datetime.combine(d, time(0), tzinfo=z)
    end = datetime.combine(d + timedelta(days=1), time(0), tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def business_day_bounds(h: Any, d: date) -> tuple[datetime, datetime]:
    """[start, end) of business date d (cutoff to cutoff), as UTC instants."""
    z = tz_of(h)
    cutoff = getattr(h, "business_day_cutoff", None) or DEFAULT_CUTOFF
    start = datetime.combine(d, cutoff, tzinfo=z)
    end = datetime.combine(d + timedelta(days=1), cutoff, tzinfo=z)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_range(h: Any, start: date, end: date) -> tuple[datetime, datetime]:
    """[start of `start`, end of `end`) for an inclusive local date range."""
    if end < start:
        raise ValueError("end before start")
    return local_day_bounds(h, start)[0], local_day_bounds(h, end)[1]


def is_weekend(h: Any, d: date) -> bool:
    """ISO weekday test against hotels.weekend_days (UAE: Sat=6, Sun=7)."""
    days = getattr(h, "weekend_days", None)
    # None (an object without the field) defaults to the UAE Sat-Sun; an empty
    # list means the hotel has no weekend at all.
    return d.isoweekday() in ([6, 7] if days is None else days)


def to_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        raise ValueError("to_ms() needs an aware datetime")
    return int(dt.timestamp() * 1000)


def from_ms(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def today_start_ms(h: Any) -> int:
    """Hotel-local midnight today, in epoch ms (for legacy BIGINT columns)."""
    return to_ms(local_day_bounds(h, local_date(h))[0])
