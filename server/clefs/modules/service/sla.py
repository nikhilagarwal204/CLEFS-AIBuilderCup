"""
SLA timers: the per-hotel sweep (03 spec 1.4).

sweep(cur, hotel_slug) runs every minute from the job (service.sla_sweep)
and also on read (maybe_sweep, from GET queue) when the last sweep is older
than 30 s: Vercel's cron is the clock, a busy dashboard is the backup.

  - Guarded by pg_try_advisory_xact_lock(hashtext('sla:' || hotel)): a
    second sweeper skips instead of waiting, and the lock ends with the
    transaction (safe through PgBouncer transaction pooling).
  - ACK ladder over unacknowledged v2 requests (new, assigned, reopened):
    each ladder step newly crossed inserts one svc_escalations row (UNIQUE
    per request, cycle, level: a step fires once, whoever sweeps), resolves
    recipients from the roster at that moment, writes an "escalated" feed
    event (devices chime or loop the alarm) and joins that person's digest.
  - Resolution: past resolve_due_at sets sla_resolve_breached and tells the
    supervisor once (the flag is the once).
  - Digests are sent at the end: one message per recipient per sweep.
  - Shadow mode (settings.escalation_live false; the first 7 days by
    default): everything above is computed, stored and shown, nothing
    external is sent (svc_escalations.shadow = true). An emergency is the
    exception: life safety pages for real from day one, like a staff SOS.
  - Late jobs move (05 spec, decision 7): when the supervisor step (3
    minutes, nobody accepted) fires, the job also goes to the next free
    person (routing.free_staff) if there is one. The ladder clock is kept,
    so the duty manager still hears at 5 minutes and the GM at 10, counted
    from the guest's request.
  - Closure (05 spec, decision 8): a completed job the guest has not
    answered for 30 minutes is closed (closure_reason no_reply).

Pre-v2 rows (intake_version NULL) never escalate: their clocks were never
set, and a deploy must not page the GM about last month's backlog.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from clefs.core import clock
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.service import config, escalation, state
from clefs.modules.service import constants as C
from clefs.modules.service import settings as svc_settings

log = logging.getLogger("clefs.service.sla")

EVAL_ON_READ_S = 30
MAX_ROWS = 500
# A completed job with no answer from the guest for this long is closed.
NO_REPLY_CLOSE_MIN = 30


def _life_safety(row: dict) -> bool:
    """An emergency pages for real even in shadow mode (safety first)."""
    return row.get("sensitivity") == "emergency"

# ─── Lock and bookkeeping ───


def try_lock(cur: Any, hotel_slug: str) -> bool:
    cur.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok", (f"sla:{hotel_slug}",))
    return bool(cur.fetchone()["ok"])


def _ensure_state(cur: Any, hotel_slug: str) -> None:
    """The first sweep starts the shadow period (clock time, not NOW(), so
    tests and replays agree)."""
    cur.execute(
        "INSERT INTO svc_sweep_state (hotel_slug, shadow_started_at) VALUES (%s, %s) ON CONFLICT (hotel_slug) DO NOTHING",
        (hotel_slug, clock.now()),
    )


class _Run:
    """What one sweep collected: digests per person and counters."""

    def __init__(self, cur: Any, hotel: Any, live: bool) -> None:
        self.cur, self.hotel, self.live = cur, hotel, live
        self.now_ms = clock.now_ms()
        self.digests: dict[str, dict] = {}
        self.result = {"escalated": 0, "ack_breached": 0, "resolve_breached": 0, "sent": 0, "shadow": not live,
                       "recipients": 0, "unreached": 0, "reassigned": 0, "closed": 0}
        self._staff: list[dict] | None = None
        self._policies: dict[tuple, list] = {}

    def staff(self) -> list[dict]:
        if self._staff is None:
            self.cur.execute(escalation._STAFF_SQL, {"now": clock.now(), "slug": self.hotel.slug})
            self._staff = [dict(r) for r in self.cur.fetchall()]
        return self._staff

    def policy_ladder(self, row: dict) -> list:
        key = (row.get("request_type_key"), row.get("department_key"), row.get("severity"))
        if key not in self._policies:
            p = config.sla_policy(self.cur, self.hotel.slug, request_type_key=key[0], department_key=key[1], severity=key[2])
            self._policies[key] = p.get("ladder") or list(C.DEFAULT_LADDER)
        return self._policies[key]

    def add(self, person: escalation.Person, item: dict, escalation_id: int | None) -> None:
        d = self.digests.setdefault(person.key, {"person": person, "items": [], "ids": []})
        d["person"].roles |= person.roles
        if not any(i["request_id"] == item["request_id"] for i in d["items"]):
            d["items"].append(item)
        if escalation_id is not None:
            d["ids"].append(escalation_id)


# ─── ACK ladder ───


def _item(row: dict, now_ms: int) -> dict:
    start = row.get("ladder_started_at") or row.get("timestamp") or now_ms
    return {"request_id": row["id"], "room": row.get("room"), "label": escalation.item_label(row),
            "minutes": max(0, (now_ms - int(start)) // 60_000), "urgent": escalation.is_urgent(row),
            "life_safety": _life_safety(row)}


def _escalate_row(run: _Run, row: dict) -> int:
    """Cross every ladder step now due for one request. -> steps crossed."""
    cur, slug = run.cur, run.hotel.slug
    elapsed_min = (run.now_ms - int(row.get("ladder_started_at") or row["timestamp"])) / 60_000
    cycle = int(row.get("ladder_cycle") or 0)
    current = int(row.get("escalation_level") or 0)
    crossed = 0
    top = current
    shadow = not (run.live or _life_safety(row))
    for step in escalation.ladder_for(row, run.policy_ladder(row)):
        if step["level"] == 0 or step["after_min"] > elapsed_min or step["level"] <= current:
            continue  # level 0 is local: the device repeats its chime
        people = escalation.people_for(cur, slug, step["to"], row, staff=run.staff())
        channels = sorted({ch for p in people for ch in escalation.channels_for(p, step["to"])})
        cur.execute(
            """INSERT INTO svc_escalations (hotel_slug, request_id, cycle, level, target_role, recipients, channels, shadow, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (request_id, cycle, level) DO NOTHING RETURNING id""",
            (slug, row["id"], cycle, step["level"], step["to"], Json([p.public() for p in people]), channels,
             shadow, clock.now()),
        )
        got = cur.fetchone()
        top = max(top, step["level"])
        if got is None:
            continue  # another sweeper crossed this step first
        crossed += 1
        item = _item(row, run.now_ms)
        for p in people:
            run.add(p, item, got["id"])
        if not people:
            run.result["unreached"] += 1
        _feed_event(cur, row, step, people, shadow=shadow)
        if step["to"] == "supervisor":
            # 05 decision 7: nobody accepted in 3 minutes, so the job moves
            # to the next free person too (savepoint: a failed hand-over
            # must never undo the escalation bookkeeping).
            moved = state.guarded(cur, "auto_reassign", _auto_reassign, run, row)
            if moved:
                run.result["reassigned"] += 1
        if step["level"] == 3:
            # Level 3 is the GM (03 spec 1.4): the brief hears it too. Only
            # the sweeper whose INSERT won gets here, so it is raised once
            # per request and cycle; in a savepoint, since a signal is
            # advice and must never undo the escalation bookkeeping.
            from clefs.modules.service import signals as svc_signals

            state.guarded(cur, "l3_signal", svc_signals.l1_or_ack_breach, cur, run.hotel, row,
                          shadow=shadow, recipients=len(people))
    if top > current:
        cur.execute("UPDATE staff_requests SET escalation_level = %s WHERE id = %s", (top, row["id"]))
        row["escalation_level"] = top
    return crossed


def _auto_reassign(run: _Run, row: dict) -> dict | None:
    """Hand an unaccepted job to the best free colleague, keeping the ladder
    clock (state.transition keep_clock). Never for urgent work (its own
    ladder pages managers at once) or work someone already accepted. The
    sweep's copy of the row is updated in place. -> the new row, or None
    when nobody else is free."""
    from clefs.modules.service import routing  # routing is not needed by the rest of the sweep

    if escalation.is_urgent(row) or row.get("state") not in C.UNACKED_STATES:
        return None
    holder = row.get("assigned_staff_id")
    free = routing.free_staff(run.cur, run.hotel, row, exclude_staff=[holder] if holder else [])
    if not free:
        return None
    best = free[0]
    action = "reassign" if row.get("state") == "assigned" else "assign"
    out = state.transition(
        run.cur, run.hotel.slug, row["id"], action, state.SYSTEM, target={"staff_id": best["staff_id"]},
        keep_clock=True, note=f"Not accepted in time: moved to {best['name']}",
        extra={"auto": True, "reason": "not_accepted", "from_staff_id": holder,
               "from_device_id": row.get("assigned_device_id"), "pool": "free"},
    )
    routing.notify_assignee(run.cur, out["row"])
    row.update(out["row"])
    return out["row"]


def _feed_event(cur: Any, row: dict, step: dict, people: list, *, shadow: bool) -> None:
    """The device feed carries the step. Security's step is addressed to the
    security department with no floor, so every Security device (and the
    unscoped desk devices) loops its alarm wherever the room is."""
    scope = {**row, "department_key": "security", "floor": None} if step["to"] == "security" else row
    state.record_event(
        cur, scope, "escalated", state.SYSTEM, action=step["to"],
        payload={"level": step["level"], "to": step["to"], "cycle": row.get("ladder_cycle") or 0, "shadow": shadow,
                 "urgent": escalation.is_urgent(row), "alarm": "loop" if step["to"] == "security" else "chime",
                 "recipients": [p.name for p in people]},
    )


def _ack_breach(run: _Run, row: dict) -> bool:
    """First time past ack_due_at: flag it once and tell the feed."""
    due = row.get("ack_due_at")
    if row.get("sla_ack_breached") or due is None or run.now_ms <= int(due):
        return False
    run.cur.execute("UPDATE staff_requests SET sla_ack_breached = TRUE WHERE id = %s", (row["id"],))
    row["sla_ack_breached"] = True
    state.record_event(run.cur, row, "sla_breach", state.SYSTEM, action="ack",
                       payload={"what": "ack", "due_at": int(due), "late_min": (run.now_ms - int(due)) // 60_000})
    return True


# ─── Resolution ───


def _resolution(run: _Run, rows: list[dict]) -> None:
    """Past resolve_due_at: set sla_resolve_breached and tell the supervisor
    once (the duty manager when the department has no supervisor on)."""
    for row in rows:
        run.cur.execute("UPDATE staff_requests SET sla_resolve_breached = TRUE WHERE id = %s", (row["id"],))
        row["sla_resolve_breached"] = True
        run.result["resolve_breached"] += 1
        people = escalation.people_for(run.cur, run.hotel.slug, "supervisor", row, staff=run.staff())
        if not people:
            people = escalation.people_for(run.cur, run.hotel.slug, "duty_manager", row, staff=run.staff())
        item = {**_item(row, run.now_ms), "label": f"{escalation.item_label(row)} (overdue)"}
        for p in people:
            run.add(p, item, None)
        state.record_event(run.cur, row, "sla_breach", state.SYSTEM, action="resolve",
                           payload={"what": "resolve", "due_at": row.get("resolve_due_at"), "shadow": not run.live,
                                    "recipients": [p.name for p in people]})


# ─── Send ───


def _flush(run: _Run) -> None:
    """One digest per person for everything this sweep collected. A digest
    carrying an emergency goes out even in shadow mode."""
    for d in run.digests.values():
        p = d["person"]
        live = run.live or any(i.get("life_safety") for i in d["items"])
        out = escalation.send_digest(run.cur, run.hotel, p, d["items"], d["ids"], live=live)
        run.result["recipients"] += 1
        if out["sent"]:
            run.result["sent"] += 1
            if out["outbox_id"] and d["ids"]:
                run.cur.execute(
                    "UPDATE svc_escalations SET notify_ids = array_append(notify_ids, %s) WHERE id = ANY(%s)",
                    (out["outbox_id"], d["ids"]),
                )


def _close_unanswered(run: _Run) -> list[dict]:
    """Completed v2 jobs the guest has not answered for 30 minutes are
    closed with closure_reason no_reply (a "yes" closed them already; a
    "no" reopened them). The guest may still answer within the tracker's
    24 h window: "not fixed" reopens a closed job too."""
    run.cur.execute(
        """
        UPDATE staff_requests SET closed_at = %s, closure_reason = 'no_reply'
        WHERE id IN (
            SELECT id FROM staff_requests
            WHERE hotel_slug = %s AND state = 'completed' AND closed_at IS NULL AND intake_version IS NOT NULL
              AND resolved_at IS NOT NULL AND resolved_at <= %s
            ORDER BY resolved_at LIMIT %s FOR UPDATE SKIP LOCKED)
        RETURNING *
        """,
        (run.now_ms, run.hotel.slug, run.now_ms - NO_REPLY_CLOSE_MIN * 60_000, MAX_ROWS),
    )
    rows = [dict(r) for r in run.cur.fetchall()]
    for row in rows:
        state.record_event(run.cur, row, "closed", state.SYSTEM, payload={"closure_reason": "no_reply"})
    run.result["closed"] += len(rows)
    return rows


def _mark_acked(cur: Any, hotel_slug: str) -> None:
    """Close the escalation rows of requests someone has acknowledged (or
    whose cycle ended), so the console's open-escalation list stays true."""
    cur.execute(
        """
        UPDATE svc_escalations e
        SET acked_at = COALESCE(to_timestamp(r.acknowledged_at / 1000.0), %s)
        FROM staff_requests r
        WHERE e.request_id = r.id AND e.hotel_slug = %s AND e.acked_at IS NULL
          AND (r.state <> ALL(%s) OR e.cycle < r.ladder_cycle)
        """,
        (clock.now(), hotel_slug, list(C.UNACKED_STATES)),
    )


# ─── The sweep ───

_UNACKED_SQL = """
    SELECT * FROM staff_requests
    WHERE hotel_slug = %s AND state = ANY(%s) AND intake_version IS NOT NULL
      AND COALESCE(ladder_started_at, "timestamp") <= %s
    ORDER BY COALESCE(ladder_started_at, "timestamp")
    LIMIT %s
    FOR UPDATE SKIP LOCKED
"""
_OVERDUE_SQL = """
    SELECT * FROM staff_requests
    WHERE hotel_slug = %s AND state = ANY(%s) AND intake_version IS NOT NULL
      AND NOT sla_resolve_breached AND resolve_due_at IS NOT NULL AND resolve_due_at < %s
    ORDER BY resolve_due_at
    LIMIT %s
    FOR UPDATE SKIP LOCKED
"""


def sweep(cur: Any, hotel_slug: str) -> dict:
    """One pass over the hotel's open work. Rows another transaction holds
    (a transition in flight) are skipped this minute (SKIP LOCKED): the ack
    that is committing would have made their step moot anyway."""
    if not try_lock(cur, hotel_slug):
        return {"skipped": "locked"}
    hotel = get_hotel(cur, hotel_slug)
    _ensure_state(cur, hotel_slug)
    s = svc_settings.load(cur, hotel_slug)
    run = _Run(cur, hotel, svc_settings.escalation_live(cur, hotel_slug, s))
    cur.execute(_UNACKED_SQL, (hotel_slug, list(C.UNACKED_STATES), run.now_ms, MAX_ROWS))
    unacked = [dict(r) for r in cur.fetchall()]
    changed: dict[int, dict] = {}
    for row in unacked:
        n = _escalate_row(run, row)
        breached = _ack_breach(run, row)
        run.result["escalated"] += n
        run.result["ack_breached"] += int(breached)
        if n or breached:
            changed[row["id"]] = row
    open_states = [x for x in C.OPEN_STATES if x != "scheduled"]
    cur.execute(_OVERDUE_SQL, (hotel_slug, open_states, run.now_ms, MAX_ROWS))
    overdue = [dict(r) for r in cur.fetchall()]
    _resolution(run, overdue)
    changed.update({r["id"]: r for r in overdue})
    closed = _close_unanswered(run)
    _flush(run)
    _mark_acked(cur, hotel_slug)
    for row in changed.values():
        state.publish_changed(cur, row, "sla")
    for row in closed:
        state.publish_changed(cur, row, "closed")
    _signals(run, list(changed.values()))
    cur.execute(
        "UPDATE svc_sweep_state SET last_sweep_at = %s, last_result = %s WHERE hotel_slug = %s",
        (clock.now(), Json(run.result), hotel_slug),
    )
    return run.result


def _signals(run: _Run, changed: list[dict]) -> None:
    """Breach clusters, only when this sweep found a new breach, in a
    savepoint: a signal is advice and never undoes the sweep's bookkeeping."""
    from clefs.modules.service import signals as svc_signals

    if run.result["ack_breached"] or run.result["resolve_breached"]:
        shift = config.shift_key(run.cur, run.hotel, clock.now())
        state.guarded(run.cur, "sla_cluster", svc_signals.breach_cluster, run.cur, run.hotel, changed, shift_key=shift)


def maybe_sweep(cur: Any, hotel_slug: str) -> dict | None:
    """Evaluate-on-read: sweep when the last one is older than 30 s. Runs in
    a savepoint, so a failure (or a read-only transaction) never breaks the
    read that triggered it. -> the sweep result, or None when it was fresh."""
    cur.execute("SELECT last_sweep_at FROM svc_sweep_state WHERE hotel_slug = %s", (hotel_slug,))
    r = cur.fetchone()
    if r and r["last_sweep_at"] and clock.now() - r["last_sweep_at"] < timedelta(seconds=EVAL_ON_READ_S):
        return None
    return state.guarded(cur, "sla_sweep", sweep, cur, hotel_slug)


def page_now(cur: Any, hotel_slug: str, request_id: int) -> dict:
    """Urgent paging for one request, right after intake files it (sensitive
    or L1 skip the ladder: Security and the duty manager at once, the GM at
    once for L1). Idempotent with the sweep through svc_escalations' unique
    key: whichever runs first sends; the other finds the step taken."""
    hotel = get_hotel(cur, hotel_slug)
    _ensure_state(cur, hotel_slug)
    run = _Run(cur, hotel, svc_settings.escalation_live(cur, hotel_slug))
    row = state.load(cur, hotel_slug, request_id)
    if row is None or row.get("state") not in C.UNACKED_STATES or not escalation.is_urgent(row):
        return {**run.result, "skipped": "not_urgent"}
    run.result["escalated"] = _escalate_row(run, row)
    _flush(run)
    return run.result
