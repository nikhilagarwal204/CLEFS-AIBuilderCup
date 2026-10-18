"""
Labour guardrails for the roster (03 spec 1.6). Guidance, not legal advice:
every rule is a warning except the summer midday outdoor ban, which blocks a
save unless a manager gives a reason (written to the audit log).

Rules, per staff member:
- weekly paid hours over `weekly_max_h` (48) in an ISO week (Mon-Sun by
  business date): warning `weekly_hours`;
- daily paid hours over `daily_max_h` (8): warning `daily_hours`; during
  Ramadan the limit is `daily_max_h - ramadan_reduction_h` (8 - 2):
  warning `ramadan_hours`;
- more than `max_consecutive_days` (6) worked days in a row: warning
  `consecutive_days` on the first day past the limit;
- an outdoor role overlapping 12:30-15:00 local between 15 Jun and 15 Sep
  (both included): `midday_ban`, blocked unless `override_reason` is set.

Paid hours are the shift length minus its break (break_min). The ban window
is half-open: a shift ending at exactly 12:30 does not overlap it.

`check()` is pure (no database): the roster feeds it the assignments being
saved plus the neighbouring ones already stored, so a week boundary never
hides a seventh day in a row.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clefs.audit import audit
from clefs.db import Json

CAPTION = "Guidance, not legal advice"

# ─── Rules ───


class MiddayBan(BaseModel):
    # MM-DD dates (inclusive) and HH:MM local times (half-open window)
    from_: str = Field("06-15", alias="from", pattern=r"^\d{2}-\d{2}$")
    to: str = Field("09-15", pattern=r"^\d{2}-\d{2}$")
    start: str = Field("12:30", pattern=r"^\d{2}:\d{2}$")
    end: str = Field("15:00", pattern=r"^\d{2}:\d{2}$")

    model_config = {"populate_by_name": True}

    def dump(self) -> dict:
        return {"from": self.from_, "to": self.to, "start": self.start, "end": self.end}


class LabourRules(BaseModel):
    weekly_max_h: int = Field(48, ge=1, le=100)
    daily_max_h: int = Field(8, ge=1, le=24)
    max_consecutive_days: int = Field(6, ge=1, le=31)
    ramadan_start: date | None = None
    ramadan_end: date | None = None
    ramadan_reduction_h: int = Field(2, ge=0, le=8)
    midday_ban: MiddayBan = Field(default_factory=MiddayBan)

    @field_validator("ramadan_end")
    @classmethod
    def _order(cls, v: date | None, info: Any) -> date | None:
        start = info.data.get("ramadan_start")
        if v is not None and start is not None and v < start:
            raise ValueError("ramadan_end is before ramadan_start")
        return v

    def out(self) -> dict:
        d = self.model_dump(exclude={"midday_ban"})
        d["ramadan_start"] = self.ramadan_start.isoformat() if self.ramadan_start else None
        d["ramadan_end"] = self.ramadan_end.isoformat() if self.ramadan_end else None
        d["midday_ban"] = self.midday_ban.dump()
        d["caption"] = CAPTION
        return d


def load_rules(cur: Any, hotel_slug: str) -> LabourRules:
    cur.execute("SELECT * FROM svc_labour_rules WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        return LabourRules()
    data = {k: row[k] for k in ("weekly_max_h", "daily_max_h", "max_consecutive_days", "ramadan_start",
                                "ramadan_end", "ramadan_reduction_h")}
    return LabourRules(**data, midday_ban=MiddayBan(**(row["midday_ban"] or {})))


def save_rules(cur: Any, hotel_slug: str, rules: LabourRules, *, actor: Any) -> LabourRules:
    before = load_rules(cur, hotel_slug)
    cur.execute(
        """
        INSERT INTO svc_labour_rules (hotel_slug, weekly_max_h, daily_max_h, max_consecutive_days, ramadan_start,
            ramadan_end, ramadan_reduction_h, midday_ban, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (hotel_slug) DO UPDATE SET weekly_max_h = EXCLUDED.weekly_max_h,
            daily_max_h = EXCLUDED.daily_max_h, max_consecutive_days = EXCLUDED.max_consecutive_days,
            ramadan_start = EXCLUDED.ramadan_start, ramadan_end = EXCLUDED.ramadan_end,
            ramadan_reduction_h = EXCLUDED.ramadan_reduction_h, midday_ban = EXCLUDED.midday_ban, updated_at = NOW()
        """,
        (hotel_slug, rules.weekly_max_h, rules.daily_max_h, rules.max_consecutive_days, rules.ramadan_start,
         rules.ramadan_end, rules.ramadan_reduction_h, Json(rules.midday_ban.dump())),
    )
    audit(cur, actor, "service.labour_rules.update", "svc_labour_rules", hotel_slug,
          before=before.out(), after=rules.out(), hotel_slug=hotel_slug)
    return rules


# ─── Checks ───


def _hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h) % 24, int(m))


def _in_ban_season(ban: MiddayBan, d: date) -> bool:
    """MM-DD inclusive season; a season that wraps the year end (11-15 to
    02-15) is read as two halves."""
    md = d.strftime("%m-%d")
    lo, hi = ban.from_, ban.to
    return lo <= md <= hi if lo <= hi else (md >= lo or md <= hi)


def in_ramadan(rules: LabourRules, d: date) -> bool:
    return bool(rules.ramadan_start and rules.ramadan_end and rules.ramadan_start <= d <= rules.ramadan_end)


def paid_hours(a: dict) -> float:
    """Shift length minus its break, in hours (never negative)."""
    span = (a["end_at"] - a["start_at"]).total_seconds() / 3600.0
    return max(0.0, span - (a.get("break_min") or 0) / 60.0)


def midday_overlap(a: dict, ban: MiddayBan, tz: Any) -> date | None:
    """The first local date whose ban window [start, end) the shift
    overlaps, or None. A night shift is checked on every local date it
    touches, so 23:00-15:30 is caught on the second day."""
    start, end = a["start_at"].astimezone(tz), a["end_at"].astimezone(tz)
    d = start.date()
    while d <= end.date():
        if _in_ban_season(ban, d):
            b0 = datetime.combine(d, _hhmm(ban.start), tzinfo=tz)
            b1 = datetime.combine(d, _hhmm(ban.end), tzinfo=tz)
            if start < b1 and end > b0:
                return d
        d += timedelta(days=1)
    return None


def _flag(code: str, level: str, staff_id: int, d: date, message: str, *, refs: list, value: float | None = None,
          limit: float | None = None) -> dict:
    return {"code": code, "level": level, "staff_member_id": staff_id, "business_date": d.isoformat(),
            "message": message, "refs": refs, "value": value, "limit": limit}


def check(assignments: list[dict], rules: LabourRules, *, tz: Any, focus: tuple[date, date] | None = None) -> list[dict]:
    """Labour flags for planned assignments.

    Each assignment: {ref, staff_member_id, business_date (date), start_at,
    end_at (aware), break_min, is_outdoor, override_reason, status}. `ref`
    (an id or the client's key) is echoed in `refs` so the scheduler can mark
    the cell. Cancelled rows are ignored. With `focus`, flags dated outside
    [focus[0], focus[1]] are dropped (the neighbours only give context).

    -> [{code, level: warning|blocked|overridden, staff_member_id,
    business_date, message, refs, value, limit}]"""
    rows = [a for a in assignments if a.get("status", "planned") != "cancelled"]
    by_staff: dict[int, list[dict]] = defaultdict(list)
    for a in rows:
        by_staff[int(a["staff_member_id"])].append(a)
    flags: list[dict] = []
    for sid, items in by_staff.items():
        flags += _daily(sid, items, rules)
        flags += _weekly(sid, items, rules)
        flags += _consecutive(sid, items, rules)
        flags += _midday(sid, items, rules, tz)
    if focus is not None:
        lo, hi = focus
        flags = [f for f in flags if lo <= date.fromisoformat(f["business_date"]) <= hi]
    return sorted(flags, key=lambda f: (f["business_date"], f["staff_member_id"], f["code"]))


# ─── Rule helpers ───


def _bd(a: dict) -> date:
    d = a["business_date"]
    return d if isinstance(d, date) else date.fromisoformat(str(d))


def _daily(sid: int, items: list[dict], rules: LabourRules) -> list[dict]:
    per_day: dict[date, list[dict]] = defaultdict(list)
    for a in items:
        per_day[_bd(a)].append(a)
    out = []
    for d, day in per_day.items():
        hours = round(sum(paid_hours(a) for a in day), 2)
        ramadan = in_ramadan(rules, d)
        limit = max(1, rules.daily_max_h - rules.ramadan_reduction_h) if ramadan else rules.daily_max_h
        if hours > limit:
            code = "ramadan_hours" if ramadan else "daily_hours"
            what = "Ramadan day limit" if ramadan else "daily limit"
            out.append(_flag(code, "warning", sid, d, f"{hours:g} paid hours is over the {what} of {limit} h",
                             refs=[a.get("ref") for a in day], value=hours, limit=limit))
    return out


def _weekly(sid: int, items: list[dict], rules: LabourRules) -> list[dict]:
    per_week: dict[date, list[dict]] = defaultdict(list)
    for a in items:
        d = _bd(a)
        per_week[d - timedelta(days=d.weekday())].append(a)
    out = []
    for monday, week in per_week.items():
        hours = round(sum(paid_hours(a) for a in week), 2)
        if hours > rules.weekly_max_h:
            # dated on the day the total crosses the limit, so the flag sits
            # on the shift that tipped it over
            running, when = 0.0, monday
            for a in sorted(week, key=lambda x: x["start_at"]):
                running += paid_hours(a)
                if running > rules.weekly_max_h:
                    when = _bd(a)
                    break
            out.append(_flag("weekly_hours", "warning", sid, when,
                             f"{hours:g} paid hours in the week of {monday.isoformat()} is over {rules.weekly_max_h} h",
                             refs=[a.get("ref") for a in week], value=hours, limit=rules.weekly_max_h))
    return out


def _consecutive(sid: int, items: list[dict], rules: LabourRules) -> list[dict]:
    days = sorted({_bd(a) for a in items})
    out, run, prev = [], 0, None
    for d in days:
        run = run + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        prev = d
        if run == rules.max_consecutive_days + 1:
            refs = [a.get("ref") for a in items if _bd(a) == d]
            out.append(_flag("consecutive_days", "warning", sid, d,
                             f"Day {run} in a row without a rest day (limit {rules.max_consecutive_days})",
                             refs=refs, value=run, limit=rules.max_consecutive_days))
    return out


def _midday(sid: int, items: list[dict], rules: LabourRules, tz: Any) -> list[dict]:
    out = []
    for a in items:
        if not a.get("is_outdoor"):
            continue
        hit = midday_overlap(a, rules.midday_ban, tz)
        if hit is None:
            continue
        reason = (a.get("override_reason") or "").strip()
        ban = rules.midday_ban
        msg = f"Outdoor work {ban.start}-{ban.end} between {ban.from_} and {ban.to} (midday break rule)"
        f = _flag("midday_ban", "overridden" if reason else "blocked", sid, hit, msg, refs=[a.get("ref")])
        if reason:
            f["override_reason"] = reason
        out.append(f)
    return out


def blocking(flags: list[dict]) -> list[dict]:
    """Flags that stop a save (a midday-ban hit without a reason)."""
    return [f for f in flags if f["level"] == "blocked"]
