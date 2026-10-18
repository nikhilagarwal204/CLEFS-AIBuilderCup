"""
Request state subscriptions (04 spec 7.2, the Wave 1 "on_request_state" ask).

Another module registers a callback through service.api.on_request_state and
hears every state machine transition (state.transition) of every request:
Upsell moves its order when the linked F&B request is completed or
cancelled, without importing Service internals and without polling.

Contract, pinned by tests/modules/service/test_gaps_hooks.py:

- fn(cur, change) runs INSIDE the transition's transaction, after the row,
  the feed event and the audit row are written, so a callback reads the new
  state and its writes commit or roll back with the transition.
- Each callback runs in its own SAVEPOINT. WHY: the transition is the
  source of truth; a subscriber's bug must never undo a housekeeper's
  "done". A failing callback is logged and rolled back alone.
- A replayed client_event_id (the device outbox resending) changed nothing,
  so it notifies nobody: subscribers see each real transition exactly once.
- Registration is by identity and idempotent (module import can run twice
  under reloaders); callbacks run in registration order.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("clefs.service.hooks")

# ─── Registry ───

StateCallback = Callable[[Any, dict], Any]
_SUBSCRIBERS: list[StateCallback] = []

# The request columns copied into `change`. WHY a copy and not the row: the
# row carries guest words (description, original_text) that a subscriber has
# no business reading, and a stable, small shape is easier to keep frozen.
ROW_KEYS = ("room", "request_type_key", "department_key", "severity", "source", "guest_ref", "amenity_item_id",
            "quantity", "resolution_code", "assigned_staff_id")


def subscribe(fn: StateCallback) -> StateCallback:
    """Add fn (idempotent). Returns fn, so it also works as a decorator."""
    if not callable(fn):
        raise TypeError("on_request_state needs a callable fn(cur, change)")
    if fn not in _SUBSCRIBERS:
        _SUBSCRIBERS.append(fn)
    return fn


def unsubscribe(fn: StateCallback) -> None:
    """Remove fn if present (tests, and a module switched off at runtime)."""
    if fn in _SUBSCRIBERS:
        _SUBSCRIBERS.remove(fn)


def subscribers() -> tuple[StateCallback, ...]:
    return tuple(_SUBSCRIBERS)


# ─── Dispatch ───


def change_of(row: dict, *, action: str, from_state: str | None, to_state: str, actor_type: str,
              event_id: int | None) -> dict:
    """The dict every subscriber receives (documented in api.on_request_state)."""
    out = {
        "hotel_slug": row["hotel_slug"],
        "request_id": row["id"],
        "action": action,
        "from_state": from_state,
        "to_state": to_state,
        "status": row.get("status"),
        "version": row.get("version"),
        "actor_type": actor_type,
        "event_id": event_id,
    }
    out.update({k: row.get(k) for k in ROW_KEYS})
    return out


def fire(cur: Any, change: dict) -> int:
    """Run every subscriber for one transition. -> how many succeeded."""
    ok = 0
    for i, fn in enumerate(tuple(_SUBSCRIBERS)):
        sp = f"svc_hook_{i}"
        cur.execute(f"SAVEPOINT {sp}")
        try:
            fn(cur, dict(change))  # a copy: one subscriber cannot edit what the next sees
        except Exception:
            cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
            log.exception("on_request_state subscriber %r failed for request %s (transition kept)",
                          getattr(fn, "__qualname__", fn), change.get("request_id"))
            continue
        cur.execute(f"RELEASE SAVEPOINT {sp}")
        ok += 1
    return ok
