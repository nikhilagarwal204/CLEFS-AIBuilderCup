"""
Legacy service routes, moved mechanically out of the monolith main.py.

Paths, methods and JSON shapes are frozen by tests/foundation/golden (captured
from main.py@aaf5cf4 before the move). Changes made with the move:
  - every handler uses `with db() as cur` (commit/rollback/return guaranteed);
  - staff-analytics no longer opens an unclosed connection per staff member,
    and "today" is the hotel's local day (was UTC midnight / Asia/Kolkata);
  - SELECT * became explicit legacy column lists, so columns Service adds to
    staff_requests / staff_members (and the foundation's PIN columns) never
    change these responses or leak pin_hash;
  - access: members/devices, or anyone while the hotel has legacy_open_access;
    POST /pending (guest QR) stays public, rate-limited in the database;
  - an unknown priority hint no longer crashes with a 500 (treated as medium).

Wave 1 (Service lane): the request handlers are thin adapters over v2.
POST /requests and POST /pending file through intake.create_request (no
semantic merge, no translation, the /pending limiter and its wording kept);
PATCH maps the old statuses onto state.transition (legacy_client actor);
DELETE leaves a "deleted" event in the feed; GET gains ?since= and a 7-day
default window that always keeps open work. The golden tests stay green.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Optional

import psycopg2
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from clefs import db, events, ratelimit
from clefs.auth.deps import HotelCtx, require_legacy_or
from clefs.core import clock
from clefs.core import rooms as rooms_mod
from clefs.core import time as htime
from clefs.core.context import request_info
from clefs.core.hotels import find_hotel
from clefs.legacy import msg91
from clefs.legacy.seed_data import DEMO_STAFF
from clefs.modules.service import constants as C
from clefs.modules.service import intake, priority, routing, state

router = APIRouter(tags=["service-legacy"])

# ─── Wire constants ───
# The SSE event the staff dashboard listens for when the voice assistant files a
# request. Renamed during the Aria -> Clefs rebrand; the legacy name is still
# emitted alongside it so an older dashboard build keeps working.
ASSISTANT_REQUEST_EVENT = "new_clefs_request"
LEGACY_ASSISTANT_REQUEST_EVENT = "new_aria_request"
REQUESTS_CHANGED_EVENT = "requests_changed"

# Placeholder categories that mean "the assistant did not classify this" and so
# should be replaced by the department mapping. "Aria Request" is the pre-rebrand
# value and is still present on historical staff_requests rows.
LEGACY_ASSISTANT_CATEGORIES = {"Clefs Request", "Aria Request"}

REQUEST_COLUMNS = (
    "id, hotel_slug, room, type, subtype, description, category, priority, status, "
    "timestamp, assigned_to, accepted_at, resolved_at"
)
STAFF_COLUMNS = "id, hotel_slug, name, role, department, is_active, is_online, created_at, phone_number"

PENDING_LIMIT = 5  # per room per window (unchanged from the in-memory limiter)
PENDING_WINDOW_S = 10 * 60
# The room comes from the (public) body, so a per-room limit alone is no limit:
# rotating "1", "2", ... files unbounded requests and staff SMS. Two ceilings
# sit above it. Both are loose because a hotel's guests share one Wi-Fi NAT
# address, so a per-IP cap is effectively a per-hotel cap for real guests.
PENDING_HOTEL_LIMIT = 120  # every room of one hotel, per window
PENDING_IP_LIMIT = 60  # one source address across all hotels, per window
# Free text from the guest page is capped (truncated, so an old guest page that
# sends a long transcript still files the request).
PENDING_TEXT_MAX = 2000
PENDING_FIELD_MAX = 100
# Staff SMS (msg91) per assignee: a flood of requests must not become a flood
# of paid SMS carrying attacker text from a trusted sender.
SMS_PER_ASSIGNEE_LIMIT = 20
SMS_WINDOW_S = 60 * 60

# The department map and the priority heuristic live in priority.py (one
# copy); re-exported here because this module's handlers and old imports
# name them.
CATEGORY_DEPARTMENT = priority.CATEGORY_DEPARTMENT
calculate_priority = priority.calculate_priority


# ─── Pydantic bodies ───


class RequestBody(BaseModel):
    room: str
    type: str
    subtype: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = "General"
    priority: Optional[str] = None  # None = let backend calculate; "high"/"medium"/"low" = use as hint
    status: Optional[str] = "new"
    timestamp: int
    assigned_to: Optional[str] = None
    accepted_at: Optional[int] = None
    resolved_at: Optional[int] = None


class StatusPatch(BaseModel):
    status: str
    assigned_to: Optional[str] = None
    accepted_at: Optional[int] = None
    resolved_at: Optional[int] = None


class PendingBody(BaseModel):
    data: dict[str, Any]


class StaffMemberBody(BaseModel):
    name: str
    role: str
    department: str
    is_active: Optional[bool] = True
    is_online: Optional[bool] = False
    phone_number: Optional[str] = None


# ─── Assignee SMS ───
# Routing (routing.notify_assignee) calls _sms_assignee for every automatic
# assignment; the per-assignee cap below is pinned by a foundation security test.


def _lookup_staff_phone(cur: Any, hotel_slug: str, staff_name: str) -> Optional[str]:
    cur.execute(
        "SELECT phone_number FROM staff_members WHERE hotel_slug = %s AND name = %s AND is_active = TRUE",
        (hotel_slug, staff_name),
    )
    row = cur.fetchone()
    return row["phone_number"] if row else None


def _sms_assignee(cur: Any, hotel_slug: str, assignee: str, row: dict) -> None:
    if not msg91.sms_enabled():
        return
    if not ratelimit.hit(cur, f"sms:staff:{hotel_slug}:{assignee}", limit=SMS_PER_ASSIGNEE_LIMIT, window_s=SMS_WINDOW_S):
        return  # the request is still filed and shown on the dashboard
    phone = _lookup_staff_phone(cur, hotel_slug, assignee)
    if phone:
        msg91.send_sms_alert(
            phone_number=phone,
            room=row.get("room") or "-",
            task_type=row.get("type") or "Service Request",
            description=row.get("description") or "",
            priority=row.get("priority") or "medium",
        )


def insert_and_assign(
    cur: Any,
    hotel_slug: str,
    *,
    room: str,
    type: str,
    description: Optional[str],
    category: Optional[str],
    subtype: Optional[str] = None,
    priority_hint: Optional[str] = None,
    status: Optional[str] = "new",
    timestamp: Optional[int] = None,
    assigned_to: Optional[str] = None,
    accepted_at: Optional[int] = None,
    resolved_at: Optional[int] = None,
    source: str = "legacy",
    announce: bool = False,
    actor: Any = None,
) -> dict:
    """The legacy insert, now a thin adapter over the v2 intake pipeline:
    smart priority, v2 state and SLA, routing (auto-assigns only an
    unassigned request in status "new", as before) and the assignee SMS.

    Legacy doors keep legacy behaviour: no semantic merge (the old concierge
    dedupes in the browser, and five identical waters are five rows in the
    golden contract), no translation, and /pending keeps its own limiter
    with its golden wording. Returns the 13-column legacy row."""
    out = intake.create_request(
        cur,
        hotel_slug,
        intake.IntakeIn(
            description=description or "",
            source=source,
            room=room,
            type=type,
            category=category,
            subtype=subtype,
            priority_hint=priority_hint,
            timestamp=timestamp,
            allow_merge=False,
            rate_limit=False,
            translate=False,
            announce=announce,
            legacy_status=status,
            legacy_assigned_to=assigned_to,
            legacy_accepted_at=accepted_at,
            legacy_resolved_at=resolved_at,
        ),
        actor=actor,
    )
    return intake.legacy_view(out.row)


# ─── Staff requests ───


# The list defaults to the last 7 days (03 spec 1.7): a pilot hotel's full
# history is thousands of rows the dashboard filters away anyway. ?since=
# (epoch ms) asks for more, or less.
REQUESTS_DEFAULT_WINDOW_MS = 7 * 24 * 3600 * 1000


@router.get("/hotels/{slug}/requests")
def get_requests(
    slug: str, since: Optional[int] = None, ctx: HotelCtx = Depends(require_legacy_or("service.view"))
) -> list[dict]:
    floor_ms = since if since is not None and since >= 0 else clock.now_ms() - REQUESTS_DEFAULT_WINDOW_MS
    with db.db() as cur:
        cur.execute(
            f"""SELECT {REQUEST_COLUMNS} FROM staff_requests
                WHERE hotel_slug = %s AND (timestamp >= %s OR state NOT IN ('completed', 'cancelled'))
                ORDER BY timestamp DESC""",
            (slug, floor_ms),
        )
        return [dict(r) for r in cur.fetchall()]


@router.post("/hotels/{slug}/requests")
def create_request(slug: str, body: RequestBody, ctx: HotelCtx = Depends(require_legacy_or("service.work"))) -> dict:
    with db.db() as cur:
        row = insert_and_assign(
            cur,
            slug,
            room=body.room,
            type=body.type,
            subtype=body.subtype,
            description=body.description,
            category=body.category,
            priority_hint=body.priority,
            status=body.status,
            timestamp=body.timestamp,
            assigned_to=body.assigned_to,
            accepted_at=body.accepted_at,
            resolved_at=body.resolved_at,
            actor=state.Actor.from_ctx(ctx),
        )
    return row


def _legacy_patch(cur: Any, ctx: HotelCtx, slug: str, request_id: int, body: StatusPatch) -> Optional[dict]:
    """The old dashboard's status PATCH as state-machine actions (03 spec 1.2):
      completed            -> complete (actor legacy_client)
      progress + assignee  -> assign by name, then ack: the old "Accept" is a
                              person typing their own name, so it counts as
                              acknowledgment
      progress, no name    -> assign through routing when nobody holds it
      new                  -> unassign
    A closed request asked to go back to progress or new is reopened first.
    Unknown statuses change nothing (the old code wrote any string)."""
    row = state.load(cur, slug, request_id, lock=True)
    if row is None:
        return None
    actor = state.Actor.from_ctx(ctx)
    st = row.get("state") or "new"

    def act(action: str, who: Optional[state.Actor] = None, **kw: Any) -> None:
        nonlocal row, st
        row = state.transition(cur, slug, request_id, action, who or actor, legacy=True, **kw)["row"]
        st = row["state"]

    if body.status == "completed":
        if st not in C.TERMINAL_STATES:
            act("complete", resolved_at=body.resolved_at)
    elif body.status == "progress":
        if st in C.TERMINAL_STATES:
            act("reopen")
        name = (body.assigned_to or "").strip() or None
        if name:
            staff_id = _staff_id_by_name(cur, slug, name)
            same = (staff_id and staff_id == row.get("assigned_staff_id")) or (not staff_id and name == row.get("assigned_to"))
            if st in ("new", "reopened", "scheduled"):
                if st == "scheduled":
                    act("release")
                act("assign", target={"staff_id": staff_id, "name": name})
            elif not same:
                act("reassign", target={"staff_id": staff_id, "name": name})
            if st == "assigned":
                act("ack", who=dataclasses.replace(actor, staff_id=staff_id, label=name))
            if not same:
                routing.notify_assignee(cur, row)
        elif st in ("new", "reopened"):
            act("assign")
    elif body.status == "new":
        if st in C.TERMINAL_STATES:
            act("reopen")
        if st in ("assigned", "acknowledged", "in_progress"):
            act("unassign")
    # Explicit instants from the old client win over "now" (it sends its own).
    if body.accepted_at is not None or (body.resolved_at is not None and body.status != "completed"):
        sets, params = [], []
        if body.accepted_at is not None:
            sets.append("accepted_at = %s")
            params.append(body.accepted_at)
        if body.resolved_at is not None and body.status != "completed":
            sets.append("resolved_at = %s")
            params.append(body.resolved_at)
        cur.execute(f"UPDATE staff_requests SET {', '.join(sets)} WHERE id = %s", params + [request_id])
    return row


def _staff_id_by_name(cur: Any, slug: str, name: str) -> Optional[int]:
    cur.execute("SELECT id FROM staff_members WHERE hotel_slug = %s AND name = %s", (slug, name))
    r = cur.fetchone()
    return r["id"] if r else None


@router.patch("/hotels/{slug}/requests/{request_id}")
def update_request_status(
    slug: str, request_id: int, body: StatusPatch, ctx: HotelCtx = Depends(require_legacy_or("service.work"))
) -> dict:
    with db.db() as cur:
        _legacy_patch(cur, ctx, slug, request_id, body)
        events.publish(slug, REQUESTS_CHANGED_EVENT, {}, cur=cur)
    return {"ok": True}


@router.delete("/hotels/{slug}/requests/{request_id}")
def delete_request(slug: str, request_id: int, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))) -> dict:
    with db.db() as cur:
        cur.execute("DELETE FROM staff_requests WHERE id = %s AND hotel_slug = %s RETURNING *", (request_id, slug))
        gone = cur.fetchone()
        if gone is not None:
            # The feed keeps a "deleted" event so an offline device drops the card.
            state.record_event(cur, dict(gone), "deleted", state.Actor.from_ctx(ctx))
        events.publish(slug, REQUESTS_CHANGED_EVENT, {}, cur=cur)
    return {"ok": True}


# ─── Pending (guest-originated) requests ───


def _ip() -> str:
    return request_info().ip or "unknown"


@router.get("/hotels/{slug}/pending")
def get_pending(slug: str, ctx: HotelCtx = Depends(require_legacy_or("service.view"))) -> Any:
    with db.db() as cur:
        cur.execute("SELECT data FROM pending_requests WHERE hotel_slug = %s", (slug,))
        row = cur.fetchone()
    return row["data"] if row else None


def _text(value: Any, limit: int) -> Optional[str]:
    """A body field as bounded text (None stays None; numbers become strings)."""
    if value is None:
        return None
    return str(value)[:limit]


def _check_pending_room(cur: Any, slug: str, room: Any) -> Optional[str]:
    """The guest-supplied room, validated. A hotel with a room register accepts
    only its registered rooms (the same rule as /public/stay); an unregistered
    legacy hotel accepts any well-formed room label ("502", "Lobby", "unknown")."""
    if room is None or room == "":
        if rooms_mod.has_register(cur, slug):
            raise HTTPException(status_code=400, detail="room is required")
        return None
    room = str(room).strip()
    if not rooms_mod.valid_room(room):
        raise HTTPException(status_code=400, detail="Invalid room")
    if rooms_mod.has_register(cur, slug):
        known = rooms_mod.get_room(cur, slug, room)
        if known is None or not known["is_active"]:
            raise HTTPException(status_code=404, detail=f"Room '{room}' not found")
    return room


@router.post("/hotels/{slug}/pending")
def set_pending(slug: str, body: PendingBody) -> dict:
    """Public: the guest concierge files requests here from the QR page."""
    with db.db() as cur:
        if find_hotel(cur, slug) is None:
            raise HTTPException(status_code=404, detail=f"Hotel '{slug}' not found")
        room = _check_pending_room(cur, slug, body.data.get("room"))
        busy = "Too many requests. Please ask the front desk, or try again in a few minutes."
        if not ratelimit.hit(cur, f"pending:ip:{_ip()}", limit=PENDING_IP_LIMIT, window_s=PENDING_WINDOW_S):
            raise HTTPException(status_code=429, detail=busy)
        if not ratelimit.hit(cur, f"pending:hotel:{slug}", limit=PENDING_HOTEL_LIMIT, window_s=PENDING_WINDOW_S):
            raise HTTPException(status_code=429, detail=busy)
        if room and not ratelimit.hit(cur, f"pending:{slug}:{room}", limit=PENDING_LIMIT, window_s=PENDING_WINDOW_S):
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit: room {room} has submitted {PENDING_LIMIT} requests in the last 10 minutes.",
            )

    req_type = _text(body.data.get("type"), PENDING_FIELD_MAX) or "Service Request"
    category = _text(body.data.get("category"), PENDING_FIELD_MAX)
    if not category or category in LEGACY_ASSISTANT_CATEGORIES:
        category = CATEGORY_DEPARTMENT.get(req_type) or "General"
    timestamp = body.data.get("timestamp")
    if isinstance(timestamp, bool) or not isinstance(timestamp, int) or not 0 < timestamp < 2**53:
        timestamp = None  # epoch ms from Date.now(); anything else is "now"

    with db.db() as cur:
        row = insert_and_assign(
            cur,
            slug,
            room=room,
            type=req_type,
            subtype=_text(body.data.get("subtype"), PENDING_FIELD_MAX) or None,
            description=_text(body.data.get("description"), PENDING_TEXT_MAX) or "Request from Clefs AI",
            category=category,
            priority_hint=_text(body.data.get("priority"), 20),
            status="new",
            timestamp=timestamp or clock.now_ms(),
            source="web",  # the guest concierge on the room QR page
            announce=True,  # new_clefs_request + new_aria_request for the dashboard
        )
    return {"ok": True}


@router.delete("/hotels/{slug}/pending")
def clear_pending(slug: str, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))) -> dict:
    with db.db() as cur:
        cur.execute("DELETE FROM pending_requests WHERE hotel_slug = %s", (slug,))
    return {"ok": True}


# ─── Staff members ───


@router.get("/hotels/{slug}/staff/{staff_id}")
def get_staff_member(slug: str, staff_id: int, ctx: HotelCtx = Depends(require_legacy_or("service.view"))) -> dict:
    with db.db() as cur:
        cur.execute(f"SELECT {STAFF_COLUMNS} FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return dict(row)


@router.get("/hotels/{slug}/staff")
def get_staff(slug: str, ctx: HotelCtx = Depends(require_legacy_or("service.view"))) -> list[dict]:
    with db.db() as cur:
        cur.execute(f"SELECT {STAFF_COLUMNS} FROM staff_members WHERE hotel_slug = %s ORDER BY department, name", (slug,))
        return [dict(r) for r in cur.fetchall()]


@router.post("/hotels/{slug}/staff")
def create_staff(slug: str, body: StaffMemberBody, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))) -> dict:
    try:
        with db.db() as cur:
            cur.execute(
                f"""
                INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, phone_number)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING {STAFF_COLUMNS}
                """,
                (slug, body.name, body.role, body.department, body.is_active, body.is_online, body.phone_number),
            )
            row = cur.fetchone()
    except psycopg2.Error as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return dict(row)


@router.patch("/hotels/{slug}/staff/{staff_id}")
def update_staff(
    slug: str, staff_id: int, body: StaffMemberBody, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))
) -> dict:
    with db.db() as cur:
        cur.execute(
            f"""
            UPDATE staff_members
            SET name = %s, role = %s, department = %s, is_active = %s, is_online = %s, phone_number = %s
            WHERE id = %s AND hotel_slug = %s
            RETURNING {STAFF_COLUMNS}
            """,
            (body.name, body.role, body.department, body.is_active, body.is_online, body.phone_number, staff_id, slug),
        )
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Staff member not found")
    return dict(row)


@router.delete("/hotels/{slug}/staff/{staff_id}")
def delete_staff(slug: str, staff_id: int, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))) -> dict:
    with db.db() as cur:
        cur.execute("DELETE FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
    return {"ok": True}


@router.patch("/hotels/{slug}/staff/{staff_id}/status")
def toggle_staff_status(
    slug: str, staff_id: int, is_online: bool, ctx: HotelCtx = Depends(require_legacy_or("service.work"))
) -> dict:
    with db.db() as cur:
        cur.execute("SELECT name, is_online FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
        staff = cur.fetchone()
        if not staff:
            raise HTTPException(status_code=404, detail="Staff not found")
        if staff["is_online"] == is_online:
            return {"ok": True, "message": "Status unchanged"}

        cur.execute("UPDATE staff_members SET is_online = %s WHERE id = %s", (is_online, staff_id))
        now = clock.now()
        if is_online:
            cur.execute(
                "INSERT INTO staff_shifts (hotel_slug, staff_name, check_in) VALUES (%s, %s, %s)",
                (slug, staff["name"], now),
            )
        else:
            # Check-out: close the open shift
            cur.execute(
                """
                UPDATE staff_shifts
                SET check_out = %s,
                    duration_minutes = EXTRACT(EPOCH FROM (%s - check_in))/60
                WHERE hotel_slug = %s AND staff_name = %s AND check_out IS NULL
                """,
                (now, now, slug, staff["name"]),
            )
    return {"ok": True, "is_online": is_online}


@router.post("/hotels/{slug}/staff/seed")
def seed_staff(slug: str, ctx: HotelCtx = Depends(require_legacy_or("service.manage"))) -> list[dict]:
    inserted = []
    with db.db() as cur:
        for s in DEMO_STAFF:
            cur.execute(
                f"""
                INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online)
                VALUES (%s, %s, %s, %s, TRUE, TRUE)
                ON CONFLICT (hotel_slug, name) DO UPDATE SET
                    role = EXCLUDED.role,
                    department = EXCLUDED.department,
                    is_active = TRUE,
                    is_online = TRUE
                RETURNING {STAFF_COLUMNS}
                """,
                (slug, s["name"], s["role"], s["department"]),
            )
            inserted.append(dict(cur.fetchone()))
    return inserted


# ─── Staff analytics ───


@router.get("/hotels/{slug}/staff-analytics")
def get_staff_analytics(slug: str, ctx: HotelCtx = Depends(require_legacy_or("service.view"))) -> dict:
    hotel = ctx.hotel
    # "Today" is the hotel's local calendar day (was UTC midnight for requests
    # and a double-converted Asia/Kolkata date for shifts).
    day_start, day_end = htime.local_day_bounds(hotel, htime.local_date(hotel))
    today_start = htime.to_ms(day_start)

    with db.db() as cur:
        cur.execute(f"SELECT {STAFF_COLUMNS} FROM staff_members WHERE hotel_slug = %s ORDER BY department, name", (slug,))
        staff_members = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT id, room, type, subtype, category, priority, status,
                   timestamp, assigned_to, accepted_at, resolved_at
            FROM staff_requests
            WHERE hotel_slug = %s AND assigned_to IS NOT NULL
            ORDER BY timestamp DESC
            """,
            (slug,),
        )
        all_reqs = [dict(r) for r in cur.fetchall()]
        # Work minutes per staff member in one grouped query on the same cursor
        # (the old code opened, and leaked, a connection per staff member).
        cur.execute(
            """
            SELECT staff_name,
                   SUM(COALESCE(duration_minutes, 0)) FILTER (WHERE check_in >= %s AND check_in < %s) AS today_mins,
                   SUM(COALESCE(duration_minutes, 0)) AS total_mins
            FROM staff_shifts
            WHERE hotel_slug = %s
            GROUP BY staff_name
            """,
            (day_start, day_end, slug),
        )
        work = {r["staff_name"]: r for r in cur.fetchall()}

    def resolution_mins(r: dict) -> Optional[float]:
        if r.get("accepted_at") and r.get("resolved_at"):
            return (r["resolved_at"] - r["accepted_at"]) / 60_000
        return None

    completed_all = [r for r in all_reqs if r["status"] == "completed"]
    completed_today = [r for r in completed_all if (r.get("resolved_at") or 0) >= today_start]
    active_all = [r for r in all_reqs if r["status"] == "progress"]
    timed_all = [m for r in completed_all if (m := resolution_mins(r)) is not None]
    hotel_avg = round(sum(timed_all) / len(timed_all), 1) if timed_all else None

    hotel_kpis = {
        "total_completed": len(completed_all),
        "completed_today": len(completed_today),
        "currently_active": len(active_all),
        "avg_resolution_mins": hotel_avg,
    }

    staff_analytics = []
    for s in staff_members:
        name = s["name"]
        mine = [r for r in all_reqs if r["assigned_to"] == name]
        mine_done = [r for r in mine if r["status"] == "completed"]
        mine_today = [r for r in mine_done if (r.get("resolved_at") or 0) >= today_start]
        mine_active = [r for r in mine if r["status"] == "progress"]
        timed = [m for r in mine_done if (m := resolution_mins(r)) is not None]

        type_breakdown: dict[str, int] = {}
        for r in mine_done:
            type_breakdown[r["type"]] = type_breakdown.get(r["type"], 0) + 1

        w = work.get(name) or {}
        recent = sorted(mine, key=lambda r: r.get("resolved_at") or r.get("accepted_at") or r["timestamp"], reverse=True)[:30]
        activity_log = []
        for r in recent:
            rm = resolution_mins(r)
            activity_log.append(
                {
                    "id": r["id"],
                    "room": r["room"],
                    "type": r["type"],
                    "subtype": r.get("subtype"),
                    "category": r["category"],
                    "priority": r["priority"],
                    "status": r["status"],
                    "notified_at": r.get("accepted_at"),  # when staff was assigned/notified
                    "done_at": r.get("resolved_at"),
                    "resolution_mins": round(rm, 1) if rm else None,
                }
            )

        staff_analytics.append(
            {
                **s,
                "total_completed": len(mine_done),
                "completed_today": len(mine_today),
                "avg_resolution_mins": round(sum(timed) / len(timed), 1) if timed else None,
                "fastest_mins": round(min(timed), 1) if timed else None,
                "slowest_mins": round(max(timed), 1) if timed else None,
                "currently_active": len(mine_active),
                "today_work_mins": int(w.get("today_mins") or 0),
                "total_work_mins": int(w.get("total_mins") or 0),
                "task_type_breakdown": type_breakdown,
                "activity_log": activity_log,
            }
        )

    # Most completed today first
    staff_analytics.sort(key=lambda s: s["completed_today"], reverse=True)
    return {"hotel_kpis": hotel_kpis, "staff": staff_analytics}
