"""
Routing: who gets a request (03 spec 1.4 routing.assign).

  1. Sensitive or L1: the duty manager on shift, else a security device (or
     whoever is logged in on it), else the GM. No ladder: page now.
  2. Candidate pools, first non-empty wins:
       dept_floor   staff logged in on a device, in the department, whose
                    roster floors include the room's floor (zoned departments:
                    Housekeeping and Engineering)
       dept_device  the department on devices, any floor
       dept_shared  a shared department device nobody is logged in to (the
                    first person who acks on it claims the job)
       supervisor   the department's supervisor on the roster now
       fo_device    Front Office on devices
       dept_legacy  legacy is_online staff of the department (the bridge
                    until devices roll out; this is the old auto-assigner's pool)
       fo_online    Front Office online, then fo_any: Front Office at all
  3. Score = 2*open_weighted + sla_pressure - idle_bonus - language_bonus
     - continuity_bonus; the lowest wins. Ties keep the legacy fairness order
     (free first, never-worked first, longest idle, then the oldest member).
  4. Cap: people at max_open are skipped while anyone else is free; when
     everyone is at the cap the least loaded still gets it (flag over_cap).

pick() only chooses. assign() applies the choice through state.transition
(state imports this module lazily, for decline and handoff).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from clefs.core import clock, signals
from clefs.core import time as htime
from clefs.core.hotels import get_hotel
from clefs.modules.service import config, state
from clefs.modules.service import constants as C

log = logging.getLogger("clefs.service.routing")

OPEN_STATES = ("assigned", "acknowledged", "in_progress")
PRIORITY_WEIGHT = {"high": 2.0, "medium": 1.0, "low": 0.5}
IDLE_CAP_MIN = 30
LANGUAGE_BONUS = 3
CONTINUITY_BONUS = 2
PRESSURE_WINDOW_MIN = 5
# Pools past this point mean the department itself had nobody.
FALLBACK_POOLS = frozenset({"fo_device", "fo_online", "fo_any"})

# ─── Candidates ───


def _staff(cur: Any, hotel_slug: str) -> list[dict]:
    """Active staff with their live device session and current roster shift."""
    now = clock.now()
    cur.execute(
        """
        SELECT s.id, s.name, s.department, s.department_key, s.languages, s.max_open, s.role_key,
               s.is_online, s.floors AS staff_floors,
               (SELECT ds.device_id FROM device_sessions ds
                 WHERE ds.staff_member_id = s.id AND ds.hotel_slug = s.hotel_slug AND ds.ended_at IS NULL
                 ORDER BY ds.started_at DESC LIMIT 1) AS session_device_id,
               sa.floors AS shift_floors, sa.shift_role, sa.department_key AS shift_department
        FROM staff_members s
        LEFT JOIN LATERAL (
            SELECT floors, shift_role, department_key FROM svc_shift_assignments a
            WHERE a.staff_member_id = s.id AND a.hotel_slug = s.hotel_slug AND a.status = 'planned'
              AND a.start_at <= %s AND a.end_at > %s
            ORDER BY a.start_at DESC LIMIT 1
        ) sa ON TRUE
        WHERE s.hotel_slug = %s AND s.is_active
        ORDER BY s.id
        """,
        (now, now, hotel_slug),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["dept"] = d.get("shift_department") or d.get("department_key") or config.department_for(cur, hotel_slug, d.get("department"))
        d["floors"] = [str(f) for f in (d.get("shift_floors") or d.get("staff_floors") or [])]
        d["languages"] = [str(x).lower()[:2] for x in (d.get("languages") or [])]
        out.append(d)
    return out


def _devices(cur: Any, hotel_slug: str) -> list[dict]:
    """Paired shared devices and whether anyone is logged in on them."""
    cur.execute(
        """
        SELECT d.id, d.name, d.kind, d.floors, d.departments,
               EXISTS (SELECT 1 FROM device_sessions ds WHERE ds.device_id = d.id AND ds.ended_at IS NULL) AS occupied
        FROM devices d
        WHERE d.hotel_slug = %s AND d.revoked_at IS NULL AND d.kind IN ('floor', 'department', 'desk')
        ORDER BY d.id
        """,
        (hotel_slug,),
    )
    out = []
    for r in cur.fetchall():
        d = dict(r)
        d["dept_keys"] = {k for k in (config.department_for(cur, hotel_slug, x) for x in d.get("departments") or []) if k}
        d["floors"] = [str(f) for f in d.get("floors") or []]
        out.append(d)
    return out


# ─── Scoring ───


def _stats(cur: Any, hotel_slug: str, row: dict, people: list[dict]) -> dict[int, dict]:
    """Per staff id: open_weighted, open_n, pressure, last_resolved,
    continuity. Legacy rows carry only assigned_to (a name), so both keys
    are grouped and merged."""
    if not people:
        return {}
    now_ms = clock.now_ms()
    soon = now_ms + PRESSURE_WINDOW_MIN * 60_000
    ids = [p["id"] for p in people]
    names = [p["name"] for p in people]
    cur.execute(
        """
        SELECT assigned_staff_id, assigned_to,
               COALESCE(SUM(CASE priority WHEN 'high' THEN 2 WHEN 'low' THEN 0.5 ELSE 1 END)
                        FILTER (WHERE state = ANY(%(open)s)), 0) AS open_weighted,
               COUNT(*) FILTER (WHERE state = ANY(%(open)s)) AS open_n,
               COUNT(*) FILTER (WHERE state = ANY(%(open)s)
                                AND LEAST(COALESCE(ack_due_at, %(far)s), COALESCE(resolve_due_at, %(far)s)) <= %(soon)s) AS pressure,
               MAX(resolved_at) FILTER (WHERE status = 'completed') AS last_resolved,
               BOOL_OR(state = ANY(%(open)s) AND (room = %(room)s OR (%(floor)s IS NOT NULL AND floor = %(floor)s))) AS continuity
        FROM staff_requests
        WHERE hotel_slug = %(slug)s AND id <> %(rid)s
          AND (assigned_staff_id = ANY(%(ids)s) OR (assigned_staff_id IS NULL AND assigned_to = ANY(%(names)s)))
        GROUP BY assigned_staff_id, assigned_to
        """,
        {"open": list(OPEN_STATES), "far": 2**62, "soon": soon, "room": row.get("room"), "floor": row.get("floor"),
         "slug": hotel_slug, "rid": row.get("id") or 0, "ids": ids, "names": names},
    )
    by_name = {p["name"]: p["id"] for p in people}
    out: dict[int, dict] = {}
    for r in cur.fetchall():
        sid = r["assigned_staff_id"] or by_name.get(r["assigned_to"])
        if sid is None:
            continue
        s = out.setdefault(sid, {"open_weighted": 0.0, "open_n": 0, "pressure": 0, "last_resolved": None, "continuity": False})
        s["open_weighted"] += float(r["open_weighted"] or 0)
        s["open_n"] += int(r["open_n"] or 0)
        s["pressure"] += int(r["pressure"] or 0)
        if r["last_resolved"] is not None:
            s["last_resolved"] = max(s["last_resolved"] or 0, int(r["last_resolved"]))
        s["continuity"] = s["continuity"] or bool(r["continuity"])
    return out


def score(person: dict, stats: dict | None, row: dict, now_ms: int) -> tuple:
    """Sort key: (score, busy, worked, last_resolved, id); lowest wins."""
    s = stats or {}
    last = s.get("last_resolved")
    idle = IDLE_CAP_MIN if last is None else max(0, min(IDLE_CAP_MIN, (now_ms - int(last)) / 60_000))
    lang = (row.get("language") or "").lower()[:2]
    total = (
        2 * float(s.get("open_weighted") or 0)
        + int(s.get("pressure") or 0)
        - idle
        - (LANGUAGE_BONUS if lang and lang != "en" and lang in person.get("languages", []) else 0)
        - (CONTINUITY_BONUS if s.get("continuity") else 0)
    )
    return (round(total, 3), 1 if s.get("open_n") else 0, 0 if last is None else 1, last or 0, person["id"])


def _best(cur: Any, hotel_slug: str, row: dict, people: list[dict]) -> tuple[dict, dict, bool] | None:
    """(person, their stats, over_cap) for the lowest score. People at their
    max_open are passed over while anyone else can take it."""
    if not people:
        return None
    stats = _stats(cur, hotel_slug, row, people)
    now_ms = clock.now_ms()
    under = [p for p in people if stats.get(p["id"], {}).get("open_n", 0) < int(p.get("max_open") or 4)]
    over_cap = not under
    pool = under or sorted(people, key=lambda p: stats.get(p["id"], {}).get("open_weighted", 0))[:1]
    best = min(pool, key=lambda p: score(p, stats.get(p["id"]), row, now_ms))
    return best, stats.get(best["id"], {}), over_cap


# ─── pick() ───


def _result(pool: str, person: dict | None = None, *, device: dict | None = None, stats: dict | None = None,
            over_cap: bool = False) -> dict:
    return {
        "pool": pool,
        "staff_id": person["id"] if person else None,
        "name": person["name"] if person else None,
        "device_id": (device or {}).get("id") if device else (person or {}).get("session_device_id"),
        "over_cap": over_cap,
        "open_n": (stats or {}).get("open_n", 0),
    }


def _on_floor(person: dict, floor: str | None) -> bool:
    return bool(floor) and floor in person["floors"]


def _urgent(cur: Any, hotel_slug: str, row: dict, staff: list[dict], exclude: set[int]) -> dict | None:
    """Sensitive or L1: duty manager on shift, else Security (a person on a
    security device, else the device), else the GM."""
    dms = [p for p in staff if p.get("shift_role") == "duty_manager" and p["id"] not in exclude]
    best = _best(cur, hotel_slug, row, dms)
    if best:
        return _result("duty_manager", best[0], stats=best[1])
    sec_people = [p for p in staff if p["dept"] == "security" and p.get("session_device_id") and p["id"] not in exclude]
    best = _best(cur, hotel_slug, row, sec_people)
    if best:
        return _result("security", best[0], stats=best[1])
    for d in _devices(cur, hotel_slug):
        if "security" in d["dept_keys"]:
            return _result("security_device", device=d)
    gms = [p for p in staff if (p.get("role_key") or "") == "gm" and p["id"] not in exclude]
    best = _best(cur, hotel_slug, row, gms)
    if best:
        return _result("gm", best[0], stats=best[1])
    return None


def pick(cur: Any, hotel: Any, row: dict, *, exclude_staff: list[int] | tuple[int, ...] = ()) -> dict | None:
    """{pool, staff_id, name, device_id, over_cap, open_n} or None when the
    hotel has nobody at all. `row` needs department_key, floor, room,
    language, severity and sensitivity (a staff_requests row or the intake
    draft)."""
    slug = hotel.slug
    exclude = {int(x) for x in exclude_staff if x is not None}
    staff = _staff(cur, slug)
    dept = row.get("department_key") or "front_office"
    floor = row.get("floor")
    if row.get("severity") == "L1" or row.get("sensitivity") in ("emergency", "sensitive"):
        hit = _urgent(cur, slug, row, staff, exclude)
        if hit:
            return hit
    avail = [p for p in staff if p["id"] not in exclude]
    in_dept = [p for p in avail if p["dept"] == dept]
    on_device = [p for p in in_dept if p.get("session_device_id")]
    pools: list[tuple[str, list[dict]]] = []
    if dept in C.ZONED_DEPARTMENTS and floor:
        pools.append(("dept_floor", [p for p in on_device if _on_floor(p, floor)]))
    pools.append(("dept_device", on_device))
    for name, people in pools:
        best = _best(cur, slug, row, people)
        if best:
            return _result(name, best[0], stats=best[1], over_cap=best[2])
    shared = _shared_device(cur, slug, dept, floor)
    if shared:
        return _result("dept_shared", device=shared)
    later: list[tuple[str, list[dict]]] = [
        ("supervisor", [p for p in in_dept if p.get("shift_role") == "supervisor"]),
        # Before Front Office (the spec lists it after): while devices roll
        # out one department at a time, an online member of the department is
        # a better holder than the desk. A PIN shift also sets is_online.
        ("dept_legacy", [p for p in in_dept if p.get("is_online")]),
        ("fo_device", [p for p in avail if p["dept"] == "front_office" and p.get("session_device_id")]),
        ("fo_online", [p for p in avail if p["dept"] == "front_office" and p.get("is_online")]),
        ("fo_any", [p for p in avail if p["dept"] == "front_office"]),
    ]
    for name, people in later:
        best = _best(cur, slug, row, people)
        if best:
            return _result(name, best[0], stats=best[1], over_cap=best[2])
    return None


def _shared_device(cur: Any, hotel_slug: str, dept: str, floor: str | None) -> dict | None:
    """A department (or floor) device nobody is logged in to: the job waits
    on the device and the first person who acks there claims it."""
    for d in _devices(cur, hotel_slug):
        if d["occupied"] or dept not in d["dept_keys"]:
            continue
        if dept in C.ZONED_DEPARTMENTS and d["floors"] and floor and floor not in d["floors"]:
            continue
        return d
    return None


# ─── assign() ───


def understaffed(cur: Any, hotel: Any, row: dict, choice: dict | None) -> None:
    """service.understaffed when the department had nobody (the request fell
    to Front Office, or to nobody) or everyone was at their cap. One signal
    per department per business day (the dedupe key)."""
    dept = row.get("department_key") or "front_office"
    fell_back = choice is None or (choice["pool"] in FALLBACK_POOLS and dept != "front_office")
    if not fell_back and not (choice or {}).get("over_cap"):
        return
    label = config.department(cur, hotel.slug, dept).get("name") or dept
    reason = "nobody on shift" if fell_back else "everyone at their limit"

    def _raise() -> None:
        signals.upsert_signal(
            cur,
            hotel.slug,
            signals.SignalIn(
                module="service",
                kind="service.understaffed",
                dedupe_key=f"service.understaffed:{dept}:{htime.business_date(hotel)}",
                title=f"{label}: {reason}",
                summary=f"Room {row.get('room') or '-'} went to {(choice or {}).get('pool') or 'nobody'}.",
                section="service_risk",
                cadence="daily",
                severity=3,
                confidence=0.9,
                owner_role="gm",
                subject_type="department",
                subject_id=dept,
                due_at=clock.now() + timedelta(minutes=30),
            ),
        )

    state.guarded(cur, "understaffed_signal", _raise)  # a signal never blocks filing


def assign(cur: Any, hotel_slug: str, request_id: int, *, actor: Any = None, exclude_staff: tuple[int, ...] = ()) -> dict | None:
    """Pick a holder for an unassigned request and apply it (state
    "assign"). -> the transition result, or None when nobody exists (the
    request stays new and the understaffed signal is raised)."""
    hotel = get_hotel(cur, hotel_slug)
    row = state.load(cur, hotel_slug, request_id)
    if row is None:
        return None
    choice = pick(cur, hotel, row, exclude_staff=exclude_staff)
    understaffed(cur, hotel, row, choice)
    if choice is None:
        return None
    out = state.transition(cur, hotel_slug, request_id, "assign", actor or state.SYSTEM,
                           target={"staff_id": choice["staff_id"], "device_id": choice["device_id"]}, publish=False,
                           extra={"pool": choice["pool"], "over_cap": choice["over_cap"]})
    out["choice"] = choice
    notify_assignee(cur, out["row"])
    return out


# ─── Assignee alerts ───


def notify_assignee(cur: Any, row: dict) -> bool:
    """SMS the new holder, as the legacy auto-assigner did, unless they are
    logged in on a device (the device chimes; a second alert is noise). The
    sender and its per-assignee cap (20 an hour, so a flood of attacker-
    chosen rooms never becomes an SMS bill) stay in legacy.py, the one SMS
    path the foundation's security test pins."""
    from clefs.modules.service import legacy  # legacy imports this module

    staff_id, name = row.get("assigned_staff_id"), row.get("assigned_to")
    if not name:
        return False
    if staff_id:
        cur.execute("SELECT 1 FROM device_sessions WHERE staff_member_id = %s AND ended_at IS NULL LIMIT 1", (staff_id,))
        if cur.fetchone():
            return False
    legacy._sms_assignee(cur, row["hotel_slug"], name, row)
    return True
