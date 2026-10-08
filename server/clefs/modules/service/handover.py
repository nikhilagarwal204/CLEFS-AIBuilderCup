"""
Shift handover (03 spec 1.6), computed on read: what the incoming person
must know before their queue opens. Nothing here is a snapshot except the
acknowledgement (svc_handover_acks), which stores the counts and ids the
person saw, never guest words, so a later "nobody told me" has an answer.

Sections, all limited to the viewer's scope (a device's floors and
departments, or a console user's departments):
- open requests and the overdue ones (ack or resolve due passed);
- rooms out of order or out of service (hotel_rooms.service_state);
- open Engineering jobs;
- returning and VIP arrivals today (guests.api.returning_arrivals, masked
  for the viewer's role);
- incidents opened in the last 24 hours;
- lost-and-found items stored in the last 72 hours;
- free-text notes: svc_handover_notes from the last 24 hours plus the
  handover_note a person left when ending a PIN shift (device_sessions).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.guests import api as guests_api
from clefs.modules.service import constants as C

NOTES_WINDOW = timedelta(hours=24)
INCIDENT_WINDOW = timedelta(hours=24)
LOST_FOUND_WINDOW = timedelta(hours=72)
LIST_CAP = 50

# ─── Scope ───


def _scope_sql(alias: str, floors: list[str], departments: list[str]) -> tuple[str, list[Any]]:
    """Rows in scope: on one of the floors (or with no floor) and in one of
    the departments (or with none). Empty lists mean the whole hotel."""
    sql, params = "", []
    if floors:
        sql += f" AND ({alias}.floor IS NULL OR {alias}.floor = ANY(%s))"
        params.append(floors)
    if departments:
        sql += f" AND ({alias}.department_key IS NULL OR {alias}.department_key = ANY(%s))"
        params.append(departments)
    return sql, params


def _req_out(r: dict, now_ms: int) -> dict:
    ack_overdue = r["state"] in C.UNACKED_STATES and r.get("ack_due_at") is not None and r["ack_due_at"] < now_ms
    res_overdue = r.get("resolve_due_at") is not None and r["resolve_due_at"] < now_ms
    return {"id": r["id"], "room": r["room"], "type": r["type"], "request_type_key": r.get("request_type_key"),
            "state": r["state"], "severity": r.get("severity"), "floor": r.get("floor"),
            "department_key": r.get("department_key"), "assigned_to": r.get("assigned_to"),
            "sensitivity": r.get("sensitivity"), "created_at_ms": r["timestamp"],
            "overdue": bool(ack_overdue or res_overdue), "ack_overdue": bool(ack_overdue),
            "resolve_overdue": bool(res_overdue)}


# ─── Sections ───


def _requests(cur: Any, hotel_slug: str, floors: list[str], departments: list[str]) -> list[dict]:
    scope, params = _scope_sql("r", floors, departments)
    cur.execute(
        f"""SELECT * FROM staff_requests r WHERE r.hotel_slug = %s AND r.state = ANY(%s) {scope}
            ORDER BY COALESCE(r.severity, 'L3'), COALESCE(r.ack_due_at, r."timestamp") LIMIT 200""",
        [hotel_slug, list(C.OPEN_STATES), *params],
    )
    now_ms = clock.now_ms()
    return [_req_out(dict(r), now_ms) for r in cur.fetchall()]


def _rooms_out_of_service(cur: Any, hotel_slug: str, floors: list[str]) -> list[dict]:
    sql = """SELECT room_number, floor, service_state, ooo_reason FROM hotel_rooms
             WHERE hotel_slug = %s AND service_state IN ('ooo', 'oos') AND is_active"""
    params: list[Any] = [hotel_slug]
    if floors:
        sql += " AND floor = ANY(%s)"
        params.append(floors)
    cur.execute(sql + " ORDER BY room_number LIMIT %s", [*params, LIST_CAP])
    return [dict(r) for r in cur.fetchall()]


def _incidents(cur: Any, hotel_slug: str, floors: list[str], since: datetime) -> list[dict]:
    sql = """SELECT id, kind, level, severity, room, floor, status, opened_at, closed_at FROM svc_incidents
             WHERE hotel_slug = %s AND (opened_at >= %s OR status = 'open')"""
    params: list[Any] = [hotel_slug, since]
    if floors:
        sql += " AND (floor IS NULL OR floor = ANY(%s))"
        params.append(floors)
    cur.execute(sql + " ORDER BY opened_at DESC LIMIT %s", [*params, LIST_CAP])
    return [{**dict(r), "opened_at": r["opened_at"].isoformat(),
             "closed_at": r["closed_at"].isoformat() if r["closed_at"] else None} for r in cur.fetchall()]


def _lost_found(cur: Any, hotel_slug: str, since: datetime) -> list[dict]:
    cur.execute(
        """SELECT id, found_at, location, room, description, status FROM svc_lost_found
           WHERE hotel_slug = %s AND status = 'stored' AND found_at >= %s ORDER BY found_at DESC LIMIT %s""",
        (hotel_slug, since, LIST_CAP),
    )
    return [{**dict(r), "found_at": r["found_at"].isoformat()} for r in cur.fetchall()]


# ─── Notes ───


def note_out(r: dict) -> dict:
    return {"id": r["id"], "source": "note", "department_key": r.get("department_key"), "floor": r.get("floor"),
            "author": r.get("author_label"), "author_staff_id": r.get("author_staff_id"), "body": r["body"],
            "created_at": r["created_at"].isoformat(), "acks": r.get("acks") or []}


def add_note(cur: Any, hotel_slug: str, body: str, *, actor: Any, author_label: str | None, staff_id: int | None = None,
             user_id: int | None = None, department_key: str | None = None, floor: str | None = None,
             shift_assignment_id: int | None = None) -> dict:
    body = (body or "").strip()
    if not body:
        raise ClefsError(400, "A handover note needs text", "bad_request")
    cur.execute(
        """INSERT INTO svc_handover_notes (hotel_slug, department_key, floor, shift_assignment_id, author_staff_id,
               author_user_id, author_label, body, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (hotel_slug, department_key, floor, shift_assignment_id, staff_id, user_id, author_label, body[:4000], clock.now()),
    )
    row = dict(cur.fetchone())
    audit(cur, actor, "service.handover_note.create", "svc_handover_notes", str(row["id"]),
          after={"department_key": department_key, "floor": floor, "length": len(body)}, hotel_slug=hotel_slug)
    return note_out(row)


def _notes(cur: Any, hotel_slug: str, floors: list[str], departments: list[str], since: datetime) -> list[dict]:
    scope, params = _scope_sql("n", floors, departments)
    cur.execute(
        f"""SELECT * FROM svc_handover_notes n WHERE n.hotel_slug = %s AND n.created_at >= %s {scope}
            ORDER BY n.created_at DESC LIMIT %s""",
        [hotel_slug, since, *params, LIST_CAP],
    )
    out = [note_out(dict(r)) for r in cur.fetchall()]
    # The note typed when ending a PIN shift lives on the foundation's
    # device_sessions row; a device only sees its own and its scope's.
    cur.execute(
        """SELECT ds.id, ds.handover_note, ds.ended_at, s.name, ds.staff_member_id, d.floors, d.departments
           FROM device_sessions ds JOIN staff_members s ON s.id = ds.staff_member_id
           JOIN devices d ON d.id = ds.device_id
           WHERE ds.hotel_slug = %s AND ds.ended_at >= %s AND COALESCE(ds.handover_note, '') <> ''
           ORDER BY ds.ended_at DESC LIMIT %s""",
        (hotel_slug, since, LIST_CAP),
    )
    for r in cur.fetchall():
        dev_floors = list(r.get("floors") or [])
        if floors and dev_floors and not set(dev_floors) & set(floors):
            continue
        out.append({"id": None, "session_id": r["id"], "source": "shift_end", "department_key": None, "floor": None,
                    "author": r["name"], "author_staff_id": r["staff_member_id"], "body": r["handover_note"],
                    "created_at": r["ended_at"].isoformat(), "acks": []})
    return sorted(out, key=lambda n: n["created_at"], reverse=True)


# ─── Compute and acknowledge ───


def compute(cur: Any, hotel: Any, *, floors: list[str] | tuple[str, ...] = (),
            departments: list[str] | tuple[str, ...] = (), role: str | None = None) -> dict:
    """The handover sheet for a scope (see the module docstring)."""
    floors, departments = [str(f) for f in floors], [str(d) for d in departments]
    now = clock.now()
    reqs = _requests(cur, hotel.slug, floors, departments)
    eng = [r for r in reqs if r["department_key"] == "engineering"]
    # Guest context is a courtesy: a fault there (or the guests module being
    # off) must not break the sheet, so it runs in its own savepoint.
    cur.execute("SAVEPOINT svc_handover_arrivals")
    try:
        arrivals = guests_api.returning_arrivals(cur, hotel.slug, day=htime.local_date(hotel), role=role,
                                                 departments=departments)
        cur.execute("RELEASE SAVEPOINT svc_handover_arrivals")
    except Exception:  # noqa: BLE001
        cur.execute("ROLLBACK TO SAVEPOINT svc_handover_arrivals")
        arrivals = []
    sheet = {
        "generated_at": now.isoformat(),
        "scope": {"floors": floors, "departments": departments},
        "open_requests": reqs,
        "overdue_requests": [r for r in reqs if r["overdue"]],
        "rooms_out_of_service": _rooms_out_of_service(cur, hotel.slug, floors),
        "engineering_open": eng,
        "arrivals": arrivals,
        "vip_arrivals": [a for a in arrivals if a.get("vip")],
        "incidents": _incidents(cur, hotel.slug, floors, now - INCIDENT_WINDOW),
        "lost_found": _lost_found(cur, hotel.slug, now - LOST_FOUND_WINDOW),
        "notes": _notes(cur, hotel.slug, floors, departments, now - NOTES_WINDOW),
    }
    sheet["counts"] = summary(sheet)
    return sheet


def summary(sheet: dict) -> dict:
    """Counts only: what the acknowledgement stores."""
    return {k: len(sheet[k]) for k in ("open_requests", "overdue_requests", "rooms_out_of_service", "engineering_open",
                                       "arrivals", "vip_arrivals", "incidents", "lost_found", "notes")}


def acknowledge(cur: Any, hotel: Any, sheet: dict, *, actor: Any, staff_id: int | None, device_id: int | None,
                session_id: int | None, user_id: int | None) -> dict:
    """Record that the incoming person saw this sheet. Notes get the ack in
    their `acks` list; the PIN session gets handover_ack_by/at."""
    note_ids = [n["id"] for n in sheet["notes"] if n.get("id")]
    ids = {"requests": [r["id"] for r in sheet["open_requests"]][:200],
           "incidents": [i["id"] for i in sheet["incidents"]], "lost_found": [x["id"] for x in sheet["lost_found"]]}
    cur.execute(
        """INSERT INTO svc_handover_acks (hotel_slug, device_id, device_session_id, staff_member_id, user_id, note_ids,
               summary, acked_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, acked_at""",
        (hotel.slug, device_id, session_id, staff_id, user_id, note_ids, Json({"counts": sheet["counts"], "ids": ids}),
         clock.now()),
    )
    row = cur.fetchone()
    stamp = {"staff_id": staff_id, "user_id": user_id, "at": row["acked_at"].isoformat()}
    if note_ids:
        cur.execute("UPDATE svc_handover_notes SET acks = acks || %s::jsonb WHERE id = ANY(%s)",
                    (Json([stamp]), note_ids))
    if session_id and staff_id:
        cur.execute(
            """UPDATE device_sessions SET handover_ack_by = %s, handover_ack_at = %s
               WHERE id = %s AND hotel_slug = %s AND handover_ack_at IS NULL""",
            (staff_id, row["acked_at"], session_id, hotel.slug),
        )
    audit(cur, actor, "service.handover.ack", "svc_handover_acks", str(row["id"]),
          after={"counts": sheet["counts"], "staff_id": staff_id, "session_id": session_id}, hotel_slug=hotel.slug)
    return {"id": row["id"], "acked_at": row["acked_at"].isoformat(), "counts": sheet["counts"]}


def pending_for(cur: Any, hotel_slug: str, *, session_id: int | None) -> bool:
    """True while the PIN session has not acknowledged its handover."""
    if not session_id:
        return False
    cur.execute("SELECT handover_ack_at FROM device_sessions WHERE id = %s AND hotel_slug = %s", (session_id, hotel_slug))
    r = cur.fetchone()
    if r is None or r["handover_ack_at"] is not None:
        return False
    cur.execute("SELECT 1 FROM svc_handover_acks WHERE device_session_id = %s LIMIT 1", (session_id,))
    return cur.fetchone() is None


def session_for(cur: Any, hotel_slug: str, device_id: int | None, staff_id: int | None) -> int | None:
    """The open PIN session of a person on a device (the newest)."""
    if not device_id or not staff_id:
        return None
    cur.execute(
        """SELECT id FROM device_sessions WHERE hotel_slug = %s AND device_id = %s AND staff_member_id = %s
           AND ended_at IS NULL ORDER BY started_at DESC LIMIT 1""",
        (hotel_slug, device_id, staff_id),
    )
    r = cur.fetchone()
    return r["id"] if r else None


# ─── Console routes ───
# The device side (GET /device/service/handover, POST .../ack) is in
# devices_api.py; the console sees the same sheet for any scope it asks for.

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service-handover"])


@router.get("/handover")
def get_handover(slug: str, floor: list[str] = Query(default_factory=list), dept: list[str] = Query(default_factory=list),
                 ctx: HotelCtx = Depends(require_perm("service.view"))) -> dict:
    """The sheet for the asked scope, or the user's own departments/floors."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        return compute(cur, hotel, floors=floor or list(ctx.floors), departments=dept or list(ctx.departments),
                       role=ctx.role)


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    department_key: str | None = Field(None, max_length=40)
    floor: str | None = Field(None, max_length=20)


@router.post("/handover/notes")
def post_note(slug: str, body: NoteIn, ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    p = ctx.principal
    with db.db() as cur:
        return add_note(cur, slug, body.body, actor=ctx, author_label=p.label or p.email, user_id=p.user_id,
                        department_key=body.department_key, floor=body.floor)
