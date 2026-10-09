"""
Service recovery (03 spec 1.5): LEARN cases and gestures under a
compensation authority matrix, recorded in the actions ledger.

- A case opens for a complaint-category request, a guest "not resolved"
  (public tracker), a post-checkout complaint, or by hand. One open case per
  request (a second "not resolved" joins the first).
- The LEARN card (Listen, Empathize, Apologize, Resolve, Notify) is stored as
  booleans in `learn`; the case resolves when a manager closes it.
- A gesture is checked against the giver's authority role (staff 100
  {amenity, fnb_credit}; supervisor 300 + late checkout; duty manager adds
  room nights and upgrades; GM unlimited; money in minor units of the
  hotel's currency, Canon 2). The role is the console membership mapped
  through MEMBERSHIP_AUTHORITY, or for a device user the shift role on the
  roster now (else their job title).
  - Within authority: the gesture is given now and a ledger action of type
    service.recovery_gesture records who authorized it (approved by the
    system on the giver's authority, then marked done by its executor).
  - Above authority: the gesture waits (pending_approval), the ledger action
    asks the next role up that covers it (actions.owner_role), and the signal
    service.recovery_over_authority is raised. The approver decides on a
    device (supervisor PIN) or in the console; approval runs the executor,
    which marks the gesture given.
- The autonomy dial for service.recovery_gesture defaults to `suggest`
  (Canon: "suggest, auto only up to AED X once per stay"). A hotel that sets
  auto_within_limits with max_amount_minor lets the system give gestures up
  to that value, once per stay per room; the check is limit_checker below.
- Recovery before the review (05 spec): flag() opens (or joins) a case for a
  guest who says it is not fixed, a frustrated guest or a job that never
  arrived, with a gesture Gemini suggests (the fast model, 3 s; a sensible
  default when AI is off), kept within the front desk's own authority so any
  manager can approve it. The duty manager on shift is alerted. decide()
  approves (the gesture is recorded and reaches the guest in their
  language) or dismisses it; either way the case leaves the "Needs your
  approval" list.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator

from clefs import db, events
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm, require_perm_or_device
from clefs.core import actions, clock, money
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.service import constants as C
from clefs.modules.service import roster

log = logging.getLogger("clefs.service.recovery")

ACTION_TYPE = "service.recovery_gesture"
ROLE_RANK = {r: i for i, r in enumerate(C.AUTHORITY_ROLES)}

# ─── Authority matrix ───


def authority_matrix(cur: Any, hotel: Any) -> dict[str, dict]:
    """{role: {max_minor (None = unlimited), max_value, currency,
    allowed_kinds, source: default|hotel}} in the hotel's currency."""
    ccy = money.ccy(hotel.currency or "AED")
    out: dict[str, dict] = {}
    for role, d in C.DEFAULT_AUTHORITY.items():
        mx = None if d["max_major"] is None else money.to_minor(d["max_major"], ccy)
        out[role] = {"role_key": role, "max_minor": mx, "currency": ccy, "allowed_kinds": list(d["allowed_kinds"]),
                     "source": "default"}
    cur.execute("SELECT * FROM svc_compensation_authority WHERE hotel_slug = %s", (hotel.slug,))
    for r in cur.fetchall():
        mx = r["max_minor"]
        if mx is not None and r["currency"].strip() != ccy:
            # a row saved before a currency change: convert, never compare raw
            mx = money.convert_minor(cur, mx, r["currency"].strip(), ccy, clock.now().date())
        out[r["role_key"]] = {"role_key": r["role_key"], "max_minor": mx, "currency": ccy,
                              "allowed_kinds": list(r["allowed_kinds"] or []), "source": "hotel"}
    for row in out.values():
        row["max_value"] = money.money_str(row["max_minor"], ccy) if row["max_minor"] is not None else None
    return out


def covers(entry: dict, kind: str, amount_minor: int) -> bool:
    return kind in entry["allowed_kinds"] and (entry["max_minor"] is None or amount_minor <= entry["max_minor"])


def approver_for(matrix: dict[str, dict], role: str, kind: str, amount_minor: int) -> str | None:
    """The lowest role above `role` whose authority covers the gesture
    (None when nobody's does: the GM row was narrowed by the hotel)."""
    nxt = C.NEXT_ROLE_UP.get(role)
    while nxt:
        if covers(matrix[nxt], kind, amount_minor):
            return nxt
        nxt = C.NEXT_ROLE_UP.get(nxt)
    return None


def authority_role(cur: Any, ctx: Any, staff_id: int | None) -> str:
    """The giver's authority role. Console: the membership role mapped
    (a Clefs admin counts as GM). Device: the roster's shift role now, else
    the person's job title, else staff."""
    p = getattr(ctx, "principal", ctx)
    if getattr(p, "kind", None) == "user":
        if getattr(p, "is_clefs_admin", False) and not getattr(ctx, "role", None):
            return "gm"
        return C.MEMBERSHIP_AUTHORITY.get(getattr(ctx, "role", None) or "staff", "staff")
    if getattr(p, "kind", None) == "service":
        return "gm"
    slug = ctx.hotel.slug
    shift = roster.shift_role_of(cur, slug, staff_id)
    if shift in ("supervisor", "duty_manager"):
        return shift
    if staff_id:
        cur.execute("SELECT role_key FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, slug))
        r = cur.fetchone()
        if r and r["role_key"] in C.AUTHORITY_ROLES:
            return r["role_key"]
    return "staff"


# ─── Ledger action type ───

STAY_FALLBACK_DAYS = 14  # "once per stay" when the stay's check-in is unknown


class GestureLimits(BaseModel):
    """Limits for auto_within_limits: the system may give a gesture worth at
    most max_amount_minor (hotel currency), and only once per stay."""

    max_amount_minor: int | None = Field(None, ge=0)
    once_per_stay: bool = True


def _stay_start(cur: Any, hotel: Any, room: str | None) -> datetime:
    fallback = clock.now() - timedelta(days=STAY_FALLBACK_DAYS)
    if not room:
        return fallback
    cur.execute("SAVEPOINT svc_stay_start")
    try:
        from clefs.modules.guests import api as guests_api

        g = guests_api.guest_context_for_room(cur, hotel.slug, room) or {}
        d = date.fromisoformat(str(g.get("checkIn"))[:10]) if g.get("checkIn") else None
        cur.execute("RELEASE SAVEPOINT svc_stay_start")
    except Exception:  # noqa: BLE001 - unknown stay dates fall back to the window
        cur.execute("ROLLBACK TO SAVEPOINT svc_stay_start")
        d = None
    return htime.local_day_bounds(hotel, d)[0] if d else fallback


def _limit_checker(ctx: actions.ActionCtx, limits: Any) -> actions.LimitCheck:
    lim = limits if isinstance(limits, GestureLimits) else GestureLimits(**(limits or {}))
    amount = int(ctx.params.get("amount_minor") or 0)
    reasons: list[str] = []
    if lim.max_amount_minor is None:
        reasons.append("no max_amount_minor set")
    elif amount > lim.max_amount_minor:
        reasons.append(f"amount {amount} exceeds max_amount_minor {lim.max_amount_minor}")
    if lim.once_per_stay:
        room = ctx.params.get("room")
        ctx.cur.execute(
            """SELECT 1 FROM svc_recovery_actions g JOIN svc_recovery_cases c ON c.id = g.case_id
               WHERE g.hotel_slug = %s AND c.room IS NOT DISTINCT FROM %s AND g.status = 'given'
                 AND g.authorized_by->>'via' = 'dial' AND g.created_at >= %s AND g.id <> %s LIMIT 1""",
            (ctx.hotel_slug, room, _stay_start(ctx.cur, ctx.hotel, room), int(ctx.params.get("gesture_id") or 0)),
        )
        if ctx.cur.fetchone():
            reasons.append("a gesture was already given automatically this stay")
    return actions.LimitCheck(ok=not reasons, reasons=reasons)


def _execute(ctx: actions.ActionCtx) -> actions.ExecResult:
    """Approval (a person, the giver's own authority, or the dial) gives the
    gesture. Idempotent: an already given gesture stays as it is."""
    gid = ctx.params.get("gesture_id")
    ctx.cur.execute("SELECT * FROM svc_recovery_actions WHERE id = %s AND hotel_slug = %s FOR UPDATE",
                    (gid, ctx.hotel_slug))
    g = ctx.cur.fetchone()
    if g is None:
        return actions.ExecResult(ok=False, error="gesture_not_found")
    if g["status"] in ("rejected", "cancelled"):
        return actions.ExecResult(ok=False, error=f"gesture_{g['status']}")
    if g["status"] == "pending_approval":
        a = ctx.action
        via = "dial" if a.get("decided_via") == "dial" else (a.get("decided_via") or "console")
        auth = dict(g["authorized_by"] or {})
        if not auth or via == "dial":
            auth = {"user_id": a.get("decided_by"), "role": g["approver_role"], "via": via, "label": "approved"}
        ctx.cur.execute(
            """UPDATE svc_recovery_actions SET status = 'given', decided_at = %s, authorized_by = %s,
                   decided_by = COALESCE(decided_by, %s) WHERE id = %s""",
            (clock.now(), Json(auth), Json({"user_id": a.get("decided_by"), "via": via}), gid),
        )
    return actions.ExecResult(ok=True, data={"gesture_id": gid})


actions.register_action_type(actions.ActionType(
    key=ACTION_TYPE, module="service", label="Service recovery gesture", executor=_execute,
    default_owner_role="gm", default_level="suggest", max_level="auto", limits_model=GestureLimits,
    limit_checker=_limit_checker, decide_perm="core.actions.decide",
    description="A gesture for a guest whose problem was not put right (amenity, F&B credit, late checkout, "
                "room night, upgrade). Within the giver's authority it is recorded; above it, the next role approves.",
))

# ─── Cases ───

CASE_KEYS = ("id", "request_id", "room", "guest_ref", "category_key", "severity", "status", "reason", "learn", "notes",
             "opened_by_staff_id", "opened_by_user_id", "opened_at", "closed_at", "updated_at", "summary", "session_id",
             "guest_language", "suggested_gesture", "suggested_kind", "suggested_value_minor", "suggestion_source",
             "approval_status", "approved_gesture", "decided_at", "gesture_id")
REASONS = ("complaint", "not_resolved", "post_checkout", "manual", "frustration", "never_arrived")
# api.flag_recovery's kinds -> the case reason they open with.
FLAG_KINDS = {"not_fixed": "not_resolved", "frustration": "frustration", "never_arrived": "never_arrived"}
# ...and how a case reads back as one of them (the approvals panel's chip).
REASON_KIND = {"not_resolved": "not_fixed", "post_checkout": "not_fixed", "frustration": "frustration",
               "never_arrived": "never_arrived", "complaint": "complaint", "manual": "manual"}


def _uuid(v: Any) -> str | None:
    if v is None:
        return None
    try:
        return str(uuid.UUID(str(v)))
    except ValueError as exc:
        raise ClefsError(400, "client_event_id must be a UUID", "bad_request") from exc


def case_out(r: dict, gestures: list[dict] | None = None) -> dict:
    out = {k: r.get(k) for k in CASE_KEYS}
    for k in ("opened_at", "closed_at", "updated_at", "decided_at"):
        out[k] = r[k].isoformat() if r.get(k) else None
    learn = r.get("learn") or {}
    out["learn"] = {s: bool(learn.get(s)) for s in C.LEARN_STEPS}
    out["learn_done"] = sum(out["learn"].values())
    cat = C.COMPLAINT_CATEGORIES.get(r.get("category_key") or "", {})
    out["category_label"] = cat.get("label_en")
    out["kind"] = REASON_KIND.get(r.get("reason") or "", r.get("reason"))
    out["needs_approval"] = r.get("status") == "open" and r.get("approval_status") == "pending"
    if gestures is not None:
        out["gestures"] = gestures
        ccy = next((g["currency"] for g in gestures), None)
        given = sum(g["value_minor"] for g in gestures if g["status"] == "given")
        out["given_minor"] = given
        out["given_value"] = money.money_str(given, ccy) if ccy else "0"
    return out


def get_case(cur: Any, hotel_slug: str, case_id: int, *, lock: bool = False) -> dict | None:
    cur.execute("SELECT * FROM svc_recovery_cases WHERE id = %s AND hotel_slug = %s" + (" FOR UPDATE" if lock else ""),
                (case_id, hotel_slug))
    r = cur.fetchone()
    return dict(r) if r else None


def open_case(cur: Any, hotel_slug: str, *, actor: Any, request_id: int | None = None, room: str | None = None,
              category_key: str | None = None, severity: str | None = None, reason: str = "manual",
              notes: str | None = None, staff_id: int | None = None, user_id: int | None = None,
              guest_ref: str | None = None, learn: dict | None = None, client_event_id: Any = None) -> tuple[dict, bool]:
    """Open a case, or return the open one for the same request (or the
    client_event_id's first result). -> (case row, created)."""
    cid = _uuid(client_event_id)
    if cid:
        cur.execute("SELECT * FROM svc_recovery_cases WHERE client_event_id = %s AND hotel_slug = %s", (cid, hotel_slug))
        prior = cur.fetchone()
        if prior:
            return dict(prior), False
    req = None
    if request_id is not None:
        cur.execute("SELECT id, room, department_key, severity FROM staff_requests WHERE id = %s AND hotel_slug = %s",
                    (request_id, hotel_slug))
        req = cur.fetchone()
        if req is None:
            raise ClefsError(404, "Request not found", "request_not_found")
        cur.execute("SELECT * FROM svc_recovery_cases WHERE request_id = %s AND status = 'open'", (request_id,))
        existing = cur.fetchone()
        if existing:
            return dict(existing), False
    if category_key and category_key not in C.COMPLAINT_CATEGORIES and not _hotel_category(cur, hotel_slug, category_key):
        raise ClefsError(400, "Unknown complaint category", "unknown_category")
    reason = reason if reason in REASONS else "manual"
    learn_in = {s: bool((learn or {}).get(s)) for s in C.LEARN_STEPS}
    cur.execute(
        """INSERT INTO svc_recovery_cases (hotel_slug, request_id, room, guest_ref, category_key, severity, reason, learn,
               notes, opened_by_staff_id, opened_by_user_id, client_event_id, opened_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT DO NOTHING RETURNING *""",
        (hotel_slug, request_id, room or (req["room"] if req else None), guest_ref, category_key,
         severity or (req["severity"] if req and req["severity"] else "L2"), reason, Json(learn_in),
         (notes or "")[:4000] or None, staff_id, user_id, cid, clock.now(), clock.now()),
    )
    row = cur.fetchone()
    if row is None:  # a concurrent open for the same request (or replay) won
        cur.execute("SELECT * FROM svc_recovery_cases WHERE (request_id = %s AND status = 'open') OR client_event_id = %s",
                    (request_id, cid))
        return dict(cur.fetchone()), False
    row = dict(row)
    audit(cur, actor, "service.recovery_case.open", "svc_recovery_cases", str(row["id"]),
          after={"request_id": request_id, "room": row["room"], "reason": reason, "category_key": category_key},
          hotel_slug=hotel_slug)
    events.publish(hotel_slug, "service.recovery_changed", {"case_id": row["id"], "status": "open"},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)
    return row, True


def _hotel_category(cur: Any, hotel_slug: str, key: str) -> bool:
    cur.execute("SELECT 1 FROM svc_complaint_categories WHERE hotel_slug = %s AND key = %s AND active", (hotel_slug, key))
    return cur.fetchone() is not None


class CasePatch(BaseModel):
    learn: dict[str, bool] | None = None
    notes: str | None = Field(None, max_length=4000)
    status: Literal["open", "resolved", "closed"] | None = None
    category_key: str | None = Field(None, max_length=40)


def update_case(cur: Any, hotel_slug: str, case_id: int, patch: CasePatch, *, actor: Any) -> dict:
    """LEARN ticks merge (a device may tick one box at a time); resolving or
    closing stamps closed_at."""
    row = get_case(cur, hotel_slug, case_id, lock=True)
    if row is None:
        raise ClefsError(404, "Case not found", "case_not_found")
    changes: dict[str, Any] = {}
    if patch.learn:
        unknown = set(patch.learn) - set(C.LEARN_STEPS)
        if unknown:
            raise ClefsError(400, f"Unknown LEARN step {sorted(unknown)[0]}", "bad_request")
        changes["learn"] = {**(row.get("learn") or {}), **patch.learn}
    if patch.notes is not None:
        changes["notes"] = patch.notes
    if patch.category_key is not None:
        changes["category_key"] = patch.category_key
    if patch.status and patch.status != row["status"]:
        changes["status"] = patch.status
        changes["closed_at"] = None if patch.status == "open" else clock.now()
    if not changes:
        return row
    cols = list(changes)
    cur.execute(
        f"UPDATE svc_recovery_cases SET {', '.join(f'{c} = %s' for c in cols)}, updated_at = NOW() WHERE id = %s RETURNING *",
        [Json(changes[c]) if isinstance(changes[c], dict) else changes[c] for c in cols] + [case_id],
    )
    new = dict(cur.fetchone())
    audit(cur, actor, "service.recovery_case.update", "svc_recovery_cases", str(case_id),
          before={c: row.get(c) for c in cols if c != "closed_at"}, after={c: new.get(c) for c in cols if c != "closed_at"},
          hotel_slug=hotel_slug)
    return new


def list_cases(cur: Any, hotel_slug: str, *, status: str = "open", room: str | None = None, limit: int = 100) -> list[dict]:
    sql, params = ["SELECT * FROM svc_recovery_cases WHERE hotel_slug = %s"], [hotel_slug]
    if status != "all":
        sql.append("AND status = %s")
        params.append(status)
    if room:
        sql.append("AND room = %s")
        params.append(room)
    sql.append("ORDER BY opened_at DESC LIMIT %s")
    params.append(max(1, min(limit, 500)))
    cur.execute(" ".join(sql), params)
    return [dict(r) for r in cur.fetchall()]

# ─── Gestures ───

GESTURE_KEYS = ("id", "case_id", "kind", "description", "value_minor", "currency", "status", "within_authority",
                "authorized_by", "requested_by", "approver_role", "action_id", "created_at", "decided_at",
                "decided_by", "decision_note")
# a ledger action in one of these states will never give the gesture
ACTION_DEAD = {"rejected": "rejected", "cancelled": "cancelled", "expired": "cancelled", "failed": "cancelled",
               "observed": "cancelled"}


def gesture_out(g: dict) -> dict:
    out = {k: g.get(k) for k in GESTURE_KEYS}
    out["value"] = money.money_str(g["value_minor"], g["currency"].strip())
    out["currency"] = g["currency"].strip()
    for k in ("created_at", "decided_at"):
        out[k] = g[k].isoformat() if g.get(k) else None
    return out


def _who(ctx: Any, staff_id: int | None, role: str, via: str) -> dict:
    p = getattr(ctx, "principal", ctx)
    who: dict[str, Any] = {"role": role, "via": via, "label": getattr(p, "label", None)}
    if staff_id:
        who["staff_id"] = staff_id
    if getattr(p, "user_id", None):
        who["user_id"] = p.user_id
    if getattr(p, "device_id", None):
        who["device_id"] = p.device_id
    return who


def gestures_for(cur: Any, hotel_slug: str, case_id: int) -> list[dict]:
    cur.execute("SELECT * FROM svc_recovery_actions WHERE case_id = %s AND hotel_slug = %s ORDER BY id", (case_id, hotel_slug))
    rows = [dict(r) for r in cur.fetchall()]
    return [reconcile(cur, g) for g in rows]


def reconcile(cur: Any, g: dict) -> dict:
    """A pending gesture whose ledger action was ignored in the console,
    expired or failed is closed on read (the actions ledger has no callback
    into modules). An approved action the executor has not reached yet is
    left for the executor."""
    if g["status"] != "pending_approval" or not g.get("action_id"):
        return g
    cur.execute("SELECT status, decided_by FROM actions WHERE id = %s", (g["action_id"],))
    a = cur.fetchone()
    new = ACTION_DEAD.get(a["status"]) if a else None
    if new is None:
        return g
    cur.execute(
        """UPDATE svc_recovery_actions SET status = %s, decided_at = COALESCE(decided_at, NOW()),
               decided_by = COALESCE(decided_by, %s) WHERE id = %s AND status = 'pending_approval' RETURNING *""",
        (new, Json({"user_id": a["decided_by"], "via": "ledger", "action_status": a["status"]}), g["id"]),
    )
    r = cur.fetchone()
    return dict(r) if r else g


def _ledger(cur: Any, hotel: Any, g: dict, case: dict, *, title: str, owner_role: str | None, dedupe: str) -> dict:
    return actions.create_action(
        cur, hotel.slug, type=ACTION_TYPE, title=title[:300],
        params={"gesture_id": g["id"], "case_id": case["id"], "request_id": case.get("request_id"), "room": case.get("room"),
                "kind": g["kind"], "amount_minor": g["value_minor"], "within_authority": g["within_authority"],
                "approver_role": g.get("approver_role")},
        expected_minor=g["value_minor"], currency=g["currency"].strip(), owner_role=owner_role,
        dedupe_key=dedupe, actor=actions.SYSTEM, via="system",
    )


class _Approver:
    """The person approving, as the actions ledger sees them. Their right to
    approve THIS gesture is the authority matrix, checked here before the
    call (core.actions.decide's generic permission, owner/GM only, would
    refuse a supervisor on a device). Passing the person, not SYSTEM, keeps
    the decision human: the executor honours it and the audit names them."""

    def __init__(self, ctx: Any):
        self.principal = getattr(ctx, "principal", ctx)
        self.hotel = getattr(ctx, "hotel", None)
        self.role = getattr(ctx, "role", None)

    def can(self, *perms: str) -> bool:
        return True


def _label(kind: str, value: str, ccy: str) -> str:
    return f"{kind.replace('_', ' ')} worth {ccy} {value}"


def add_gesture(cur: Any, ctx: Any, case_id: int, *, kind: str, amount_minor: int, description: str | None = None,
                staff_id: int | None = None, client_event_id: Any = None, via: str = "console") -> tuple[dict, bool]:
    """Give (within authority) or request (above it) a gesture on a case.
    -> (gesture, replayed)."""
    slug = ctx.hotel.slug
    cid = _uuid(client_event_id)
    if cid:
        cur.execute("SELECT * FROM svc_recovery_actions WHERE client_event_id = %s AND hotel_slug = %s", (cid, slug))
        prior = cur.fetchone()
        if prior:
            return dict(prior), True
    if kind not in C.GESTURE_KINDS:
        raise ClefsError(400, f"kind must be one of {', '.join(C.GESTURE_KINDS)}", "bad_request")
    if amount_minor < 0:
        raise ClefsError(400, "A gesture cannot be negative", "bad_request")
    case = get_case(cur, slug, case_id, lock=True)
    if case is None:
        raise ClefsError(404, "Case not found", "case_not_found")
    if case["status"] == "closed":
        raise ClefsError(409, "This case is closed", "case_closed")
    hotel = get_hotel(cur, slug)
    matrix = authority_matrix(cur, hotel)
    role = authority_role(cur, ctx, staff_id)
    within = covers(matrix[role], kind, amount_minor)
    approver = None if within else approver_for(matrix, role, kind, amount_minor)
    if not within and approver is None:
        raise ClefsError(409, "No role may give this gesture under the authority matrix", "above_all_authority")
    who = _who(ctx, staff_id, role, via)
    ccy = matrix[role]["currency"]
    cur.execute(
        """INSERT INTO svc_recovery_actions (hotel_slug, case_id, kind, description, value_minor, currency, status,
               within_authority, authorized_by, requested_by, approver_role, client_event_id, decided_at, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (slug, case_id, kind, (description or "")[:500] or None, amount_minor, ccy,
         "given" if within else "pending_approval", within, Json(who if within else {}), Json(who), approver, cid,
         clock.now() if within else None, clock.now()),
    )
    g = dict(cur.fetchone())
    value = money.money_str(amount_minor, ccy)
    if within:
        a = _ledger(cur, hotel, g, case, title=f"Recovery gesture given: {_label(kind, value, ccy)}", owner_role=None,
                    dedupe=f"svc.gesture:{g['id']}")
        if a.get("id") and a.get("status") == "proposed":
            # the giver's own authority is the approval (recorded as such)
            actions.decide(cur, a["id"], "approve", actor=_Approver(ctx), via="api", hotel_slug=slug,
                           note=f"Within the authority of {role}")
    else:
        a = _ledger(cur, hotel, g, case, title=f"Approve recovery gesture: {_label(kind, value, ccy)}",
                    owner_role=C.APPROVER_OWNER_ROLE.get(approver, "gm"), dedupe=f"svc.gesture:{g['id']}")
        from clefs.modules.service import signals as svc_signals

        svc_signals.raise_signal(
            cur, slug, "service.recovery_over_authority", f"svc:gesture:{g['id']}",
            f"Gesture needs {approver.replace('_', ' ')} approval: {_label(kind, value, ccy)}",
            summary=f"Room {case.get('room') or '-'}: asked by a {role.replace('_', ' ')}", severity=2,
            subject_type="recovery_case", subject_id=str(case_id), due_in_min=30,
        )
    if a.get("id"):
        cur.execute("UPDATE svc_recovery_actions SET action_id = %s WHERE id = %s RETURNING *", (a["id"], g["id"]))
        g = dict(cur.fetchone())
    audit(cur, ctx, "service.recovery_gesture.give" if within else "service.recovery_gesture.request",
          "svc_recovery_actions", str(g["id"]),
          after={"case_id": case_id, "kind": kind, "value_minor": amount_minor, "currency": ccy, "role": role,
                 "within_authority": within, "approver_role": approver, "action_id": g.get("action_id")},
          hotel_slug=slug)
    events.publish(slug, "service.recovery_changed", {"case_id": case_id, "gesture_id": g["id"], "status": g["status"]},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)
    return g, False


def decide_gesture(cur: Any, ctx: Any, gesture_id: int, decision: Literal["approve", "reject"], *,
                   staff_id: int | None = None, note: str | None = None, via: str = "console") -> dict:
    """An approver's decision on a pending gesture. The decider's own
    authority must cover it (a supervisor approves up to 300, not a room
    night). Approval gives the gesture now; the ledger action is approved on
    their authority and its executor records it as done."""
    slug = ctx.hotel.slug
    cur.execute("SELECT * FROM svc_recovery_actions WHERE id = %s AND hotel_slug = %s FOR UPDATE", (gesture_id, slug))
    g = cur.fetchone()
    if g is None:
        raise ClefsError(404, "Gesture not found", "gesture_not_found")
    g = reconcile(cur, dict(g))
    if g["status"] != "pending_approval":
        raise ClefsError(409, f"This gesture is already {g['status'].replace('_', ' ')}", "already_decided")
    hotel = get_hotel(cur, slug)
    matrix = authority_matrix(cur, hotel)
    role = authority_role(cur, ctx, staff_id)
    if not covers(matrix[role], g["kind"], int(g["value_minor"])):
        raise ClefsError(403, "This gesture is above your authority", "above_authority")
    who = _who(ctx, staff_id, role, via)
    status = "given" if decision == "approve" else "rejected"
    cur.execute(
        """UPDATE svc_recovery_actions SET status = %s, decided_at = %s, decided_by = %s, decision_note = %s,
               authorized_by = CASE WHEN %s = 'given' THEN %s ELSE authorized_by END WHERE id = %s RETURNING *""",
        (status, clock.now(), Json(who), (note or "")[:1000] or None, status, Json(who), gesture_id),
    )
    new = dict(cur.fetchone())
    if g.get("action_id"):
        try:
            actions.decide(cur, g["action_id"], "approve" if decision == "approve" else "ignore", actor=_Approver(ctx),
                           via="api", hotel_slug=slug, note=f"{decision} by {role} {who.get('label') or ''}".strip())
        except ClefsError as exc:  # already decided in the console: the gesture row is the truth here
            log.info("ledger action %s not moved: %s", g["action_id"], exc.detail)
    from clefs.core import signals as core_signals

    core_signals.resolve_signal(cur, slug, f"svc:gesture:{gesture_id}", resolution=status)
    audit(cur, ctx, f"service.recovery_gesture.{decision}", "svc_recovery_actions", str(gesture_id),
          before={"status": "pending_approval"}, after={"status": status, "role": role, "note": note}, hotel_slug=slug)
    events.publish(slug, "service.recovery_changed", {"case_id": g["case_id"], "gesture_id": gesture_id, "status": status},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)
    return new

# ─── Guest "not resolved" ───


def on_not_resolved(cur: Any, hotel_slug: str, row: dict, *, post_checkout: bool, comment: str | None = None) -> dict:
    """The tracker's "no": open (or join) the request's case, and when the
    hotel set a default gesture, put one to the dial (suggest by default, so
    a person sees it; auto_within_limits may give it once per stay)."""
    from clefs.modules.service import settings as svc_settings

    case, created = open_case(cur, hotel_slug, actor=actions.SYSTEM, request_id=row["id"], room=row.get("room"),
                              reason="post_checkout" if post_checkout else "not_resolved",
                              severity=row.get("severity") or "L2", notes=comment)
    s = svc_settings.load(cur, hotel_slug)
    if created and s.gesture_default_minor > 0 and s.gesture_default_kind in C.GESTURE_KINDS:
        propose_gesture(cur, hotel_slug, case, kind=s.gesture_default_kind, amount_minor=s.gesture_default_minor)
    return case


def propose_gesture(cur: Any, hotel_slug: str, case: dict, *, kind: str, amount_minor: int) -> dict:
    """A system-proposed gesture: pending until a person (or the dial within
    its limits) approves it. The approver role is the lowest that covers it."""
    hotel = get_hotel(cur, hotel_slug)
    matrix = authority_matrix(cur, hotel)
    approver = next((r for r in C.AUTHORITY_ROLES if covers(matrix[r], kind, amount_minor)), "gm")
    ccy = matrix["staff"]["currency"]
    cur.execute(
        """INSERT INTO svc_recovery_actions (hotel_slug, case_id, kind, value_minor, currency, status, within_authority,
               requested_by, approver_role) VALUES (%s, %s, %s, %s, %s, 'pending_approval', FALSE, %s, %s) RETURNING *""",
        (hotel_slug, case["id"], kind, amount_minor, ccy, Json({"via": "system", "label": "Clefs"}), approver),
    )
    g = dict(cur.fetchone())
    value = money.money_str(amount_minor, ccy)
    a = _ledger(cur, hotel, g, case, title=f"Offer a recovery gesture: {_label(kind, value, ccy)}",
                owner_role=C.APPROVER_OWNER_ROLE.get(approver, "gm"), dedupe=f"svc.gesture:{g['id']}")
    if a.get("id"):
        cur.execute("UPDATE svc_recovery_actions SET action_id = %s WHERE id = %s RETURNING *", (a["id"], g["id"]))
        g = dict(cur.fetchone())
    elif a.get("status") == "suppressed":  # the dial is off: nothing to offer
        cur.execute("UPDATE svc_recovery_actions SET status = 'cancelled' WHERE id = %s RETURNING *", (g["id"],))
        g = dict(cur.fetchone())
    return g


# ─── Recovery before the review: flag, suggest, decide (05 spec) ───

SUGGEST_TIMEOUT_S = 3.0
# A suggestion stays within the front desk's own authority, so whoever
# approves it on the Dashboard may give it.
SUGGEST_ROLE = "staff"


class GestureSuggestion(BaseModel):
    """What the fast model proposes for the duty manager to approve."""

    gesture: str
    kind: Literal["amenity", "fnb_credit", "other"]
    value: float | None = None
    reason: str


DEFAULT_GESTURES: dict[str, tuple[str, str]] = {
    "not_fixed": ("amenity", "We are sorry it was not right the first time. As an apology, we are sending a "
                             "complimentary fruit platter to your room."),
    "frustration": ("fnb_credit", "We are sorry for the trouble. The duty manager will look after you personally, "
                                  "and your next drink at the lobby lounge is on us."),
    "never_arrived": ("amenity", "We are sorry your request did not reach you. It is on its way now, with a "
                                 "complimentary dessert as our apology."),
}


def _suggest_cap(cur: Any, hotel: Any) -> tuple[int, list[str], str]:
    """(max value in minor units, kinds, currency) a suggestion keeps to."""
    m = authority_matrix(cur, hotel)[SUGGEST_ROLE]
    cap = m["max_minor"] if m["max_minor"] is not None else money.to_minor(Decimal("100"), m["currency"])
    return int(cap), [k for k in ("amenity", "fnb_credit") if k in m["allowed_kinds"]], m["currency"]


def suggest_gesture(cur: Any, hotel: Any, *, kind: str, summary: str, room: str | None, request: dict | None) -> dict:
    """{gesture, kind, value_minor, reason, source: ai|default}: one gesture
    for the duty manager to approve, by the fast model within 3 seconds, else
    the default for the kind. Never raises."""
    from clefs import ai
    from clefs.settings import get_settings

    cap, kinds, ccy = _suggest_cap(cur, hotel)
    default_kind, default_text = DEFAULT_GESTURES.get(kind, DEFAULT_GESTURES["not_fixed"])
    fallback = {"gesture": default_text, "kind": default_kind if default_kind in kinds else "other",
                "value_minor": cap // 2 if default_kind in kinds else 0,
                "reason": "Standard gesture for this kind of problem.", "source": "default"}
    facts = {"problem": kind.replace("_", " "), "summary": (summary or "")[:500], "room": room,
             "request": (request or {}).get("item_label"), "times_reopened": (request or {}).get("reopen_count") or 0,
             "hotel": hotel.name}
    try:
        out = ai.structured(
            "service.recovery_gesture",
            system=(
                "You suggest one service-recovery gesture a hotel's duty manager can approve for an unhappy guest. "
                "kind is amenity (something sent to the room), fnb_credit (food or drink on the house) or other (no "
                f"cost, such as a personal call from the duty manager). value is in {ccy}, at most "
                f"{money.money_str(cap, ccy)}, or null when it costs nothing. Write gesture as one or two warm "
                "sentences to the guest, in English, that apologise and name the gesture; never promise refunds, "
                "discounts, upgrades or free nights. Write reason, for the manager, in one short sentence."
            ),
            input=json.dumps(facts), schema=GestureSuggestion, hotel_slug=hotel.slug,
            model=get_settings().AI_MODEL_FAST, max_output_tokens=300, timeout_s=SUGGEST_TIMEOUT_S, temperature=0.3,
        )
    except Exception as exc:  # AIDisabled, AIError, a timeout: the default
        log.info("gesture suggestion skipped for %s: %s", hotel.slug, type(exc).__name__)
        return fallback
    text = " ".join((out.gesture or "").split())[:500]
    if not text:
        return fallback
    gkind = out.kind if out.kind in kinds else "other"
    value = 0
    if gkind != "other" and out.value:
        try:
            value = min(cap, max(0, money.to_minor(Decimal(str(out.value)), ccy)))
        except (ArithmeticError, ValueError):
            value = 0
    return {"gesture": text, "kind": gkind, "value_minor": value, "reason": " ".join((out.reason or "").split())[:300],
            "source": "ai"}


def _open_for_flag(cur: Any, hotel_slug: str, *, reason: str, req: dict | None, room: str | None,
                   session_id: str | None, summary: str) -> tuple[dict, bool]:
    """The case a flag joins: the conversation's open case, else the
    request's, else the room's for the same reason in the last hour; a new
    one otherwise."""
    if session_id:
        cur.execute("SELECT * FROM svc_recovery_cases WHERE hotel_slug = %s AND session_id = %s AND status = 'open' "
                    "ORDER BY id DESC LIMIT 1", (hotel_slug, session_id))
        hit = cur.fetchone()
        if hit:
            return dict(hit), False
    if req is None and room:
        cur.execute("""SELECT * FROM svc_recovery_cases WHERE hotel_slug = %s AND room = %s AND reason = %s
                         AND status = 'open' AND opened_at > %s ORDER BY id DESC LIMIT 1""",
                    (hotel_slug, room, reason, clock.now() - timedelta(hours=1)))
        hit = cur.fetchone()
        if hit:
            return dict(hit), False
    return open_case(cur, hotel_slug, actor=actions.SYSTEM, request_id=(req or {}).get("id"), room=room,
                     reason=reason, severity=(req or {}).get("severity") or "L2", notes=summary or None,
                     guest_ref=(req or {}).get("guest_ref"))


def flag(cur: Any, hotel_slug: str, *, kind: str, room: str | None, summary: str, request_id: int | None = None,
         session_id: str | None = None, guest_language: str | None = None, post_checkout: bool = False) -> dict:
    """api.flag_recovery's body. Opens (or joins) the case, asks the fast
    model for a gesture (once per case), alerts the duty manager on shift
    and tells the console and the devices. -> flag_out(case)."""
    from clefs.modules.service import staff_lang, state
    from clefs.modules.service import settings as svc_settings

    if kind not in FLAG_KINDS:
        raise ClefsError(400, f"kind must be one of {', '.join(FLAG_KINDS)}", "bad_request")
    reason = "post_checkout" if post_checkout else FLAG_KINDS[kind]
    summary = " ".join((summary or "").split())[:1000]
    session_id = (str(session_id)[:80] if session_id else None) or None
    req = None
    if request_id is not None:
        req = state.load(cur, hotel_slug, int(request_id))
        if req is None:
            raise ClefsError(404, "Request not found", "request_not_found")
    elif session_id:
        cur.execute("SELECT * FROM staff_requests WHERE hotel_slug = %s AND session_id = %s ORDER BY id DESC LIMIT 1",
                    (hotel_slug, session_id))
        found = cur.fetchone()
        req = dict(found) if found else None
    room = ((room or "").strip()[:64] or None) or (req or {}).get("room")
    lang = staff_lang.norm_lang(guest_language or (req or {}).get("language"))
    case, created = _open_for_flag(cur, hotel_slug, reason=reason, req=req, room=room, session_id=session_id,
                                   summary=summary)
    if created and reason in ("not_resolved", "post_checkout"):
        # the hotel's own default gesture still goes to the dial (on_not_resolved)
        s = svc_settings.load(cur, hotel_slug)
        if s.gesture_default_minor > 0 and s.gesture_default_kind in C.GESTURE_KINDS:
            propose_gesture(cur, hotel_slug, case, kind=s.gesture_default_kind, amount_minor=s.gesture_default_minor)
    changes: dict[str, Any] = {}
    if summary and summary not in (case.get("summary") or ""):
        changes["summary"] = f"{case['summary']}\n{summary}"[:2000] if case.get("summary") else summary
    if session_id and not case.get("session_id"):
        changes["session_id"] = session_id
    if not case.get("guest_language") or guest_language:
        changes["guest_language"] = lang
    hotel = get_hotel(cur, hotel_slug)
    alert = case.get("approval_status") is None
    if alert:
        item = staff_lang.item_label(req) if req else None
        s = suggest_gesture(cur, hotel, kind=kind, summary=summary or case.get("summary") or "", room=room,
                            request={"item_label": item, "reopen_count": (req or {}).get("reopen_count")})
        changes.update(suggested_gesture=s["gesture"], suggested_kind=s["kind"], suggested_value_minor=s["value_minor"],
                       suggestion_source=s["source"], approval_status="pending")
    if changes:
        cols = list(changes)
        cur.execute(f"UPDATE svc_recovery_cases SET {', '.join(f'{c} = %s' for c in cols)}, updated_at = %s "
                    "WHERE id = %s RETURNING *", [changes[c] for c in cols] + [clock.now(), case["id"]])
        case = dict(cur.fetchone())
    if alert:
        _alert_duty_manager(cur, hotel, case, req, kind)
    out = flag_out(cur, hotel, case)
    out["created"] = created
    return out


def flag_out(cur: Any, hotel: Any, case: dict) -> dict:
    ccy = money.ccy(hotel.currency or "AED")
    minor = case.get("suggested_value_minor")
    return {"case_id": case["id"], "kind": REASON_KIND.get(case.get("reason") or "", case.get("reason")),
            "reason": case.get("reason"), "status": case.get("status"), "approval_status": case.get("approval_status"),
            "request_id": case.get("request_id"), "room": case.get("room"), "session_id": case.get("session_id"),
            "summary": case.get("summary"), "suggested_gesture": case.get("suggested_gesture"),
            "suggested_kind": case.get("suggested_kind"), "suggested_value_minor": minor,
            "suggested_value": money.money_str(minor, ccy) if minor else None, "currency": ccy,
            "suggestion_source": case.get("suggestion_source")}


def _alert_duty_manager(cur: Any, hotel: Any, case: dict, req: dict | None, kind: str) -> None:
    """The duty manager on shift hears of a gesture to approve: a message
    (live unless the hotel is in shadow mode), the request's feed event (the
    devices chime) and the console's service.recovery_changed."""
    from clefs.modules.service import escalation, staff_lang, state
    from clefs.modules.service import settings as svc_settings

    what = {"not_fixed": "says it is not fixed", "frustration": "is frustrated",
            "never_arrived": "says it never arrived"}.get(kind, "needs recovery")
    item = f" ({staff_lang.item_label(req)})" if req else ""
    text = f"Needs your approval: room {case.get('room') or '-'} guest {what}{item}. Open the Clefs Dashboard."
    live = svc_settings.escalation_live(cur, hotel.slug)
    people = escalation.alert(cur, hotel, "duty_manager", req or {"department_key": None}, text,
                              key=f"svc.recovery:{case['id']}", live=live)
    if req is not None:
        state.record_event(cur, req, "recovery", state.SYSTEM,
                           payload={"case_id": case["id"], "kind": kind, "to": "duty_manager", "alarm": "chime",
                                    "shadow": not live, "recipients": [p.name for p in people]})
    events.publish(hotel.slug, "service.recovery_changed",
                   {"case_id": case["id"], "status": "open", "approval_status": "pending", "kind": kind},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)


def decide(cur: Any, ctx: Any, case_id: int, action: Literal["approve", "dismiss"], *, gesture: str | None = None,
           kind: str | None = None, value_minor: int | None = None) -> dict:
    """The Dashboard's Approve / Edit / Dismiss. Approve records the gesture
    (within the approver's authority: 403 above it), writes it to the guest
    in their language and resolves the case; dismiss closes the case with
    nothing sent. -> case_view."""
    from clefs.modules.service import staff_lang, state

    slug = ctx.hotel.slug
    case = get_case(cur, slug, case_id, lock=True)
    if case is None:
        raise ClefsError(404, "Case not found", "case_not_found")
    if case.get("approval_status") in ("approved", "dismissed") or case["status"] != "open":
        raise ClefsError(409, "This case was already decided", "already_decided")
    who = _who(ctx, None, authority_role(cur, ctx, None), "console")
    now = clock.now()
    if action == "dismiss":
        cur.execute(
            """UPDATE svc_recovery_cases SET approval_status = 'dismissed', status = 'closed', closed_at = %s,
                   decided_at = %s, decided_by = %s, updated_at = %s WHERE id = %s RETURNING *""",
            (now, now, Json(who), now, case_id),
        )
        new = dict(cur.fetchone())
        audit(cur, ctx, "service.recovery_case.dismiss", "svc_recovery_cases", str(case_id),
              before={"approval_status": case.get("approval_status")}, after={"approval_status": "dismissed"},
              hotel_slug=slug)
        events.publish(slug, "service.recovery_changed", {"case_id": case_id, "status": "closed",
                                                          "approval_status": "dismissed"},
                       audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)
        return case_view(cur, slug, new)
    text = " ".join((gesture or case.get("suggested_gesture") or "").split())[:500]
    if not text:
        raise ClefsError(400, "Write the gesture the guest will read", "gesture_required")
    gkind = kind or case.get("suggested_kind") or "other"
    if gkind not in C.GESTURE_KINDS:
        raise ClefsError(400, f"kind must be one of {', '.join(C.GESTURE_KINDS)}", "bad_request")
    value = int(value_minor if value_minor is not None else (case.get("suggested_value_minor") or 0))
    if value < 0:
        raise ClefsError(400, "A gesture cannot be negative", "bad_request")
    hotel = get_hotel(cur, slug)
    matrix = authority_matrix(cur, hotel)
    role = authority_role(cur, ctx, None)
    if value > 0 and not covers(matrix[role], gkind, value):
        raise ClefsError(403, "This gesture is above your authority: ask a manager who can give it, or change it",
                         "above_authority")
    if value > 0:
        g, _ = add_gesture(cur, ctx, case_id, kind=gkind, amount_minor=value, description=text)
    else:
        # a gesture that costs nothing (an apology, a call) needs no authority
        cur.execute(
            """INSERT INTO svc_recovery_actions (hotel_slug, case_id, kind, description, value_minor, currency, status,
                   within_authority, authorized_by, requested_by, decided_at, created_at)
               VALUES (%s, %s, %s, %s, 0, %s, 'given', TRUE, %s, %s, %s, %s) RETURNING *""",
            (slug, case_id, gkind, text, matrix[role]["currency"], Json(who), Json(who), now, now),
        )
        g = dict(cur.fetchone())
    req = state.load(cur, slug, case["request_id"]) if case.get("request_id") else None
    lang = staff_lang.norm_lang(case.get("guest_language") or (req or {}).get("language"))
    for_guest, translated = staff_lang.english_to(slug, text, lang, cur=cur)
    msg = staff_lang.add_message(cur, slug, kind="gesture", text=for_guest, language=lang,
                                 request_id=case.get("request_id"), session_id=case.get("session_id"), case_id=case_id,
                                 text_original=text, language_original="en", translated=translated, via="approval")
    if req is not None:
        state.record_event(cur, req, "guest_message", state.Actor.from_ctx(ctx),
                           payload={"message_id": msg["id"], "kind": "gesture", "case_id": case_id,
                                    "text_original": text, "text_for_guest": for_guest, "language": lang})
        state.publish_changed(cur, req, "guest_message")
    cur.execute(
        """UPDATE svc_recovery_cases SET approval_status = 'approved', approved_gesture = %s, gesture_id = %s,
               status = 'resolved', closed_at = %s, decided_at = %s, decided_by = %s, updated_at = %s
           WHERE id = %s RETURNING *""",
        (text, g["id"], now, now, Json(who), now, case_id),
    )
    new = dict(cur.fetchone())
    audit(cur, ctx, "service.recovery_case.approve", "svc_recovery_cases", str(case_id),
          before={"approval_status": case.get("approval_status")},
          after={"approval_status": "approved", "gesture_id": g["id"], "kind": gkind, "value_minor": value,
                 "edited": text != (case.get("suggested_gesture") or "")}, hotel_slug=slug)
    events.publish(slug, "service.recovery_changed", {"case_id": case_id, "status": "resolved",
                                                      "approval_status": "approved"},
                   audience={"roles": ["owner", "gm", "dept_head", "front_desk"]}, cur=cur)
    return case_view(cur, slug, new)


# ─── Queries for api.py ───


def recovery_cost(cur: Any, hotel_slug: str, *, start: date, end: date) -> int:
    """Gestures given in hotel-local [start, end], minor units of the hotel's
    currency (api.recovery_cost's body; Finance's weekly pulse)."""
    hotel = get_hotel(cur, hotel_slug)
    t0, t1 = htime.local_range(hotel, start, end)
    ccy = money.ccy(hotel.currency or "AED")
    cur.execute(
        """SELECT currency, SUM(value_minor)::BIGINT AS n FROM svc_recovery_actions
           WHERE hotel_slug = %s AND status = 'given' AND COALESCE(decided_at, created_at) >= %s
             AND COALESCE(decided_at, created_at) < %s GROUP BY currency""",
        (hotel_slug, t0, t1),
    )
    total = 0
    for r in cur.fetchall():
        c = r["currency"].strip()
        total += int(r["n"]) if c == ccy else money.convert_minor(cur, int(r["n"]), c, ccy, end)
    return total


def has_open_complaint(cur: Any, hotel_slug: str, *, room: str | None = None, since: datetime) -> bool:
    """An open recovery case for the room opened since `since`
    (api.has_open_complaint's body: pauses marketing)."""
    if not room:
        return False
    cur.execute(
        "SELECT 1 FROM svc_recovery_cases WHERE hotel_slug = %s AND room = %s AND status = 'open' AND opened_at >= %s LIMIT 1",
        (hotel_slug, room, since),
    )
    return cur.fetchone() is not None


# ─── Routes ───

router = APIRouter(prefix="/hotels/{slug}/service", tags=["service-recovery"])


class CaseIn(BaseModel):
    request_id: int | None = None
    room: str | None = Field(None, max_length=64)
    category_key: str | None = Field(None, max_length=40)
    severity: Literal["L1", "L2", "L3"] | None = None
    reason: Literal["complaint", "not_resolved", "post_checkout", "manual", "frustration", "never_arrived"] = "manual"
    notes: str | None = Field(None, max_length=4000)
    learn: dict[str, bool] | None = None
    client_event_id: str | None = None


class GestureIn(BaseModel):
    kind: Literal["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount", "other"]
    # a Decimal string in the hotel's currency ("150.00"), or minor units
    value: Decimal | None = Field(None, ge=0)
    value_minor: int | None = Field(None, ge=0)
    description: str | None = Field(None, max_length=500)
    client_event_id: str | None = None

    @model_validator(mode="after")
    def _one(self) -> "GestureIn":
        if (self.value is None) == (self.value_minor is None):
            raise ValueError("give exactly one of value or value_minor")
        return self

    def minor(self, currency: str) -> int:
        return self.value_minor if self.value_minor is not None else money.to_minor(self.value, currency)


class DecideIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = Field(None, max_length=1000)


def case_view(cur: Any, slug: str, row: dict) -> dict:
    """case_out with the gestures, the suggestion's value in the hotel's
    currency and the request it is about (what the approvals panel shows)."""
    from clefs.modules.service import staff_lang
    from clefs.modules.service.routing import first_name

    out = case_out(row, [gesture_out(g) for g in gestures_for(cur, slug, row["id"])])
    ccy = money.ccy(get_hotel(cur, slug).currency or "AED")
    minor = row.get("suggested_value_minor")
    out["suggested_value"] = money.money_str(minor, ccy) if minor else None
    out["currency"] = ccy
    out["request"] = None
    if row.get("request_id"):
        cur.execute("SELECT * FROM staff_requests WHERE id = %s AND hotel_slug = %s", (row["request_id"], slug))
        r = cur.fetchone()
        if r is not None:
            out["request"] = {"id": r["id"], "room": r["room"], "item_label": staff_lang.item_label(dict(r)),
                              "state": r["state"], "staff_first_name": first_name(r["assigned_to"]) if r["assigned_staff_id"] else None,
                              "reopen_count": r["reopen_count"], "language": r["language"]}
    return out


@router.get("/recovery-cases")
def get_cases(slug: str, status: Literal["open", "resolved", "closed", "all"] = "open", room: str | None = None,
              approval: Literal["pending"] | None = None, limit: int = Query(100, ge=1, le=500),
              ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> list[dict]:
    """Cases, newest first. ?status=open lists what still needs a manager;
    approval=pending narrows it to gestures waiting for a decision (the
    Dashboard's "Needs your approval" panel)."""
    with db.db() as cur:
        rows = list_cases(cur, slug, status=status, room=room, limit=limit)
        if approval == "pending":
            rows = [r for r in rows if r.get("approval_status") == "pending"]
        return [case_view(cur, slug, r) for r in rows]


class CaseDecideIn(BaseModel):
    """Approve (optionally with the gesture edited) or dismiss. kind and
    value_minor change the suggestion's kind and value (minor units)."""

    action: Literal["approve", "dismiss"]
    gesture: str | None = Field(None, max_length=500)
    kind: Literal["amenity", "fnb_credit", "late_checkout", "room_night", "upgrade", "discount", "other"] | None = None
    value_minor: int | None = Field(None, ge=0)


@router.post("/recovery-cases/{case_id}/decide")
def post_case_decide(slug: str, case_id: int, body: CaseDecideIn,
                     ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    with db.db() as cur:
        return decide(cur, ctx, case_id, body.action, gesture=body.gesture, kind=body.kind, value_minor=body.value_minor)


@router.post("/recovery-cases")
def post_case(slug: str, body: CaseIn, ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    with db.db() as cur:
        row, created = open_case(cur, slug, actor=ctx, request_id=body.request_id, room=body.room,
                                 category_key=body.category_key, severity=body.severity, reason=body.reason,
                                 notes=body.notes, user_id=ctx.principal.user_id, learn=body.learn,
                                 client_event_id=body.client_event_id)
        return {**case_view(cur, slug, row), "created": created}


@router.get("/recovery-cases/{case_id}")
def get_one_case(slug: str, case_id: int, ctx: HotelCtx = Depends(require_perm_or_device("service.view"))) -> dict:
    with db.db() as cur:
        row = get_case(cur, slug, case_id)
        if row is None:
            raise ClefsError(404, "Case not found", "case_not_found")
        return case_view(cur, slug, row)


@router.patch("/recovery-cases/{case_id}")
def patch_case(slug: str, case_id: int, body: CasePatch, ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    if body.status in ("resolved", "closed") and not ctx.can("service.manage"):
        raise ClefsError(403, "Only a manager closes a recovery case", "forbidden")
    with db.db() as cur:
        return case_view(cur, slug, update_case(cur, slug, case_id, body, actor=ctx))


@router.post("/recovery-cases/{case_id}/actions")
def post_gesture(slug: str, case_id: int, body: GestureIn, ctx: HotelCtx = Depends(require_perm("service.work"))) -> dict:
    with db.db() as cur:
        ccy = money.ccy(ctx.hotel.currency or "AED")
        g, replayed = add_gesture(cur, ctx, case_id, kind=body.kind, amount_minor=body.minor(ccy),
                                  description=body.description, client_event_id=body.client_event_id)
        return {**gesture_out(g), "replayed": replayed}


@router.post("/recovery-actions/{gesture_id}/decide")
def post_decide(slug: str, gesture_id: int, body: DecideIn, ctx: HotelCtx = Depends(require_perm("service.manage"))) -> dict:
    with db.db() as cur:
        return gesture_out(decide_gesture(cur, ctx, gesture_id, body.decision, note=body.note))
