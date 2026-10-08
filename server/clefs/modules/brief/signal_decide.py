"""
Decisions on a brief item that has a signal but no action yet.

A detector that raises a signal without proposing an action (Service's L1
incidents, a VCC to charge, the GHC checklist) left its brief card with
"No action proposed yet" and nothing to tap, so a Today brief could be five
dead ends (review completeness-05, QA OWN-02). The card now offers:

    propose   create the signal's recommended action (else a core.task named
              after the signal) as a proposed action linked to the signal;
              the card then decides it through core decide() as usual.
    assign    propose, then decide "assign" on it: a person owns it and the
              escalation ladder watches its deadline.
    snooze    hide the signal until a time (core set_status "snoozed"); the
              core wake job reopens it.
    dismiss   close the signal as "ignored" (reopens only if its impact grows
              by 25%, core signals rule).

Nothing here is a state machine: actions go through clefs.core.actions
(create_action + decide, with the dial and role checks) and signals through
clefs.core.signals.set_status.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal

from clefs.auth.deps import HotelCtx
from clefs.core import actions as core_actions
from clefs.core import clock, money
from clefs.core import signals as core_signals
from clefs.core.errors import ClefsError

SignalDecision = Literal["propose", "assign", "snooze", "dismiss"]
OPEN_STATUSES = ("open", "surfaced")
SNOOZE_DEFAULT = timedelta(hours=24)
SNOOZE_MAX = timedelta(days=30)
# Money a proposed action carries: never a verified figure (that money is banked).
MONEY_SECTIONS = ("money_lost", "money_available")


# ─── Proposal from a signal ───


def _proposal(sig: dict) -> dict:
    """create_action kwargs from the signal's recommended action. An
    unregistered or missing type becomes a core.task (a person does it by
    hand) titled after the recommendation, so there is always something to
    own; the recommended type rides along in params for the record."""
    rec = sig.get("recommended_action") or {}
    t = core_actions.get_action_type(str(rec.get("type"))) if rec.get("type") else None
    params = dict(rec.get("params") or {}) if t else {"recommended_type": rec.get("type"), "signal_kind": sig["kind"]}
    title = str(rec.get("label") or sig["title"])[:300]
    ev = rec.get("expected_value")
    kw: dict[str, Any] = {"type": t.key if t else "core.task", "title": title, "params": params}
    if ev is not None and sig.get("currency"):
        kw.update(expected_value=Decimal(str(ev)), currency=sig["currency"])
    elif sig.get("impact_minor") and sig.get("currency") and sig.get("section") in MONEY_SECTIONS:
        kw.update(expected_minor=int(sig["impact_minor"]), currency=sig["currency"])
    if rec.get("due_in_hours"):
        kw["due_at"] = clock.now() + timedelta(hours=float(rec["due_in_hours"]))
    elif sig.get("due_at"):
        kw["due_at"] = sig["due_at"]
    kw["owner_role"] = rec.get("owner_role") or sig.get("owner_role")
    return kw


def _live_action(cur: Any, hotel_slug: str, sig: dict) -> dict | None:
    """The signal's newest non-terminal action (get_signal carries no
    action_id; only list rows do), so a second tap never duplicates one."""
    rows = core_actions.list_actions(cur, hotel_slug, signal_id=int(sig["id"]), limit=20)
    live = [a for a in rows if not a.get("terminal")]
    return max(live, key=lambda a: a["id"]) if live else None


def _propose(cur: Any, ctx: HotelCtx, sig: dict) -> dict:
    live = _live_action(cur, ctx.slug, sig)
    if live is not None:
        return live  # someone proposed it meanwhile: decide that one
    kw = _proposal(sig)
    t = core_actions.get_action_type(kw["type"])
    if t is None or not ctx.can(t.decide_perm, "core.actions.decide"):
        raise ClefsError(403, "Your role cannot propose this action", "forbidden")
    try:
        a = core_actions.create_action(cur, ctx.slug, signal_id=sig["id"], actor=ctx, via="console", **kw)
    except ValueError as e:  # the type's validate_params refused the recommended params
        raise ClefsError(422, f"This recommendation cannot be proposed as is: {e}", "invalid_params") from e
    if a.get("status") == "suppressed" or a.get("id") is None:
        raise ClefsError(409, "This kind of action is switched off in the autonomy settings", "action_suppressed")
    return a


def _may_close(ctx: HotelCtx, sig: dict) -> bool:
    """Hiding an item from everyone's brief is an owner or GM call, or the
    call of the role the item belongs to (finance snoozes a finance to-do)."""
    return ctx.can("core.actions.decide", "brief.manage") or (
        bool(sig.get("owner_role")) and sig.get("owner_role") == ctx.role)


# ─── Entry point ───


def decide_signal(cur: Any, ctx: HotelCtx, signal_id: int, decision: SignalDecision, *,
                  assignee_user_id: int | None = None, assignee_role: str | None = None,
                  snooze_until: datetime | None = None, note: str | None = None) -> dict:
    """-> {signal, action}. 404 unknown signal, 409 when it is already closed."""
    sig = core_signals.get_signal(cur, ctx.slug, signal_id)
    if sig is None:
        raise ClefsError(404, "Signal not found", "signal_not_found")
    if sig["status"] not in OPEN_STATUSES:
        state = str(sig["status"]).replace("_", " ")
        raise ClefsError(409, f"This item was already handled (it is {state}).", "signal_closed")
    action: dict | None = None
    if decision in ("propose", "assign"):
        action = _propose(cur, ctx, sig)
        if decision == "assign":
            if not (assignee_user_id or assignee_role):
                raise ClefsError(400, "Choose a person or a role to assign it to", "assignee_required")
            action = core_actions.decide(cur, int(action["id"]), "assign", actor=ctx, via="console",
                                         assignee_user_id=assignee_user_id, assignee_role=assignee_role,
                                         note=note, hotel_slug=ctx.slug)
    elif decision in ("snooze", "dismiss") and not _may_close(ctx, sig):
        raise ClefsError(403, "Only an owner, a GM or the item's owner can snooze or dismiss it", "forbidden")
    elif decision == "snooze":
        now = clock.now()
        until = snooze_until or now + SNOOZE_DEFAULT
        if until.tzinfo is None:
            raise ClefsError(400, "snooze_until must carry a timezone", "invalid_snooze")
        if until <= now or until > now + SNOOZE_MAX:
            raise ClefsError(400, "Snooze for up to 30 days from now", "invalid_snooze")
        core_signals.set_status(cur, sig["id"], "snoozed", snoozed_until=until, resolution=note)
    elif decision == "dismiss":
        core_signals.set_status(cur, sig["id"], "ignored", resolution=note or "dismissed from the brief")
    else:
        raise ClefsError(400, "decision must be propose, assign, snooze or dismiss", "invalid_decision")
    out = core_signals.get_signal(cur, ctx.slug, sig["id"])
    if out and out.get("impact_minor") is not None and out.get("currency"):
        out["impact_value"] = money.money_str(out["impact_minor"], out["currency"])
    return {"signal": out, "action": action}
