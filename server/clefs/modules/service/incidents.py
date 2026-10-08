"""
Incidents (emergencies, sensitive pathways, staff SOS) and lost and found
(03 spec 1.5, 1.6).

- Intake opens an incident for every emergency or sensitive request
  (intake._open_incident). This file adds the staff SOS: a 2-second
  long-press on a device files `kind='staff_sos'` with the device's floor
  and the person on shift, and pages Security and the duty manager at once.
- A staff SOS is a safety matter, so it pages even while SLA escalations are
  in shadow mode (shadow mode calibrates ladder thresholds; it never mutes a
  person asking for help). NOTIFY_MODE still decides whether a message
  leaves the building.
- Only a supervisor (a supervisor or duty-manager shift, or that job title)
  or a console manager can close or cancel an SOS; the person who pressed it
  cannot silence it alone.
- Writes from a device carry a client_event_id (the offline outbox): a
  replay returns the first row and changes nothing.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db, events
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm, require_perm_or_device
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.service import escalation, roster

log = logging.getLogger("clefs.service.incidents")

SUPERVISOR_ROLES = frozenset({"supervisor", "duty_manager", "gm"})
SOS_PURPOSE = "service.sos"
INCIDENT_KEYS = ("id", "kind", "level", "severity", "room", "floor", "request_id", "summary", "status", "opened_at",
                 "closed_at", "opened_by", "closed_by", "close_note", "device_id", "staff_member_id")


def _uuid(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return str(uuid.UUID(str(v)))
    except ValueError as exc:
        raise ClefsError(400, "client_event_id must be a UUID", "bad_request") from exc


def incident_out(r: dict) -> dict:
    out = {k: r.get(k) for k in INCIDENT_KEYS}
    for k in ("opened_at", "closed_at"):
        out[k] = r[k].isoformat() if r.get(k) else None
    return out


def get(cur: Any, hotel_slug: str, incident_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT * FROM svc_incidents WHERE id = %s AND hotel_slug = %s" + (" FOR UPDATE" if lock else ""),
                (incident_id, hotel_slug))
    r = cur.fetchone()
    return dict(r) if r else None


def list_incidents(cur: Any, hotel_slug: str, *, status: str = "open", since: datetime | None = None,
                   floors: list[str] | None = None, limit: int = 100) -> list[dict]:
    sql = ["SELECT * FROM svc_incidents WHERE hotel_slug = %s"]
    params: list[Any] = [hotel_slug]
    if status != "all":
        sql.append("AND status = %s")
        params.append(status)
    if since is not None:
        sql.append("AND opened_at >= %s")
        params.append(since)
    if floors:
        sql.append("AND (floor IS NULL OR floor = ANY(%s))")
        params.append(floors)
    sql.append("ORDER BY opened_at DESC LIMIT %s")
    params.append(max(1, min(limit, 500)))
    cur.execute(" ".join(sql), params)
    return [dict(r) for r in cur.fetchall()]


def actor_json(ctx: Any, staff_id: int | None, label: str | None = None) -> dict:
    p = getattr(ctx, "principal", ctx)
    kind = getattr(p, "kind", "system")
    return {"type": {"user": "user", "device": "device"}.get(kind, "system"), "staff_id": staff_id,
            "user_id": getattr(p, "user_id", None), "device_id": getattr(p, "device_id", None),
            "label": label or getattr(p, "label", None)}


def can_supervise(cur: Any, ctx: Any, staff_id: int | None) -> bool:
    """A console manager, or a device user on a supervisor / duty manager
    shift (or with that job title)."""
    if getattr(getattr(ctx, "principal", None), "kind", None) != "device":
        return bool(getattr(ctx, "can", lambda *_: False)("service.manage"))
    if not staff_id:
        return False
    hotel_slug = ctx.hotel.slug
    if roster.shift_role_of(cur, hotel_slug, staff_id) in SUPERVISOR_ROLES:
        return True
    cur.execute("SELECT role_key FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, hotel_slug))
    r = cur.fetchone()
    return bool(r and (r["role_key"] or "") in SUPERVISOR_ROLES)


# ─── Staff SOS ───


def _sos_floor(cur: Any, ctx: Any, staff_id: int | None) -> str | None:
    """Where help is needed: the person's rostered floor now, else the
    device's first floor."""
    if staff_id:
        a = roster.current_assignment(cur, ctx.hotel.slug, staff_id)
        if a and a.get("floors"):
            return a["floors"][0]
    floors = getattr(ctx.principal, "device_floors", ()) or ()
    return floors[0] if floors else None


def page_sos(cur: Any, hotel: Any, incident: dict, staff_name: str | None) -> list[dict]:
    """WhatsApp (then SMS) to Security and the duty manager on shift, one
    message per person, never the guest's words (there are none here)."""
    from clefs import notify

    where = f"floor {incident['floor']}" if incident.get("floor") else "location unknown"
    text = f"SOS {hotel.name}: {staff_name or 'a staff member'} needs help, {where}. Open the Clefs device to respond."
    row = {"department_key": None, "floor": incident.get("floor")}
    people: dict[str, escalation.Person] = {}
    for role in ("security", "duty_manager"):
        for p in escalation.people_for(cur, hotel.slug, role, row):
            if p.staff_id != incident.get("staff_member_id"):
                people.setdefault(p.key, p)
    out = []
    for p in people.values():
        channels = tuple(ch for ch in ("whatsapp", "sms") if (ch == "sms" and p.phone) or (ch == "whatsapp" and p.phone and p.whatsapp_ok))
        res = {"person": p.public(), "channels": list(channels), "sent": False}
        if channels:
            try:
                r = notify.notify(
                    notify.Recipient(phone=p.phone, email=p.email, name=p.name, user_id=p.user_id),
                    notify.Message(purpose=SOS_PURPOSE, hotel_slug=hotel.slug, category="utility", sender="platform",
                                   whatsapp_text=text, sms_text=text[:300],
                                   whatsapp_template={"name": escalation.TEMPLATE, "lang": "en",
                                                      "params": [hotel.name[:60], text[:900]]},
                                   idempotency_key=f"svc.sos:{incident['id']}:{p.key}"),
                    channels=channels, cur=cur,
                )
                res.update(sent=bool(r.ok), channel=r.channel, status=r.status)
            except Exception as exc:  # noqa: BLE001 - a provider fault must not lose the incident
                log.warning("SOS page to %s failed for %s: %s", p.key, hotel.slug, exc)
                res["status"] = "error"
        out.append(res)
    return out


def sos(cur: Any, ctx: Any, *, staff_id: int | None, client_event_id: Any = None, client_at: datetime | None = None,
        note: str | None = None) -> tuple[dict, bool]:
    """File a staff SOS. -> (incident, replayed)."""
    cid = _uuid(client_event_id)
    slug = ctx.hotel.slug
    if cid:
        cur.execute("SELECT * FROM svc_incidents WHERE client_event_id = %s AND hotel_slug = %s", (cid, slug))
        prior = cur.fetchone()
        if prior:
            return dict(prior), True
    staff_name = None
    if staff_id:
        cur.execute("SELECT name FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
        r = cur.fetchone()
        staff_name = r["name"] if r else None
    floor = _sos_floor(cur, ctx, staff_id)
    cur.execute(
        """INSERT INTO svc_incidents (hotel_slug, kind, level, severity, floor, summary, opened_by, device_id,
               staff_member_id, client_event_id, client_at, opened_at)
           VALUES (%s, 'staff_sos', 'emergency', 'L1', %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (client_event_id) WHERE client_event_id IS NOT NULL DO NOTHING RETURNING *""",
        (slug, floor, (note or "Staff SOS")[:500], Json(actor_json(ctx, staff_id, staff_name)),
         getattr(ctx.principal, "device_id", None), staff_id, cid, client_at, clock.now()),
    )
    row = cur.fetchone()
    if row is None:  # a concurrent replay won the insert
        cur.execute("SELECT * FROM svc_incidents WHERE client_event_id = %s", (cid,))
        return dict(cur.fetchone()), True
    inc = dict(row)
    hotel = get_hotel(cur, slug)
    paged = page_sos(cur, hotel, inc, staff_name)
    # Wake every device of the hotel whose tab is backgrounded (the feed's
    # open_incidents loops the alarm once it is foregrounded). The floor
    # only, no name: the push service is a third party. Log-only until VAPID
    # keys exist; never raises.
    from clefs.modules.service import push

    push.send_to_devices(cur, slug, title="SOS", tag=f"sos-{inc['id']}", urgent=True,
                         body=f"Staff SOS, {'floor ' + floor if floor else 'location unknown'}. Open the Clefs device.")
    audit(cur, ctx, "service.sos.open", "svc_incidents", str(inc["id"]),
          after={"floor": floor, "staff_id": staff_id, "paged": len(paged)}, hotel_slug=slug)
    events.publish(slug, "service.incident", {"id": inc["id"], "kind": "staff_sos", "floor": floor, "status": "open"},
                   cur=cur)
    from clefs.modules.service import signals as svc_signals

    svc_signals.raise_signal(cur, slug, "service.l1_incident", f"svc:sos:{inc['id']}", f"Staff SOS on {floor or 'unknown floor'}",
                             summary=f"{staff_name or 'A staff member'} pressed SOS", severity=4,
                             subject_type="incident", subject_id=str(inc["id"]))
    inc["paged"] = paged
    return inc, False


# ─── Close ───


def close(cur: Any, ctx: Any, incident_id: int, *, staff_id: int | None, status: Literal["closed", "cancelled"] = "closed",
          note: str | None = None) -> dict:
    """Close (handled) or cancel (false alarm) an open incident. A staff SOS
    needs a supervisor; any incident needs service.manage in the console."""
    slug = ctx.hotel.slug
    inc = get(cur, slug, incident_id, lock=True)
    if inc is None:
        raise ClefsError(404, "Incident not found", "incident_not_found")
    if inc["status"] != "open":
        return inc
    if not can_supervise(cur, ctx, staff_id):
        raise ClefsError(403, "Only a supervisor can close this", "supervisor_required")
    cur.execute(
        """UPDATE svc_incidents SET status = %s, closed_at = %s, closed_by = %s, close_note = %s
           WHERE id = %s RETURNING *""",
        (status, clock.now(), Json(actor_json(ctx, staff_id)), (note or "")[:1000] or None, incident_id),
    )
    out = dict(cur.fetchone())
    # The case is over, so its brief items are too: a staff SOS by incident,
    # an L1 by its (finished) request.
    from clefs.core import signals as core_signals
    from clefs.modules.service import signals as svc_signals

    core_signals.resolve_signal(cur, slug, f"svc:sos:{incident_id}", resolution=f"incident_{status}")
    svc_signals.resolve_finished(cur, slug)
    audit(cur, ctx, f"service.incident.{'cancel' if status == 'cancelled' else 'close'}", "svc_incidents",
          str(incident_id), before={"status": "open"}, after={"status": status, "note": note}, hotel_slug=slug)
    events.publish(slug, "service.incident", {"id": incident_id, "kind": inc["kind"], "status": status}, cur=cur)
    return out


# ─── SOS cancel with a supervisor PIN (device) ───
# The person who pressed SOS is usually the one holding the device, so the
# supervisor types their own PIN on it instead of starting a shift there.
# Wrong PINs are throttled like the foundation's shift login (5 per device
# per 5 minutes, 15 across devices per 15 minutes) through clefs.ratelimit,
# and the foundation's own lockout (staff_members.pin_locked_until) is
# honoured. The route commits the failure before raising: a rolled-back
# counter would allow unlimited guesses.

SUP_PIN_DEVICE = (5, 300)
SUP_PIN_STAFF = (15, 900)


def _pin_keys(device_id: Any, staff_id: int) -> tuple[str, str]:
    return f"svc:sup_pin:{device_id}:{staff_id}", f"svc:sup_pin:staff:{staff_id}"


def verify_supervisor_pin(cur: Any, ctx: Any, staff_id: int, pin: str) -> ClefsError | None:
    """None when `pin` is this staff member's and they may supervise; else
    the error to raise AFTER the caller commits (the failure is recorded)."""
    from clefs import ratelimit
    from clefs.auth import passwords

    slug = ctx.hotel.slug
    cur.execute("SELECT id, pin_hash, pin_locked_until, is_active FROM staff_members WHERE id = %s AND hotel_slug = %s",
                (staff_id, slug))
    staff = cur.fetchone()
    if staff is None or staff["is_active"] is False:
        passwords.burn_time(pin or "")  # the same cost whether or not the person exists
        return ClefsError(404, "Staff member not found", "staff_not_found")
    dev_key, staff_key = _pin_keys(getattr(ctx.principal, "device_id", None), staff_id)
    locked = staff["pin_locked_until"] and staff["pin_locked_until"] > clock.now()
    if locked or ratelimit.remaining(cur, dev_key, limit=SUP_PIN_DEVICE[0], window_s=SUP_PIN_DEVICE[1]) == 0 \
            or ratelimit.remaining(cur, staff_key, limit=SUP_PIN_STAFF[0], window_s=SUP_PIN_STAFF[1]) == 0:
        return ClefsError(423, "Too many wrong PINs. Try again in a few minutes.", "pin_locked")
    if not staff["pin_hash"]:
        return ClefsError(409, "No PIN is set for this staff member", "pin_not_set")
    if not passwords.verify_pin(pin or "", staff["pin_hash"]):
        ratelimit.hit(cur, dev_key, limit=SUP_PIN_DEVICE[0], window_s=SUP_PIN_DEVICE[1])
        ratelimit.hit(cur, staff_key, limit=SUP_PIN_STAFF[0], window_s=SUP_PIN_STAFF[1])
        left = ratelimit.remaining(cur, dev_key, limit=SUP_PIN_DEVICE[0], window_s=SUP_PIN_DEVICE[1])
        audit(cur, ctx, "service.sos.pin_failed", "staff_member", str(staff_id), hotel_slug=slug)
        return ClefsError(401, f"Wrong PIN ({left} tries left)", "invalid_pin")
    ratelimit.reset(cur, dev_key)
    if not can_supervise(cur, ctx, staff_id):
        return ClefsError(403, "Only a supervisor or the duty manager can cancel an SOS", "supervisor_required")
    return None


def find_sos(cur: Any, hotel_slug: str, ref: str, *, lock: bool = False) -> dict | None:
    """An incident by id, or by the device outbox's client_event_id (the
    device knows only that until its SOS has synced)."""
    ref = (ref or "").strip()
    if ref.isdigit():
        return get(cur, hotel_slug, int(ref), lock=lock)
    try:
        cid = str(uuid.UUID(ref))
    except ValueError:
        return None
    cur.execute("SELECT * FROM svc_incidents WHERE client_event_id = %s AND hotel_slug = %s" + (" FOR UPDATE" if lock else ""),
                (cid, hotel_slug))
    r = cur.fetchone()
    return dict(r) if r else None


def cancel_sos(cur: Any, ctx: Any, ref: str, *, supervisor_staff_id: int, note: str | None = None) -> tuple[dict, bool]:
    """Cancel (false alarm) an open staff SOS once verify_supervisor_pin
    passed. -> (incident, changed). Already closed or cancelled: returned
    unchanged (a double tap, or the console got there first)."""
    slug = ctx.hotel.slug
    inc = find_sos(cur, slug, ref, lock=True)
    if inc is None:
        raise ClefsError(404, "This SOS has not reached the server yet. Try again in a moment.", "incident_not_found")
    if inc["kind"] != "staff_sos":
        raise ClefsError(409, "Only a staff SOS can be cancelled here", "not_an_sos")
    if inc["status"] != "open":
        return inc, False
    out = close(cur, ctx, inc["id"], staff_id=supervisor_staff_id, status="cancelled",
                note=(note or "Cancelled with a supervisor PIN")[:1000])
    return out, True

# ─── Lost and found ───

LF_KEYS = ("id", "found_at", "location", "room", "description", "status", "file_id", "claimed_by", "created_by", "updated_at")
LF_STATUSES = ("stored", "claimed", "returned", "disposed")


def lf_out(r: dict) -> dict:
    out = {k: r.get(k) for k in LF_KEYS}
    for k in ("found_at", "updated_at"):
        out[k] = r[k].isoformat() if r.get(k) else None
    return out


class LostFoundIn(BaseModel):
    description: str = Field(..., min_length=1, max_length=1000)
    location: str | None = Field(None, max_length=200)
    room: str | None = Field(None, max_length=64)
    file_id: str | None = Field(None, max_length=64)  # a photo uploaded through /hotels/{slug}/files
    client_event_id: str | None = None
    client_at: datetime | None = None


def log_found(cur: Any, ctx: Any, body: LostFoundIn, *, staff_id: int | None) -> tuple[dict, bool]:
    slug = ctx.hotel.slug
    cid = _uuid(body.client_event_id)
    if cid:
        cur.execute("SELECT * FROM svc_lost_found WHERE client_event_id = %s AND hotel_slug = %s", (cid, slug))
        prior = cur.fetchone()
        if prior:
            return dict(prior), True
    # the device clock is not trusted for ordering, but an item found while
    # offline keeps its own time (never in the future)
    found_at = min(body.client_at, clock.now()) if body.client_at else clock.now()
    cur.execute(
        """INSERT INTO svc_lost_found (hotel_slug, found_at, location, room, description, file_id, created_by,
               client_event_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (client_event_id) WHERE client_event_id IS NOT NULL DO NOTHING RETURNING *""",
        (slug, found_at, body.location, body.room, body.description.strip(), body.file_id,
         Json(actor_json(ctx, staff_id)), cid),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute("SELECT * FROM svc_lost_found WHERE client_event_id = %s", (cid,))
        return dict(cur.fetchone()), True
    audit(cur, ctx, "service.lost_found.create", "svc_lost_found", str(row["id"]),
          after={"room": body.room, "location": body.location}, hotel_slug=slug)
    return dict(row), False


class LostFoundPatch(BaseModel):
    status: Literal["stored", "claimed", "returned", "disposed"] | None = None
    claimed_by: str | None = Field(None, max_length=200)
    location: str | None = Field(None, max_length=200)
    description: str | None = Field(None, min_length=1, max_length=1000)


def update_found(cur: Any, ctx: Any, item_id: int, body: LostFoundPatch) -> dict:
    slug = ctx.hotel.slug
    cur.execute("SELECT * FROM svc_lost_found WHERE id = %s AND hotel_slug = %s FOR UPDATE", (item_id, slug))
    before = cur.fetchone()
    if before is None:
        raise ClefsError(404, "Item not found", "item_not_found")
    changes = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not changes:
        return dict(before)
    cols = list(changes)
    cur.execute(
        f"UPDATE svc_lost_found SET {', '.join(f'{c} = %s' for c in cols)}, updated_at = NOW() WHERE id = %s RETURNING *",
        [changes[c] for c in cols] + [item_id],
    )
    row = dict(cur.fetchone())
    audit(cur, ctx, "service.lost_found.update", "svc_lost_found", str(item_id),
          before={c: before[c] for c in cols}, after=changes, hotel_slug=slug)
    return row


def list_found(cur: Any, hotel_slug: str, *, status: str | None = "stored", limit: int = 200) -> list[dict]:
    sql, params = "SELECT * FROM svc_lost_found WHERE hotel_slug = %s", [hotel_slug]
    if status and status != "all":
        sql += " AND status = %s"
        params.append(status)
    cur.execute(sql + " ORDER BY found_at DESC LIMIT %s", [*params, max(1, min(limit, 500))])
    return [dict(r) for r in cur.fetchall()]


# ─── Routes ───

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service-incidents"])


@router.get("/incidents")
def get_incidents(slug: str, status: Literal["open", "closed", "cancelled", "all"] = "open",
                  limit: int = Query(100, ge=1, le=500),
                  ctx: HotelCtx = Depends(require_perm("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [incident_out(r) for r in list_incidents(cur, slug, status=status, limit=limit)]


class CloseIn(BaseModel):
    status: Literal["closed", "cancelled"] = "closed"
    note: str | None = Field(None, max_length=1000)


@router.post("/incidents/{incident_id}/close")
def close_incident(slug: str, incident_id: int, body: CloseIn,
                   ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    with db.db() as cur:
        return incident_out(close(cur, ctx, incident_id, staff_id=None, status=body.status, note=body.note))


@router.get("/lost-found")
def get_lost_found(slug: str, status: Literal["stored", "claimed", "returned", "disposed", "all"] = "stored",
                   ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [lf_out(r) for r in list_found(cur, slug, status=status)]


@router.post("/lost-found")
def post_lost_found(slug: str, body: LostFoundIn, ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    with db.db() as cur:
        row, replayed = log_found(cur, ctx, body, staff_id=None)
        return {**lf_out(row), "replayed": replayed}


@router.patch("/lost-found/{item_id}")
def patch_lost_found(slug: str, item_id: int, body: LostFoundPatch,
                     ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    with db.db() as cur:
        return lf_out(update_found(cur, ctx, item_id, body))
