"""
Service-risk producer (02 spec 6.1): per-request SLA breaches read from the
`service_requests_v1` contract view (never Service's tables).

    brief.sla_breach   a request still new (unacknowledged) after 10 minutes,
                       or in progress longer than its department's SLA
                       (Housekeeping 30, Maintenance 60, F&B 45 minutes;
                       other departments 60).

The spec names the kind `ops.sla_breach`; `ops.` is Service's key prefix
(registry.KEY_PREFIXES), so Brief registers it under its own `brief.`.
Service itself raises the cluster view (`service.sla_breach_cluster`); this
producer is the per-request line the owner can act on.

Cadence (Canon 18): daily only for high-priority requests (the service L1
case); anything else is a weekly item, which in practice resolves first.
Titles name the room and the department, never the guest's words.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core import signals as core_signals
from clefs.core.pii import canonical_department

KIND = "brief.sla_breach"
NEW_LIMIT = timedelta(minutes=10)
DEPARTMENT_SLA_MIN = {"housekeeping": 30, "maintenance": 60, "fnb": 45}
DEFAULT_SLA_MIN = 60
LOOKBACK = timedelta(hours=48)  # older open rows are abandoned legacy data, not live risk
DONE = ("completed", "cancelled", "resolved", "done", "closed")
URGENT_PRIORITIES = ("urgent", "critical", "emergency")

core_signals.register_signal_kind(
    KIND, label="Request past its SLA", default_owner_role="gm", section="service_risk", cadence="daily",
    icon="service",
)


# ─── Detection ───


def sla_minutes(department: str | None) -> int:
    return DEPARTMENT_SLA_MIN.get(canonical_department(department or ""), DEFAULT_SLA_MIN)


def _severity(priority: str | None) -> int:
    p = (priority or "").strip().lower()
    if p in URGENT_PRIORITIES:
        return 4
    if p == "high":
        return 3
    return 2


def breaches(rows: list[dict], *, now: datetime) -> list[dict]:
    """Pure: which open requests breach, and how. Each result carries the
    request row plus phase ("new" | "progress"), overdue minutes and due_at."""
    out = []
    for r in rows:
        status = (r.get("status") or "").strip().lower()
        if status in DONE or r.get("resolved_at") is not None or r.get("created_at") is None:
            continue
        if status == "new" and r.get("acknowledged_at") is None:
            due = r["created_at"] + NEW_LIMIT
            phase = "new"
        else:
            started = r.get("acknowledged_at") or r["created_at"]
            due = started + timedelta(minutes=sla_minutes(r.get("department")))
            phase = "progress"
        if now <= due:
            continue
        out.append({**r, "phase": phase, "due_at": due, "late_min": int((now - due).total_seconds() // 60)})
    return out


def _signal(hotel_slug: str, b: dict) -> core_signals.SignalIn:
    dept = (b.get("department") or "Service").strip() or "Service"
    room = b.get("room") or "?"
    if b["phase"] == "new":
        waited = int(NEW_LIMIT.total_seconds() // 60) + b["late_min"]
        title = f"Room {room}: {dept} request unacknowledged for {waited} min"
    else:
        title = f"Room {room}: {dept} request {b['late_min']} min past its {sla_minutes(dept)} min SLA"
    sev = _severity(b.get("priority"))
    return core_signals.SignalIn(
        module="brief", kind=KIND, dedupe_key=f"brief:sla:{b['id']}:{b['phase']}", title=title[:300],
        severity=sev, cadence="daily" if sev >= 3 else "weekly", confidence=0.95, impact_kind="service",
        owner_role="dept_head" if b["phase"] == "progress" else "gm", due_at=b["due_at"],
        subject_type="service_request", subject_id=str(b["id"]),
        evidence=[core_signals.Evidence(kind="row", label="Service request", ref={"table": "service_requests_v1", "id": b["id"]})],
        recommended_action=core_signals.RecommendedAction(
            type="core.task", label=f"Follow up on room {room}", params={"request_id": b["id"]}, due_in_hours=1,
        ),
    )


def run(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    """Upsert a signal per breaching request and resolve the ones that no
    longer breach (acknowledged, completed, or out of the lookback)."""
    now = now or clock.now()
    cur.execute(
        """
        SELECT id, room, department, priority, status, request_type, created_at, acknowledged_at, resolved_at
        FROM service_requests_v1
        WHERE hotel_slug = %s AND resolved_at IS NULL AND created_at >= %s
        ORDER BY id LIMIT 1000
        """,
        (hotel_slug, now - LOOKBACK),
    )
    found = breaches([dict(r) for r in cur.fetchall()], now=now)
    seen = []
    for b in found:
        s = _signal(hotel_slug, b)
        core_signals.upsert_signal(cur, hotel_slug, s)
        seen.append(s.dedupe_key)
    closed = core_signals.resolve_missing(cur, hotel_slug, "brief", KIND, seen)
    return {"breaches": len(found), "resolved": closed}


@jobs.scheduled("brief.service_risk", jobs.every(minutes=5))
def _job(ctx: jobs.JobCtx) -> dict:
    return run(ctx.cur, ctx.hotel_slug, now=ctx.now)
