"""
Inquiry stage machine (04 spec 4.2): every stage change goes through
`transition`, which logs inq_stage_events and refreshes money at risk.

    new --first outbound--> replied --quote sent--> quoted --link sent--> payment_pending --verify--> confirmed_verified
    new|replied --quote sent--> quoted            quoted (pay at hotel) --verify--> confirmed_verified
    any open --lost | decline | dates_changed | spam--> lost | turned_down | future | spam
    new --routed to concierge or service--> rerouted
    future --remind date or new inbound--> new ;  lost --new inbound within 30 d--> new (reopen)

`quoted` + a guest reply only sets guest_engaged (no stage change).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.audit import actor_fields, audit
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel, get_hotel
from clefs.db import Json
from clefs.modules.inquiries import risk, store
from clefs.modules.inquiries.models import LOST_REASONS, OPEN_STAGES, REOPEN_DAYS, TURNDOWN_REASONS

# ─── Allowed moves ───

_CLOSE = ("lost", "turned_down", "future", "spam")
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("replied", "quoted", "rerouted", *_CLOSE),
    "replied": ("quoted", *_CLOSE),
    "quoted": ("payment_pending", "confirmed_verified", *_CLOSE),
    # A cancelled link can be followed by a new quote version.
    "payment_pending": ("confirmed_verified", "quoted", *_CLOSE),
    "future": ("new", "lost", "spam"),
    "lost": ("new",),
    "turned_down": (),
    "spam": (),
    "rerouted": (),
    "confirmed_verified": (),
}


def can_transition(from_stage: str, to_stage: str) -> bool:
    return to_stage in TRANSITIONS.get(from_stage, ())


def transition(
    cur: Any,
    row: dict,
    to_stage: str,
    *,
    actor: Any = None,
    reason: str | None = None,
    data: dict | None = None,
    lost_reason: str | None = None,
    lost_note: str | None = None,
    remind_on: date | None = None,
    hotel: Hotel | None = None,
) -> dict:
    """Move `row` to `to_stage` (409 illegal_stage otherwise) and return the
    updated row. Idempotent for a no-op move."""
    frm = row["stage"]
    if frm == to_stage:
        return row
    if not can_transition(frm, to_stage):
        raise ClefsError(409, f"cannot move an inquiry from {frm} to {to_stage}", "illegal_stage")
    changes: dict[str, Any] = {"stage": to_stage, "stage_changed_at": clock.now()}
    if to_stage == "lost":
        if lost_reason not in LOST_REASONS:
            raise ClefsError(400, "lost needs a lost reason code", "invalid_lost_reason")
        changes.update(lost_reason=lost_reason, lost_note=lost_note)
    elif to_stage == "turned_down":
        if lost_reason not in TURNDOWN_REASONS:
            raise ClefsError(400, "turned_down needs a turn-down reason code", "invalid_lost_reason")
        changes.update(lost_reason=lost_reason, lost_note=lost_note)
    elif to_stage == "future":
        if remind_on is None:
            raise ClefsError(400, "future needs a remind date", "remind_on_required")
        changes.update(future_remind_on=remind_on, lost_reason="DATES_CHANGED" if lost_reason is None else lost_reason)
    elif to_stage == "spam":
        changes.update(lost_reason=lost_reason or None)
    elif to_stage == "new":
        # Reopen: the old close reason no longer holds, and the guest who
        # wrote again is owed a fresh first response (a new SLA clock).
        changes.update(lost_reason=None, lost_note=None, future_remind_on=None, first_response_at=None,
                       first_response_by=None, first_response_business_min=None, first_response_due_at=None,
                       sla_breached=False)
    updated = store.update_inquiry(cur, row["id"], changes)
    hotel = hotel or get_hotel(cur, row["hotel_slug"])
    updated = store.update_inquiry(cur, row["id"], risk.compute(cur, hotel, updated))
    actor_type, actor_id, _ = actor_fields(actor)
    cur.execute(
        """
        INSERT INTO inq_stage_events (hotel_slug, inquiry_id, from_stage, to_stage, at, actor_type, actor_id, reason, data)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (row["hotel_slug"], row["id"], frm, to_stage, clock.now(), actor_type, actor_id, reason, Json(data or {})),
    )
    if actor is not None:
        audit(cur, actor, "inquiries.stage", "inquiry", row["id"],
              before={"stage": frm}, after={"stage": to_stage, "reason": reason}, hotel_slug=row["hotel_slug"])
    if to_stage not in OPEN_STAGES:
        from clefs.modules.inquiries import sla

        sla.resolve_unanswered(cur, row["hotel_slug"], row["id"], resolution=f"stage:{to_stage}")
    row.clear()
    row.update(updated)
    return row


# ─── Reopen rule (4.2) ───


def reopenable(row: dict) -> bool:
    """A lost or future inquiry reopens on a new inbound from the same
    contact while its last activity is within 30 days; everything else that
    is closed stays closed (the caller then opens a new inquiry)."""
    return row["stage"] in ("lost", "future") and store.within(row.get("last_activity_at"), REOPEN_DAYS)


# ─── Qualifying outbound (4.2, 4.3, 4.5) ───

CTWA_FREE_WINDOW_H = 72


def on_outbound(cur: Any, row: dict, *, by: str, at: Any = None, hotel: Hotel | None = None,
                actor: Any = None) -> dict:
    """Call after a staff- or approved-AI reply reached the guest (never the
    ack): stamps the first response (stops the SLA clock and resolves
    inq.unanswered), moves new -> replied and, for a click-to-WhatsApp lead
    answered inside its 24 h window, records Meta's 72 h free window."""
    from datetime import timedelta

    from clefs.modules.inquiries import sla

    hotel = hotel or get_hotel(cur, row["hotel_slug"])
    at = at or clock.now()
    first = sla.record_first_response(cur, hotel, row, by=by, at=at)
    if first and row.get("ctwa_clid") and row.get("wa_window_expires_at") and at <= row["wa_window_expires_at"]:
        until = at + timedelta(hours=CTWA_FREE_WINDOW_H)
        row.update(store.update_inquiry(cur, row["id"], {"ctwa_free_window_expires_at": until}))
    if row["stage"] == "new":
        transition(cur, row, "replied", actor=actor, reason=f"outbound:{by}", hotel=hotel)
    return row
