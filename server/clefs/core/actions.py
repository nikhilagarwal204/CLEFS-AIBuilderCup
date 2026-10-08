"""
Actions ledger and the autonomy dial: detect -> explain -> approve -> execute -> verify.
(Canon 7 and 17, 01 spec E2, 04 spec section 1.)

    register_action_type(ActionType(
        key="profit.dispute", module="profit_guard", label="Dispute a commission",
        executor=submit_packet, verifier=check_credit_note, verify_after=timedelta(days=14),
        default_owner_role="finance", default_level="approve", always_approve=True))

    a = create_action(cur, slug, type="profit.dispute", title="Dispute 12 no-shows",
                      params={...}, signal_id=s["id"], expected_value=Decimal("1020"), actor=ctx)
    decide(cur, a["id"], "approve", actor=ctx, via="console")      # enqueues the executor
    record_verification(cur, a["id"], verified=True, outcome_value=Decimal("1020"), evidence=[...])

State machine (Canon 7; `observed` from 04 spec 1.1; `failed` and `expired`
from Canon amendment K1):

    proposed --approve/adjust--> approved --assign--> assigned --start--> in_progress --done--> done
    done --verify--> verified | not_verified
    proposed --dispute--> approved(subtype=dispute) --submit--> submitted --await--> awaiting_counterparty
    submitted | awaiting_counterparty --verify--> verified | rejected_by_counterparty --give_up--> unresolved
    rejected_by_counterparty --submit--> submitted (a second attempt)
    proposed | approved | assigned --snooze(until)--> snoozed --timer or any decision--> previous state
    proposed --ignore--> rejected          any open state --cancel (supersede/expire)--> cancelled
    approved | assigned --executor ok=false, or an exception after the last retry--> failed
    proposed | approved (or snoozed from either) --actions.expires_at passes--> expired
    remind: no state change, logged (Brief delivers reminders and escalates)

Every transition writes an action_events row (with the WhatsApp button nonce
when there is one: a nonce is single-use) and an audit row, and publishes
core.action_changed.

The dial (one per hotel and action type) decides what create_action does:
    off                 nothing is written; returns {"status": "suppressed"}
    observe             written as `observed`, never executed
    suggest             `proposed`, surface=console (not pushed to the brief)
    approve             `proposed`, surface=brief (one tap)
    auto_within_limits  approved by the dial and executed when the limits pass,
                        otherwise `proposed` with limit_reasons
    auto                approved by the dial and executed
The effective level is the lowest of the hotel's setting, the type's ceiling
(max_level; `always_approve` types have ceiling `approve`, so refunds, folio,
rate and OTA changes are never automatic) and the type's per-action gate.
Execution re-resolves the level; a dial-approved action whose level has since
dropped never runs and lands where create_action would put it now (approve:
proposed on the brief; suggest: proposed on the console; observe: observed;
off: cancelled, reason dial_off).

A hotel has no action_autonomy row for a type until someone sets it or an
incident demotes it (04 spec 1.4): resolve_level falls back to the type's
default_level, so a type registered later, or a default lowered after a safety
review, reaches every hotel that never touched that dial. The first write also
logs a kind='seed' event recording the default that was in force.

The dial's own actor is SYSTEM (a service Principal labelled "system"): a
dial approval is logged with actor_type "dial" and decided_via "dial".
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable, Literal

from pydantic import BaseModel, ValidationError

from clefs import events, jobs
from clefs.audit import actor_fields, audit
from clefs.auth import permissions as perms_mod
from clefs.auth.deps import Principal
from clefs.core import clock, money
from clefs.core import signals as signals_mod
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel, get_hotel
from clefs.core.signals import Evidence
from clefs.db import Json

log = logging.getLogger("clefs.actions")

Level = Literal["off", "observe", "suggest", "approve", "auto_within_limits", "auto"]
LEVELS: tuple[str, ...] = ("off", "observe", "suggest", "approve", "auto_within_limits", "auto")
AUTO_LEVELS = frozenset({"auto_within_limits", "auto"})
Decision = Literal["approve", "adjust", "assign", "dispute", "remind", "ignore", "snooze"]
DECISIONS: tuple[str, ...] = ("approve", "adjust", "assign", "dispute", "remind", "ignore", "snooze")
Via = Literal["console", "whatsapp", "email", "api", "system", "dial"]
# actions.decided_via CHECK (0011). "dial" is written only by create_action's
# system approval: a person's decision can never claim to be the dial's.
VIAS: tuple[str, ...] = ("console", "whatsapp", "email", "api", "system", "dial")

# The dial's actor (04 spec 1.1): a dial approval is logged with it (as
# actor_type "dial"), and detectors, jobs and module stubs pass it as `actor`
# when no person is involved. A service Principal, so _is_system() passes it
# through every permission check, exactly like a job.
SYSTEM = Principal(kind="service", label="system")

# A magic-link session whose scope carries this key may decide only those
# action ids, and it decides them with the membership role's full permission
# set (04 spec 1.1): Brief mints the link with scope={"action_ids": [...]}.
MAGIC_SCOPE_ACTION_IDS = "action_ids"

# observed: the dial logged it and nothing ran. failed / expired: Canon K1.
TERMINAL: frozenset[str] = frozenset(
    {"verified", "not_verified", "rejected", "cancelled", "unresolved", "observed", "failed", "expired"}
)
EXPIRABLE: frozenset[str] = frozenset({"proposed", "approved"})
SNOOZABLE: frozenset[str] = frozenset({"proposed", "approved", "assigned"})
EXECUTABLE: frozenset[str] = frozenset({"approved", "assigned"})
WORKING: frozenset[str] = frozenset({"approved", "assigned", "in_progress"})
VERIFYING: frozenset[str] = frozenset({"done", "submitted", "awaiting_counterparty"})
# Supersede / expire. The dispute path ends through give_up, not cancel.
CANCELLABLE: frozenset[str] = frozenset({"proposed", "approved", "assigned", "in_progress", "snoozed"})

# (from_status, decision) -> to_status. Anything else is an illegal transition.
TRANSITIONS: dict[tuple[str, str], str] = {
    ("proposed", "approve"): "approved",
    ("proposed", "adjust"): "approved",
    ("approved", "adjust"): "approved",  # only before it executed
    ("proposed", "assign"): "assigned",
    ("approved", "assign"): "assigned",
    ("assigned", "assign"): "assigned",
    ("in_progress", "assign"): "assigned",
    ("proposed", "dispute"): "approved",
    ("proposed", "ignore"): "rejected",
    ("proposed", "snooze"): "snoozed",
    ("approved", "snooze"): "snoozed",
    ("assigned", "snooze"): "snoozed",
    ("snoozed", "snooze"): "snoozed",
}
REMINDABLE: frozenset[str] = frozenset(
    {"proposed", "approved", "assigned", "in_progress", "submitted", "awaiting_counterparty", "rejected_by_counterparty"}
)

PROMOTION_MIN_DECISIONS = 50
PROMOTION_MIN_RATE = 0.95
PROMOTION_WINDOW = timedelta(days=30)
DISMISSED_QUIET = timedelta(days=30)
EXECUTION_MAX_ATTEMPTS = 5
EXECUTION_BACKOFF_S = 60
MANAGER_ROLES = ["owner", "gm", "finance", "revenue", "dept_head"]


def level_rank(level: str) -> int:
    try:
        return LEVELS.index(level)
    except ValueError as exc:
        raise ValueError(f"unknown autonomy level {level!r}") from exc


def min_level(*levels: str) -> str:
    return min(levels, key=level_rank)


# The core.autonomy.*, core.metrics.view and core.files.view permissions this
# file checks live in auth/permissions.CORE_PERMISSIONS, so they survive
# permissions.reset() without importing this module.


# ─── Contracts for executors, verifiers, gates and limits ───


class LimitCheck(BaseModel):
    ok: bool
    reasons: list[str] = []


class Gate(BaseModel):
    max_level: Level
    reasons: list[str] = []


def template_gate(channels: Mapping[str, bool], *, open_level: str = "auto") -> Gate:
    """The per-channel template gate for messaging action types (04 spec 3.4,
    3.5): `channels` maps each channel eligible for this guest to whether it
    has an approved template (WhatsApp: owner approval AND Meta APPROVED from
    brief.api.wa_template_info; email: owner approval only).

    One approved channel is enough: the step may run at `open_level` and the
    executor picks the channel. Only when no eligible channel has an approved
    template is the step capped at `observe` (logged, never sent). The gate is
    re-evaluated at execution, so a dial-approved send whose template was
    revoked mid-flight is marked `observed` and never sent, exactly as if the
    gate had closed before it was created (see _revoke_dial_approval)."""
    level_rank(open_level)
    if any(channels.values()):
        return Gate(max_level=open_level)  # type: ignore[arg-type]
    if not channels:
        return Gate(max_level="observe", reasons=["no eligible channel"])
    names = ", ".join(sorted(channels))
    return Gate(max_level="observe", reasons=[f"no approved template on any eligible channel ({names})"])


class ExecResult(BaseModel):
    """What an executor returns (a plain dict is accepted as `data`)."""

    ok: bool = True
    data: dict = {}
    error: str | None = None
    template_id: str | None = None  # "meta_name@version"
    template_category: Literal["UTILITY", "MARKETING"] | None = None
    consent_basis: str | None = None  # "contract:reservation:<id>" | "consent:<id>"
    reversal_handle: dict = {"kind": "none", "reason": "not_reversible"}


class VerifyResult(BaseModel):
    status: Literal["verified", "not_verified", "pending"]
    outcome_minor: int | None = None
    outcome_value: Decimal | None = None
    currency: str | None = None
    counts_toward_verified_profit: bool | None = None  # None = the ActionType default
    attribution: Literal["caused", "influenced", "observed"] = "caused"
    evidence: list[Evidence] = []
    confidence: float = 1.0


@dataclass(frozen=True)
class ActionCtx:
    """Passed to executors, verifiers, gates, limit checkers and
    validate_params. The 04 spec's fields (action_id, type, params) are
    properties over the action row, so a gate evaluated inside create_action
    sees the draft row (action_id None) and an executor sees the stored one.
    Frozen: a callback must not swap the row or the actor under the ledger."""

    cur: Any
    hotel: Hotel
    action: dict
    action_type: "ActionType"
    actor: Any = None

    @property
    def params(self) -> dict:
        return self.action.get("params") or {}

    @property
    def hotel_slug(self) -> str:
        return self.hotel.slug

    @property
    def action_id(self) -> int | None:
        """None while create_action evaluates the gate and the limits."""
        return self.action.get("id")

    @property
    def type(self) -> str:  # noqa: A003 - the 04 spec's field name
        return self.action_type.key


@dataclass(frozen=True)
class ActionType:
    key: str  # "<module or segment>.<verb>"
    module: str  # owning module ("core" for foundation types)
    label: str
    executor: Callable[[ActionCtx], ExecResult | dict | None] | None = None  # None = a human task
    verifier: Callable[[ActionCtx], VerifyResult | dict] | None = None
    verify_after: timedelta | None = None
    default_owner_role: str = "gm"
    default_level: Level = "approve"
    max_level: Level = "auto"  # hard ceiling: a hotel setting can never exceed it
    always_approve: bool = False  # refunds, folio, rate, OTA changes: never automatic
    limits_model: type[BaseModel] | None = None  # JSON schema for the console's limits editor
    limit_checker: Callable[[ActionCtx, Any], LimitCheck | bool] | None = None
    gate: Callable[[ActionCtx], Gate] | None = None  # per-action precondition (template approved, offer live)
    params_model: type[BaseModel] | None = None
    validate_params: Callable[[ActionCtx, dict], None] | None = None  # raise ValueError on bad bounds
    decide_perm: str = "core.actions.decide"
    counts_toward_verified_profit: bool = False
    verify_every: timedelta | None = None  # re-check cadence while the verifier says "pending"
    verify_until: timedelta | None = None  # give up (not_verified) this long after execution
    reversible: bool = False
    description: str = ""
    # (cur, hotel_slug, target level) -> reason codes. Non-empty refuses a
    # raise of the dial (PUT or accepting a promotion) with 409 <first code>,
    # e.g. reviews.reply_positive: "auto_reply_consent_required" (04 spec 1.4).
    level_check: Callable[[Any, str, str], list[str]] | None = None
    # (old limits, new limits) -> True when the new ones loosen any bound; a
    # loosening needs core.autonomy.raise_auto (04 spec 1.6). Models when the
    # type has a limits_model, else dicts. None = the generic rule
    # (limits_loosen): higher max_*, lower min_*, a wider list, anything else
    # changed counts as looser.
    is_looser: Callable[[Any, Any], bool] | None = None

    @property
    def ceiling(self) -> str:
        return "approve" if self.always_approve else self.max_level

    @property
    def default_autonomy(self) -> str:
        return min_level(self.default_level, self.ceiling)


_types: dict[str, ActionType] = {}
_type_origin: dict[str, str] = {}
_lock = threading.Lock()


def register_action_type(t: ActionType) -> ActionType:
    if "." not in t.key:
        raise ValueError(f"action type {t.key!r} must look like '<module>.<verb>'")
    for lvl in (t.default_level, t.max_level):
        level_rank(lvl)
    origin = sys._getframe(1).f_globals.get("__name__", "?")
    with _lock:
        prev = _type_origin.get(t.key)
        if prev is not None and prev != origin:
            raise ValueError(f"action type {t.key!r} already registered by {prev}")
        _types[t.key] = t
        _type_origin[t.key] = origin
    return t


def action_types() -> list[ActionType]:
    with _lock:
        return sorted(_types.values(), key=lambda t: t.key)


def action_type_origin(key: str) -> str | None:
    with _lock:
        return _type_origin.get(key)


def get_action_type(key: str) -> ActionType | None:
    with _lock:
        return _types.get(key)


def _require_type(key: str) -> ActionType:
    t = get_action_type(key)
    if t is None:
        raise ClefsError(400, f"unknown action type {key!r}", "unknown_action_type")
    return t


def unregister_action_type(key: str) -> None:
    """Tests only."""
    with _lock:
        _types.pop(key, None)
        _type_origin.pop(key, None)


# ─── Serialization ───


def to_api(row: dict) -> dict:
    out = dict(row)
    ccy = (out.get("currency") or "").strip() or None
    out["currency"] = ccy
    out["expected_value"] = money.money_str(out.get("expected_minor"), ccy)
    out["outcome_value"] = money.money_str(out.get("outcome_minor"), ccy)
    if out.get("outcome_confidence") is not None:
        out["outcome_confidence"] = float(out["outcome_confidence"])
    out["limit_reasons"] = list(out.get("limit_reasons") or [])
    out["terminal"] = out.get("status") in TERMINAL
    return out


def _load(cur: Any, action_id: int, *, hotel_slug: str | None = None, lock: bool = True) -> dict:
    cur.execute(f"SELECT * FROM actions WHERE id = %s{' FOR UPDATE' if lock else ''}", (action_id,))
    row = cur.fetchone()
    if row is None or (hotel_slug is not None and row["hotel_slug"] != hotel_slug):
        raise ClefsError(404, "Action not found", "action_not_found")
    return dict(row)


def get_action(cur: Any, hotel_slug: str, action_id: int, *, with_events: bool = True) -> dict:
    out = to_api(_load(cur, action_id, hotel_slug=hotel_slug, lock=False))
    if with_events:
        out["events"] = list_events(cur, action_id)
    return out


def list_events(cur: Any, action_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT id, at, actor_type, actor_id, actor_label, event, from_status, to_status, via, data
        FROM action_events WHERE action_id = %s ORDER BY id
        """,
        (action_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def list_actions(
    cur: Any,
    hotel_slug: str,
    *,
    status: list[str] | None = None,
    types: list[str] | None = None,
    overdue: bool = False,
    signal_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    cur.execute(
        """
        SELECT * FROM actions
        WHERE hotel_slug = %(h)s
          AND (%(status)s::TEXT[] IS NULL OR status = ANY(%(status)s))
          AND (%(types)s::TEXT[] IS NULL OR type = ANY(%(types)s))
          AND (%(signal)s::BIGINT IS NULL OR signal_id = %(signal)s)
          AND (NOT %(overdue)s OR (due_at < %(now)s AND status IN ('approved', 'assigned', 'in_progress')))
        ORDER BY (status = ANY(%(terminal)s)), due_at ASC NULLS LAST, id DESC
        LIMIT %(limit)s
        """,
        {
            "h": hotel_slug,
            "status": list(status) if status else None,
            "types": list(types) if types else None,
            "signal": signal_id,
            "overdue": overdue,
            "now": clock.now(),
            "limit": limit,
            "terminal": sorted(TERMINAL),
        },
    )
    return [to_api(dict(r)) for r in cur.fetchall()]


# ─── Events, audit, publish ───


def _log_event(
    cur: Any,
    action: dict,
    event: str,
    *,
    actor: Any,
    from_status: str | None,
    to_status: str | None,
    via: str | None = None,
    data: dict | None = None,
    nonce: str | None = None,
    actor_type: str | None = None,
) -> None:
    a_type, a_id, a_label = actor_fields(actor)
    try:
        cur.execute("SAVEPOINT clefs_action_event")
        cur.execute(
            """
            INSERT INTO action_events (action_id, hotel_slug, at, actor_type, actor_id, actor_label, event,
                                       from_status, to_status, via, nonce, data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                action["id"],
                action["hotel_slug"],
                clock.now(),
                actor_type or a_type,
                a_id,
                a_label,
                event,
                from_status,
                to_status,
                via,
                nonce,
                Json(_jsonable(data or {})),
            ),
        )
        cur.execute("RELEASE SAVEPOINT clefs_action_event")
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_action_event")
        cur.execute("RELEASE SAVEPOINT clefs_action_event")
        if nonce and "action_events_nonce_idx" in str(exc):
            raise ClefsError(409, "This button was already used", "nonce_replayed") from exc
        raise


def _jsonable(v: Any) -> Any:
    import json

    return json.loads(json.dumps(v, default=str))


def _changed(
    cur: Any, action: dict, *, actor: Any, verb: str, before: dict | None = None, meta: dict | None = None
) -> None:
    audit(
        cur,
        actor,
        f"core.actions.{verb}",
        "action",
        action["id"],
        before=before,
        after={"status": action["status"], "type": action["type"]},
        meta=meta,
        hotel_slug=action["hotel_slug"],
    )
    events.publish(
        action["hotel_slug"],
        "core.action_changed",
        {"id": action["id"], "status": action["status"], "type": action["type"], "signal_id": action.get("signal_id")},
        audience={"roles": MANAGER_ROLES},
        cur=cur,
    )


def _update(cur: Any, action_id: int, changes: dict) -> dict:
    changes = {**changes, "updated_at": clock.now()}
    sets = ", ".join(f"{k} = %({k})s" for k in changes)
    params = {k: (Json(_jsonable(v)) if isinstance(v, dict) else v) for k, v in changes.items()}
    params["_id"] = action_id
    cur.execute(f"UPDATE actions SET {sets} WHERE id = %(_id)s RETURNING *", params)
    return dict(cur.fetchone())


def _user_id(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    return getattr(principal, "user_id", None) if getattr(principal, "kind", None) == "user" else None


def _is_system(actor: Any) -> bool:
    principal = getattr(actor, "principal", actor)
    return actor is None or getattr(principal, "kind", None) == "service"


def _check_perm(actor: Any, perm: str) -> None:
    """HotelCtx actors must hold `perm`; service principals and None (system
    code, detectors, jobs) pass. A bare user Principal has no hotel context,
    so it is refused: routes pass the HotelCtx."""
    if _is_system(actor):
        return
    can = getattr(actor, "can", None)
    if can is None:
        raise ClefsError(403, "A hotel context is required for this decision", "forbidden")
    if not can(perm):
        principal = getattr(actor, "principal", None)
        if principal is not None and not principal.is_authenticated:
            raise ClefsError(401, "Sign in required", "unauthorized")
        raise ClefsError(403, "You cannot decide this action", "forbidden")


def _is_magic(actor: Any) -> bool:
    principal = getattr(actor, "principal", None)
    return getattr(principal, "session_kind", None) == "magic"


def _surfaced_ids(actor: Any) -> set[int] | None:
    """The action ids a magic session's link covers, or None when the link
    names none (older links, and Brief links minted before it lists ids)."""
    scope = getattr(getattr(actor, "principal", None), "scope", None) or {}
    raw = scope.get(MAGIC_SCOPE_ACTION_IDS)
    if raw is None:
        return None
    ids: set[int] = set()
    for x in raw if isinstance(raw, (list, tuple)) else ():
        try:
            ids.add(int(x))
        except (TypeError, ValueError):
            continue
    return ids


def _check_decide_perm(actor: Any, action: dict, perm: str) -> None:
    """decide()'s permission rule (04 spec 1.1). A magic-link session holds
    only brief./core.signals.view/core.actions. keys, so a revenue user could
    never approve a group quote (decide_perm inquiries.approve) from the
    mobile brief. When the link lists the action ids the brief surfaced, the
    session may decide exactly those, checked against the membership role's
    FULL permission set; any other action is 403 not_surfaced. A link without
    the list keeps the plain rule (the magic-filtered set)."""
    if _is_magic(actor):
        surfaced = _surfaced_ids(actor)
        if surfaced is not None:
            if action["id"] not in surfaced:
                raise ClefsError(403, "This link does not cover that action", "not_surfaced")
            # role is None when the session is used at a hotel other than the
            # one its link names (resolve_ctx drops it), so that is a 403 too.
            if perm not in perms_mod.permissions_for_role(getattr(actor, "role", None)):
                raise ClefsError(403, "You cannot decide this action", "forbidden")
            return
    _check_perm(actor, perm)


def _ctx_for(cur: Any, action: dict, t: ActionType, actor: Any = None) -> ActionCtx:
    return ActionCtx(cur=cur, hotel=get_hotel(cur, action["hotel_slug"]), action=action, action_type=t, actor=actor)


# ─── Autonomy dial ───


@dataclass(frozen=True)
class Resolution:
    configured: str
    effective: str
    ceiling: str
    reasons: list[str] = field(default_factory=list)
    limit_reasons: list[str] = field(default_factory=list)
    policy_version: int = 0
    limits: dict = field(default_factory=dict)
    source: str = "default"  # "hotel" when the hotel has an action_autonomy row


def _default_limits(t: ActionType) -> dict:
    if t.limits_model is None:
        return {}
    try:
        return t.limits_model().model_dump(mode="json")
    except ValidationError:
        return {}


def _dial_row(cur: Any, hotel_slug: str, action_type: str) -> dict | None:
    cur.execute(
        "SELECT level, limits, policy_version, reason, updated_by, updated_at FROM action_autonomy "
        "WHERE hotel_slug = %s AND action_type = %s",
        (hotel_slug, action_type),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _generic_limits(ctx: ActionCtx, limits: dict) -> LimitCheck:
    """No limit_checker: every `max_<field>` limit is compared with the
    action's params[<field>] (or expected_minor). With nothing comparable the
    check fails, so auto_within_limits never runs without explicit limits."""
    candidates = dict(ctx.params)
    if ctx.action.get("expected_minor") is not None:
        candidates.setdefault("expected_minor", ctx.action["expected_minor"])
        candidates.setdefault("amount_minor", ctx.action["expected_minor"])
    reasons: list[str] = []
    checked = 0
    for key, limit in (limits or {}).items():
        if not key.startswith("max_") or isinstance(limit, bool) or not isinstance(limit, (int, float)):
            continue
        name = key[4:]
        value = candidates.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            continue
        checked += 1
        if Decimal(str(value)) > Decimal(str(limit)):
            reasons.append(f"{name} {value} exceeds {key} {limit}")
    if checked == 0:
        return LimitCheck(ok=False, reasons=["no limits apply to this action"])
    return LimitCheck(ok=not reasons, reasons=reasons)


def check_limits(ctx: ActionCtx, limits: dict) -> LimitCheck:
    t = ctx.action_type
    if t.limit_checker is None:
        return _generic_limits(ctx, limits)
    parsed: Any = limits
    if t.limits_model is not None:
        try:
            parsed = t.limits_model.model_validate(limits or {})
        except ValidationError as exc:
            return LimitCheck(ok=False, reasons=[f"invalid limits: {exc.errors()[0]['msg']}"])
    result = t.limit_checker(ctx, parsed)
    if isinstance(result, LimitCheck):
        return result
    if isinstance(result, dict):
        return LimitCheck.model_validate(result)
    return LimitCheck(ok=bool(result), reasons=[] if result else ["limit check failed"])


def resolve_level(cur: Any, hotel_slug: str, action_type: str, *, ctx: ActionCtx | None = None) -> Resolution:
    """The level that applies to one action now (04 spec 1.1).

    configured = the hotel's row, else the type's default; effective = the
    lowest of configured, the type's ceiling and its gate; auto_within_limits
    falls back to approve (with reasons) when the limits do not pass."""
    t = _require_type(action_type)
    row = _dial_row(cur, hotel_slug, action_type)
    configured = row["level"] if row else t.default_autonomy
    limits = (row["limits"] if row else None) or _default_limits(t)
    reasons: list[str] = []
    effective = configured
    if level_rank(effective) > level_rank(t.ceiling):
        effective = t.ceiling
        reasons.append(f"ceiling {t.ceiling}")
    if t.gate is not None and ctx is not None:
        g = t.gate(ctx)
        if level_rank(g.max_level) < level_rank(effective):
            effective = g.max_level
            reasons += g.reasons or [f"gate {g.max_level}"]
    limit_reasons: list[str] = []
    if effective == "auto_within_limits" and ctx is not None:
        check = check_limits(ctx, limits)
        if not check.ok:
            effective = "approve"
            limit_reasons = check.reasons
    return Resolution(
        configured=configured,
        effective=effective,
        ceiling=t.ceiling,
        reasons=reasons,
        limit_reasons=limit_reasons,
        policy_version=row["policy_version"] if row else 0,
        limits=limits,
        source="hotel" if row else "default",
    )


def _autonomy_event(
    cur: Any,
    hotel_slug: str,
    action_type: str,
    kind: str,
    *,
    from_level: str | None,
    to_level: str | None,
    policy_version: int | None,
    actor: Any = None,
    trigger: str | None = None,
    evidence: list | None = None,
    stats: dict | None = None,
    resolves_event_id: int | None = None,
) -> int:
    """One row of the dial's history. A promotion_suggested row starts
    `open` (0015: at most one open per hotel and type, enforced by a partial
    unique index); every other kind has no status. accepted / dismissed rows
    point at the suggestion they answer through resolves_event_id."""
    a_type, a_id, _ = actor_fields(actor)
    cur.execute(
        """
        INSERT INTO action_autonomy_events (hotel_slug, action_type, kind, from_level, to_level, trigger,
                                            policy_version, evidence, stats, actor_type, actor_id, at,
                                            status, resolves_event_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        (
            hotel_slug,
            action_type,
            kind,
            from_level,
            to_level,
            trigger,
            policy_version,
            Json(_jsonable(evidence or [])),
            Json(_jsonable(stats)) if stats is not None else None,
            a_type,
            a_id,
            clock.now(),
            "open" if kind == "promotion_suggested" else None,
            resolves_event_id,
        ),
    )
    return cur.fetchone()["id"]


def _close_suggestions(cur: Any, hotel_slug: str, action_type: str, status: str, *, event_id: int | None = None) -> int:
    """Move this type's open suggestion (or the one `event_id` names) to
    accepted / dismissed / superseded (04 spec 1.5 lifecycle)."""
    cur.execute(
        """
        UPDATE action_autonomy_events SET status = %s
        WHERE hotel_slug = %s AND action_type = %s AND kind = 'promotion_suggested' AND status = 'open'
          AND (%s::BIGINT IS NULL OR id = %s)
        """,
        (status, hotel_slug, action_type, event_id, event_id),
    )
    return cur.rowcount


def limits_loosen(old: Mapping[str, Any] | None, new: Mapping[str, Any] | None) -> bool:
    """The generic loosening rule for a type without is_looser. Fails closed:
    only changes it can prove are tighter pass with core.autonomy.manage.

    max_<x> higher (or removed)    looser     max_<x> lower (or added)   tighter
    min_<x> lower (or removed)     looser     min_<x> higher (or added)  tighter
    a list gaining an item         looser     a list only losing items   tighter
    any other change (a flag such as once_per_stay flipped, a new key)   looser"""
    old, new = dict(old or {}), dict(new or {})

    def num(v: Any) -> bool:
        return isinstance(v, (int, float, Decimal)) and not isinstance(v, bool)

    for key in set(old) | set(new):
        a, b = old.get(key), new.get(key)
        if a == b:
            continue
        if key.startswith(("max_", "min_")) and a is None and num(b):
            continue  # a bound where there was none
        numeric = num(a) and num(b)
        if key.startswith("max_") and numeric:
            if Decimal(str(b)) > Decimal(str(a)):
                return True
            continue
        if key.startswith("min_") and numeric:
            if Decimal(str(b)) < Decimal(str(a)):
                return True
            continue
        if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
            if not set(map(str, b)) <= set(map(str, a)):
                return True
            continue
        return True
    return False


def _loosens(t: ActionType, old: dict, new: dict) -> bool:
    if t.is_looser is None:
        return limits_loosen(old, new)
    if t.limits_model is not None:
        try:
            before = t.limits_model.model_validate(old or {})
        except ValidationError:
            # A stored row the model no longer accepts: judge on the raw
            # values rather than 500 on a GM's attempt to tighten it.
            return limits_loosen(old, new)
        return bool(t.is_looser(before, t.limits_model.model_validate(new or {})))
    return bool(t.is_looser(old, new))


def _level_check(cur: Any, t: ActionType, hotel_slug: str, level: str) -> None:
    if t.level_check is None:
        return
    reasons = [r for r in (t.level_check(cur, hotel_slug, level) or []) if r]
    if reasons:
        raise ClefsError(409, "; ".join(reasons), str(reasons[0]).split(":", 1)[0].strip())


def accept_perm_for(to_level: str) -> str:
    """Who may accept a promotion to `to_level` (04 spec 1.5 / 7.1):
    core.autonomy.manage up to approve, core.autonomy.raise_auto above."""
    return "core.autonomy.raise_auto" if level_rank(to_level) > level_rank("approve") else "core.autonomy.manage"


def get_autonomy(cur: Any, hotel_slug: str, action_type: str) -> dict:
    t = _require_type(action_type)
    row = _dial_row(cur, hotel_slug, action_type)
    res = resolve_level(cur, hotel_slug, action_type)
    return {
        "action_type": t.key,
        "label": t.label,
        "module": t.module,
        "level": res.configured,
        "effective_level": res.effective,
        "default_level": t.default_autonomy,
        "max_level": t.ceiling,
        "always_approve": t.always_approve,
        "limits": res.limits,
        "limits_schema": t.limits_model.model_json_schema() if t.limits_model else None,
        "policy_version": res.policy_version,
        "source": res.source,
        "reason": row["reason"] if row else None,
        "updated_by": row["updated_by"] if row else None,
        "updated_at": row["updated_at"] if row else None,
        "stats": promotion_stats(cur, hotel_slug, action_type),
        "open_suggestion": _open_suggestion(cur, hotel_slug, action_type),
    }


def list_autonomy(cur: Any, hotel_slug: str) -> list[dict]:
    return [get_autonomy(cur, hotel_slug, t.key) for t in action_types()]


AUTONOMY_EVENT_FIELDS = (
    "id", "kind", "from_level", "to_level", "trigger", "policy_version", "actor_type", "actor_id", "at", "status",
    "resolves_event_id",
)


def list_autonomy_events(cur: Any, hotel_slug: str, action_type: str, *, limit: int = 50) -> list[dict]:
    """One dial's history, newest first (04 spec 1.6): seeds, manual sets,
    promotion suggestions and their answers, demotions. Evidence and stats are
    left out: a demotion's evidence can quote a guest, and the console only
    needs who moved the dial, from where to where, and why."""
    _require_type(action_type)
    cur.execute(
        f"""
        SELECT {", ".join(AUTONOMY_EVENT_FIELDS)} FROM action_autonomy_events
        WHERE hotel_slug = %s AND action_type = %s
        ORDER BY at DESC, id DESC
        LIMIT %s
        """,
        (hotel_slug, action_type, max(1, min(int(limit), 200))),
    )
    return [dict(r) for r in cur.fetchall()]


def set_autonomy(
    cur: Any,
    hotel_slug: str,
    action_type: str,
    *,
    level: str,
    limits: dict | None = None,
    actor: Any = None,
    reason: str | None = None,
    kind: str = "set",
    trigger: str | None = None,
    evidence: list | None = None,
    resolves_event_id: int | None = None,
) -> dict:
    """Set the dial. Never above the type's ceiling (422 above_ceiling).

    HotelCtx actors need core.autonomy.manage. Raising the level INTO
    auto_within_limits / auto, or loosening any limit (ActionType.is_looser,
    else limits_loosen), needs core.autonomy.raise_auto; lowering and
    tightening never do, so a GM can always make the dial safer. A raise is
    refused with 409 <code> when the type's level_check objects. Bumps
    policy_version when anything changes, and supersedes an open promotion
    suggestion (a manual change makes it stale). The hotel's first row for
    the type is preceded by a kind='seed' event (04 spec 1.4)."""
    t = _require_type(action_type)
    level_rank(level)
    if level_rank(level) > level_rank(t.ceiling):
        raise ClefsError(422, f"{t.key} cannot go above {t.ceiling}", "above_ceiling")
    row = _dial_row(cur, hotel_slug, action_type)
    before_level = row["level"] if row else t.default_autonomy
    old_limits = (row or {}).get("limits") or _default_limits(t)
    new_limits = limits if limits is not None else old_limits
    if t.limits_model is not None:
        try:
            new_limits = t.limits_model.model_validate(new_limits or {}).model_dump(mode="json")
        except ValidationError as exc:
            raise ClefsError(422, f"invalid limits: {exc.errors()[0]['msg']}", "invalid_limits") from exc
    raising = level_rank(level) > level_rank(before_level)
    loosening = limits is not None and _loosens(t, old_limits, new_limits or {})
    _check_perm(
        actor,
        "core.autonomy.raise_auto" if (raising and level in AUTO_LEVELS) or loosening else "core.autonomy.manage",
    )
    if raising:
        _level_check(cur, t, hotel_slug, level)
    changed = row is None or row["level"] != level or (row["limits"] or {}) != (new_limits or {})
    version = (row["policy_version"] if row else 0) + (1 if changed else 0)
    cur.execute(
        """
        INSERT INTO action_autonomy (hotel_slug, action_type, level, limits, policy_version, reason, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, action_type) DO UPDATE SET
            level = EXCLUDED.level, limits = EXCLUDED.limits, policy_version = EXCLUDED.policy_version,
            reason = EXCLUDED.reason, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
        RETURNING (xmax = 0) AS inserted
        """,
        (hotel_slug, action_type, level, Json(new_limits or {}), max(version, 1), reason, _user_id(actor), clock.now()),
    )
    if cur.fetchone()["inserted"]:
        _seed_event(cur, hotel_slug, t)
    _autonomy_event(
        cur,
        hotel_slug,
        action_type,
        kind,
        from_level=before_level,
        to_level=level,
        policy_version=max(version, 1),
        actor=actor,
        trigger=trigger,
        evidence=evidence,
        resolves_event_id=resolves_event_id,
    )
    # What the hotel sees moved (a first row pinning the default moves nothing).
    moved = before_level != level or (old_limits or {}) != (new_limits or {})
    if moved and kind != "promotion_accepted":  # accept_suggestion closes its own suggestion
        # A manual change makes the open suggestion stale (409
        # suggestion_stale on accept), so the brief must stop showing it. A
        # PUT that changes nothing leaves it standing.
        _close_suggestions(cur, hotel_slug, action_type, "superseded")
        _retire_autonomy_signals(cur, hotel_slug, "core.autonomy_promotion", action_type, resolution="dial_changed")
    audit(
        cur,
        actor,
        f"core.autonomy.{kind}",
        "action_autonomy",
        f"{hotel_slug}:{action_type}",
        before={"level": before_level, "limits": (row or {}).get("limits")},
        after={"level": level, "limits": new_limits, "policy_version": max(version, 1)},
        hotel_slug=hotel_slug,
    )
    return get_autonomy(cur, hotel_slug, action_type)


set_level = set_autonomy  # the 04 spec's name


def _seed_event(cur: Any, hotel_slug: str, t: ActionType) -> None:
    """04 spec 1.4: no hotel is seeded up front. The hotel follows the type's
    default_level until its first row is written (set_autonomy or demote),
    and that first write logs the default then in force, so the history shows
    where the dial started. policy_version 0 is what actions created under
    the default carry (resolve_level without a row). No actor: nobody chose
    it. Callers detect "first row" from the upsert itself (xmax = 0), so two
    racing first writes cannot both log a seed."""
    _autonomy_event(cur, hotel_slug, t.key, "seed", from_level=None, to_level=t.default_autonomy, policy_version=0)


def promotion_stats(cur: Any, hotel_slug: str, action_type: str, *, now: datetime | None = None) -> dict:
    """Human decisions on this type over the trailing 30 days (04 spec 1.5):
    approve, adjust and ignore (ignore moves the action to rejected). An
    approve is unedited by definition (an edit is an adjust). assign, remind
    and dispute are not decisions about whether the dial should act alone,
    so they count neither way."""
    now = now or clock.now()
    cur.execute(
        """
        SELECT COUNT(*) AS decisions,
               COUNT(*) FILTER (WHERE e.event = 'approve') AS approved_unedited
        FROM action_events e JOIN actions a ON a.id = e.action_id
        WHERE a.hotel_slug = %s AND a.type = %s AND e.actor_type = 'user' AND e.at > %s
          AND e.event IN ('approve', 'adjust', 'ignore')
        """,
        (hotel_slug, action_type, now - PROMOTION_WINDOW),
    )
    row = cur.fetchone()
    decisions, unedited = int(row["decisions"]), int(row["approved_unedited"])
    rate = (unedited / decisions) if decisions else 0.0
    return {
        "window_days": PROMOTION_WINDOW.days,
        "decisions": decisions,
        "approved_unedited": unedited,
        "approval_rate": round(rate, 4),
        "meets_threshold": decisions >= PROMOTION_MIN_DECISIONS and rate >= PROMOTION_MIN_RATE,
    }


def _open_suggestion_row(cur: Any, hotel_slug: str, action_type: str) -> dict | None:
    cur.execute(
        """
        SELECT id, from_level, to_level, at, stats FROM action_autonomy_events
        WHERE hotel_slug = %s AND action_type = %s AND kind = 'promotion_suggested' AND status = 'open'
        """,
        (hotel_slug, action_type),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _open_suggestion(cur: Any, hotel_slug: str, action_type: str) -> dict | None:
    """The console's open_suggestion, {event_id, to_level, at} (the shape
    client/app/lib/types.ts AutonomySuggestion pins). Who may accept follows
    to_level: accept_perm_for(to_level)."""
    row = _open_suggestion_row(cur, hotel_slug, action_type)
    if row is None:
        return None
    return {"event_id": row["id"], "to_level": row["to_level"], "at": row["at"]}


def _dismissed_recently(cur: Any, hotel_slug: str, action_type: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM action_autonomy_events
        WHERE hotel_slug = %s AND action_type = %s AND kind = 'promotion_dismissed' AND at > %s
        LIMIT 1
        """,
        (hotel_slug, action_type, clock.now() - DISMISSED_QUIET),
    )
    return cur.fetchone() is not None


# 04 spec 1.5: a promotion skips auto_within_limits when the type has no
# limits_model. With nothing to compare, the generic limit check fails, so that
# level would behave exactly like approve and the suggestion would promise a
# step that changes nothing.
PROMOTION_SKIPS_UNLIMITED = True


def next_level(t: ActionType, current: str, *, skip_unlimited: bool | None = None) -> str | None:
    """The promotion target: one step up, never above the ceiling; None at
    the ceiling (or when the only step up is a skipped auto_within_limits)."""
    skip = PROMOTION_SKIPS_UNLIMITED if skip_unlimited is None else skip_unlimited
    ceiling = level_rank(t.ceiling)
    i = level_rank(current) + 1
    if i > ceiling:
        return None
    if skip and LEVELS[i] == "auto_within_limits" and t.limits_model is None:
        i += 1
        if i > ceiling:
            return None
    return LEVELS[i]


def _signal_key(kind: str, action_type: str, event_id: int) -> str:
    # One signal per autonomy event: a person who ignored last month's
    # suggestion must still see the next one. A single key per type would stay
    # 'ignored' (upsert_signal reopens a human-closed signal only when its
    # impact grows, and these carry none).
    return f"{kind}:{action_type}:{event_id}"


def _retire_autonomy_signals(
    cur: Any, hotel_slug: str, kind: str, action_type: str, *, keep: str | None = None, resolution: str = "stale"
) -> None:
    """Supersede this type's still-open suggestions of `kind` (a newer event,
    a manual dial change or a demotion made them stale)."""
    cur.execute(
        """
        SELECT id FROM signals
        WHERE hotel_slug = %s AND kind = %s AND starts_with(dedupe_key, %s) AND dedupe_key <> %s
          AND status IN ('open', 'surfaced', 'snoozed', 'decided')
        """,
        (hotel_slug, kind, f"{kind}:{action_type}:", keep or ""),
    )
    for row in cur.fetchall():
        signals_mod.set_status(cur, row["id"], "superseded", resolution=resolution)


def review_promotions(cur: Any, hotel_slug: str) -> list[dict]:
    """The core.autonomy_review body: suggest (never apply) one step up for each
    type that met the 50-decision / 95%-unedited bar and is below its ceiling.

    Quiet while a suggestion is open (at most one per hotel and type) and for
    30 days after a dismissal. A target the type's level_check would refuse
    is not suggested: the owner could only get a 409 from Accept."""
    out = []
    for t in action_types():
        res = resolve_level(cur, hotel_slug, t.key)
        target = next_level(t, res.configured)
        if target is None or res.configured == "off":
            continue
        stats = promotion_stats(cur, hotel_slug, t.key)
        if not stats["meets_threshold"]:
            continue
        if _open_suggestion_row(cur, hotel_slug, t.key) is not None:
            continue
        if _dismissed_recently(cur, hotel_slug, t.key):
            continue
        if t.level_check is not None and [r for r in (t.level_check(cur, hotel_slug, target) or []) if r]:
            continue
        event_id = _autonomy_event(
            cur, hotel_slug, t.key, "promotion_suggested", from_level=res.configured, to_level=target,
            policy_version=res.policy_version, stats=stats,
        )
        key = _signal_key("core.autonomy_promotion", t.key, event_id)
        _retire_autonomy_signals(cur, hotel_slug, "core.autonomy_promotion", t.key, keep=key)
        signals_mod.upsert_signal(
            cur,
            hotel_slug,
            signals_mod.SignalIn(
                module="core",
                kind="core.autonomy_promotion",
                dedupe_key=key,
                title=f"{t.label}: move from {res.configured} to {target}?",
                summary=(
                    f"{stats['approved_unedited']} of {stats['decisions']} decisions in the last "
                    f"{stats['window_days']} days were approved unedited."
                ),
                confidence=min(1.0, stats["approval_rate"]),
                evidence=[
                    Evidence(kind="metric", label="Decisions (30 days)", value=stats["decisions"]),
                    Evidence(kind="metric", label="Approved unedited", value=stats["approval_rate"]),
                ],
                subject_type="action_autonomy_event",
                subject_id=str(event_id),
            ),
        )
        out.append({"action_type": t.key, "event_id": event_id, "from_level": res.configured, "to_level": target})
    return out


def _suggestion(cur: Any, hotel_slug: str, event_id: int) -> dict:
    """An open suggestion whose starting level still holds, else 409
    suggestion_stale (it was accepted, dismissed or superseded, or the dial
    moved some way that did not supersede it)."""
    cur.execute(
        "SELECT * FROM action_autonomy_events WHERE id = %s AND hotel_slug = %s AND kind = 'promotion_suggested' "
        "FOR UPDATE",
        (event_id, hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Suggestion not found", "suggestion_not_found")
    if row["status"] != "open":
        raise ClefsError(409, "This suggestion is no longer current", "suggestion_stale")
    t = _require_type(row["action_type"])
    configured = (_dial_row(cur, hotel_slug, row["action_type"]) or {}).get("level") or t.default_autonomy
    if configured != row["from_level"]:
        raise ClefsError(409, "This suggestion is no longer current", "suggestion_stale")
    return dict(row)


def accept_suggestion(cur: Any, hotel_slug: str, event_id: int, *, actor: Any) -> dict:
    """Accept a promotion. The permission follows the target (04 spec 1.5):
    core.autonomy.manage up to approve, core.autonomy.raise_auto above it. The
    type's level_check can still refuse with 409. Never applied by itself."""
    s = _suggestion(cur, hotel_slug, event_id)
    _check_perm(actor, accept_perm_for(s["to_level"]))
    # set_autonomy first: its permission and level_check refusals leave the
    # suggestion open for someone who may accept it.
    set_autonomy(
        cur, hotel_slug, s["action_type"], level=s["to_level"], actor=actor, reason="promotion accepted",
        kind="promotion_accepted", resolves_event_id=event_id,
    )
    _close_suggestions(cur, hotel_slug, s["action_type"], "accepted", event_id=event_id)
    out = get_autonomy(cur, hotel_slug, s["action_type"])
    signals_mod.resolve_signal(
        cur, hotel_slug, _signal_key("core.autonomy_promotion", s["action_type"], event_id), resolution="accepted"
    )
    return out


def dismiss_suggestion(cur: Any, hotel_slug: str, event_id: int, *, actor: Any) -> dict:
    """Dismiss a promotion (core.autonomy.manage). The review stays quiet for
    this type for 30 days."""
    _check_perm(actor, "core.autonomy.manage")
    s = _suggestion(cur, hotel_slug, event_id)
    res = resolve_level(cur, hotel_slug, s["action_type"])
    _close_suggestions(cur, hotel_slug, s["action_type"], "dismissed", event_id=event_id)
    _autonomy_event(
        cur, hotel_slug, s["action_type"], "promotion_dismissed", from_level=res.configured, to_level=res.configured,
        policy_version=res.policy_version, actor=actor, resolves_event_id=event_id,
    )
    audit(
        cur,
        actor,
        "core.autonomy.promotion_dismissed",
        "action_autonomy",
        f"{hotel_slug}:{s['action_type']}",
        meta={"event_id": event_id, "to_level": s["to_level"]},
        hotel_slug=hotel_slug,
    )
    cur.execute(
        "SELECT id FROM signals WHERE hotel_slug = %s AND dedupe_key = %s",
        (hotel_slug, _signal_key("core.autonomy_promotion", s["action_type"], event_id)),
    )
    sig = cur.fetchone()
    if sig:
        signals_mod.set_status(cur, sig["id"], "ignored", resolution="dismissed")
    return get_autonomy(cur, hotel_slug, s["action_type"])


INCIDENT_KINDS = ("guest_complaint", "optout_spike", "meta_quality", "eval_regression")


def demote(
    cur: Any,
    hotel_slug: str,
    action_type: str,
    *,
    trigger: str,
    reason: str | None = None,
    evidence: list | None = None,
    actor: Any = None,
) -> dict:
    """Automatic demotion to min(current, approve) after an incident (a
    complaint, an opt-out spike, a Meta quality drop, an eval regression).
    Always records the event and a compliance signal for the brief; a hotel
    that never set this dial gets its first row here, after a seed event."""
    if trigger not in INCIDENT_KINDS + ("manual",):
        raise ValueError(f"trigger must be one of {INCIDENT_KINDS}")
    t = _require_type(action_type)
    row = _dial_row(cur, hotel_slug, action_type)
    current = row["level"] if row else t.default_autonomy
    target = min_level(current, "approve")
    version = (row["policy_version"] if row else 0) + 1
    ev = [e.model_dump(mode="json") if isinstance(e, BaseModel) else e for e in (evidence or [])]
    cur.execute(
        """
        INSERT INTO action_autonomy (hotel_slug, action_type, level, limits, policy_version, reason, updated_by, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, action_type) DO UPDATE SET
            level = EXCLUDED.level, policy_version = EXCLUDED.policy_version, reason = EXCLUDED.reason,
            updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
        RETURNING (xmax = 0) AS inserted
        """,
        (
            hotel_slug,
            action_type,
            target,
            Json((row or {}).get("limits") or _default_limits(t)),
            version,
            reason or f"demoted: {trigger}",
            _user_id(actor),
            clock.now(),
        ),
    )
    if cur.fetchone()["inserted"]:
        _seed_event(cur, hotel_slug, t)
    event_id = _autonomy_event(
        cur, hotel_slug, action_type, "demoted", from_level=current, to_level=target, policy_version=version,
        actor=actor, trigger=trigger, evidence=ev,
    )
    audit(
        cur,
        actor,
        "core.autonomy.demoted",
        "action_autonomy",
        f"{hotel_slug}:{action_type}",
        before={"level": current},
        after={"level": target, "policy_version": version, "trigger": trigger},
        hotel_slug=hotel_slug,
    )
    # A demotion makes any open promotion suggestion for the type stale.
    _close_suggestions(cur, hotel_slug, action_type, "superseded")
    _retire_autonomy_signals(cur, hotel_slug, "core.autonomy_promotion", action_type, resolution="demoted")
    signals_mod.upsert_signal(
        cur,
        hotel_slug,
        signals_mod.SignalIn(
            module="core",
            kind="core.autonomy_demoted",
            dedupe_key=_signal_key("core.autonomy_demoted", action_type, event_id),
            title=f"{t.label}: lowered to {target} after {trigger.replace('_', ' ')}",
            summary=reason,
            confidence=1.0,
            evidence=[Evidence.model_validate(e) for e in ev if isinstance(e, dict) and "kind" in e and "label" in e],
            subject_type="action_autonomy_event",
            subject_id=str(event_id),
        ),
    )
    return {"action_type": action_type, "from_level": current, "to_level": target, "policy_version": version, "event_id": event_id}


def report_incident(
    cur: Any, hotel_slug: str, *, kind: str, action_types: list[str], evidence: list | None = None, actor: Any = None
) -> list[dict]:
    if kind not in INCIDENT_KINDS:
        raise ValueError(f"kind must be one of {INCIDENT_KINDS}")
    return [demote(cur, hotel_slug, t, trigger=kind, evidence=evidence, actor=actor) for t in action_types]


# ─── Create ───


def _validate_params(ctx: ActionCtx, params: dict) -> dict:
    t = ctx.action_type
    out = dict(params or {})
    try:
        if t.params_model is not None:
            out = t.params_model.model_validate(out).model_dump(mode="json")
        if t.validate_params is not None:
            t.validate_params(ctx, out)
    except (ValidationError, ValueError) as exc:
        msg = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        raise ClefsError(422, f"invalid params: {msg}", "invalid_params") from exc
    return out


def _enqueue_execution(cur: Any, action: dict, *, delay_s: int = 0) -> None:
    # One task per transition that asks for execution. The event count only
    # grows, so a re-approval after a dial revocation or a failed attempt gets
    # a fresh key, while a double call within one transition stays deduped.
    cur.execute("SELECT COUNT(*) AS n FROM action_events WHERE action_id = %s", (action["id"],))
    generation = cur.fetchone()["n"]
    jobs.enqueue(
        "core.actions.execute",
        {"action_id": action["id"]},
        hotel_slug=action["hotel_slug"],
        run_at=clock.now() + timedelta(seconds=delay_s),
        dedupe_key=f"core.actions.execute:{action['id']}:{generation}",
        cur=cur,
    )


def _surface_for(level: str) -> str:
    """Where an action placed at `level` shows up (04 spec 1.1). suggest is a
    console draft and is never pushed to the brief; observe shows nowhere.
    The auto levels start on the brief and create_action moves them to none
    once the dial approves. Shared with the executor's dial revocation, so a
    revoked action lands exactly where a fresh one at that level would."""
    return {"observe": "none", "suggest": "console"}.get(level, "brief")


def create_action(
    cur: Any,
    hotel_slug: str,
    *,
    type: str,  # noqa: A002 - the contract's name
    title: str,
    params: dict | None = None,
    signal_id: int | None = None,
    expected_minor: int | None = None,
    expected_value: Decimal | str | None = None,
    currency: str | None = None,
    owner_role: str | None = None,
    due_at: datetime | None = None,
    assignee_user_id: int | None = None,
    dedupe_key: str | None = None,
    actor: Any = None,
    via: str = "system",
    expires_at: datetime | None = None,
    verify_until: datetime | None = None,
) -> dict:
    """Create an action as the dial says (see the module docstring). Returns the
    action (API shape) with `created` and `level` (the effective dial level it
    was created at); {"id": None, "status": "suppressed"} when the dial is off.
    With `dedupe_key`, an existing action is returned instead.

    expires_at (K1): a quote's validity or a plan's valid_until. If the action
    is still proposed or approved then, core.actions.expire moves it to
    `expired` and it can no longer be decided or executed.

    verify_until (04 spec 1.3 / 4.9): an absolute end of the verification
    window that overrides ActionType.verify_until (an inquiry verifies until
    its stay anchor + 14 days, whenever the quote was sent). Kept through the
    first execution; a dispute re-submission recomputes it from the type."""
    t = _require_type(type)
    hotel = get_hotel(cur, hotel_slug)
    if expires_at is not None and expires_at.tzinfo is None:
        raise ValueError("expires_at must be timezone-aware")
    if verify_until is not None and verify_until.tzinfo is None:
        raise ValueError("verify_until must be timezone-aware")
    if via not in VIAS or via == "dial":
        raise ClefsError(400, f"via must be one of {', '.join(v for v in VIAS if v != 'dial')}", "invalid_via")
    if dedupe_key:
        cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND dedupe_key = %s", (hotel_slug, dedupe_key))
        existing = cur.fetchone()
        if existing is not None:
            return {**to_api(dict(existing)), "created": False, "level": existing["dial_level_at_creation"]}
    if signal_id is not None:
        cur.execute("SELECT id FROM signals WHERE id = %s AND hotel_slug = %s", (signal_id, hotel_slug))
        if cur.fetchone() is None:
            raise ClefsError(404, "Signal not found", "signal_not_found")
    if expected_value is not None and expected_minor is None:
        currency = money.ccy(currency or hotel.currency)
        expected_minor = money.to_minor(expected_value, currency)
    if expected_minor is not None:
        currency = money.ccy(currency or hotel.currency)
    draft = {
        "id": None,
        "hotel_slug": hotel_slug,
        "type": t.key,
        "module": t.module,
        "title": title,
        "params": params or {},
        "expected_minor": expected_minor,
        "currency": currency,
        "signal_id": signal_id,
        "status": "proposed",
    }
    ctx = ActionCtx(cur=cur, hotel=hotel, action=draft, action_type=t, actor=actor)
    draft["params"] = _validate_params(ctx, draft["params"])
    res = resolve_level(cur, hotel_slug, t.key, ctx=ctx)
    if res.effective == "off":
        return {"id": None, "status": "suppressed", "reason": "dial_off", "type": t.key, "created": False}

    system_approves = res.effective in AUTO_LEVELS
    status = {"observe": "observed", "suggest": "proposed", "approve": "proposed"}.get(res.effective, "proposed")
    surface = _surface_for(res.effective)
    now = clock.now()
    cur.execute(
        """
        INSERT INTO actions (hotel_slug, signal_id, module, type, title, params, status, surface, dedupe_key,
                             expected_minor, currency, owner_role, assignee_role, assignee_user_id, due_at,
                             dial_level_at_creation, policy_version, limit_reasons, counts_toward_verified_profit,
                             created_by, created_via, created_at, updated_at, expires_at, verify_until)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (
            hotel_slug,
            signal_id,
            t.module,
            t.key,
            title,
            Json(draft["params"]),
            status,
            surface,
            dedupe_key,
            expected_minor,
            currency,
            owner_role or t.default_owner_role,
            owner_role or t.default_owner_role,
            assignee_user_id,
            due_at,
            res.effective,
            res.policy_version,
            res.limit_reasons + res.reasons,
            t.counts_toward_verified_profit,
            _user_id(actor),
            via,
            now,
            now,
            expires_at,
            verify_until,
        ),
    )
    action = dict(cur.fetchone())
    _log_event(
        cur, action, "created", actor=actor, from_status=None, to_status=status, via=via,
        data={"level": res.effective, "configured": res.configured, "reasons": res.reasons, "limit_reasons": res.limit_reasons},
    )
    if system_approves:
        action = _update(
            cur, action["id"],
            {"status": "approved", "decided_at": now, "decided_via": "dial", "surface": "none"},
        )
        # decided_via 'dial', actor SYSTEM logged as actor_type 'dial': the
        # ledger tells a system approval from a person's (04 spec 1.1 step 4),
        # and promotion_stats never counts it as a human decision.
        _log_event(
            cur, action, "approve", actor=SYSTEM, actor_type="dial", from_status="proposed", to_status="approved",
            via="dial", data={"level": res.effective, "policy_version": res.policy_version},
        )
        if t.executor is not None:
            _enqueue_execution(cur, action)
        if signal_id is not None:
            signals_mod.set_status(cur, signal_id, "decided")
    _changed(cur, action, actor=actor, verb="create")
    return {**to_api(action), "created": True, "level": res.effective}


# ─── Decide ───


def _wake(cur: Any, action: dict, *, actor: Any, via: str | None) -> dict:
    back = action.get("snoozed_from") or "proposed"
    woken = _update(cur, action["id"], {"status": back, "snoozed_from": None, "snoozed_until": None})
    _log_event(cur, woken, "unsnooze", actor=actor, from_status="snoozed", to_status=back, via=via)
    if woken.get("signal_id"):
        signals_mod.set_status(cur, woken["signal_id"], "decided" if back != "proposed" else "open")
    # An approved action snoozed before its executor ran had that run skipped
    # ("snoozed"), and the job is done. Queue it again, or it would sit in
    # approved forever with executed_at NULL. The unsnooze event just logged
    # makes the dedupe key fresh. A lapsed one is not queued: it is about to
    # be expired (and execute_action would expire it anyway).
    t = get_action_type(woken["type"])
    if (
        t is not None and t.executor is not None and woken["status"] in EXECUTABLE
        and woken.get("executed_at") is None and not _past_expiry(woken)
    ):
        _enqueue_execution(cur, woken)
    return woken


def _past_expiry(action: dict) -> bool:
    """K1: expires_at has passed while the action is still undecided or not yet
    run. A snoozed action counts by the state it was snoozed from: snoozing a
    quote does not extend its validity."""
    exp = action.get("expires_at")
    status = action["status"]
    if status == "snoozed":
        status = action.get("snoozed_from") or "proposed"
    return exp is not None and status in EXPIRABLE and exp <= clock.now()


def decide(
    cur: Any,
    action_id: int,
    decision: str,
    *,
    actor: Any,
    via: str = "console",
    params_patch: dict | None = None,
    assignee_user_id: int | None = None,
    assignee_role: str | None = None,
    assignee_staff_id: int | None = None,
    note: str | None = None,
    snooze_until: datetime | None = None,
    nonce: str | None = None,
    hotel_slug: str | None = None,
) -> dict:
    """Apply a person's (or the system's) decision. Enforces the type's
    decide_perm for HotelCtx actors and the legal transitions (409
    illegal_transition). Audited; an approval enqueues the executor.

    A magic-link session whose link lists action ids decides only those, with
    its membership role's full permissions (04 spec 1.1); the audit row then
    carries meta.via = "magic". `via` is where the decision came from
    (console | whatsapp | email | api | system); "dial" belongs to
    create_action's system approvals only (400 invalid_via)."""
    if decision not in DECISIONS:
        raise ClefsError(400, f"decision must be one of {', '.join(DECISIONS)}", "invalid_decision")
    if via not in VIAS or via == "dial":
        raise ClefsError(400, f"via must be one of {', '.join(v for v in VIAS if v != 'dial')}", "invalid_via")
    if hotel_slug is None and getattr(getattr(actor, "hotel", None), "slug", None):
        # A HotelCtx is one hotel's permissions: never let it decide another
        # hotel's action because the caller forgot hotel_slug.
        hotel_slug = actor.hotel.slug
    action = _load(cur, action_id, hotel_slug=hotel_slug)
    t = get_action_type(action["type"])
    _check_decide_perm(actor, action, t.decide_perm if t else "core.actions.decide")
    audit_meta = {"via": "magic", "channel": via} if _is_magic(actor) else None
    if nonce:
        # Checked before any write; the unique index still catches a race.
        cur.execute("SELECT 1 FROM action_events WHERE nonce = %s", (nonce,))
        if cur.fetchone() is not None:
            raise ClefsError(409, "This button was already used", "nonce_replayed")
    before = {"status": action["status"], "params": action["params"]}
    status = action["status"]

    if decision == "remind":
        if status not in REMINDABLE:
            raise ClefsError(409, f"Cannot remind on an action that is {status}", "illegal_transition")
        updated = _update(
            cur, action_id, {"reminders_sent": action["reminders_sent"] + 1, "last_reminded_at": clock.now()}
        )
        _log_event(cur, updated, "remind", actor=actor, from_status=status, to_status=status, via=via, data={"note": note}, nonce=nonce)
        _changed(cur, updated, actor=actor, verb="remind", before=before, meta=audit_meta)
        return to_api(updated)

    if status == "snoozed" and decision != "snooze":
        action = _wake(cur, action, actor=actor, via=via)
        status = action["status"]
    if _past_expiry(action):
        # core.actions.expire runs every 5 minutes; nobody may approve a quote
        # or plan in the gap after it lapsed.
        raise ClefsError(409, "This action has expired", "action_expired")
    to_status = TRANSITIONS.get((status, decision))
    if to_status is None:
        raise ClefsError(409, f"Cannot {decision} an action that is {status.replace('_', ' ')}", "illegal_transition")
    if decision == "adjust" and action.get("executed_at") is not None:
        raise ClefsError(409, "This action already ran; create a new one instead", "illegal_transition")

    now = clock.now()
    uid = _user_id(actor)
    human = not _is_system(actor)
    changes: dict[str, Any] = {"status": to_status}
    data: dict[str, Any] = {"note": note} if note else {}
    decided = {"decided_by": uid, "decided_at": now, "decided_via": via, "human_decided": human or action["human_decided"]}

    if decision in ("approve", "adjust", "dispute"):
        changes.update(decided)
        if decision == "adjust":
            if not params_patch:
                raise ClefsError(400, "adjust needs params_patch", "params_patch_required")
            ctx = _ctx_for(cur, action, t, actor) if t else None
            merged = {**(action["params"] or {}), **params_patch}
            if ctx is not None:
                merged = _validate_params(ctx, merged)
            data["params_diff"] = {k: [(action["params"] or {}).get(k), merged.get(k)] for k in params_patch}
            changes["params"] = merged
            changes["edited"] = True
        if decision == "dispute":
            changes["subtype"] = "dispute"
        if assignee_user_id is not None or assignee_role is not None or assignee_staff_id is not None:
            changes.update(
                status="assigned",
                assignee_user_id=assignee_user_id,
                assignee_role=assignee_role or action["assignee_role"],
                assignee_staff_id=assignee_staff_id,
            )
            to_status = "assigned"
    elif decision == "assign":
        if assignee_user_id is None and assignee_role is None and assignee_staff_id is None:
            raise ClefsError(400, "assign needs assignee_user_id, assignee_role or assignee_staff_id", "assignee_required")
        if assignee_user_id is not None:
            cur.execute(
                "SELECT 1 FROM hotel_memberships WHERE user_id = %s AND hotel_slug = %s AND is_active",
                (assignee_user_id, action["hotel_slug"]),
            )
            if cur.fetchone() is None:
                raise ClefsError(400, "That person is not a member of this hotel", "invalid_assignee")
        changes.update(
            assignee_user_id=assignee_user_id,
            assignee_role=assignee_role or action["assignee_role"],
            assignee_staff_id=assignee_staff_id,
        )
        if status == "proposed":
            changes.update(decided)
        data["assignee_user_id"] = assignee_user_id
    elif decision == "ignore":
        changes.update(decided)
    elif decision == "snooze":
        until = snooze_until or (now + timedelta(days=1))
        if until <= now:
            raise ClefsError(400, "snooze_until must be in the future", "invalid_snooze")
        changes.update(snoozed_until=until, snoozed_from=action["snoozed_from"] if status == "snoozed" else status)
        data["until"] = until.isoformat()

    updated = _update(cur, action_id, changes)
    _log_event(cur, updated, decision, actor=actor, from_status=status, to_status=to_status, via=via, data=data, nonce=nonce)

    sig = updated.get("signal_id")
    if sig:
        if decision == "ignore":
            signals_mod.set_status(cur, sig, "ignored", resolution="action_ignored")
        elif decision == "snooze":
            signals_mod.set_status(cur, sig, "snoozed", snoozed_until=updated["snoozed_until"])
        else:
            signals_mod.set_status(cur, sig, "decided")

    if decision in ("approve", "adjust", "dispute", "assign") and t is not None and t.executor is not None:
        if updated["status"] in EXECUTABLE and updated.get("executed_at") is None:
            _enqueue_execution(cur, updated)
    _changed(cur, updated, actor=actor, verb=decision, before=before, meta=audit_meta)
    return to_api(updated)


# ─── Work transitions ───


def _transition(
    cur: Any, action_id: int, *, allowed: frozenset[str] | set[str], to_status: str, event: str, actor: Any,
    changes: dict | None = None, data: dict | None = None, hotel_slug: str | None = None, via: str | None = None,
) -> dict:
    action = _load(cur, action_id, hotel_slug=hotel_slug)
    status = action["status"]
    if status == "snoozed" and "snoozed" not in allowed:
        action = _wake(cur, action, actor=actor, via=via)
        status = action["status"]
    if event != "cancel" and _past_expiry(action):
        # Same gap as decide(): the expire job runs every 5 minutes, and
        # nobody may start, finish or submit a lapsed quote or plan in it.
        # cancel still works (it closes the action either way).
        raise ClefsError(409, "This action has expired", "action_expired")
    if status not in allowed:
        raise ClefsError(409, f"Cannot {event.replace('_', ' ')} an action that is {status.replace('_', ' ')}", "illegal_transition")
    updated = _update(cur, action_id, {"status": to_status, **(changes or {})})
    _log_event(cur, updated, event, actor=actor, from_status=status, to_status=to_status, via=via, data=data)
    _changed(cur, updated, actor=actor, verb=event, before={"status": status})
    return updated


def _can_work(actor: Any, action: dict, t: ActionType | None) -> None:
    """Deciders, or the assignee themselves (staff move their own tasks)."""
    if _is_system(actor):
        return
    uid = _user_id(actor)
    if uid is not None and uid == action.get("assignee_user_id"):
        return
    principal = getattr(actor, "principal", None)
    if principal is not None and action.get("assignee_staff_id") in getattr(principal, "active_staff_ids", ()):
        return
    _check_perm(actor, t.decide_perm if t else "core.actions.decide")


def start(cur: Any, action_id: int, *, actor: Any, hotel_slug: str | None = None) -> dict:
    action = _load(cur, action_id, hotel_slug=hotel_slug, lock=False)
    _can_work(actor, action, get_action_type(action["type"]))
    return to_api(
        _transition(cur, action_id, allowed={"approved", "assigned"}, to_status="in_progress", event="start", actor=actor)
    )


def _verify_schedule(t: ActionType | None, now: datetime, action: dict | None = None) -> dict:
    """When to call the verifier first and when to give up. An absolute
    verify_until given to create_action wins on the first execution (the row
    still has executed_at NULL); later runs (a dispute re-submission) count
    from the type's verify_until again."""
    if t is None or t.verifier is None:
        return {}
    first = now + (t.verify_after or timedelta(0))
    until = (now + t.verify_until) if t.verify_until else None
    if action is not None and action.get("executed_at") is None and action.get("verify_until") is not None:
        until = action["verify_until"]
    return {"verify_after": first, "next_verify_at": first, "verify_until": until}


def mark_done(
    cur: Any, action_id: int, *, actor: Any, result: dict | None = None, note: str | None = None, hotel_slug: str | None = None
) -> dict:
    """A human task is finished (approved / assigned / in_progress -> done).
    Schedules verification when the type has a verifier."""
    action = _load(cur, action_id, hotel_slug=hotel_slug, lock=False)
    t = get_action_type(action["type"])
    _can_work(actor, action, t)
    now = clock.now()
    changes = {"executed_at": action.get("executed_at") or now, **_verify_schedule(t, now, action)}
    if result is not None:
        changes["execution_result"] = result
    updated = _transition(
        cur, action_id, allowed=WORKING, to_status="done", event="done", actor=actor, changes=changes,
        data={"note": note} if note else None,
    )
    return to_api(updated)


def submit(cur: Any, action_id: int, *, actor: Any = None, data: dict | None = None) -> dict:
    """Dispute path: the packet went to the counterparty (or went again)."""
    action = _load(cur, action_id, lock=False)
    if action.get("subtype") != "dispute":
        raise ClefsError(409, "Only a disputed action can be submitted", "illegal_transition")
    t = get_action_type(action["type"])
    now = clock.now()
    return to_api(
        _transition(
            cur, action_id, allowed={"approved", "assigned", "in_progress", "rejected_by_counterparty"},
            to_status="submitted", event="submit", actor=actor, data=data,
            changes={"executed_at": action.get("executed_at") or now, **_verify_schedule(t, now, action)},
        )
    )


def await_counterparty(cur: Any, action_id: int, *, actor: Any = None, data: dict | None = None) -> dict:
    return to_api(
        _transition(cur, action_id, allowed={"submitted"}, to_status="awaiting_counterparty", event="await_counterparty", actor=actor, data=data)
    )


def give_up(cur: Any, action_id: int, *, actor: Any = None, note: str | None = None) -> dict:
    """Dispute path end: the counterparty rejected and nothing more will be tried."""
    return to_api(
        _transition(
            cur, action_id, allowed={"rejected_by_counterparty", "awaiting_counterparty"}, to_status="unresolved",
            event="give_up", actor=actor, data={"note": note} if note else None,
        )
    )


def cancel(cur: Any, action_id: int, *, actor: Any = None, reason: str = "superseded") -> dict:
    """Supersede / expire: any open state -> cancelled."""
    action = _load(cur, action_id, lock=False)
    updated = _transition(
        cur, action_id, allowed=CANCELLABLE, to_status="cancelled", event="cancel", actor=actor, data={"reason": reason}
    )
    if action.get("signal_id") and reason in ("superseded", "expired"):
        signals_mod.set_status(cur, action["signal_id"], "superseded" if reason == "superseded" else "expired")
    return to_api(updated)


# ─── Verification ───


def record_verification(
    cur: Any,
    action_id: int,
    *,
    verified: bool | None = None,
    status: str | None = None,
    outcome_value: Decimal | str | None = None,
    outcome_minor: int | None = None,
    currency: str | None = None,
    evidence: list | None = None,
    confidence: float = 1.0,
    attribution: str = "caused",
    counts_toward_verified_profit: bool | None = None,
    actor: Any = None,
    result: VerifyResult | dict | None = None,
) -> dict:
    """Close the loop. done -> verified | not_verified; on the dispute path
    submitted / awaiting_counterparty -> verified | rejected_by_counterparty.
    status="pending" only reschedules the next check (verify_every) until
    verify_until, after which it becomes not_verified.

    The 04 spec form is record_verification(cur, id, result=VerifyResult(...))
    (money in minor units); the keyword form stays for 01 callers. Passing
    `result` together with any outcome keyword is a programming error."""
    if result is not None:
        given = (verified, status, outcome_value, outcome_minor, currency, evidence, counts_toward_verified_profit)
        if any(v is not None for v in given) or confidence != 1.0 or attribution != "caused":
            raise ValueError("pass either result= or the outcome keywords, not both")
        vr = result if isinstance(result, VerifyResult) else VerifyResult.model_validate(result)
        return record_verification(
            cur, action_id, status=vr.status, outcome_minor=vr.outcome_minor, outcome_value=vr.outcome_value,
            currency=vr.currency, evidence=vr.evidence, confidence=vr.confidence, attribution=vr.attribution,
            counts_toward_verified_profit=vr.counts_toward_verified_profit, actor=actor,
        )
    action = _load(cur, action_id)
    t = get_action_type(action["type"])
    state = status or ("verified" if verified else "not_verified")
    if state not in ("verified", "not_verified", "pending"):
        raise ClefsError(400, "status must be verified, not_verified or pending", "invalid_verification")
    if action["status"] not in VERIFYING:
        raise ClefsError(409, f"Cannot verify an action that is {action['status']}", "illegal_transition")
    now = clock.now()
    ev = [e.model_dump(mode="json") if isinstance(e, BaseModel) else e for e in (evidence or [])]

    if state == "pending":
        every = (t.verify_every if t else None) or timedelta(days=1)
        until = action.get("verify_until")
        if until is not None and now + every > until:
            state = "not_verified"
        else:
            updated = _update(cur, action_id, {"next_verify_at": now + every})
            _log_event(cur, updated, "verify_pending", actor=actor, from_status=action["status"], to_status=action["status"], data={"evidence": ev})
            return to_api(updated)

    dispute = action["status"] in ("submitted", "awaiting_counterparty")
    to_status = "verified" if state == "verified" else ("rejected_by_counterparty" if dispute else "not_verified")
    # One currency per action: actions.currency also labels expected_minor,
    # and the verified-value metric sums outcomes in the hotel's currency. An
    # outcome in another currency is converted on write (the original is kept
    # in `verification`); with no rate, 409 fx_unavailable and the verify job
    # retries later rather than storing a mislabelled amount.
    ccy = (action.get("currency") or "").strip() or None
    original: dict | None = None
    if outcome_minor is not None or outcome_value is not None:
        hotel = get_hotel(cur, action["hotel_slug"])
        target = money.ccy(ccy or hotel.currency)
        given = money.ccy(currency or target)
        if outcome_minor is None:
            outcome_minor = money.to_minor(outcome_value, given)
        if given != target:
            original = {"outcome_minor": outcome_minor, "currency": given}
            outcome_minor = money.convert_minor(cur, outcome_minor, given, target, htime.local_date(hotel))
        ccy = target
    counts = counts_toward_verified_profit if counts_toward_verified_profit is not None else bool(t and t.counts_toward_verified_profit)
    counts = counts and to_status == "verified" and attribution == "caused"
    changes = {
        "status": to_status,
        "verified_at": now,
        "next_verify_at": None,
        "verification": {
            "evidence": ev, "confidence": confidence, "attribution": attribution, "result": state,
            **({"original_outcome": original} if original else {}),
        },
        "outcome_minor": outcome_minor,
        "currency": ccy,
        "outcome_confidence": round(float(confidence), 2),
        "attribution": attribution,
        "counts_toward_verified_profit": counts,
    }
    updated = _update(cur, action_id, changes)
    _log_event(
        cur, updated, "verified" if to_status == "verified" else to_status, actor=actor, from_status=action["status"],
        to_status=to_status, data={"outcome_minor": outcome_minor, "currency": ccy, "attribution": attribution},
    )
    if updated.get("signal_id") and to_status == "verified":
        signals_mod.set_status(cur, updated["signal_id"], "resolved", resolution="verified")
    _changed(cur, updated, actor=actor, verb="verify", before={"status": action["status"]})
    return to_api(updated)


def reverse_verification(cur: Any, action_id: int, *, reason: str, evidence: list | None = None, actor: Any = None) -> dict:
    """An outcome unwound later (refund, PMS cancellation): the value nets to
    zero; the status stays verified (terminal in Canon 7)."""
    action = _load(cur, action_id)
    if action["status"] != "verified":
        raise ClefsError(409, "Only a verified action can be reversed", "illegal_transition")
    if action.get("reversed_at") is not None:
        raise ClefsError(409, "Already reversed", "already_reversed")
    ev = [e.model_dump(mode="json") if isinstance(e, BaseModel) else e for e in (evidence or [])]
    updated = _update(
        cur, action_id,
        {"outcome_minor": 0 if action.get("outcome_minor") is not None else None, "reversed_at": clock.now(), "reversal_reason": reason},
    )
    _log_event(
        cur, updated, "verification_reversed", actor=actor, from_status="verified", to_status="verified",
        data={"reason": reason, "previous_outcome_minor": action.get("outcome_minor"), "currency": action.get("currency"), "evidence": ev},
    )
    _changed(cur, updated, actor=actor, verb="reverse", before={"outcome_minor": action.get("outcome_minor")})
    return to_api(updated)


# ─── Execution (the core.actions.execute task) ───


def _as_exec_result(raw: Any) -> ExecResult:
    """Executors may return an ExecResult, a plain dict of result data, or None."""
    if isinstance(raw, ExecResult):
        return raw
    if raw is None:
        return ExecResult()
    if isinstance(raw, dict):
        return ExecResult(data=raw)
    return ExecResult.model_validate(raw)


def execute_action(cur: Any, action_id: int) -> dict:
    """Run the executor for an approved action. Idempotent: an action that
    already ran, or is no longer approved/assigned, is skipped."""
    action = _load(cur, action_id)
    t = get_action_type(action["type"])
    if t is None or t.executor is None:
        return {"skipped": "no executor"}
    if action.get("executed_at") is None and _past_expiry(action):
        # A retry (backoff up to 16 minutes) or a woken snooze can come due
        # after expires_at but before core.actions.expire has run. A lapsed
        # quote must never be sent: expire it here instead of executing.
        _expire(cur, action)
        return {"skipped": "expired"}
    if action["status"] not in EXECUTABLE or action.get("executed_at") is not None:
        return {"skipped": action["status"]}
    ctx = _ctx_for(cur, action, t)
    res = resolve_level(cur, action["hotel_slug"], t.key, ctx=ctx)
    approved_by_dial = not action["human_decided"]
    if approved_by_dial and res.effective not in AUTO_LEVELS:
        updated = _revoke_dial_approval(cur, action, res)
        return {"revoked": res.effective, "status": updated["status"]}

    attempts = action["execution_attempts"] + 1
    cur.execute("SAVEPOINT clefs_action_exec")
    try:
        result = _as_exec_result(t.executor(ctx))
        cur.execute("RELEASE SAVEPOINT clefs_action_exec")
    except Exception as exc:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_action_exec")
        cur.execute("RELEASE SAVEPOINT clefs_action_exec")
        log.exception("executor for %s #%s failed", t.key, action_id)
        error = f"{type(exc).__name__}: {exc}"[:2000]
        if attempts < EXECUTION_MAX_ATTEMPTS:
            updated = _update(cur, action_id, {"execution_attempts": attempts, "execution_error": error})
            _log_event(cur, updated, "execution_failed", actor=None, actor_type="system", from_status=action["status"],
                       to_status=action["status"], data={"error": error, "attempt": attempts})
            _enqueue_execution(cur, updated, delay_s=EXECUTION_BACKOFF_S * 2 ** (attempts - 1))
            return {"failed": error, "attempt": attempts}
        _fail(cur, action, attempts=attempts, error=error)
        return {"failed": error, "attempt": attempts, "status": "failed"}
    if not result.ok:
        # A declined result is final: the executor looked and said no.
        error = result.error or "executor declined"
        _fail(cur, action, attempts=attempts, error=error)
        return {"failed": error, "attempt": attempts, "status": "failed"}

    now = clock.now()
    to_status = "submitted" if action.get("subtype") == "dispute" else "done"
    updated = _update(
        cur,
        action_id,
        {
            "status": to_status,
            "executed_at": now,
            "execution_attempts": attempts,
            "execution_error": None,
            "execution_result": result.data,
            "dial_level_at_execution": res.effective,
            "policy_version": res.policy_version,
            "template_id": result.template_id,
            "template_category": result.template_category,
            "consent_basis": result.consent_basis,
            "reversal_handle": result.reversal_handle,
            **_verify_schedule(t, now, action),
        },
    )
    _log_event(
        cur, updated, "executed", actor=None, actor_type="system", from_status=action["status"], to_status=to_status,
        data={"level": res.effective, "policy_version": res.policy_version, "template_id": result.template_id},
    )
    _changed(cur, updated, actor=None, verb="execute", before={"status": action["status"]})
    return {"executed": True, "status": to_status}


# Where a dial-approved action goes when its level drops before it runs.
# approve / suggest: back to proposed, on the surface that level uses.
DIAL_REVOKED_STATUS = {"observe": "observed", "off": "cancelled"}


def _revoke_dial_approval(cur: Any, action: dict, res: Resolution) -> dict:
    """04 spec 1.1 step 5: the dial approved this action, and by the time
    the executor reached it the effective level had dropped (a demotion, an
    owner lowering the dial, a gate closing). It never runs; it lands where
    create_action would place a new action at the new level:

        approve   proposed, surface brief (a one-tap approval)
        suggest   proposed, surface console (a draft; never pushed to the brief)
        observe   observed (terminal: logged, never executed)
        off       cancelled, reason dial_off (the feature is switched off)

    Putting every revocation on the brief would hand the owner, as one-tap
    approvals, the very sends they just switched off or down to drafts after
    a bad one. The signal goes back to open in every case: at creation only a
    dial approval marks it decided. The event is always dial_revoked;
    to_status and data.reason say where it went."""
    to_status = DIAL_REVOKED_STATUS.get(res.effective, "proposed")
    changes: dict[str, Any] = {
        "status": to_status,
        "surface": _surface_for(res.effective) if to_status == "proposed" else "none",
        "decided_at": None,
        "decided_via": None,
        "limit_reasons": res.limit_reasons + res.reasons,
    }
    updated = _update(cur, action["id"], changes)
    data: dict[str, Any] = {"level": res.effective, "policy_version": res.policy_version}
    if res.effective == "off":
        data["reason"] = "dial_off"
    _log_event(
        cur, updated, "dial_revoked", actor=None, actor_type="dial", from_status=action["status"], to_status=to_status,
        data=data,
    )
    if updated.get("signal_id"):
        signals_mod.set_status(cur, updated["signal_id"], "open")
    _changed(cur, updated, actor=None, verb="dial_revoked", before={"status": action["status"]})
    return updated


def _fail(cur: Any, action: dict, *, attempts: int, error: str) -> dict:
    """K1 `failed`: the executor declined (ok=false) or raised on its last
    attempt. Terminal; the signal reopens so the brief shows the problem
    again and someone can try another way."""
    updated = _update(
        cur, action["id"], {"status": "failed", "execution_attempts": attempts, "execution_error": error}
    )
    _log_event(
        cur, updated, "execution_failed", actor=None, actor_type="system", from_status=action["status"],
        to_status="failed", data={"error": error, "attempt": attempts, "final": True},
    )
    if updated.get("signal_id"):
        signals_mod.set_status(cur, updated["signal_id"], "open")
    _changed(cur, updated, actor=None, verb="fail", before={"status": action["status"]})
    return updated


def _expire(cur: Any, action: dict) -> dict:
    """K1 `expired` for one locked row. Terminal; the signal expires too, so a
    detector that still sees the problem raises it afresh. A snoozed row
    leaves its snooze fields cleared (it will never wake)."""
    updated = _update(cur, action["id"], {"status": "expired", "snoozed_from": None, "snoozed_until": None})
    _log_event(
        cur, updated, "expire", actor=None, actor_type="system", from_status=action["status"], to_status="expired",
        data={"expires_at": action["expires_at"].isoformat()},
    )
    if updated.get("signal_id"):
        signals_mod.set_status(cur, updated["signal_id"], "expired", resolution="action_expired")
    _changed(cur, updated, actor=None, verb="expire", before={"status": action["status"]})
    return updated


def expire_due_actions(cur: Any) -> int:
    """K1 `expired`: proposed / approved actions whose expires_at has passed
    (a lapsed quote, a plan past valid_until), including ones snoozed from
    either state: a snooze defers the decision, not the quote's validity."""
    cur.execute(
        """
        SELECT id FROM actions
        WHERE expires_at IS NOT NULL AND expires_at <= %s
          AND status IN ('proposed', 'approved', 'snoozed')
          AND (status <> 'snoozed' OR COALESCE(snoozed_from, 'proposed') IN ('proposed', 'approved'))
        ORDER BY expires_at LIMIT 1000 FOR UPDATE SKIP LOCKED
        """,
        (clock.now(),),
    )
    ids = [r["id"] for r in cur.fetchall()]
    for action_id in ids:
        _expire(cur, _load(cur, action_id))
    return len(ids)


def run_verifier(cur: Any, action_id: int) -> dict:
    action = _load(cur, action_id)
    t = get_action_type(action["type"])
    if t is None or t.verifier is None or action["status"] not in VERIFYING:
        return {"skipped": True}
    ctx = _ctx_for(cur, action, t)
    raw = t.verifier(ctx)
    vr = raw if isinstance(raw, VerifyResult) else VerifyResult.model_validate(raw)
    out = record_verification(cur, action_id, result=vr)
    return {"status": out["status"]}


@jobs.task("core.actions.execute", max_attempts=3, backoff_s=30)
def _execute_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    return execute_action(ctx.cur, int(payload["action_id"]))


@jobs.scheduled("core.actions.verify", jobs.every(hours=1), per_hotel=False, timeout_s=300)
def _verify_job(ctx: jobs.JobCtx) -> dict:
    """Call each due verifier. One savepoint per action, so one broken
    verifier cannot stop the rest."""
    ctx.cur.execute(
        """
        SELECT id FROM actions
        WHERE status IN ('done', 'submitted', 'awaiting_counterparty') AND next_verify_at IS NOT NULL
          AND next_verify_at <= %s
        ORDER BY next_verify_at LIMIT 500
        """,
        (ctx.now,),
    )
    ids = [r["id"] for r in ctx.cur.fetchall()]
    done = failed = 0
    for action_id in ids:
        ctx.cur.execute("SAVEPOINT clefs_verify_one")
        try:
            run_verifier(ctx.cur, action_id)
            ctx.cur.execute("RELEASE SAVEPOINT clefs_verify_one")
            done += 1
        except Exception:
            ctx.cur.execute("ROLLBACK TO SAVEPOINT clefs_verify_one")
            ctx.cur.execute("RELEASE SAVEPOINT clefs_verify_one")
            log.exception("verifier failed for action %s", action_id)
            failed += 1
    return {"checked": done, "failed": failed}


def wake_due(cur: Any) -> int:
    """Snoozed actions whose time came go back to their previous state. One
    whose expires_at passed during the snooze is expired instead (the wake and
    expire jobs run in either order within a tick). Returns how many woke."""
    cur.execute("SELECT id FROM actions WHERE status = 'snoozed' AND snoozed_until <= %s FOR UPDATE", (clock.now(),))
    ids = [r["id"] for r in cur.fetchall()]
    woken = 0
    for action_id in ids:
        action = _load(cur, action_id)
        if _past_expiry(action):
            _expire(cur, action)
            continue
        _wake(cur, action, actor=None, via="system")
        woken += 1
    return woken


@jobs.scheduled("core.actions.wake", jobs.every(minutes=5), per_hotel=False)
def _wake_job(ctx: jobs.JobCtx) -> dict:
    woken = wake_due(ctx.cur)
    signals_touched = signals_mod.expire_due(ctx.cur)
    return {"actions_woken": woken, "signals_touched": signals_touched}


@jobs.scheduled("core.actions.expire", jobs.every(minutes=5), per_hotel=False)
def _expire_job(ctx: jobs.JobCtx) -> dict:
    return {"actions_expired": expire_due_actions(ctx.cur)}


@jobs.scheduled("core.autonomy_review", jobs.daily_at("04:10"))
def _autonomy_review_job(ctx: jobs.JobCtx) -> dict:
    return {"suggested": review_promotions(ctx.cur, ctx.hotel_slug)}


# ─── Core action types ───

register_action_type(
    ActionType(
        key="core.task",
        module="core",
        label="Task",
        description="A person does something by hand; created from the console or a signal.",
        default_owner_role="gm",
        default_level="approve",
        max_level="approve",
    )
)
