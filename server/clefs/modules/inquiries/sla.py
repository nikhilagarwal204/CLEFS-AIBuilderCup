"""
First-response SLA (04 spec 4.5).

The clock counts business minutes only (08:00-22:00 hotel-local every day by
default, assumption A3). Outside those hours the due time is the next
opening time + the SLA, so a FIT inquiry at 21:59 is due 08:14 the next day
and one at 22:01 is due 08:15.

The timer is one queued task per due time (`enqueue(run_at=due)`), never a
poller. Extraction may change the segment after the timer was set, so the
task re-reads `first_response_due_at` and re-arms itself when it moved.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core.hotels import Hotel, get_module_settings
from clefs.modules.inquiries.models import DEFAULT_SLA_MINUTES
from clefs.modules.inquiries.settings import InquirySettings

SLA_TASK = "inquiries.sla_check"


# ─── Business-hours arithmetic ───


def _minute_of_day(hhmm: str) -> int:
    hh, _, mm = hhmm.partition(":")
    return min(int(hh) * 60 + int(mm), 24 * 60)


def _at(hotel: Hotel, d: date, minute: int) -> datetime:
    """Local wall-clock minute-of-day on day d as an aware datetime (24:00 is
    the next midnight)."""
    base = datetime.combine(d, time(0, 0), tzinfo=hotel.tz)
    return base + timedelta(minutes=minute)


def _window(settings: InquirySettings) -> tuple[int, int]:
    open_m, close_m = _minute_of_day(settings.business_open), _minute_of_day(settings.business_close)
    if close_m <= open_m:  # a misconfigured window counts the whole day rather than never
        return 0, 24 * 60
    return open_m, close_m


def business_due(hotel: Hotel, start: datetime, minutes: int, settings: InquirySettings) -> datetime:
    """`minutes` of business time after `start` (4.5 goldens: 21:59 + 15 ->
    08:14 next day; 22:01 + 15 -> 08:15)."""
    open_m, close_m = _window(settings)
    local = start.astimezone(hotel.tz)
    d = local.date()
    now_m = local.hour * 60 + local.minute + local.second / 60
    if now_m < open_m:
        cursor = _at(hotel, d, open_m)
    elif now_m >= close_m:
        cursor = _at(hotel, d + timedelta(days=1), open_m)
    else:
        cursor = local
    remaining = timedelta(minutes=minutes)
    for _ in range(400):  # bounded: at most a year of business days
        day_end = _at(hotel, cursor.astimezone(hotel.tz).date(), close_m)
        avail = day_end - cursor
        if remaining <= avail:
            return cursor + remaining
        remaining -= avail
        cursor = _at(hotel, cursor.astimezone(hotel.tz).date() + timedelta(days=1), open_m)
    return cursor + remaining


def business_minutes_between(hotel: Hotel, start: datetime, end: datetime, settings: InquirySettings) -> int:
    """Business minutes in [start, end): the response-time metric (4.5)."""
    if end <= start:
        return 0
    open_m, close_m = _window(settings)
    total = timedelta(0)
    d = start.astimezone(hotel.tz).date()
    last = end.astimezone(hotel.tz).date()
    while d <= last:
        lo = max(start, _at(hotel, d, open_m))
        hi = min(end, _at(hotel, d, close_m))
        if hi > lo:
            total += hi - lo
        d += timedelta(days=1)
    return int(total.total_seconds() // 60)


# ─── Per-inquiry due time and timer ───


def settings_for(cur: Any, hotel_slug: str) -> InquirySettings:
    return get_module_settings(cur, hotel_slug, "inquiries", InquirySettings)


def sla_minutes(settings: InquirySettings, segment: str, is_rfp: bool) -> int:
    key = "rfp" if is_rfp else (segment if segment in DEFAULT_SLA_MINUTES else "unknown")
    return int(settings.sla.get(key, DEFAULT_SLA_MINUTES[key]))


def timer_key(inquiry_id: int, due: datetime) -> str:
    return f"inq.sla:{inquiry_id}:{int(due.timestamp())}"


def arm(cur: Any, hotel: Hotel, row: dict, *, settings: InquirySettings | None = None) -> datetime | None:
    """(Re)compute first_response_due_at from the inquiry's arrival and
    segment and queue the timer. A no-op once answered or closed."""
    if row.get("first_response_at") is not None or row["stage"] != "new":
        return None
    settings = settings or settings_for(cur, hotel.slug)
    # The clock starts when the inquiry entered `new`: its arrival, or the
    # moment a reopen brought it back (4.2).
    start = row.get("stage_changed_at") or row["created_at"]
    due = business_due(hotel, start, sla_minutes(settings, row["segment"], row["is_rfp"]), settings)
    if row.get("first_response_due_at") != due:
        cur.execute(
            "UPDATE inq_inquiries SET first_response_due_at = %s, updated_at = %s WHERE id = %s",
            (due, clock.now(), row["id"]),
        )
        row["first_response_due_at"] = due
    jobs.enqueue(
        SLA_TASK,
        {"inquiry_id": row["id"], "due_epoch": int(due.timestamp())},
        hotel_slug=hotel.slug,
        run_at=due,
        dedupe_key=timer_key(row["id"], due),
        cur=cur,
    )
    return due


# ─── The unanswered signal (4.11) ───

UNANSWERED_KIND = "inq.unanswered"


def unanswered_key(inquiry_id: int) -> str:
    return f"{UNANSWERED_KIND}:{inquiry_id}"


def _free_window_evidence(row: dict) -> list:
    """'ad lead: free window closes in 1h40m' (4.11). For a click-to-WhatsApp
    lead, answering inside the 24 h window opens Meta's 72 h free window
    (ctwa_free_window_expires_at once it is open), so the countdown is the
    cheapest moment to reply; other WhatsApp senders show the plain window."""
    from clefs.core.signals import Evidence

    now = clock.now()
    is_ad = bool(row.get("ctwa_clid"))
    for col, label in (
        ("ctwa_free_window_expires_at", "ad lead: free window closes in"),
        ("wa_window_expires_at", "ad lead: free window closes in" if is_ad else "WhatsApp window closes in"),
    ):
        until = row.get(col)
        if until and until > now:
            mins = int((until - now).total_seconds() // 60)
            return [Evidence(kind="calc", label=f"{label} {mins // 60}h{mins % 60:02d}m", value=until.isoformat())]
    return []


def upsert_unanswered(cur: Any, hotel: Hotel, row: dict) -> dict:
    from clefs.core.signals import Evidence, RecommendedAction, SignalIn, upsert_signal
    from clefs.modules.inquiries import risk

    evidence = [
        Evidence(kind="row", label=f"Inquiry {row['ref'].upper()} ({row['source']}, {row['segment']})",
                 ref={"table": "inq_inquiries", "id": row["id"]}),
        *_free_window_evidence(row),
    ]
    if row.get("money_at_risk_minor") is not None:
        evidence.append(Evidence(
            kind="calc", label="room-nights x expected ADR x stage probability (est.)",
            value={"room_nights": row.get("room_nights_est"), "adr_minor": row.get("expected_adr_minor"),
                   "p": str(row.get("stage_probability")) if row.get("stage_probability") is not None else None},
        ))
    impact = row.get("money_at_risk_minor")
    s = SignalIn(
        module="inquiries",
        kind=UNANSWERED_KIND,
        dedupe_key=unanswered_key(row["id"]),
        title=f"Unanswered inquiry {row['ref'].upper()} past its first-response SLA",
        summary=f"{row['segment']} inquiry via {row['source']}; money at risk is an estimate",
        impact_minor=impact,
        currency=row["currency"] if impact is not None else None,
        impact_kind="revenue_upside",
        confidence=risk.confidence(row),
        evidence=evidence,
        recommended_action=RecommendedAction(type="core.task", label="Reply to the guest",  # registered type
                                             params={"inquiry_id": row["id"]}, owner_role="front_desk"),
        owner_role="front_desk",
        due_at=row.get("first_response_due_at"),
        subject_type="inquiry",
        subject_id=str(row["id"]),
    )
    return upsert_signal(cur, hotel.slug, s)


def resolve_unanswered(cur: Any, hotel_slug: str, inquiry_id: int, *, resolution: str) -> bool:
    from clefs.core.signals import resolve_signal

    return resolve_signal(cur, hotel_slug, unanswered_key(inquiry_id), resolution=resolution)


# ─── The timer task body ───


def check(cur: Any, hotel: Hotel, inquiry_id: int, due_epoch: int | None) -> dict:
    """Run at the due time. Re-arms when extraction moved the due time;
    otherwise marks the breach and raises inq.unanswered."""
    cur.execute("SELECT * FROM inq_inquiries WHERE id = %s AND hotel_slug = %s FOR UPDATE", (inquiry_id, hotel.slug))
    got = cur.fetchone()
    if got is None:
        return {"skipped": "missing"}
    row = dict(got)
    if row["first_response_at"] is not None or row["stage"] != "new" or row["first_response_due_at"] is None:
        return {"skipped": "answered_or_closed"}
    due = row["first_response_due_at"]
    now = clock.now()
    if due_epoch is not None and int(due.timestamp()) != int(due_epoch) and due > now:
        arm(cur, hotel, row)
        return {"rearmed": due.isoformat()}
    if due > now:  # early run (clock skew): come back at the due time
        arm(cur, hotel, row)
        return {"early": True}
    if not row["sla_breached"]:
        cur.execute("UPDATE inq_inquiries SET sla_breached = TRUE, updated_at = %s WHERE id = %s", (now, row["id"]))
        row["sla_breached"] = True
    if row["is_test"]:
        return {"breached": True, "signal": False}
    upsert_unanswered(cur, hotel, row)
    return {"breached": True, "signal": True}


# ─── First response (4.5) ───


def record_first_response(cur: Any, hotel: Hotel, row: dict, *, by: str, at: datetime | None = None) -> bool:
    """Stamp the first qualifying outbound (staff or approved AI; never the
    ack) and stop the clock. False when one was already recorded."""
    if row.get("first_response_at") is not None:
        return False
    at = at or clock.now()
    start = row.get("stage_changed_at") if row["stage"] == "new" and row.get("stage_changed_at") else row["created_at"]
    minutes = business_minutes_between(hotel, start, at, settings_for(cur, hotel.slug))
    cur.execute(
        """
        UPDATE inq_inquiries SET first_response_at = %s, first_response_by = %s, first_response_business_min = %s,
               updated_at = %s
        WHERE id = %s AND first_response_at IS NULL
        """,
        (at, by, minutes, clock.now(), row["id"]),
    )
    row.update(first_response_at=at, first_response_by=by, first_response_business_min=minutes)
    if row.get("first_response_due_at"):
        jobs.cancel(cur, dedupe_key=timer_key(row["id"], row["first_response_due_at"]))
    resolve_unanswered(cur, hotel.slug, row["id"], resolution="answered")
    return True
