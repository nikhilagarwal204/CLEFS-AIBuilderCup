"""
Inquiry Guard action types on the quote-to-cash side (04 spec 1.4, 4.7-4.10).

| Key | Default | Ceiling | Notes |
|---|---|---|---|
| inquiries.send_quote | approve | auto_within_limits | auto only at or above a fresh BAR, no discount (4.7) |
| inquiries.send_payment_link | approve | auto_within_limits | same rule as its quote |
| inquiries.follow_up | approve | auto_within_limits | limits {max_followups: 2} |
| inquiries.direct_booking | auto | auto | system record, no-op executor (4.9) |
| inquiries.create_pms_reservation | approve | approve | human task for the front desk (4.8) |
| inquiries.respond | suggest | approve | AI reply draft, price-linted; never sent without a person (4.11) |

The ceiling of the send types is auto_within_limits, so a quote below BAR can
never go out on its own even with the dial hand-set to `auto` (Canon: rate
changes always need approval). Executors live next to their data (quotes,
payments.service); the verifier is verify.verifier.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from clefs.core.actions import ActionCtx, ActionType, ExecResult, LimitCheck, register_action_type
from clefs.modules.inquiries.quotes import SendQuoteLimits

SEND_QUOTE = "inquiries.send_quote"
SEND_PAYMENT_LINK = "inquiries.send_payment_link"
FOLLOW_UP = "inquiries.follow_up"
DIRECT_BOOKING = "inquiries.direct_booking"
CREATE_PMS_RESERVATION = "inquiries.create_pms_reservation"
RESPOND = "inquiries.respond"
SEND_TYPES = (SEND_QUOTE, SEND_PAYMENT_LINK)


class FollowUpLimits(BaseModel):
    max_followups: int = Field(2, ge=0, le=2)


# ─── Limit checkers (re-run the 4.7 rule on the stored quote) ───


def _quote_check(ctx: ActionCtx, limits: Any) -> LimitCheck:
    from clefs.modules.inquiries import quotes, store

    lim = limits if isinstance(limits, SendQuoteLimits) else SendQuoteLimits.model_validate(limits or {})
    qid = ctx.params.get("quote_id")
    if not qid:
        return LimitCheck(ok=False, reasons=["no_quote"])
    q = quotes.quote_by_id(ctx.cur, int(qid))
    inquiry = store.get_inquiry(ctx.cur, ctx.hotel_slug, q["inquiry_id"])
    reasons = quotes.auto_send_reasons(inquiry, q["lines"], total_minor=q["total_minor"], limits=lim)
    return LimitCheck(ok=not reasons, reasons=reasons)


def _link_check(ctx: ActionCtx, limits: Any) -> LimitCheck:
    """A payment link goes out on its own only for a quote that itself met
    the auto-send rule, and never for more than that quote's total."""
    from clefs.modules.inquiries import quotes

    check = _quote_check(ctx, limits)
    if not check.ok:
        return check
    q = quotes.quote_by_id(ctx.cur, int(ctx.params["quote_id"]))
    if int(ctx.params.get("amount_minor") or 0) > q["total_minor"]:
        return LimitCheck(ok=False, reasons=["amount_over_quote"])
    return check


def _followup_check(ctx: ActionCtx, limits: Any) -> LimitCheck:
    lim = limits if isinstance(limits, FollowUpLimits) else FollowUpLimits.model_validate(limits or {})
    n = int(ctx.params.get("n") or 1)
    return LimitCheck(ok=n <= lim.max_followups, reasons=[] if n <= lim.max_followups else ["max_followups"])


# ─── Executors (lazy imports: the executors import quotes and payments) ───


def _exec_send_quote(ctx: ActionCtx) -> Any:
    from clefs.modules.inquiries import quotes

    return quotes.execute_send(ctx)


def _exec_send_link(ctx: ActionCtx) -> Any:
    from clefs.modules.inquiries.payments import service

    return service.execute_send_link(ctx)


def _exec_follow_up(ctx: ActionCtx) -> Any:
    from clefs.modules.inquiries import quotes

    return quotes.execute_follow_up(ctx)


def _exec_direct_booking(ctx: ActionCtx) -> ExecResult:
    """No-op: the booking already happened in the PMS; the action exists so
    the verified outcome has exactly one carrier in the ledger (4.9)."""
    return ExecResult(ok=True, data={"reservation_id": ctx.params.get("reservation_id"),
                                     "inquiry_id": ctx.params.get("inquiry_id")})


def _exec_respond(ctx: ActionCtx) -> Any:
    from clefs.modules.inquiries import replies

    return replies.execute_respond(ctx)


def _validate_respond(ctx: ActionCtx, params: dict) -> None:
    from clefs.modules.inquiries import replies

    replies.validate_respond_params(ctx, params)


def _verifier(ctx: ActionCtx) -> Any:
    from clefs.modules.inquiries import verify

    return verify.verifier(ctx)


# ─── Registration ───

register_action_type(ActionType(
    key=SEND_QUOTE, module="inquiries", label="Send a quote",
    description="Send an inquiry quote with its link. Auto only at or above BAR with no discount.",
    executor=_exec_send_quote, verifier=_verifier, verify_every=timedelta(days=1),
    verify_until=timedelta(days=120), default_owner_role="revenue", default_level="approve",
    max_level="auto_within_limits", limits_model=SendQuoteLimits, limit_checker=_quote_check,
    decide_perm="inquiries.approve", counts_toward_verified_profit=True,
))
register_action_type(ActionType(
    key=SEND_PAYMENT_LINK, module="inquiries", label="Send a payment link",
    description="Create and send a deposit or balance link for a quote.",
    executor=_exec_send_link, verifier=_verifier, verify_every=timedelta(days=1),
    verify_until=timedelta(days=120), default_owner_role="revenue", default_level="approve",
    max_level="auto_within_limits", limits_model=SendQuoteLimits, limit_checker=_link_check,
    decide_perm="inquiries.approve", counts_toward_verified_profit=True,
))
register_action_type(ActionType(
    key=FOLLOW_UP, module="inquiries", label="Follow up on a quote",
    description="A 'still available' nudge at quote-sent + 24 h and + 72 h while the quote is valid.",
    executor=_exec_follow_up, default_owner_role="revenue", default_level="approve",
    max_level="auto_within_limits", limits_model=FollowUpLimits, limit_checker=_followup_check,
    decide_perm="inquiries.approve",
))
register_action_type(ActionType(
    key=DIRECT_BOOKING, module="inquiries", label="Direct booking from an inquiry",
    description="System record that carries a verified direct booking with no Clefs send.",
    executor=_exec_direct_booking, verifier=_verifier, verify_every=timedelta(days=1),
    verify_until=timedelta(days=120), default_owner_role="gm", default_level="auto", max_level="auto",
    decide_perm="inquiries.approve", counts_toward_verified_profit=True,
))
register_action_type(ActionType(
    key=CREATE_PMS_RESERVATION, module="inquiries", label="Create the reservation in the PMS",
    description="A guest paid but no PMS reservation matched after 48 h.",
    executor=None, default_owner_role="front_desk", default_level="approve", max_level="approve",
    decide_perm="inquiries.respond",
))
register_action_type(ActionType(
    key=RESPOND, module="inquiries", label="Reply to an inquiry (AI draft)",
    description="An AI-drafted reply with no prices; a person reads and approves it before it goes out.",
    executor=_exec_respond, validate_params=_validate_respond, default_owner_role="front_desk",
    # Ceiling approve (04 spec 1.4 table): an AI reply to a prospect never
    # goes out on its own, and there is no limits model to make "within
    # limits" mean anything.
    default_level="suggest", max_level="approve", decide_perm="inquiries.respond",
))
