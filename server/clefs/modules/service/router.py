"""
Service Control console routes, under /hotels/{slug}/service/ (Canon 13):
the queue, filing, one request with its events, transitions, notes and the
request_events feed. A paired device may call the work routes too (its
acting staff member comes from X-Clefs-Staff, checked against its open PIN
shifts). Public tracker routes live in router_public.py, the device feed in
router_device.py.

The guest service desk (05 spec, section 3) adds to every card: the ETA,
the guest's mood, the photo check (with a 5-minute photo link), closure,
escalation_level as the role reached (supervisor | duty_manager | gm) and
accepted_at as the moment a staff member accepted the job. Plus GET
.../requests/{id}/free-staff (the "Choose staff" list), GET
.../requests/{id}/proof-photo and, for demo hotels only, POST
.../requests/{id}/demo-advance.
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import (HotelCtx, acting_staff, current_principal, device_principal, require_perm,
                             require_perm_or_device, resolve_ctx)
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.service import constants as C
from clefs.modules.service import intake, proof, routing, staff_lang, state

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
    "eta_minutes", "eta_at", "mood", "frustration", "needs_proof", "proof_status", "proof_reason", "closed_at",
    "closure_reason",
)
EVENT_KEYS = ("id", "request_id", "at", "kind", "action", "from_state", "to_state", "version", "actor_type",
              "actor_staff_id", "actor_user_id", "device_id", "actor_label", "late_sync", "floor", "department_key", "payload")


def card(row: dict) -> dict:
    out = {k: row.get(k) for k in CARD_KEYS}
    step = int(row.get("escalation_level") or 0)
    out.update({
        # 05 spec 3 and decision 13: accepted_at is when a staff member
        # accepted the job ("Accepted in 00:41"), not the legacy column of
        # that name (the deprecated alias of assigned_at, still on the row).
        "accepted_at": row.get("acknowledged_at"),
        # the role the ladder reached, never L1-L3 (the severity scale); the
        # number stays as escalation_step
        "escalation_level": C.ESCALATION_LABEL.get(step),
        "escalation_step": step,
        "guest_language": row.get("language"),
        "staff_first_name": routing.first_name(row.get("assigned_to")) if row.get("assigned_staff_id") else None,
        # decision 11: URGENT is priority high; an emergency is its own flag
        "urgent": row.get("priority") == "high",
        "emergency": row.get("sensitivity") == "emergency",
        "awaiting_guest": row.get("state") == "completed" and row.get("closed_at") is None,
        "proof_photo_url": proof.photo_path(row) if row.get("proof_id") else None,
    })
    return out


def event_out(ev: dict) -> dict:
    out = {k: ev.get(k) for k in EVENT_KEYS}
    out["at"] = ev["at"].isoformat() if ev.get("at") else None
    return out


def _actor(ctx: HotelCtx, request: Request) -> state.Actor:
    return state.Actor.from_ctx(ctx, staff_id=acting_staff(ctx, request))


# ─── Queue ───


def _device_reader(ctx: HotelCtx, request: Request, cur: Any) -> str | None:
    """The reading language of the person working a paired device (named by
    X-Clefs-Staff; English without one), or None for a console user. A
    stale header reads English rather than failing the list."""
    if ctx.principal.kind != "device":
        return None
    try:
        staff_id = acting_staff(ctx, request)
    except ClefsError:
        staff_id = None
    return staff_lang.reading_language(cur, ctx.hotel.slug, staff_id)


@router.get("/queue")
def queue(
    slug: str,
    request: Request,
    state_filter: Literal["open", "all", "closed"] = Query("open", alias="state"),
    dept: str | None = None,
    floor: str | None = None,
    since: int | None = None,
    limit: int = Query(200, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm_or_device("service.view")),
) -> list[dict]:
    """Open work by default (every open request, however old), most urgent
    first: L1 before L2 before L3, then the earliest ack due. For a paired
    device each card also carries display_title and display_text in the
    worker's language (as GET /device/service/requests does)."""
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
        rows = [dict(r) for r in cur.fetchall()]
        lang = _device_reader(ctx, request, cur)
    if lang is None:
        return [card(r) for r in rows]
    display = staff_lang.display_fields(slug, rows, lang)  # outside the read: it may call the model
    return [{**card(r), **display[r["id"]]} for r in rows]


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
def get_one(slug: str, request_id: int, request: Request,
            ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    with db.db(readonly=True) as cur:
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        evs = state.events_after(cur, slug, 0, request_id=request_id, limit=500)
        lang = _device_reader(ctx, request, cur)
    out = card(row)
    if lang is not None:
        out.update(staff_lang.translate_job(slug, row, lang))
    return {"request": out, "events": [event_out(e) for e in evs]}


@router.get("/requests/{request_id}/free-staff")
def free_staff(slug: str, request_id: int, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    """The manager's "Choose staff" list (05 spec, decision 5): {free: the
    free people best first, the same order auto-assignment uses, each with
    open_jobs, floor_distance, idle_min and the eta_minutes they would get;
    others: everyone else active, with the reason they are not free}."""
    with db.db(readonly=True) as cur:
        row = state.load(cur, slug, request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        return routing.staff_options(cur, get_hotel(cur, slug), row)


@router.get("/requests/{request_id}/proof-photo")
def proof_photo(slug: str, request_id: int, request: Request, t: str | None = None,
                proof_id: int | None = None) -> Response:
    """The completion photo. A card's proof_photo_url carries a 5-minute
    ticket (t) that works in an <img> without a session; otherwise a user
    with service.view or a paired device of the hotel may read any of the
    request's photos (proof_id, else the latest)."""
    granted = proof.verify_link(slug, request_id, t) if t else None
    if t and granted is None:
        raise ClefsError(401, "Photo link expired or invalid", "invalid_ticket")
    with db.db(readonly=True) as cur:
        if granted is None:
            principal = current_principal(request)
            ctx = resolve_ctx(principal, slug, cur=cur)
            dev = device_principal(request)
            if not (ctx.can("service.view") or (dev is not None and dev.device_hotel == slug)):
                if not principal.is_authenticated:
                    raise ClefsError(401, "Sign in required", "unauthorized")
                raise ClefsError(403, "You cannot open this photo", "forbidden")
        row = proof.photo_row(cur, slug, request_id, granted or proof_id)
    data = proof.photo_bytes(row)
    return Response(content=data, media_type=row["content_type"],
                    headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"})


class DemoAdvanceIn(BaseModel):
    minutes: int = Field(..., ge=1, le=240)


# Every instant of a request that the ladder, the SLA and closure read.
_CLOCK_COLUMNS = ('"timestamp"', "ladder_started_at", "ack_due_at", "resolve_due_at", "assigned_at", "accepted_at",
                  "acknowledged_at", "started_at", "resolved_at", "eta_at")


@router.post("/requests/{request_id}/demo-advance")
def demo_advance(slug: str, request_id: int, body: DemoAdvanceIn,
                 ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    """Demo hotels only (hotel status demo; 404 elsewhere): move one job's
    clock back `minutes`, as if the guest had asked that much earlier, and
    sweep the hotel at once, so the escalation ladder (supervisor at 3,
    duty manager at 5, GM at 10) and the 30-minute closure can be shown
    live. Audited. -> {request, sweep}."""
    from clefs.modules.service import sla

    with db.db() as cur:
        if get_hotel(cur, slug).status != "demo":
            raise ClefsError(404, "Not found", "not_found")
        row = state.load(cur, slug, request_id, lock=True)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        shift = body.minutes * 60_000
        cur.execute(
            f"UPDATE staff_requests SET {', '.join(f'{c} = {c} - %s' for c in _CLOCK_COLUMNS)} WHERE id = %s",
            [shift] * len(_CLOCK_COLUMNS) + [request_id],
        )
        audit(cur, ctx, "service.request.demo_advance", "staff_request", request_id,
              before={"timestamp": row["timestamp"]}, after={"minutes": body.minutes}, hotel_slug=slug)
        result = sla.sweep(cur, slug)
        row = state.load(cur, slug, request_id)
        state.publish_changed(cur, row, "demo_advance")
        return {"request": card(row), "sweep": result}


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
