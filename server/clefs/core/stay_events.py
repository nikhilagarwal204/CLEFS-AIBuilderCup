"""
Stay events: the frozen names and payload of the core/stays event outbox
(04 spec D1, frozen in wave 0a so lane F2 (guest journeys) builds against it
while the domain lane builds core/stays).

Names and payload types only. Nothing here emits, stores or dispatches: the
domain lane owns the `stay_events` table, the `@on_stay_event(consumer)`
registration and the `stays.dispatch_events` job (every minute, one cursor per
consumer). A consumer written today looks like:

    from clefs.core.stay_events import StayEvent, CHECKED_OUT

    @on_stay_event("guests.journeys")              # domain lane, core/stays
    def on_event(cur, ev: StayEvent) -> None:
        if ev["event"] == CHECKED_OUT:
            ...

Rules every emitter follows and every consumer may rely on:
- One event per change, in `occurred_at` order per reservation. Delivery is
  at least once: a consumer must be idempotent (key on the event `id`).
- `changes` maps a field to [old, new], JSON-typed: dates as "YYYY-MM-DD",
  instants as ISO 8601 with an offset, statuses as the PMS status strings
  ("CONFIRMED", "IN_HOUSE", "CHECKED_OUT", "CANCELLED", "NO_SHOW"). Emitters may
  add fields beyond CHANGE_FIELDS below; consumers ignore what they do not know.
- No contact value ever appears in a payload. contact_changed carries
  reservation_contacts ids only; a consumer that needs a value unseals it
  through core.stays.contact_value (audited), never from the event.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, TypedDict

# ─── Names ───

CREATED = "created"
DATES_CHANGED = "dates_changed"
CANCELLED = "cancelled"
NO_SHOW = "no_show"
CHECKED_IN = "checked_in"
CHECKED_OUT = "checked_out"
CONTACT_CHANGED = "contact_changed"

StayEventName = Literal[
    "created", "dates_changed", "cancelled", "no_show", "checked_in", "checked_out", "contact_changed"
]
STAY_EVENTS: tuple[str, ...] = (
    CREATED,
    DATES_CHANGED,
    CANCELLED,
    NO_SHOW,
    CHECKED_IN,
    CHECKED_OUT,
    CONTACT_CHANGED,
)

# The outbox table and the dispatcher job the domain lane creates (0020-0099).
STAY_EVENTS_TABLE = "stay_events"
DISPATCH_JOB = "stays.dispatch_events"

# ─── Payload ───


class StayEvent(TypedDict):
    """What a consumer receives (and what one stay_events row holds): the D1
    payload {event, hotel_slug, reservation_id, occurred_at, changes} plus the
    row id, which the per-consumer cursor already orders by."""

    id: int  # stay_events.id: the idempotency key for consumers
    event: StayEventName
    hotel_slug: str
    reservation_id: int
    occurred_at: datetime  # when it happened (PMS time), tz-aware; not when it was imported
    changes: dict[str, list[Any]]  # field -> [old, new]; see CHANGE_FIELDS


# The fields each event always carries in `changes` ([old, new]). created
# carries the new values with old = None, so a consumer never needs a second
# read to learn the stay dates or the status.
CHANGE_FIELDS: dict[str, tuple[str, ...]] = {
    CREATED: ("status", "check_in", "check_out"),
    DATES_CHANGED: ("check_in", "check_out"),
    CANCELLED: ("status",),
    NO_SHOW: ("status",),
    CHECKED_IN: ("status", "checked_in_at"),
    CHECKED_OUT: ("status", "checked_out_at"),
    CONTACT_CHANGED: ("reservation_contact_ids",),  # sorted id lists, never values
}


class DatesChanged(TypedDict):
    """`changes` of a dates_changed event. Both dates are always present (the
    one that did not move as [same, same]), so a consumer re-plans from the
    event alone."""

    check_in: list[str]  # ["2026-10-01", "2026-10-02"]
    check_out: list[str]


class ContactChanged(TypedDict):
    """`changes` of a contact_changed event: which reservation_contacts rows
    the reservation had before and after (added, replaced or purged)."""

    reservation_contact_ids: list[list[int]]  # [[old ids], [new ids]]
