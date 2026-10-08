"""
Service signals (03 spec 1.7): the kinds Service raises into the owner's
brief, and the detectors behind the ones this module computes itself.

Kinds (all section service_risk except labour, which is compliance):
  service.l1_incident            immediate, at intake (intake.py)
  service.sla_breach_cluster     3+ SLA breaches in one shift, or any L1
                                 left unacknowledged past its due time
  service.repeat_fault           same room and fault twice in 7 days, or the
                                 same room type and fault 3 times in 14 days
  service.reopen_spike           today's reopens at least 3 and double the
                                 14-day daily average
  service.guest_unresolved       a guest said "not resolved" and is a VIP or
                                 on the second reopen
  service.understaffed           routing fell back or everyone was capped
  service.recovery_over_authority  a gesture above the giver's authority
  service.labour_flag            roster labour guardrails (guidance)
  service.l1_or_ack_breach       the ACK ladder reached level 3 (the GM): an
                                 L1 or emergency at once, anything else when
                                 nobody acknowledged it in time (sla.py)

Titles name rooms and request types, never the guest's words.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from clefs.core import clock
from clefs.core import signals as core_signals
from clefs.core import time as htime
from clefs.modules.service import constants as C

log = logging.getLogger("clefs.service.signals")

# ─── Kinds ───

KINDS: dict[str, dict] = {
    "service.l1_incident": {"label": "L1 incident", "section": "service_risk", "cadence": "daily", "owner": "gm"},
    "service.sla_breach_cluster": {"label": "SLA breaches", "section": "service_risk", "cadence": "daily", "owner": "gm"},
    "service.repeat_fault": {"label": "Repeat fault", "section": "service_risk", "cadence": "weekly", "owner": "dept_head"},
    "service.reopen_spike": {"label": "Reopen spike", "section": "service_risk", "cadence": "weekly", "owner": "gm"},
    "service.guest_unresolved": {"label": "Guest not resolved", "section": "service_risk", "cadence": "daily", "owner": "gm"},
    "service.understaffed": {"label": "Understaffed", "section": "service_risk", "cadence": "daily", "owner": "gm"},
    "service.recovery_over_authority": {"label": "Gesture needs approval", "section": "service_risk", "cadence": "daily", "owner": "gm"},
    "service.labour_flag": {"label": "Labour guardrail", "section": "compliance", "cadence": "weekly", "owner": "gm"},
    "service.l1_or_ack_breach": {"label": "Reached the GM", "section": "service_risk", "cadence": "daily", "owner": "gm"},
}

for _key, _k in KINDS.items():
    core_signals.register_signal_kind(_key, label=_k["label"], default_owner_role=_k["owner"],
                                      section=_k["section"], cadence=_k["cadence"], icon="service")


def raise_signal(cur: Any, hotel_slug: str, kind: str, dedupe_key: str, title: str, *, summary: str | None = None,
                 severity: int = 3, subject_type: str | None = None, subject_id: str | None = None,
                 due_in_min: int | None = None, evidence: list[dict] | None = None) -> dict | None:
    """Upsert one Service signal (keyed on dedupe_key). Never raises: a
    signal is advice, and losing one must not roll back the work that found
    it (callers inside a request transaction wrap it in state.guarded)."""
    k = KINDS[kind]
    s = core_signals.SignalIn(
        module="service", kind=kind, dedupe_key=dedupe_key, title=title[:200], summary=summary,
        section=k["section"], cadence=k["cadence"], severity=severity, confidence=0.9, owner_role=k["owner"],
        subject_type=subject_type, subject_id=subject_id,
        due_at=clock.now() + timedelta(minutes=due_in_min) if due_in_min else None,
        evidence=[core_signals.Evidence(**e) for e in evidence or []],
    )
    return core_signals.upsert_signal(cur, hotel_slug, s)


def _label(row: dict) -> str:
    return C.REQUEST_TYPES.get(row.get("request_type_key") or "", {}).get("label_en") or row.get("type") or "request"


# ─── SLA breach cluster (from the sweep) ───

BREACH_CLUSTER_N = 3  # breaches in one shift that make a cluster
SHIFT_WINDOW_H = 8  # "this shift": the last 8 hours (the shift templates' length)


def breach_cluster(cur: Any, hotel: Any, rows: list[dict], *, shift_key: str | None) -> int:
    """Any L1 past its ack time is its own signal; 3+ breaches since the
    shift began (the last 8 hours) are one signal per shift. -> raised."""
    raised = 0
    for row in rows:
        if row.get("severity") == "L1" and row.get("sla_ack_breached"):
            raise_signal(cur, hotel.slug, "service.sla_breach_cluster", f"service.sla_breach_cluster:l1:{row['id']}",
                         f"L1 not acknowledged in time: room {row.get('room') or '-'} ({_label(row)})",
                         severity=4, subject_type="staff_request", subject_id=str(row["id"]), due_in_min=15)
            raised += 1
    since = clock.now_ms() - SHIFT_WINDOW_H * 3600 * 1000
    cur.execute(
        """SELECT COUNT(*) AS n FROM staff_requests WHERE hotel_slug = %s AND intake_version IS NOT NULL
           AND "timestamp" >= %s AND (sla_ack_breached OR sla_resolve_breached)""",
        (hotel.slug, since),
    )
    n = int(cur.fetchone()["n"])
    if n >= BREACH_CLUSTER_N:
        bdate = htime.business_date(hotel)
        raise_signal(cur, hotel.slug, "service.sla_breach_cluster",
                     f"service.sla_breach_cluster:{bdate}:{shift_key or 'shift'}",
                     f"{n} requests missed their SLA this shift", severity=3, due_in_min=60,
                     evidence=[{"kind": "metric", "label": "Breaches in the last 8 hours", "value": n}])
        raised += 1
    return raised


# ─── Ladder level 3: the GM was paged (03 spec 1.4) ───


def l1_or_ack_breach(cur: Any, hotel: Any, row: dict, *, shadow: bool, recipients: int) -> dict | None:
    """One signal per request and ladder cycle, raised when the sweep (or
    intake's urgent paging) first crosses level 3. WHY per cycle: a reopen
    or reassignment restarts the ladder, and reaching the GM again is a new
    fact for the brief; a second sweeper crossing the same step is not
    (the svc_escalations unique key already stopped it before this call).

    Raised in shadow mode too: the brief is internal, and seeing what WOULD
    have reached the GM is how the thresholds get calibrated. The summary
    says so, and names the room and type only, never the guest's words."""
    urgent = row.get("severity") == "L1" or row.get("sensitivity") in ("emergency", "sensitive")
    why = "L1 or safety request" if urgent else "not acknowledged in time"
    cycle = int(row.get("ladder_cycle") or 0)
    note = "Shadow mode: nobody was messaged." if shadow else (
        "The GM was messaged." if recipients else "No GM could be reached (no phone on file).")
    return raise_signal(
        cur, hotel.slug, "service.l1_or_ack_breach", f"service.l1_or_ack_breach:{row['id']}:{cycle}",
        f"Reached the GM: room {row.get('room') or '-'} ({_label(row)}), {why}",
        summary=note, severity=4 if urgent else 3, subject_type="staff_request", subject_id=str(row["id"]),
        due_in_min=15 if urgent else 60,
        evidence=[{"kind": "metric", "label": "Ladder level", "value": 3},
                  {"kind": "metric", "label": "Ladder cycle", "value": cycle}],
    )


# ─── Finished requests leave the brief ───


def resolve_finished(cur: Any, hotel_slug: str) -> int:
    """Resolve the per-request signals whose work is over: "Reached the GM"
    once its request is completed or cancelled, and "L1 in room ..." once
    its incident is closed as well (the request ends when the guest is safe;
    the incident stays open until the duty manager files the report). Both
    are raised once and never re-detected, so without this a handled
    emergency stayed on the daily brief as overdue for good. -> resolved."""
    cur.execute(
        """
        SELECT s.dedupe_key
        FROM signals s
        JOIN staff_requests r ON r.hotel_slug = s.hotel_slug AND r.id::text = s.subject_id
        LEFT JOIN svc_incidents i ON i.hotel_slug = r.hotel_slug AND i.request_id = r.id AND i.status = 'open'
        WHERE s.hotel_slug = %s AND s.module = 'service' AND s.subject_type = 'staff_request'
          AND s.status IN ('open', 'surfaced', 'snoozed', 'decided') AND r.state = ANY(%s)
          AND (s.kind = 'service.l1_or_ack_breach' OR (s.kind = 'service.l1_incident' AND i.id IS NULL))
        """,
        (hotel_slug, list(C.TERMINAL_STATES)),
    )
    keys = [r["dedupe_key"] for r in cur.fetchall()]
    for key in keys:
        core_signals.resolve_signal(cur, hotel_slug, key, resolution="request_done")
    return len(keys)


# ─── Guest said "not resolved" ───


def guest_unresolved(cur: Any, hotel: Any, *, window_h: int = 2) -> int:
    """VIP guests, or anyone on a second reopen, who said "not resolved" in
    the last `window_h` hours (the hourly job looks back two, so one missed
    tick loses nothing). One signal per request. The request itself was
    already reopened and re-routed; this is the owner's brief item."""
    now_ms = clock.now_ms()
    cur.execute(
        """SELECT id, room, type, request_type_key, reopen_count, flags FROM staff_requests
           WHERE hotel_slug = %s AND "timestamp" >= %s AND guest_confirmation = 'no' AND guest_confirmed_at >= %s
             AND (reopen_count >= 2 OR COALESCE((flags->>'vip')::boolean, FALSE))""",
        (hotel.slug, now_ms - 30 * 86_400_000, now_ms - window_h * 3600 * 1000),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        why = "VIP guest" if (r.get("flags") or {}).get("vip") else f"reopened {r['reopen_count']} times"
        raise_signal(cur, hotel.slug, "service.guest_unresolved", f"service.guest_unresolved:{r['id']}",
                     f"Room {r.get('room') or '-'} says {_label(r)} is not resolved ({why})",
                     severity=3, subject_type="staff_request", subject_id=str(r["id"]), due_in_min=30)
    return len(rows)


# ─── Repeat faults (hourly) ───

FAULT_DEPARTMENTS = ("engineering",)


def repeat_faults(cur: Any, hotel: Any) -> int:
    """Same room and fault twice in 7 days, or the same room type and fault
    3 times in 14 days (a model-wide problem: a bad AC unit type). Signals
    whose pattern is gone are resolved, so a fixed room drops off the brief
    and a relapse reopens it."""
    now_ms = clock.now_ms()
    base = """FROM staff_requests WHERE hotel_slug = %s AND intake_version IS NOT NULL AND "timestamp" >= %s
              AND department_key = ANY(%s) AND request_type_key IS NOT NULL AND state <> 'cancelled'"""
    seen: list[str] = []
    cur.execute(
        f"""SELECT room, request_type_key, COUNT(*) AS n, MAX(id) AS last_id {base} AND room IS NOT NULL
            GROUP BY room, request_type_key HAVING COUNT(*) >= 2""",
        (hotel.slug, now_ms - 7 * 86_400_000, list(FAULT_DEPARTMENTS)),
    )
    for r in cur.fetchall():
        key = f"service.repeat_fault:room:{r['room']}:{r['request_type_key']}"
        seen.append(key)
        raise_signal(cur, hotel.slug, "service.repeat_fault", key,
                     f"Room {r['room']}: {_label(r)} reported {r['n']} times in 7 days",
                     severity=3, subject_type="room", subject_id=str(r["room"]),
                     evidence=[{"kind": "row", "label": "Latest request", "value": r["last_id"],
                                "ref": {"table": "staff_requests", "id": r["last_id"]}}])
    cur.execute(
        f"""SELECT room_type, request_type_key, COUNT(*) AS n {base} AND room_type IS NOT NULL
            GROUP BY room_type, request_type_key HAVING COUNT(*) >= 3""",
        (hotel.slug, now_ms - 14 * 86_400_000, list(FAULT_DEPARTMENTS)),
    )
    for r in cur.fetchall():
        key = f"service.repeat_fault:room_type:{r['room_type']}:{r['request_type_key']}"
        seen.append(key)
        raise_signal(cur, hotel.slug, "service.repeat_fault", key,
                     f"{r['room_type']} rooms: {_label(r)} reported {r['n']} times in 14 days",
                     severity=2, subject_type="room_type", subject_id=str(r["room_type"]))
    core_signals.resolve_missing(cur, hotel.slug, "service", "service.repeat_fault", seen)
    return len(seen)


# ─── Reopen spike (hourly) ───


def reopen_spike(cur: Any, hotel: Any) -> bool:
    """Today's reopens (hotel-local day) at least 3 and at least double the
    daily average of the 14 days before."""
    today = htime.local_date(hotel)
    t0, t1 = htime.local_day_bounds(hotel, today)
    b0, _ = htime.local_day_bounds(hotel, today - timedelta(days=14))
    cur.execute(
        """SELECT COUNT(*) FILTER (WHERE at >= %s AND at < %s) AS today,
                  COUNT(*) FILTER (WHERE at >= %s AND at < %s) AS before
           FROM svc_request_events WHERE hotel_slug = %s AND kind = 'transition' AND action = 'reopen'
             AND at >= %s AND at < %s""",
        (t0, t1, b0, t0, hotel.slug, b0, t1),
    )
    r = cur.fetchone()
    n_today, avg = int(r["today"]), int(r["before"]) / 14
    if n_today < 3 or n_today < 2 * avg:
        return False
    raise_signal(cur, hotel.slug, "service.reopen_spike", f"service.reopen_spike:{today}",
                 f"{n_today} requests reopened today (usually {avg:.1f} a day)", severity=3,
                 evidence=[{"kind": "metric", "label": "Reopens today", "value": n_today},
                           {"kind": "metric", "label": "Daily average, previous 14 days", "value": round(avg, 1)}])
    return True
