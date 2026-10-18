"""
Period resolution: a preset or an explicit {from, to} becomes hotel-local
[start, end] dates. "Today" is always the hotel's today (clefs.core.time), so
a Dubai owner asking at 01:00 gets Dubai's day, never the server's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from clefs.core.time import local_date
from clefs.modules.ask.constants import MAX_SPAN_DAYS, PERIOD_PRESETS


class PeriodError(ValueError):
    """A period the model or a saved view asked for that we will not run."""


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    preset: str | None = None

    def to_api(self) -> dict:
        return {"preset": self.preset, "from": self.start.isoformat(), "to": self.end.isoformat(),
                "label": label(self)}


# ─── Calendar helpers ───


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _prev_month_end(d: date) -> date:
    return _month_start(d) - timedelta(days=1)


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _parse_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as exc:
        raise PeriodError(f"not a date: {v!r} (use YYYY-MM-DD)") from exc


# ─── Resolution ───


def resolve_preset(preset: str, today: date) -> tuple[date, date]:
    """[start, end] for a preset, relative to the hotel-local `today`."""
    if preset == "today":
        return today, today
    if preset == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if preset == "last_7d":
        return today - timedelta(days=6), today
    if preset == "last_30d":
        return today - timedelta(days=29), today
    if preset == "wtd":
        return today - timedelta(days=today.weekday()), today
    if preset == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if preset == "mtd":
        return _month_start(today), today
    if preset == "last_month":
        end = _prev_month_end(today)
        return _month_start(end), end
    if preset == "qtd":
        return _quarter_start(today), today
    if preset == "last_quarter":
        end = _quarter_start(today) - timedelta(days=1)
        return _quarter_start(end), end
    if preset == "ytd":
        return date(today.year, 1, 1), today
    if preset == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if preset.startswith("next_") and preset.endswith("d"):
        days = int(preset[5:-1])
        return today, today + timedelta(days=days - 1)
    raise PeriodError(f"unknown period preset {preset!r}")


def resolve(period: dict | None, hotel: Any, *, today: date | None = None) -> Period:
    """{preset} or {from, to} (either may be null in the strict schema) ->
    Period. A missing period means month to date, the owner's default frame."""
    today = today or local_date(hotel)
    period = period or {}
    preset = period.get("preset")
    if preset:
        if preset not in PERIOD_PRESETS:
            raise PeriodError(f"unknown period preset {preset!r}")
        start, end = resolve_preset(preset, today)
        return Period(start, end, preset)
    raw_from, raw_to = period.get("from"), period.get("to")
    if not raw_from and not raw_to:
        start, end = resolve_preset("mtd", today)
        return Period(start, end, "mtd")
    start = _parse_date(raw_from or raw_to)
    end = _parse_date(raw_to or raw_from)
    if end < start:
        raise PeriodError("the period ends before it starts")
    if (end - start).days > MAX_SPAN_DAYS:
        raise PeriodError("a period can span at most 3 years")
    return Period(start, end, None)


# ─── Labels ───


def label(p: Period) -> str:
    """"1-31 Aug 2026", "28 Sep 2026", "30 Sep - 6 Oct 2026" (no em dash)."""
    s, e = p.start, p.end
    if s == e:
        return f"{s.day} {s:%b} {s.year}"
    if s.year == e.year and s.month == e.month:
        return f"{s.day}-{e.day} {s:%b} {s.year}"
    if s.year == e.year:
        return f"{s.day} {s:%b} - {e.day} {e:%b} {e.year}"
    return f"{s.day} {s:%b} {s.year} - {e.day} {e:%b} {e.year}"
