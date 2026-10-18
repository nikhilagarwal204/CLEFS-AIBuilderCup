"""
Service Control console routes, under /hotels/{slug}/service/ (Canon 13):
the queue, filing, one request with its events, transitions, notes and the
request_events feed. A paired device may call the work routes too (its
acting staff member comes from X-Clefs-Staff, checked against its open PIN
shifts). Public tracker routes live in router_public.py, the device feed in
router_device.py.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, acting_staff, require_perm_or_device
from clefs.core.errors import ClefsError
from clefs.modules.service import constants as C
from clefs.modules.service import intake, state

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service"])

# Columns a console or device card needs (no internal bookkeeping such as
# idempotency keys or the ladder cycle).
CARD_KEYS = (
    "id", "room", "type", "subtype", "description", "category", "priority", "status", "timestamp", "assigned_to",
    "accepted_at", "resolved_at", "state", "version", "source", "language", "original_text", "translated", "floor",
    "room_type", "stay_stage", "location_slug", "department_key", "request_type_key", "amenity_item_id", "quantity",
    "severity", "sensitivity", "sensitive_kind", "assigned_staff_id", "assigned_device_id", "assigned_at",
    "acknowledged_at", "ack_implicit", "started_at", "cancelled_at", "scheduled_for", "ack_due_at", "resolve_due_at",
    "sla_policy_ref", "sla_ack_breached", "sla_resolve_breached", "escalation_level", "reopen_count", "merged_count",
    "guest_confirmation", "guest_confirmed_at", "shift_key", "resolution_code", "verified_room", "incident_id", "flags",
)
EVENT_KEYS = ("id", "request_id", "at", "kind", "action", "from_state", "to_state", "version", "actor_type",
              "actor_staff_id", "actor_user_id", "device_id", "actor_label", "late_sync", "floor", "department_key", "payload")


def card(row: dict) -> dict:
    return {k: row.get(k) for k in CARD_KEYS}


def event_out(ev: dict) -> dict:
    out = {k: ev.get(k) for k in EVENT_KEYS}
    out["at"] = ev["at"].isoformat() if ev.get("at") else None
    return out


def _actor(ctx: HotelCtx, request: Request) -> state.Actor:
    return state.Actor.from_ctx(ctx, staff_id=acting_staff(ctx, request))


# ─── Queue ───


@router.get("/queue")
def queue(
    slug: str,
    state_filter: Literal["open", "all", "closed"] = Query("open", alias="state"),
    dept: str | None = None,
    floor: str | None = None,
    since: int | None = None,
    limit: int = Query(200, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm_or_device("service.view")),
) -> list[dict]:
    """Open work by default (every open request, however old), most urgent
    first: L1 before L2 before L3, then the earliest ack due."""
    sql = ["SELECT * FROM staff_requests WHERE hotel_slug = %s"]
    params: list[Any] = [slug]
    if state_filter == "open":
        sql.append("AND state = ANY(%s)")
        params.append(list(C.OPEN_STATES))
    elif state_filter == "closed":
        sql.append("AND state = ANY(%s)")
        params.append(list(C.TERMINAL_STATES))
    if dept:
        sql.append("AND department_key = %s")
        params.append(dept)
    if floor:
        sql.append("AND floor = %s")
        params.append(floor)
    if since is not None:
        sql.append('AND "timestamp" >= %s')
        params.append(since)
    sql.append("ORDER BY COALESCE(severity, 'L3'), COALESCE(ack_due_at, \"timestamp\"), id DESC LIMIT %s")
    params.append(limit)
    with db.db(readonly=True) as cur:
        cur.execute(" ".join(sql), params)
        return [card(dict(r)) for r in cur.fetchall()]


# ─── Filing ───


class RequestIn(BaseModel):
    room: str | None = Field(None, max_length=64)
    description: str = Field(..., min_length=1, max_length=2000)
    type: str | None = Field(None, max_length=100)
    request_type_key: str | None = Field(None, max_length=60)
    category: str | None = Field(None, max_length=100)
    priority: Literal["high", "medium", "low"] | None = None
    severity: Literal["L1", "L2", "L3"] | None = None
    language: str | None = Field(None, max_length=10)
    amenity_item_id: int | None = None
    quantity: int | None = Field(None, ge=1, le=50)
    scheduled_for: int | None = None
    location_slug: str | None = Field(None, max_length=64)
    idempotency_key: str | None = Field(None, max_length=200)
    # phone_desk: a call taken at the desk (the phone-entry console)
    source: Literal["console", "phone_desk", "device"] = "console"


@router.post("/requests")
def create(slug: str, body: RequestIn, request: Request,
           ctx: HotelCtx = Depends(require_perm_or_device("service.work"))) -> dict:
    actor = _actor(ctx, request)
    source = "device" if ctx.principal.kind == "device" else body.source
    with db.db() as cur:
        out = intake.create_request(
            cur, slug,
            intake.IntakeIn(
                description=body.description, source=source, room=body.room, type=body.type,
                request_type_key=body.request_type_key, category=body.category, priority_hint=body.priority,
                severity_hint=body.severity, language=body.language, amenity_item_id=body.amenity_item_id,
                quantity=body.quantity, scheduled_for=body.scheduled_for, location_slug=body.location_slug,
                idempotency_key=body.idempotency_key, created_by_staff_id=actor.staff_id,
                created_by_user_id=actor.user_id,
            ),
            actor=actor,
        )
        return {"request": card(out.row), "merged": out.merged, "replayed": out.replayed,
                "tracking_token": out.tracking_token, "routed": out.routed}


@router.get("/requests/{request_id}")
def get_one(slug: str, request_id: int, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    with db.db(readonly=True) as cur:
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        evs = state.events_after(cur, slug, 0, request_id=request_id, limit=500)
    return {"request": card(row), "events": [event_out(e) for e in evs]}


# ─── Transitions and notes ───

# Actions that close or undo someone's work need a manager's permission;
# the people doing the work may ack, start, complete, decline and hand off.
MANAGE_ACTIONS = frozenset({"cancel", "reopen", "assign", "reassign", "unassign", "release"})


class TransitionIn(BaseModel):
    action: Literal["release", "assign", "ack", "start", "complete", "reassign", "decline", "handoff", "cancel",
                    "reopen", "unassign"]
    staff_id: int | None = None  # the new holder for assign / reassign / handoff
    device_id: int | None = None  # or a shared device
    note: str | None = Field(None, max_length=2000)
    resolution_code: str | None = Field(None, max_length=60)
    expected_version: int | None = None
    client_event_id: UUID | None = None  # the device outbox's idempotency key


def conflict_response(exc: state.TransitionConflict) -> JSONResponse:
    """409 with who got there first ({by, at, state, version})."""
    return JSONResponse(status_code=409, content={"detail": exc.detail, "code": exc.code, "conflict": exc.info})


@router.post("/requests/{request_id}/transition")
def transition(slug: str, request_id: int, body: TransitionIn, request: Request,
               ctx: HotelCtx = Depends(require_perm_or_device("service.work"))) -> Any:
    actor = _actor(ctx, request)
    target = {"staff_id": body.staff_id, "device_id": body.device_id} if (body.staff_id or body.device_id) else None
    self_claim = body.action == "assign" and actor.staff_id is not None and body.staff_id in (None, actor.staff_id)
    if self_claim:
        target = {"staff_id": actor.staff_id}  # "I'll take it" on a device
    elif body.action in MANAGE_ACTIONS and not ctx.can("service.manage"):
        raise ClefsError(403, f"You cannot {body.action} requests", "forbidden")
    with db.db() as cur:
        try:
            out = state.transition(
                cur, slug, request_id, body.action, actor, expected_version=body.expected_version,
                client_event_id=str(body.client_event_id) if body.client_event_id else None, target=target, note=body.note,
                resolution_code=body.resolution_code,
            )
        except state.TransitionConflict as exc:
            return conflict_response(exc)
        return {"request": card(out["row"]), "event": event_out(out["event"]), "replayed": out["replayed"]}


class NoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)


@router.post("/requests/{request_id}/notes")
def add_note(slug: str, request_id: int, body: NoteIn, request: Request,
             ctx: HotelCtx = Depends(require_perm_or_device("service.work"))) -> dict:
    with db.db() as cur:
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        ev = state.record_event(cur, row, "note", _actor(ctx, request), payload={"note": body.body})
        state.publish_changed(cur, row, "note")
    return event_out(ev)


# ─── The request_events feed ───


@router.get("/events")
def feed(
    slug: str,
    cursor: str = Query("0", max_length=20),
    request_id: int | None = None,
    limit: int = Query(200, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm_or_device("service.view")),
) -> dict:
    """Events after `cursor`, oldest first; poll with the returned cursor
    (polling first, SSE only wakes the poller, 03 spec 0.4). cursor=latest
    answers {events: [], cursor: <newest id>} so a poller that has just
    refreshed its list starts at the head instead of walking the feed."""
    with db.db(readonly=True) as cur:
        if cursor == "latest":
            cur.execute("SELECT COALESCE(MAX(id), 0) AS head FROM svc_request_events WHERE hotel_slug = %s", (slug,))
            return {"events": [], "cursor": int(cur.fetchone()["head"])}
        if not cursor.isdigit():
            raise ClefsError(400, "cursor must be an event id or 'latest'", "invalid_cursor")
        after = int(cursor)
        evs = state.events_after(cur, slug, after, request_id=request_id, limit=limit)
    return {"events": [event_out(e) for e in evs], "cursor": evs[-1]["id"] if evs else after}
