"""
Demand Radar action types (02 spec 4.3 graph actions; Canon 7, 17).

| Key                  | Kind        | Ceiling  | What happens on approval                                   |
|----------------------|-------------|----------|------------------------------------------------------------|
| dr.rate_change       | human task  | approve  | the owner changes the BAR in their own channel manager;    |
|                      |             | (always) | verified from later BAR evidence within +/- 2% (observed)  |
| dr.pre_arrival_offer | human task  | approve  | the drafted offer goes to booked guests (by the owner)      |
| dr.staffing          | human task  | approve  | the GM adds housekeeping shifts                             |
| dr.kb_update         | executor    | approve  | concierge.api.propose_kb_entry queues the KB entry; the     |
|                      |             |          | concierge's own approval puts it live (verified then)      |
| dr.campaign          | human task  | approve  | the draft is exportable (CSV + copy); nothing is sent      |

Rates are never pushed to a channel manager and campaigns are never sent
from here, so every ceiling is approve. None counts toward verified profit:
Demand outcomes are observed, not causal.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from clefs.core.actions import ActionCtx, ActionType, ExecResult, VerifyResult, register_action_type

log = logging.getLogger("clefs.demand.actions")

RATE_CHANGE = "dr.rate_change"
PRE_ARRIVAL_OFFER = "dr.pre_arrival_offer"
STAFFING = "dr.staffing"
KB_UPDATE = "dr.kb_update"
CAMPAIGN = "dr.campaign"
DECIDE_PERM = "demand.manage"


# ─── Rate change: bounds and verification ───


# The facts a rate action was proposed on. An adjust edits the rate only:
# a patched ceiling_minor would let any rate through its own bounds check,
# and a patched current_bar_minor is the QA OWN-01 failure (the owner's rate
# stored where nothing reads it).
RATE_FIXED = ("recommendation_id", "stay_date", "room_type_code", "currency", "current_bar_minor", "floor_minor",
              "ceiling_minor", "adjust")
RATE_FIELD = "recommended_minor"


def rate_adjust_spec(floor_minor: int | None, ceiling_minor: int | None) -> dict:
    """params.adjust for dr.rate_change: the one editable field and its
    bounds, honoured first by every Adjust surface (brief console, WhatsApp,
    magic brief) before any key-name heuristic."""
    return {"field": RATE_FIELD, "min_minor": floor_minor, "max_minor": ceiling_minor, "label": "rate"}


def _legacy_rate_patch(before: dict, params: dict) -> None:
    """Actions created before params.adjust existed: an Adjust surface that
    guessed current_bar_minor meant the rate. Move the value (in place:
    core keeps the dict validate_params saw) and restore the fact."""
    if params.get("current_bar_minor") != before.get("current_bar_minor") \
            and params.get(RATE_FIELD) == before.get(RATE_FIELD) and "adjust" not in before:
        params[RATE_FIELD] = params.get("current_bar_minor")
        params["current_bar_minor"] = before.get("current_bar_minor")


def _validate_rate(ctx: ActionCtx, params: dict) -> None:
    """An adjusted rate stays inside the night's guardrails, and only the
    rate moves (ctx.action_id is set only on core's adjust path; create's
    gate sees the draft, whose params are these params)."""
    if ctx.action_id is not None:
        before = ctx.params
        _legacy_rate_patch(before, params)
        moved = [k for k in RATE_FIXED if k in before and params.get(k) != before.get(k)]
        if moved:
            raise ValueError(f"only the rate can be adjusted ({', '.join(moved)} is fixed)")
    rec, lo, hi = params.get("recommended_minor"), params.get("floor_minor"), params.get("ceiling_minor")
    if rec is None:
        raise ValueError("recommended_minor is required")
    rec = int(rec)
    if rec <= 0:
        raise ValueError("the rate must be positive")
    if lo is not None and rec < int(lo):
        raise ValueError("the rate is below the floor")
    if hi is not None and rec > int(hi):
        raise ValueError("the rate is above the ceiling")


def _verify_rate(ctx: ActionCtx) -> VerifyResult | dict:
    from clefs.modules.demand import measure

    return measure.verify_rate_action(ctx)


# ─── KB update: executor and verifier ───


def _execute_kb(ctx: ActionCtx) -> ExecResult:
    from clefs.modules.concierge import api as concierge_api

    p = ctx.params
    try:
        entry_id = concierge_api.propose_kb_entry(
            ctx.cur, ctx.hotel_slug, question_en=str(p.get("question_en") or "")[:300],
            answer_en=str(p.get("answer_en") or "")[:2000], source="demand", actor=ctx.actor)
    except Exception as exc:  # noqa: BLE001  (a stubbed or failing concierge: the action fails, the signal reopens)
        log.warning("propose_kb_entry failed: %s", exc)
        return ExecResult(ok=False, error=str(exc)[:200])
    return ExecResult(ok=True, data={"kb_entry_id": entry_id})


def _verify_kb(ctx: ActionCtx) -> VerifyResult:
    from clefs.modules.concierge import api as concierge_api

    entry_id = ((ctx.action.get("execution_result") or {}).get("data") or {}).get("kb_entry_id") \
        or (ctx.action.get("execution_result") or {}).get("kb_entry_id")
    status = None
    if entry_id:
        try:
            status = (concierge_api.kb_entry_status(ctx.cur, ctx.hotel_slug, int(entry_id)) or {}).get("status")
        except Exception:  # noqa: BLE001
            status = None
    if status == "approved":
        return VerifyResult(status="verified", attribution="observed", counts_toward_verified_profit=False)
    if status in ("rejected", "archived"):
        return VerifyResult(status="not_verified", attribution="observed")
    return VerifyResult(status="pending", attribution="observed")


# ─── Registration ───

register_action_type(ActionType(
    key=RATE_CHANGE, module="demand", label="Change a night's BAR (Demand Radar)",
    verifier=_verify_rate, verify_after=timedelta(0), verify_every=timedelta(hours=6),
    verify_until=timedelta(days=10), default_owner_role="revenue", default_level="approve",
    always_approve=True, validate_params=_validate_rate, decide_perm=DECIDE_PERM,
    description="A suggested BAR for one night and room type. Never pushed to a channel manager: the owner "
                "changes the rate and later BAR evidence verifies it (observed, not causal).",
))
register_action_type(ActionType(
    key=PRE_ARRIVAL_OFFER, module="demand", label="Send a pre-arrival offer", default_owner_role="revenue",
    default_level="approve", max_level="approve", decide_perm=DECIDE_PERM,
    description="A drafted offer for guests already booked on a soft night or around an event.",
))
register_action_type(ActionType(
    key=STAFFING, module="demand", label="Add housekeeping cover", default_owner_role="gm",
    default_level="approve", max_level="approve", decide_perm=DECIDE_PERM,
    description="Room attendants needed (departures + 0.8 x stayovers, 14 credits each) against the roster.",
))
register_action_type(ActionType(
    key=KB_UPDATE, module="demand", label="Add event travel advice to the concierge", executor=_execute_kb,
    verifier=_verify_kb, verify_after=timedelta(hours=1), verify_every=timedelta(days=1),
    verify_until=timedelta(days=14), default_owner_role="gm", default_level="approve", max_level="approve",
    decide_perm=DECIDE_PERM,
    description="Queues a concierge knowledge-base entry (metro route or traffic advice) for approval.",
))
register_action_type(ActionType(
    key=CAMPAIGN, module="demand", label="Approve a campaign draft (export only)", default_owner_role="revenue",
    default_level="approve", max_level="approve", decide_perm=DECIDE_PERM,
    description="WhatsApp, Instagram or email copy for an event; exported as CSV, never sent from here.",
))


def create(cur: Any, hotel_slug: str, **kw: Any) -> dict:
    """core create_action with the module's defaults; the dial may suppress
    it ({id: None, status: 'suppressed'}), which callers accept."""
    from clefs.core.actions import create_action

    return create_action(cur, hotel_slug, **kw)


# ─── Cross-module calls inside a savepoint ───

_sp_seq = 0


def safe_call(cur: Any, fn: Any, *args: Any, default: Any = None, **kwargs: Any) -> Any:
    """Call another module's api inside a SAVEPOINT: a stub, a missing table
    (module disabled or not migrated) or any error rolls back only that call
    and returns `default`, so the caller's transaction stays usable."""
    global _sp_seq
    _sp_seq += 1
    name = f"dr_sp_{_sp_seq}"
    cur.execute(f"SAVEPOINT {name}")
    try:
        out = fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        log.info("cross-module call %s failed: %s", getattr(fn, "__qualname__", fn), exc)
        return default
    cur.execute(f"RELEASE SAVEPOINT {name}")
    return out
