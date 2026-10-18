"""
Device routes for Service Control (03 spec 1.6, Canon 13 `/device/...`).

The paired device is the principal (clefs_device cookie, foundation); the
person working it is named by X-Clefs-Staff and must be on an open PIN
shift on that device (auth.deps.acting_staff). Pairing, PIN login, logout
and handoff are the foundation's (/auth/devices/...). Everything here is
scoped to the device's floors and departments; an unscoped device (a desk)
sees the whole hotel.

- GET  /device/service/feed: request_events after a cursor, long-polled up
  to `wait` seconds (<= 25, the Vercel budget); plus whether this person
  still owes a handover acknowledgement, the open incidents in scope (a
  Security or desk device loops its alarm on them) and the server time (the
  device clock is never trusted: the client computes its offset).
- POST /device/service/actions: the offline outbox's batch. Each item is
  idempotent on client_event_id and runs in its own savepoint, so one
  conflict never loses the rest. A closed request answers `conflict {by,
  at}` ("Already done by Amina"). An ack queued before a reassignment is
  accepted only when the device's client_at is within 60 s of server time.
- POST /device/service/sos, incident close (supervisor PIN), handover (read,
  acknowledge, notes), staff-created requests (source 'staff'), recovery
  (LEARN, gestures, supervisor approval) and lost-and-found.
"""

from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, acting_staff, require_device
from clefs.core import clock
from clefs.core import money
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.service import config, handover, incidents, intake, recovery, roster, state
from clefs.modules.service.router import card, event_out

router = APIRouter(prefix="/device/service", tags=["service-device"])
log = logging.getLogger("clefs.service.devices")

MAX_WAIT_S = 25
POLL_EVERY_S = 1.0
BATCH_MAX = 100
REASSIGN_ACK_WINDOW = timedelta(seconds=60)

# ─── Scope and actor ───


def _scope(cur: Any, ctx: HotelCtx) -> tuple[list[str], list[str]]:
    p = ctx.principal
    depts = [k for k in (config.department_for(cur, p.device_hotel, d) for d in p.device_departments) if k]
    return list(p.device_floors), depts


def _staff(ctx: HotelCtx, request: Request, *, required: bool = False) -> int | None:
    sid = acting_staff(ctx, request)
    if required and sid is None:
        raise ClefsError(400, "Log in with your PIN first (X-Clefs-Staff)", "staff_required")
    return sid


def _touch(cur: Any, ctx: HotelCtx, staff_id: int | None) -> None:
    """Staff activity keeps a shared-device PIN session alive
    (service.session_autologout reads last_activity_at)."""
    if staff_id:
        cur.execute(
            """UPDATE device_sessions SET last_activity_at = %s WHERE device_id = %s AND staff_member_id = %s
               AND ended_at IS NULL""",
            (clock.now(), ctx.principal.device_id, staff_id),
        )


def _slug(ctx: HotelCtx) -> str:
    return ctx.principal.device_hotel


# ─── Feed ───


def _feed_once(ctx: HotelCtx, cursor: int, limit: int, staff_id: int | None) -> dict:
    slug = _slug(ctx)
    with db.db(readonly=True) as cur:
        floors, depts = _scope(cur, ctx)
        evs = state.events_after(cur, slug, cursor, floors=floors, departments=depts, limit=limit)
        session_id = handover.session_for(cur, slug, ctx.principal.device_id, staff_id)
        pending = handover.pending_for(cur, slug, session_id=session_id)
        open_inc = incidents.list_incidents(cur, slug, status="open", floors=floors or None, limit=20)
    return {
        "events": [event_out(e) for e in evs],
        "cursor": evs[-1]["id"] if evs else cursor,
        "handover_pending": pending,
        "session_id": session_id,
        "open_incidents": [{k: i[k] for k in ("id", "kind", "level", "floor", "room")} | {"opened_at": i["opened_at"].isoformat()}
                           for i in open_inc],
        "server_time": clock.now().isoformat(),
    }


@router.get("/feed")
def feed(
    request: Request,
    cursor: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    wait: int = Query(0, ge=0, le=MAX_WAIT_S, description="seconds to hold the request open while nothing is new"),
    ctx: HotelCtx = Depends(require_device()),
) -> dict:
    """Events after `cursor` in the device's scope, oldest first. With
    wait > 0 the call holds (short read transactions, one per second, never
    a connection held across the sleep) until something arrives or the wait
    runs out, then returns the same shape with no events."""
    staff_id = _staff(ctx, request)
    deadline = _time.monotonic() + wait
    while True:
        out = _feed_once(ctx, cursor, limit, staff_id)
        if out["events"] or _time.monotonic() >= deadline:
            return out
        _time.sleep(min(POLL_EVERY_S, max(0.0, deadline - _time.monotonic())))


# ─── Batch actions ───


class DeviceAction(BaseModel):
    client_event_id: UUID
    action: Literal["ack", "start", "complete", "decline", "handoff", "assign", "note"]
    request_id: int
    base_version: int | None = None  # the version the device saw (kept in the event, not enforced)
    client_at: datetime | None = None
    note: str | None = Field(None, max_length=2000)
    resolution_code: str | None = Field(None, max_length=60)
    staff_id: int | None = None  # handoff target; "assign" is always a self-claim
    actor_staff_id: int | None = None  # who did it (default X-Clefs-Staff); must be on shift here


class BatchIn(BaseModel):
    actions: list[DeviceAction] = Field(..., min_length=1, max_length=BATCH_MAX)


def _client_at(v: datetime | None) -> datetime | None:
    """Stored as sent, but never in the future (server time is authoritative)."""
    if v is None:
        return None
    if v.tzinfo is None:
        raise ClefsError(400, "client_at must carry a timezone", "bad_request")
    return min(v, clock.now())


def _apply(cur: Any, ctx: HotelCtx, item: DeviceAction, actor: state.Actor) -> dict:
    slug = _slug(ctx)
    cid = str(item.client_event_id)
    client_at = _client_at(item.client_at)
    extra = {"base_version": item.base_version} if item.base_version is not None else None
    if item.action == "note":
        prior = state.event_by_client_id(cur, cid)
        if prior is not None:
            return {"status": "replayed", "request": card(state.load(cur, slug, prior["request_id"])), "event": event_out(prior)}
        row = state.load(cur, slug, item.request_id)
        if row is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        ev = state.record_event(cur, row, "note", actor, payload={"note": (item.note or "")[:2000], **(extra or {})},
                                client_event_id=cid, client_at=client_at)
        state.publish_changed(cur, row, "note")
        return {"status": "applied", "request": card(row), "event": event_out(ev)}
    if state.event_by_client_id(cur, cid) is None and item.action == "ack":
        row = state.load(cur, slug, item.request_id)
        holder = row.get("assigned_staff_id") if row else None
        if holder and holder != actor.staff_id and client_at is not None and clock.now() - client_at > REASSIGN_ACK_WINDOW:
            # queued offline before someone else got it: their job now
            raise state.TransitionConflict("This request went to someone else", "reassigned",
                                           {"request_id": row["id"], "state": row.get("state"), "version": row.get("version"),
                                            "by": row.get("assigned_to"), "at": row.get("assigned_at")})
    target = None
    if item.action == "assign":
        target = {"staff_id": actor.staff_id}
    elif item.action == "handoff" and item.staff_id:
        target = {"staff_id": item.staff_id}
    out = state.transition(cur, slug, item.request_id, item.action, actor, client_event_id=cid, client_at=client_at,
                           target=target, note=item.note, resolution_code=item.resolution_code, extra=extra)
    return {"status": "replayed" if out["replayed"] else "applied", "request": card(out["row"]),
            "event": event_out(out["event"])}


@router.post("/actions")
def batch(body: BatchIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    """Apply the outbox in order. -> {results: [{client_event_id, ok, status:
    applied|replayed|conflict|error, request?, event?, conflict?, error?}]}"""
    header_staff = _staff(ctx, request)
    on_shift = set(ctx.principal.active_staff_ids)
    results = []
    with db.db() as cur:
        for item in body.actions:
            res: dict[str, Any] = {"client_event_id": str(item.client_event_id), "request_id": item.request_id}
            who = item.actor_staff_id or header_staff
            if who is None or who not in on_shift:
                results.append({**res, "ok": False, "status": "error",
                                "error": {"code": "staff_not_on_shift", "detail": "Log in with your PIN first"}})
                continue
            actor = state.Actor.from_ctx(ctx, staff_id=who)
            cur.execute("SAVEPOINT svc_device_action")
            try:
                out = _apply(cur, ctx, item, actor)
                cur.execute("RELEASE SAVEPOINT svc_device_action")
                results.append({**res, "ok": True, **out})
            except state.TransitionConflict as exc:
                cur.execute("ROLLBACK TO SAVEPOINT svc_device_action")
                results.append({**res, "ok": False, "status": "conflict", "conflict": exc.info,
                                "error": {"code": exc.code, "detail": exc.detail}})
            except ClefsError as exc:
                cur.execute("ROLLBACK TO SAVEPOINT svc_device_action")
                results.append({**res, "ok": False, "status": "error", "error": {"code": exc.code, "detail": exc.detail}})
            except Exception:  # noqa: BLE001 - one bad item must not lose the rest of the outbox
                cur.execute("ROLLBACK TO SAVEPOINT svc_device_action")
                log.exception("device action %s failed", item.client_event_id)
                results.append({**res, "ok": False, "status": "error",
                                "error": {"code": "internal", "detail": "Could not apply this action; it will retry"}})
        for sid in {r for r in (header_staff, *(i.actor_staff_id for i in body.actions)) if r in on_shift}:
            _touch(cur, ctx, sid)
    return {"results": results, "server_time": clock.now().isoformat()}


# ─── SOS and incidents ───


class SosIn(BaseModel):
    client_event_id: UUID | None = None
    client_at: datetime | None = None
    note: str | None = Field(None, max_length=500)


@router.post("/sos")
def sos(body: SosIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    """Long-press SOS. Works without a PIN login too (a person in trouble may
    not have time to log in); then the incident carries only the device."""
    staff_id = _staff(ctx, request)
    with db.db() as cur:
        inc, replayed = incidents.sos(cur, ctx, staff_id=staff_id, client_event_id=body.client_event_id,
                                      client_at=_client_at(body.client_at), note=body.note)
        _touch(cur, ctx, staff_id)
        out = incidents.incident_out(inc)
        out["paged"] = len(inc.get("paged") or [])
        return {**out, "replayed": replayed}


class IncidentCloseIn(BaseModel):
    status: Literal["closed", "cancelled"] = "closed"
    note: str | None = Field(None, max_length=1000)


@router.post("/incidents/{incident_id}/close")
def close_incident(incident_id: int, body: IncidentCloseIn, request: Request,
                   ctx: HotelCtx = Depends(require_device())) -> dict:
    """Only a person on a supervisor or duty-manager shift (their PIN
    session on this device) can close or cancel an SOS."""
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        return incidents.incident_out(incidents.close(cur, ctx, incident_id, staff_id=staff_id, status=body.status,
                                                      note=body.note))


class SosCancelIn(BaseModel):
    supervisor_staff_id: int
    pin: str = Field(..., min_length=1, max_length=12)
    note: str | None = Field(None, max_length=1000)


@router.post("/sos/{sos_ref}/cancel")
def cancel_sos(sos_ref: str, body: SosCancelIn, ctx: HotelCtx = Depends(require_device())) -> dict:
    """Cancel an SOS as a false alarm with a supervisor's PIN typed on this
    device (no shift login needed). `sos_ref` is the incident id or the
    outbox's client_event_id. A wrong PIN is recorded before the 401 (the
    transaction commits first), so guesses stay throttled."""
    err: ClefsError | None = None
    with db.db() as cur:
        err = incidents.verify_supervisor_pin(cur, ctx, body.supervisor_staff_id, body.pin)
        if err is None:
            inc, changed = incidents.cancel_sos(cur, ctx, sos_ref, supervisor_staff_id=body.supervisor_staff_id,
                                                note=body.note)
            out = {**incidents.incident_out(inc), "changed": changed}
    if err is not None:
        raise err
    return out


# ─── Web Push subscriptions ───


class PushSubIn(BaseModel):
    subscription: dict


class PushUnsubIn(BaseModel):
    endpoint: str | None = Field(None, max_length=2000)


@router.post("/push-subscriptions")
def save_push_subscription(body: PushSubIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    """Keep this device's Web Push subscription (PushSubscription.toJSON()).
    The person on the PIN session (X-Clefs-Staff), if any, is recorded."""
    from clefs.modules.service import push

    staff_id = _staff(ctx, request)
    with db.db() as cur:
        row, created = push.save(cur, _slug(ctx), ctx.principal.device_id, body.subscription, staff_id=staff_id,
                                 user_agent=request.headers.get("user-agent"))
        return {**push.out(row), "created": created, "mode": push.mode()}


@router.delete("/push-subscriptions")
def delete_push_subscription(body: PushUnsubIn | None = None, ctx: HotelCtx = Depends(require_device())) -> dict:
    """Revoke this device's subscription for `endpoint`, or all of them."""
    from clefs.modules.service import push

    with db.db() as cur:
        n = push.revoke(cur, _slug(ctx), ctx.principal.device_id, body.endpoint if body else None)
    return {"ok": True, "revoked": n}


# ─── Handover ───


@router.get("/handover")
def get_handover(request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request)
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, _slug(ctx))
        floors, depts = _scope(cur, ctx)
        sheet = handover.compute(cur, hotel, floors=floors, departments=depts, role="device")
        sid = handover.session_for(cur, hotel.slug, ctx.principal.device_id, staff_id)
        sheet["session_id"] = sid
        sheet["pending"] = handover.pending_for(cur, hotel.slug, session_id=sid)
        return sheet


class AckIn(BaseModel):
    session_id: int | None = None  # default: this person's open PIN session here


@router.post("/handover/ack")
def ack_handover(body: AckIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    """The incoming person read the sheet; their queue may open. The sheet is
    recomputed here (what they acknowledge is what the server shows now)."""
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        hotel = get_hotel(cur, _slug(ctx))
        sid = body.session_id or handover.session_for(cur, hotel.slug, ctx.principal.device_id, staff_id)
        if body.session_id:
            cur.execute("SELECT 1 FROM device_sessions WHERE id = %s AND device_id = %s AND staff_member_id = %s",
                        (body.session_id, ctx.principal.device_id, staff_id))
            if cur.fetchone() is None:
                raise ClefsError(404, "That shift is not open on this device", "session_not_found")
        floors, depts = _scope(cur, ctx)
        sheet = handover.compute(cur, hotel, floors=floors, departments=depts, role="device")
        out = handover.acknowledge(cur, hotel, sheet, actor=ctx, staff_id=staff_id, device_id=ctx.principal.device_id,
                                   session_id=sid, user_id=None)
        _touch(cur, ctx, staff_id)
        return out


class DeviceNoteIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=4000)
    floor: str | None = Field(None, max_length=20)
    department_key: str | None = Field(None, max_length=40)


@router.post("/handover/notes")
def post_note(body: DeviceNoteIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        slug = _slug(ctx)
        floors, depts = _scope(cur, ctx)
        cur.execute("SELECT name FROM staff_members WHERE id = %s", (staff_id,))
        name = (cur.fetchone() or {}).get("name")
        a = roster.current_assignment(cur, slug, staff_id)
        return handover.add_note(cur, slug, body.body, actor=ctx, author_label=name, staff_id=staff_id,
                                 department_key=body.department_key or (depts[0] if len(depts) == 1 else None),
                                 floor=body.floor or (floors[0] if len(floors) == 1 else None),
                                 shift_assignment_id=a["id"] if a else None)


# ─── Today's roster (lock screen) ───


@router.get("/roster/today")
def roster_today(ctx: HotelCtx = Depends(require_device())) -> list[dict]:
    """Staff tiles for the lock screen: who is rostered now or later today
    in this device's scope (the foundation's /auth/devices/roster lists
    everyone with a PIN; this adds the shift)."""
    slug = _slug(ctx)
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, slug)
        floors, depts = _scope(cur, ctx)
        today = htime.business_date(hotel)
        rows = roster.list_assignments(cur, slug, today, today)
        now = clock.now()
        out = []
        for r in rows:
            if r["end_at"] <= now:
                continue
            if depts and r.get("department_key") and r["department_key"] not in depts:
                continue
            if floors and r.get("floors") and not set(r["floors"]) & set(floors):
                continue
            out.append({"staff_member_id": r["staff_member_id"], "name": r["staff_name"], "shift_role": r["shift_role"],
                        "start_at": r["start_at"].astimezone(hotel.tz).isoformat(),
                        "end_at": r["end_at"].astimezone(hotel.tz).isoformat(), "floors": r["floors"],
                        "department_key": r.get("department_key"), "on_now": r["start_at"] <= now,
                        "on_shift_here": r["staff_member_id"] in ctx.principal.active_staff_ids})
        return out


# ─── Requests, recovery, lost and found ───


# ─── Requests, recovery, lost and found ───


class StaffRequestIn(BaseModel):
    """A job a staff member spotted or was asked for in the corridor."""

    room: str | None = Field(None, max_length=64)
    description: str = Field(..., min_length=1, max_length=2000)
    request_type_key: str | None = Field(None, max_length=60)
    type: str | None = Field(None, max_length=100)
    severity: Literal["L1", "L2", "L3"] | None = None
    amenity_item_id: int | None = None
    quantity: int | None = Field(None, ge=1, le=50)
    language: str | None = Field(None, max_length=10)
    location_slug: str | None = Field(None, max_length=64)
    client_event_id: UUID  # the outbox key doubles as the idempotency key


@router.post("/requests")
def create_request(body: StaffRequestIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request, required=True)
    slug = _slug(ctx)
    actor = state.Actor.from_ctx(ctx, staff_id=staff_id)
    with db.db() as cur:
        out = intake.create_request(
            cur, slug,
            intake.IntakeIn(description=body.description, source="staff", room=body.room, type=body.type,
                            request_type_key=body.request_type_key, severity_hint=body.severity,
                            amenity_item_id=body.amenity_item_id, quantity=body.quantity, language=body.language,
                            location_slug=body.location_slug, idempotency_key=f"device:{body.client_event_id}",
                            created_by_staff_id=staff_id),
            actor=actor,
        )
        _touch(cur, ctx, staff_id)
        return {"request": card(out.row), "merged": out.merged, "replayed": out.replayed, "routed": out.routed}


@router.get("/recovery-cases")
def recovery_cases(room: str | None = None, status: Literal["open", "resolved", "closed", "all"] = "open",
                   ctx: HotelCtx = Depends(require_device())) -> list[dict]:
    slug = _slug(ctx)
    with db.db() as cur:
        return [recovery.case_view(cur, slug, r) for r in recovery.list_cases(cur, slug, status=status, room=room, limit=50)]


@router.post("/recovery-cases")
def open_case(body: recovery.CaseIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request, required=True)
    slug = _slug(ctx)
    with db.db() as cur:
        row, created = recovery.open_case(cur, slug, actor=ctx, request_id=body.request_id, room=body.room,
                                          category_key=body.category_key, severity=body.severity, reason=body.reason,
                                          notes=body.notes, staff_id=staff_id, learn=body.learn,
                                          client_event_id=body.client_event_id)
        _touch(cur, ctx, staff_id)
        return {**recovery.case_view(cur, slug, row), "created": created}


@router.patch("/recovery-cases/{case_id}")
def patch_case(case_id: int, body: recovery.CasePatch, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    """LEARN ticks and notes from the device. Resolving or closing needs a
    supervisor on shift."""
    staff_id = _staff(ctx, request, required=True)
    slug = _slug(ctx)
    with db.db() as cur:
        if body.status in ("resolved", "closed") and not incidents.can_supervise(cur, ctx, staff_id):
            raise ClefsError(403, "Only a supervisor closes a recovery case", "supervisor_required")
        return recovery.case_view(cur, slug, recovery.update_case(cur, slug, case_id, body, actor=ctx))


@router.post("/recovery-cases/{case_id}/actions")
def give_gesture(case_id: int, body: recovery.GestureIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        ccy = money.ccy(ctx.hotel.currency or "AED")
        g, replayed = recovery.add_gesture(cur, ctx, case_id, kind=body.kind, amount_minor=body.minor(ccy),
                                           description=body.description, staff_id=staff_id,
                                           client_event_id=body.client_event_id, via="device")
        _touch(cur, ctx, staff_id)
        return {**recovery.gesture_out(g), "replayed": replayed}


@router.post("/recovery-actions/{gesture_id}/decide")
def decide_gesture(gesture_id: int, body: recovery.DecideIn, request: Request,
                   ctx: HotelCtx = Depends(require_device())) -> dict:
    """A supervisor (or duty manager) on shift approves with their PIN
    session; the matrix decides whether their authority covers it."""
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        return recovery.gesture_out(recovery.decide_gesture(cur, ctx, gesture_id, body.decision, staff_id=staff_id,
                                                            note=body.note, via="device"))


@router.post("/lost-found")
def log_found(body: incidents.LostFoundIn, request: Request, ctx: HotelCtx = Depends(require_device())) -> dict:
    staff_id = _staff(ctx, request, required=True)
    with db.db() as cur:
        row, replayed = incidents.log_found(cur, ctx, body, staff_id=staff_id)
        _touch(cur, ctx, staff_id)
        return {**incidents.lf_out(row), "replayed": replayed}
