"""Stay-event outbox (04 spec D1): emit, register consumers, dispatch.

Names and payload are frozen in clefs.core.stay_events. Emission is a row in
`stay_events` inside the writer's transaction, so an event exists exactly when
the change it describes committed. Dispatch reads per consumer from a cursor
(tx_id, id) and only past transactions that can no longer add a lower key
(see _ELIGIBLE), so a long import that commits after a short one is never
skipped.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time as _time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

from clefs.core import clock
from clefs.core.stay_events import CHANGE_FIELDS, STAY_EVENTS, StayEvent

log = logging.getLogger("clefs.stays.events")

# A consumer that fails this many times in a row on one event moves past it
# (the event goes to stay_event_failures, a dead letter an operator can replay).
MAX_FAILURES = 5
BATCH = 200

Handler = Callable[[Any, StayEvent], Any]
_CONSUMER_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z0-9_.]+$")

# ─── Emission ───


def _json_value(v: Any) -> Any:
    """JSON types per the frozen contract: dates "YYYY-MM-DD", instants ISO
    8601 with an offset, lists and dicts recursively."""
    if isinstance(v, datetime):
        if v.tzinfo is None:
            raise ValueError("stay event instants must be timezone-aware")
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _json_value(x) for k, x in v.items()}
    return v


def emit(
    cur: Any,
    hotel_slug: str,
    reservation_id: int,
    event: str,
    changes: dict[str, Any],
    *,
    occurred_at: datetime | None = None,
) -> int:
    """Write one event in the caller's transaction and return its id.

    `changes` maps field -> (old, new) and must carry the event's
    CHANGE_FIELDS; extra fields are allowed. No contact value may appear:
    contact_changed carries reservation_contacts ids only."""
    if event not in STAY_EVENTS:
        raise ValueError(f"unknown stay event {event!r}")
    missing = [f for f in CHANGE_FIELDS[event] if f not in changes]
    if missing:
        raise ValueError(f"{event} needs changes for {missing}")
    payload: dict[str, list[Any]] = {}
    for field, pair in changes.items():
        old, new = pair
        payload[field] = [_json_value(old), _json_value(new)]
    at = occurred_at or clock.now()
    if at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")
    cur.execute(
        """
        INSERT INTO stay_events (hotel_slug, reservation_id, event, occurred_at, changes)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (hotel_slug, reservation_id, event, at, json.dumps(payload)),
    )
    return int(_first(cur.fetchone()))


def _first(row: Any) -> Any:
    return row[0] if isinstance(row, (tuple, list)) else next(iter(row.values()))


def events_for(cur: Any, hotel_slug: str, reservation_id: int) -> list[StayEvent]:
    """Every event of one reservation, oldest first (for consoles and tests)."""
    cur.execute(
        """
        SELECT id, event, hotel_slug, reservation_id, occurred_at, changes FROM stay_events
        WHERE hotel_slug = %s AND reservation_id = %s ORDER BY id
        """,
        (hotel_slug, reservation_id),
    )
    return [_as_event(r) for r in cur.fetchall()]


def _as_event(r: Any) -> StayEvent:
    row = dict(r)
    changes = row["changes"]
    if isinstance(changes, str):
        changes = json.loads(changes)
    return StayEvent(
        id=int(row["id"]),
        event=row["event"],
        hotel_slug=row["hotel_slug"],
        reservation_id=int(row["reservation_id"]),
        occurred_at=row["occurred_at"],
        changes=changes or {},
    )


# ─── Consumers ───


@dataclass(frozen=True)
class Consumer:
    name: str
    fn: Handler
    registered_from: str


_consumers: dict[str, Consumer] = {}
_lock = threading.Lock()


def on_stay_event(consumer: str) -> Callable[[Handler], Handler]:
    """Register fn(cur, ev: StayEvent) under a consumer name such as
    "guests.journeys". Each consumer has its own cursor, so consumers never
    block each other. The handler runs in a savepoint of the dispatcher's
    transaction; raising rolls back only its own writes."""
    if not _CONSUMER_RE.match(consumer):
        raise ValueError(f"consumer {consumer!r} must look like '<module>.<what>'")

    def deco(fn: Handler) -> Handler:
        with _lock:
            existing = _consumers.get(consumer)
            if existing is not None and existing.registered_from != fn.__module__:
                raise ValueError(f"stay-event consumer {consumer!r} already registered by {existing.registered_from}")
            _consumers[consumer] = Consumer(consumer, fn, fn.__module__)
        return fn

    return deco


def registered_consumers() -> list[str]:
    with _lock:
        return sorted(_consumers)


def unregister_consumer(consumer: str) -> None:
    """Tests only."""
    with _lock:
        _consumers.pop(consumer, None)


# ─── Dispatch ───

# Which events a cursor may pass now. Rows of a transaction that is still in
# flight are invisible, and one that commits later may carry a LOWER tx_id
# than rows already read; so a row is eligible only below the smallest
# in-flight xid of this database. xids positively attributed to another
# database (pg_stat_activity) are ignored: they cannot write our stay_events,
# and on a shared server (tests, previews) they would otherwise stall
# dispatch. An xid we cannot attribute (a hidden session) still blocks, which
# is the safe side. The dispatcher's own transaction may read its own rows
# when nothing older in this database is in flight.
_ELIGIBLE = """
WITH s AS (
    SELECT pg_current_snapshot() AS snap, pg_current_xact_id_if_assigned() AS own
), foreign_x AS (
    SELECT backend_xid FROM pg_stat_activity
    WHERE backend_xid IS NOT NULL
      AND datid IS DISTINCT FROM (SELECT oid FROM pg_database WHERE datname = current_database())
), blocking AS (
    SELECT min(x) AS m FROM s, pg_snapshot_xip(s.snap) AS x
    WHERE NOT EXISTS (SELECT 1 FROM foreign_x f WHERE f.backend_xid = x::xid)
)
SELECT e.id, e.event, e.hotel_slug, e.reservation_id, e.occurred_at, e.changes, e.tx_id::text AS tx_id
FROM stay_events e, s, blocking b
WHERE (e.tx_id, e.id) > (%(tx)s::xid8, %(id)s)
  AND (e.tx_id < COALESCE(b.m, pg_snapshot_xmax(s.snap))
       OR (e.tx_id = s.own AND (b.m IS NULL OR s.own < b.m)))
ORDER BY e.tx_id, e.id
LIMIT %(n)s
"""


def _cursor_row(cur: Any, consumer: str) -> dict | None:
    """Lock the consumer's cursor (created on first use). None when another
    dispatcher holds it: that one is already delivering these events."""
    cur.execute("INSERT INTO stay_event_cursors (consumer) VALUES (%s) ON CONFLICT DO NOTHING", (consumer,))
    cur.execute(
        """
        SELECT consumer, last_tx_id::text AS last_tx_id, last_id, failures
        FROM stay_event_cursors WHERE consumer = %s FOR UPDATE SKIP LOCKED
        """,
        (consumer,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _advance(cur: Any, consumer: str, tx_id: str, event_id: int) -> None:
    cur.execute(
        """
        UPDATE stay_event_cursors
        SET last_tx_id = %s::xid8, last_id = %s, failures = 0, last_error = NULL, updated_at = NOW()
        WHERE consumer = %s
        """,
        (tx_id, event_id, consumer),
    )


def _fail(cur: Any, consumer: str, row: dict, failures: int, error: str) -> bool:
    """Count a failure; True when the event was dead-lettered (cursor moved)."""
    if failures >= MAX_FAILURES:
        cur.execute(
            """
            INSERT INTO stay_event_failures (consumer, event_id, error, attempts) VALUES (%s, %s, %s, %s)
            ON CONFLICT (consumer, event_id) DO UPDATE SET error = EXCLUDED.error, attempts = EXCLUDED.attempts
            """,
            (consumer, row["id"], error[:2000], failures),
        )
        _advance(cur, consumer, row["tx_id"], int(row["id"]))
        log.error("stay event %s dead-lettered for %s after %s failures: %s", row["id"], consumer, failures, error)
        return True
    cur.execute(
        "UPDATE stay_event_cursors SET failures = %s, last_error = %s, updated_at = NOW() WHERE consumer = %s",
        (failures, error[:2000], consumer),
    )
    return False


def dispatch(
    cur: Any,
    *,
    consumer: str | None = None,
    batch: int = BATCH,
    budget_s: float = 20.0,
) -> dict[str, dict[str, int]]:
    """Deliver pending events to one consumer (or all), in (tx_id, id) order,
    each in a savepoint. A failing event stops that consumer for this run
    (so order holds) and is retried next run; after MAX_FAILURES it is
    dead-lettered. Returns {consumer: {delivered, failed, dead}}."""
    with _lock:
        targets = [c for n, c in sorted(_consumers.items()) if consumer in (None, n)]
    deadline = _time.monotonic() + budget_s
    report: dict[str, dict[str, int]] = {}
    for c in targets:
        stats = report.setdefault(c.name, {"delivered": 0, "failed": 0, "dead": 0})
        state = _cursor_row(cur, c.name)
        if state is None:
            continue
        failures = int(state["failures"])
        last_tx, last_id = state["last_tx_id"], int(state["last_id"])
        while _time.monotonic() < deadline:
            cur.execute(_ELIGIBLE, {"tx": last_tx, "id": last_id, "n": batch})
            rows = [dict(r) for r in cur.fetchall()]
            if not rows:
                break
            stopped = False
            for row in rows:
                cur.execute("SAVEPOINT stay_event")
                try:
                    c.fn(cur, _as_event(row))
                except Exception as exc:  # noqa: BLE001 - a consumer bug must not stop the others
                    cur.execute("ROLLBACK TO SAVEPOINT stay_event")
                    failures += 1
                    stats["failed"] += 1
                    if _fail(cur, c.name, row, failures, f"{type(exc).__name__}: {exc}"):
                        stats["dead"] += 1
                        failures = 0
                        last_tx, last_id = row["tx_id"], int(row["id"])
                        continue
                    stopped = True
                    break
                cur.execute("RELEASE SAVEPOINT stay_event")
                _advance(cur, c.name, row["tx_id"], int(row["id"]))
                failures = 0
                last_tx, last_id = row["tx_id"], int(row["id"])
                stats["delivered"] += 1
            if stopped or len(rows) < batch:
                break
    return report


def replay_failure(cur: Any, consumer: str, event_id: int) -> bool:
    """Run a dead-lettered event through its consumer again (operator tool).
    True when it succeeded (the dead letter is removed)."""
    with _lock:
        c = _consumers.get(consumer)
    if c is None:
        raise ValueError(f"unknown stay-event consumer {consumer!r}")
    cur.execute(
        "SELECT id, event, hotel_slug, reservation_id, occurred_at, changes FROM stay_events WHERE id = %s",
        (event_id,),
    )
    row = cur.fetchone()
    if row is None:
        return False
    cur.execute("SAVEPOINT stay_event_replay")
    try:
        c.fn(cur, _as_event(row))
    except Exception as exc:  # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT stay_event_replay")
        cur.execute(
            "UPDATE stay_event_failures SET attempts = attempts + 1, error = %s WHERE consumer = %s AND event_id = %s",
            (f"{type(exc).__name__}: {exc}"[:2000], consumer, event_id),
        )
        return False
    cur.execute("RELEASE SAVEPOINT stay_event_replay")
    cur.execute("DELETE FROM stay_event_failures WHERE consumer = %s AND event_id = %s", (consumer, event_id))
    return True
