"""
ICS calendar import (02 spec 4.2: manual entry and ICS go through ingest,
kind ICS). Two doors, one writer:
- the core ingest pipeline: a .ics file uploaded or emailed to the hotel is
  staged by clefs.core.ingest and, on commit, handed to commit_ics();
- POST /hotels/{slug}/demand/events/import-ics: the console posts the file
  text and it is imported at once (no review step for a calendar).

Every VEVENT becomes a hotel-scoped ICS source keyed "<slug>:<UID>", so a
re-import updates rather than duplicates, and a UID that moves its dates is
logged as a reschedule.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from clefs import db as clefs_db
from clefs.core.errors import ClefsError
from clefs.modules.demand import events

MAX_EVENTS = 500
_KEYWORDS = (
    ("SPORTS", r"\b(grand prix|f1|formula|marathon|race|cup|match|rugby|sevens|golf|tennis|championship|tournament)\b"),
    ("CONFERENCE", r"\b(conference|summit|forum|congress|symposium|convention)\b"),
    ("EXPO", r"\b(expo|exhibition|show|fair|week)\b"),
    ("CONCERT", r"\b(concert|live|tour|gig|orchestra|opera)\b"),
    ("FESTIVAL", r"\b(festival|fest|carnival|celebration)\b"),
    ("PUBLIC_HOLIDAY", r"\b(holiday|national day|eid al etihad|new year)\b"),
    ("RELIGIOUS", r"\b(eid|ramadan|diwali|christmas|easter)\b"),
)


def guess_category(title: str, description: str | None = None) -> str:
    text = f"{title} {description or ''}".lower()
    for category, pattern in _KEYWORDS:
        if re.search(pattern, text):
            return category
    return "COMMUNITY"


def _day(v: Any, tz: Any = None) -> tuple[date | None, bool]:
    """(hotel-local date, was an all-day DATE value)."""
    if isinstance(v, datetime):
        return (v.astimezone(tz).date() if tz is not None and v.tzinfo else v.date()), False
    if isinstance(v, date):
        return v, True
    return None, False


def parse_ics(data: bytes | str, *, tz: Any = None) -> list[dict]:
    """[{uid, summary, start, end, location, description, url}] with
    inclusive dates (an all-day DTEND is exclusive in RFC 5545)."""
    import icalendar

    raw = data.encode() if isinstance(data, str) else data
    try:
        cal = icalendar.Calendar.from_ical(raw)
    except ValueError as exc:
        raise ClefsError(400, "The calendar file could not be read", "unreadable_file") from exc
    rows = []
    for i, ev in enumerate(cal.walk("VEVENT"), start=1):
        start, all_day = _day(ev.decoded("DTSTART", None), tz) if ev.get("DTSTART") else (None, False)
        if start is None or not str(ev.get("SUMMARY") or "").strip():
            continue
        end, _ = _day(ev.decoded("DTEND", None), tz) if ev.get("DTEND") else (None, False)
        if end is not None and all_day and end > start:
            end -= timedelta(days=1)
        rows.append({
            "uid": str(ev.get("UID") or f"row-{i}")[:200], "summary": str(ev.get("SUMMARY")).strip(),
            "start": start, "end": end if end and end >= start else start,
            "location": str(ev.get("LOCATION") or "").strip() or None,
            "description": str(ev.get("DESCRIPTION") or "").strip()[:2000] or None,
            "url": str(ev.get("URL") or "").strip() or None,
        })
        if len(rows) >= MAX_EVENTS:
            break
    return rows


def row_candidate(row: dict) -> dict:
    return {
        "title": row["summary"], "category": guess_category(row["summary"], row.get("description")),
        "start": row["start"], "end": row["end"], "venue_text": row.get("location"),
        "description": row.get("description"), "urls": [row["url"]] if row.get("url") else [],
        "verification": "VERIFIED",
    }


def import_rows(cur: Any, hotel_slug: str, rows: list[dict]) -> dict:
    venue_rows = events.venues(cur)
    out = {"imported": 0, "created": 0, "matched": 0, "rescheduled": 0, "event_ids": []}
    for row in rows:
        res = events.ingest(cur, row_candidate(row), source="ICS", external_id=f"{hotel_slug}:{row['uid']}",
                            hotel_slug=hotel_slug, payload={k: row.get(k) for k in ("uid", "summary", "location")},
                            venue_rows=venue_rows)
        out["imported"] += 1
        out["created"] += int(res["created"])
        out["matched"] += int(res["matched"])
        out["rescheduled"] += int(any(c["reason"] == "rescheduled" for c in res["changes"]))
        out["event_ids"].append(res["event_id"])
    return out


def import_ics(cur: Any, hotel_slug: str, data: bytes | str, *, tz: Any = None) -> dict:
    rows = parse_ics(data, tz=tz)
    if not rows:
        raise ClefsError(400, "The calendar has no events with a title and a start date", "no_events")
    return import_rows(cur, hotel_slug, rows)


# ─── core ingest commit handler (kind ICS) ───


def commit_ics(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """Staged ICS rows (uid, summary, starts_on, ends_on, location,
    description). The stager keeps DTEND as written, so an all-day end
    (a bare date in the raw row) is exclusive and loses a day here."""
    if not batch.get("hotel_slug"):
        raise ClefsError(400, "An ICS batch needs a hotel", "invalid_batch")
    raw_by_id = {r["id"]: r["raw"] for r in clefs_db.fetch_all(
        "SELECT id, raw FROM import_rows WHERE batch_id = %s", (batch["id"],), cur=cur)}
    parsed = []
    for r in rows:
        start, end = r.get("starts_on"), r.get("ends_on")
        if not r.get("summary") or not start:
            continue
        start = events._as_date(start)
        end = events._as_date(end) if end else start
        raw_end = str((raw_by_id.get(r.get("_row_id")) or {}).get("ends_on") or "")
        if raw_end and "T" not in raw_end and end > start:
            end -= timedelta(days=1)
        parsed.append({"uid": str(r.get("uid") or f"row-{r.get('_row_no')}"), "summary": r["summary"],
                       "start": start, "end": max(end, start), "location": r.get("location"),
                       "description": r.get("description")})
    result = import_rows(cur, batch["hotel_slug"], parsed)
    result.pop("event_ids", None)
    return result
