"""
The request state machine (03 spec 1.2).

Every change to a request's state goes through transition(): it locks the
row (SELECT ... FOR UPDATE), checks the table below, bumps `version`, derives
the legacy `status` in the same UPDATE, recomputes the SLA due times when the
clock restarts, and writes one svc_request_events row plus the audit row.

Why the legacy status is written here and not only by the 0100 trigger: the
old staff page filters `status !== "completed"`, and a cancelled request must
never look active to it. The trigger only covers writers that know nothing of
v2 (raw INSERTs, old scripts).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from clefs import audit as audit_mod
from clefs import events
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.service import config
from clefs.modules.service import constants as C

# ─── Actors ───

# Who may reopen a cancelled request, or reopen a completed one after 24 h.
MANAGER_ROLES = frozenset({"owner", "gm", "dept_head", "front_desk", "supervisor", "duty_manager"})
REOPEN_WINDOW_MS = 24 * 60 * 60 * 1000
# A device batch older than this is a late sync (analytics may use client_at).
LATE_SYNC_MS = 120_000


@dataclass(frozen=True)
class Actor:
    """Who moved a request. `type`: user | device | staff | guest | system |
    legacy_client. `principal` (a Principal or HotelCtx) feeds the audit row."""

    type: str
    staff_id: int | None = None
    user_id: int | None = None
    device_id: int | None = None
    label: str | None = None
    role: str | None = None
    principal: Any = field(default=None, compare=False, repr=False)

    @property
    def is_manager(self) -> bool:
        return self.type == "system" or (self.role or "") in MANAGER_ROLES

    @classmethod
    def from_ctx(cls, ctx: Any, *, staff_id: int | None = None) -> "Actor":
        """A console user or a paired device (with the PIN-verified staff id)."""
        p = getattr(ctx, "principal", ctx)
        kind = getattr(p, "kind", None)
        role = getattr(ctx, "role", None)
        if kind == "device":
            return cls("device", staff_id=staff_id, device_id=p.device_id, label=getattr(p, "label", None), role=role or "device", principal=ctx)
        if kind == "user":
            return cls("user", staff_id=staff_id, user_id=p.user_id, label=getattr(p, "label", None) or getattr(p, "email", None),
                       role=role or ("owner" if getattr(p, "is_clefs_admin", False) else None), principal=ctx)
        # anonymous visitor of a legacy_open_access hotel (the old dashboard)
        return cls("legacy_client", staff_id=staff_id, label="legacy dashboard", role="front_desk", principal=ctx)


SYSTEM = Actor("system", label="system")
GUEST = Actor("guest", label="guest")

# ─── Transition table ───
# state -> {action: to_state}. Anything else is 409 illegal_transition.

TRANSITIONS: dict[str, dict[str, str]] = {
    "scheduled": {"release": "new", "cancel": "cancelled"},
    "new": {"assign": "assigned", "cancel": "cancelled"},
    "reopened": {"assign": "assigned", "cancel": "cancelled"},
    "assigned": {"ack": "acknowledged", "reassign": "assigned", "complete": "completed", "cancel": "cancelled", "unassign": "new"},
    "acknowledged": {"start": "in_progress", "complete": "completed", "reassign": "assigned", "decline": "assigned",
                     "cancel": "cancelled", "unassign": "new"},
    "in_progress": {"complete": "completed", "handoff": "assigned", "reassign": "assigned", "cancel": "cancelled", "unassign": "new"},
    "completed": {"reopen": "reopened", "confirm": "completed"},
    "cancelled": {"reopen": "reopened"},
}
# The old dashboard could mark any open card done ("Resolve" on an unassigned
# card). Only the legacy adapter may do that; v2 clients assign first.
LEGACY_EXTRA: dict[str, dict[str, str]] = {
    "new": {"complete": "completed"},
    "reopened": {"complete": "completed"},
    "scheduled": {"complete": "completed"},
}
ACTIONS = tuple(sorted({a for m in TRANSITIONS.values() for a in m}))
# Actions that hand the request to a (new) person: the ack clock restarts.
ASSIGNING = frozenset({"assign", "reassign", "decline", "handoff"})


class TransitionConflict(ClefsError):
    """409 with who got there first, so a device can say "Already done by
    Amina" instead of a bare error (03 spec 1.6)."""

    def __init__(self, detail: str, code: str, info: dict):
        super().__init__(409, detail, code)
        self.info = info


def allowed(state: str, action: str, *, legacy: bool = False) -> str | None:
    """The target state, or None when the action is illegal from `state`."""
    to = TRANSITIONS.get(state, {}).get(action)
    if to is None and legacy:
        to = LEGACY_EXTRA.get(state, {}).get(action)
    return to


def legacy_status(state: str) -> str:
    return C.LEGACY_STATUS[state]


# ─── Rows and events ───


def load(cur: Any, hotel_slug: str, request_id: int, *, lock: bool = False) -> dict | None:
    cur.execute(
        f"SELECT * FROM staff_requests WHERE id = %s AND hotel_slug = %s{' FOR UPDATE' if lock else ''}",
        (request_id, hotel_slug),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def record_event(
    cur: Any,
    row: dict,
    kind: str,
    actor: Actor,
    *,
    action: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    payload: dict | None = None,
    client_event_id: str | None = None,
    client_at: Any = None,
) -> dict:
    """One svc_request_events row: the cursor feed devices and dashboards
    poll (polling first, SSE as an accelerator, 03 spec 0.4). Floor and
    department are copied so a device filters its scope without a join."""
    late = False
    if client_at is not None:
        late = (clock.now() - client_at).total_seconds() * 1000 > LATE_SYNC_MS
    cur.execute(
        """
        INSERT INTO svc_request_events (hotel_slug, request_id, kind, action, from_state, to_state, version,
            actor_type, actor_staff_id, actor_user_id, device_id, actor_label, client_event_id, client_at,
            late_sync, floor, department_key, payload, at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            row["hotel_slug"], row["id"], kind, action, from_state, to_state, row.get("version"),
            actor.type, actor.staff_id, actor.user_id, actor.device_id, (actor.label or "")[:200] or None,
            client_event_id, client_at, late, row.get("floor"), row.get("department_key"),
            Json(payload or {}), clock.now(),
        ),
    )
    return dict(cur.fetchone())


def event_by_client_id(cur: Any, client_event_id: str) -> dict | None:
    cur.execute("SELECT * FROM svc_request_events WHERE client_event_id = %s", (client_event_id,))
    r = cur.fetchone()
    return dict(r) if r else None


def events_after(
    cur: Any,
    hotel_slug: str,
    cursor: int = 0,
    *,
    floors: tuple[str, ...] | list[str] = (),
    departments: tuple[str, ...] | list[str] = (),
    request_id: int | None = None,
    limit: int = 200,
) -> list[dict]:
    """The feed after `cursor` (an event id), oldest first. A scoped device
    (floors / department keys) sees its own events plus the ones with no
    floor or department (a lobby request, a hotel-wide notice)."""
    sql = ["SELECT * FROM svc_request_events WHERE hotel_slug = %s AND id > %s"]
    params: list[Any] = [hotel_slug, int(cursor or 0)]
    if request_id is not None:
        sql.append("AND request_id = %s")
        params.append(request_id)
    if floors:
        sql.append("AND (floor IS NULL OR floor = ANY(%s))")
        params.append(list(floors))
    if departments:
        sql.append("AND (department_key IS NULL OR department_key = ANY(%s))")
        params.append(list(departments))
    sql.append("ORDER BY id LIMIT %s")
    params.append(max(1, min(int(limit), 500)))
    cur.execute(" ".join(sql), params)
    return [dict(r) for r in cur.fetchall()]


def publish_changed(cur: Any, row: dict, kind: str) -> None:
    """Legacy dashboards refetch on requests_changed; v2 clients get the id,
    state and version and fetch the card (payloads stay small)."""
    slug = row["hotel_slug"]
    events.publish(slug, "requests_changed", {}, cur=cur)
    events.publish(
        slug,
        "service.request_changed",
        {"id": row["id"], "kind": kind, "state": row.get("state"), "version": row.get("version")},
        cur=cur,
    )


def _audit(cur: Any, actor: Actor, action: str, row: dict, before: dict | None, meta: dict | None = None) -> None:
    keep = ("state", "status", "version", "assigned_staff_id", "assigned_to", "assigned_device_id", "severity", "priority")
    audit_mod.audit(
        cur,
        actor.principal,
        f"service.request.{action}",
        "staff_request",
        row["id"],
        before={k: before.get(k) for k in keep} if before else None,
        after={k: row.get(k) for k in keep},
        meta={"actor_type": actor.type, "staff_id": actor.staff_id, "device_id": actor.device_id, **(meta or {})},
        hotel_slug=row["hotel_slug"],
    )


# ─── SLA clock ───


def sla_fields(cur: Any, hotel: Any, row: dict, *, start_ms: int) -> dict:
    """ack/resolve due times and the matched policy, counted from start_ms."""
    policy = config.sla_policy(
        cur, hotel.slug, request_type_key=row.get("request_type_key"),
        department_key=row.get("department_key"), severity=row.get("severity"),
    )
    ack_due, resolve_due = config.due_times(hotel, policy, start_ms=start_ms)
    return {"ack_due_at": ack_due, "resolve_due_at": resolve_due, "sla_policy_ref": policy["ref"]}


def _restart_ladder(cur: Any, hotel: Any, row: dict, now_ms: int, *, resolve_too: bool) -> dict:
    """A new person (or a reopen / release) restarts the ack ladder: level 0,
    a new cycle so the sweep's per-cycle escalation rows start again."""
    sla = sla_fields(cur, hotel, row, start_ms=now_ms)
    out = {
        "ladder_started_at": now_ms,
        "ladder_cycle": int(row.get("ladder_cycle") or 0) + 1,
        "escalation_level": 0,
        "ack_due_at": sla["ack_due_at"],
    }
    if resolve_too:
        out["resolve_due_at"] = sla["resolve_due_at"]
        out["sla_policy_ref"] = sla["sla_policy_ref"]
    return out


def _staff_name(cur: Any, hotel_slug: str, staff_id: int | None) -> str | None:
    if staff_id is None:
        return None
    cur.execute("SELECT name FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, hotel_slug))
    r = cur.fetchone()
    if r is None:
        raise ClefsError(404, "Staff member not found", "staff_not_found")
    return r["name"]


# ─── transition() ───


def transition(
    cur: Any,
    hotel_slug: str,
    request_id: int,
    action: str,
    actor: Actor,
    *,
    expected_version: int | None = None,
    client_event_id: str | None = None,
    client_at: Any = None,
    target: dict | None = None,
    note: str | None = None,
    resolution_code: str | None = None,
    resolved_at: int | None = None,
    legacy: bool = False,
    publish: bool = True,
    extra: dict | None = None,
) -> dict:
    """Apply one action. -> {row, event, replayed}.

    `target` ({staff_id, device_id}) names the new holder for assign /
    reassign / decline / handoff; without one, routing picks (excluding the
    person who declined or handed off). `resolved_at` is the legacy PATCH's
    own completion instant (the old dashboard sends it). A replayed
    `client_event_id` returns the original outcome and changes nothing.
    """
    if client_event_id:
        try:
            client_event_id = str(uuid.UUID(str(client_event_id)))
        except ValueError as exc:
            raise ClefsError(400, "client_event_id must be a UUID", "bad_request") from exc
        prior = event_by_client_id(cur, client_event_id)
        if prior is not None:
            return {"row": load(cur, hotel_slug, prior["request_id"]), "event": prior, "replayed": True}
    row = load(cur, hotel_slug, request_id, lock=True)
    if row is None:
        raise ClefsError(404, "Request not found", "request_not_found")
    state = row.get("state") or "new"
    if expected_version is not None and int(expected_version) != int(row["version"]):
        raise _conflict(cur, row, "The request changed since you loaded it", "version_conflict")
    to = allowed(state, action, legacy=legacy)
    if to is None:
        if state in C.TERMINAL_STATES and action in ("complete", "cancel", "ack", "start"):
            raise _conflict(cur, row, "This request is already closed", "already_closed")
        raise ClefsError(409, f"Cannot {action} a request that is {state}", "illegal_transition")
    now_ms = clock.now_ms()
    if action == "reopen":
        _check_reopen(row, actor, now_ms)
    hotel = get_hotel(cur, hotel_slug)
    changes = _changes(cur, hotel, row, action, to, actor, now_ms, target=target, resolution_code=resolution_code,
                       resolved_at=resolved_at)
    changes.update({"state": to, "status": legacy_status(to), "version": int(row["version"]) + 1})
    cols = list(changes)
    cur.execute(
        f"UPDATE staff_requests SET {', '.join(f'{c} = %s' for c in cols)} WHERE id = %s RETURNING *",
        [Json(changes[c]) if isinstance(changes[c], dict) else changes[c] for c in cols] + [row["id"]],
    )
    new = dict(cur.fetchone())
    payload: dict[str, Any] = dict(extra or {})
    if note:
        payload["note"] = note[:2000]
    for k in ("assigned_staff_id", "assigned_device_id", "resolution_code"):
        if k in changes:
            payload[k] = changes[k]
    if changes.get("flags", {}).get("took_over_from") and changes["flags"] != row.get("flags"):
        payload["took_over_from"] = changes["flags"]["took_over_from"]
    ev = record_event(cur, new, "transition", actor, action=action, from_state=state, to_state=to, payload=payload,
                      client_event_id=client_event_id, client_at=client_at)
    _audit(cur, actor, action, new, row, {"note": note} if note else None)
    # Subscribers (service.api.on_request_state: Upsell's order follows its
    # linked request) hear every real transition, each in its own savepoint.
    # A replay returned above and never gets here: nothing changed, so
    # nobody is told twice.
    from clefs.modules.service import hooks

    if hooks.subscribers():
        hooks.fire(cur, hooks.change_of(new, action=action, from_state=state, to_state=to, actor_type=actor.type,
                                        event_id=ev.get("id")))
    if publish:
        publish_changed(cur, new, action)
    return {"row": new, "event": ev, "replayed": False}


def _conflict(cur: Any, row: dict, detail: str, code: str) -> TransitionConflict:
    """Who closed or last moved it, for "Already done by Amina"."""
    by = row.get("assigned_to")
    if row.get("completed_by_staff_id"):
        cur.execute("SELECT name FROM staff_members WHERE id = %s", (row["completed_by_staff_id"],))
        r = cur.fetchone()
        by = r["name"] if r else by
    at = row.get("resolved_at") or row.get("cancelled_at") or row.get("assigned_at")
    info = {"request_id": row["id"], "state": row.get("state"), "version": row.get("version"), "by": by, "at": at}
    if row.get("state") == "completed" and by:
        detail = f"Already done by {by}"
    return TransitionConflict(detail, code, info)


def _check_reopen(row: dict, actor: Actor, now_ms: int) -> None:
    """Completed: the guest ("not resolved"), anyone within 24 h, a manager
    any time. Cancelled: a manager only."""
    if row.get("state") == "cancelled":
        if not actor.is_manager:
            raise ClefsError(403, "Only a manager can reopen a cancelled request", "forbidden")
        return
    if actor.type in ("guest", "system") or actor.is_manager:
        return
    done = row.get("resolved_at") or 0
    if done and now_ms - int(done) > REOPEN_WINDOW_MS:
        raise ClefsError(403, "Only a manager can reopen a request closed more than 24 hours ago", "forbidden")


def _holder(cur: Any, hotel: Any, row: dict, action: str, target: dict | None) -> dict:
    """{staff_id, device_id} of the new holder. Without a target, routing
    picks, never handing a decline or handoff back to the same person."""
    if target and (target.get("staff_id") or target.get("device_id") or target.get("name")):
        # name only: the old dashboard's free-text assignee, not a staff row
        return {"staff_id": target.get("staff_id"), "device_id": target.get("device_id"), "name": target.get("name")}
    from clefs.modules.service import routing  # routing never imports state at module level

    exclude = [row["assigned_staff_id"]] if action in ("decline", "handoff", "reassign") and row.get("assigned_staff_id") else []
    pick = routing.pick(cur, hotel, row, exclude_staff=exclude)
    if pick is None:
        raise ClefsError(409, "Nobody is available to take this request", "no_assignee")
    return {"staff_id": pick.get("staff_id"), "device_id": pick.get("device_id")}


def _changes(
    cur: Any, hotel: Any, row: dict, action: str, to: str, actor: Actor, now_ms: int, *,
    target: dict | None, resolution_code: str | None, resolved_at: int | None,
) -> dict:
    ch: dict[str, Any] = {}
    flags = dict(row.get("flags") or {})
    if action in ASSIGNING:
        h = _holder(cur, hotel, row, action, target)
        ch["assigned_staff_id"] = h["staff_id"]
        ch["assigned_device_id"] = h["device_id"] if not h["staff_id"] else None
        ch["assigned_to"] = _staff_name(cur, hotel.slug, h["staff_id"]) if h["staff_id"] else (h.get("name") or None)
        # accepted_at is the documented deprecated alias of assigned_at, so
        # the legacy analytics keep meaning what they always meant.
        ch["assigned_at"] = ch["accepted_at"] = now_ms
        if action != "assign":
            # A new person after a decline, handoff or reassignment gets a
            # fresh ack clock. The first assignment keeps the one counted
            # from creation (or release / reopen): the guest has been
            # waiting since then, whoever picks it up.
            ch.update(_restart_ladder(cur, hotel, row, now_ms, resolve_too=False))
    elif action == "ack":
        ch["acknowledged_at"] = row.get("acknowledged_at") or now_ms
        ch["ack_implicit"] = False
        if actor.staff_id and actor.staff_id != row.get("assigned_staff_id"):
            # A shared-device assignment is claimed by the first person who
            # acks it (per-staff attribution); an ack by someone else takes over.
            if row.get("assigned_staff_id"):
                flags["took_over_from"] = row["assigned_staff_id"]
            ch["assigned_staff_id"] = actor.staff_id
            ch["assigned_to"] = _staff_name(cur, hotel.slug, actor.staff_id)
            ch["assigned_device_id"] = None
    elif action == "start":
        ch["started_at"] = now_ms
        ch["acknowledged_at"] = row.get("acknowledged_at") or now_ms
    elif action == "complete":
        ch["resolved_at"] = resolved_at if resolved_at is not None else now_ms
        ch["completed_by_staff_id"] = actor.staff_id or row.get("assigned_staff_id")
        if resolution_code:
            ch["resolution_code"] = resolution_code[:60]
        if not row.get("acknowledged_at") and row.get("state") in ("assigned", "acknowledged", "in_progress"):
            # Completing without an ack is an implicit ack (03 spec 1.2).
            ch["acknowledged_at"] = now_ms
            ch["ack_implicit"] = True
    elif action == "cancel":
        ch["cancelled_at"] = now_ms
        ch["resolution_code"] = "cancelled"
    elif action == "reopen":
        ch["reopen_count"] = int(row.get("reopen_count") or 0) + 1
        ch["resolved_at"] = None
        ch["cancelled_at"] = None
        ch["resolution_code"] = None
        if actor.type == "guest":
            ch["guest_confirmation"] = "no"
            ch["guest_confirmed_at"] = now_ms
        ch.update(_restart_ladder(cur, hotel, row, now_ms, resolve_too=True))
    elif action == "confirm":
        ch["guest_confirmation"] = "yes"
        ch["guest_confirmed_at"] = now_ms
    elif action == "release":
        ch.update(_restart_ladder(cur, hotel, row, now_ms, resolve_too=True))
    elif action == "unassign":
        ch.update({"assigned_staff_id": None, "assigned_device_id": None, "assigned_to": None,
                   "assigned_at": None, "accepted_at": None})
    if flags != (row.get("flags") or {}):
        ch["flags"] = flags
    return ch


__all__ = ["ACTIONS", "Actor", "GUEST", "SYSTEM", "TRANSITIONS", "TransitionConflict", "allowed", "events_after",
           "legacy_status", "load", "record_event", "transition"]


def guarded(cur: Any, name: str, fn: Any, *args: Any, **kw: Any) -> Any:
    """Run a side step (a signal, the amenity ledger) inside a savepoint: if
    it fails, the request itself is still filed. A bare try/except is not
    enough in Postgres, where one failed statement aborts the transaction."""
    sp = f"svc_{name}"
    cur.execute(f"SAVEPOINT {sp}")
    try:
        out = fn(*args, **kw)
    except Exception:
        cur.execute(f"ROLLBACK TO SAVEPOINT {sp}")
        logging.getLogger("clefs.service").exception("%s failed (request kept)", name)
        return None
    cur.execute(f"RELEASE SAVEPOINT {sp}")
    return out
