"""
Per-recipient quiet hours (PLAN Owner Brief row: "per-recipient quiet
hours"; review completeness-07).

A recipient's window is hotel-local wall-clock time, e.g. 22:00-07:00
(overnight when start > end; start == end means no window). Inside it:

    escalations  a non-critical reminder is recorded as deferred on its
                 brf_escalations row and sent by the next brief.escalate
                 pass after the window ends (escalate.flush_deferred).
                 Critical items still go out at once: an action whose
                 signal is severity 4 (a Service L1, the U=1 critical case).
    the brief    a scheduled run skips that person with a deferred outcome
                 on brf_runs.delivery and brief.deliver_deferred sends it
                 when the window ends. "Send now" is a person asking, so it
                 ignores quiet hours.

Stored in hotel_module_settings["brief"].quiet_hours (no migration): a list
of {user_id, start, end}.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field

from clefs.core.hotels import Hotel

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"
CRITICAL_SEVERITY = 4  # signals.severity 4 = critical (Service raises L1 as 4)


class QuietWindow(BaseModel):
    user_id: int
    start: str = Field(pattern=HHMM)
    end: str = Field(pattern=HHMM)


def _t(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(start: str, end: str, t: time) -> bool:
    """Is local time `t` inside [start, end)? Overnight when start > end."""
    a, b = _t(start), _t(end)
    if a == b:
        return False
    return a <= t < b if a < b else (t >= a or t < b)


def window_for(windows: list[QuietWindow] | list[dict], user_id: int | None) -> QuietWindow | None:
    if user_id is None:
        return None
    for w in windows or []:
        w = w if isinstance(w, QuietWindow) else QuietWindow.model_validate(w)
        if int(w.user_id) == int(user_id):
            return w
    return None


def quiet_until(hotel: Hotel, windows: list[QuietWindow] | list[dict], user_id: int | None,
                now: datetime) -> datetime | None:
    """When `user_id` is inside their quiet window at `now`, the aware
    datetime the window ends (hotel-local wall clock); else None."""
    w = window_for(windows, user_id)
    if w is None:
        return None
    local = now.astimezone(hotel.tz)
    if not in_window(w.start, w.end, local.time().replace(second=0, microsecond=0)):
        return None
    end = _t(w.end)
    day = local.date() if local.time() < end else local.date() + timedelta(days=1)
    # Built from the wall clock, then normalised, so a DST gap cannot shift it.
    return datetime.combine(day, end, tzinfo=hotel.tz).astimezone(now.tzinfo or hotel.tz)


def is_critical(cur: Any, action: dict) -> bool:
    """A critical item breaks through quiet hours: its signal is severity 4."""
    if not action.get("signal_id"):
        return False
    cur.execute("SELECT severity FROM signals WHERE id = %s", (action["signal_id"],))
    row = cur.fetchone()
    return bool(row) and int(row["severity"] or 0) >= CRITICAL_SEVERITY
