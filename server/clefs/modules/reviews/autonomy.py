"""
Reply action types and their autonomy rules (04 spec 1.4, Canon autonomy dial).

| type                     | default | ceiling | gate                                             |
| reviews.reply_positive   | suggest | auto    | approve unless GBP consent + 4-5 stars + no      |
|                          |         |         | negative theme + severity none/low               |
| reviews.reply_negative   | approve | approve | 1-3 stars always need a person                   |

level_check refuses a raise of reply_positive into an auto level with 409
auto_reply_consent_required until a GBP source records the owner's consent
(GBP API policy: no automated replies without prior specific and express
consent). The executor posts through GBP when the review allows it, else it
marks the reply approved for the Copy path: approving never fails just
because Google is not connected.
"""

from __future__ import annotations

from typing import Any

from clefs.core import actions
from clefs.core.actions import AUTO_LEVELS, ActionCtx, ActionType, ExecResult, Gate
from clefs.core.errors import ClefsError

POSITIVE = "reviews.reply_positive"
NEGATIVE = "reviews.reply_negative"
CONSENT_REQUIRED = "auto_reply_consent_required"


# ─── Eligibility ───


def _review_and_derived(cur: Any, hotel_slug: str, review_id: Any) -> tuple[dict | None, dict | None]:
    cur.execute(
        """SELECT r.id, r.rating_norm_5, r.platform, r.platform_ref, s.kind AS source_kind, s.auto_reply_consent,
                  d.themes, d.severity, d.negative
           FROM rev_reviews r LEFT JOIN rev_sources s ON s.id = r.source_id
           LEFT JOIN rev_derived d ON d.review_id = r.id
           WHERE r.hotel_slug = %s AND r.id = %s""",
        (hotel_slug, review_id),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    row = dict(row)
    return row, {"themes": row.get("themes") or [], "severity": row.get("severity"), "negative": row.get("negative")}


def auto_reasons(review: dict | None, derived: dict | None) -> list[str]:
    """Why this review may NOT be answered without a person (empty = may)."""
    if not review:
        return ["review_missing"]
    reasons: list[str] = []
    if review.get("source_kind") != "gbp" or not review.get("auto_reply_consent"):
        reasons.append("auto_reply_consent_missing")
    n5 = review.get("rating_norm_5")
    if n5 is None or float(n5) < 4:
        reasons.append("rating_below_4")
    if derived is None or derived.get("severity") is None:
        reasons.append("not_analyzed")
    else:
        if any(t.get("polarity") == "negative" for t in derived.get("themes") or []):
            reasons.append("negative_theme")
        if derived.get("severity") not in ("none", "low"):
            reasons.append("severity")
    return reasons


def _gate(ctx: ActionCtx) -> Gate:
    review, derived = _review_and_derived(ctx.cur, ctx.hotel_slug, ctx.params.get("review_id"))
    reasons = auto_reasons(review, derived)
    return Gate(max_level="approve" if reasons else "auto", reasons=reasons)


def has_consent(cur: Any, hotel_slug: str) -> bool:
    cur.execute("SELECT 1 FROM rev_sources WHERE hotel_slug = %s AND kind = 'gbp' AND auto_reply_consent LIMIT 1",
                (hotel_slug,))
    return cur.fetchone() is not None


def _level_check(cur: Any, hotel_slug: str, target: str) -> list[str]:
    if target in AUTO_LEVELS and not has_consent(cur, hotel_slug):
        return [CONSENT_REQUIRED]
    return []


# ─── Executor ───


def _execute(ctx: ActionCtx) -> ExecResult:
    """Approved (by a person or the dial): post through GBP when possible,
    else leave the reply approved for Copy. Idempotent: a reply already
    posted, copied or discarded is reported, never sent twice."""
    from clefs.modules.reviews import replies

    reply_id = ctx.params.get("reply_id")
    try:
        reply = replies.get_reply(ctx.cur, ctx.hotel_slug, int(reply_id))
    except (ClefsError, TypeError, ValueError):
        return ExecResult(ok=False, error="reply_missing")
    if reply["status"] in ("posted", "copied", "discarded"):
        return ExecResult(ok=True, data={"reply_id": reply["id"], "status": reply["status"], "posted": False})
    by_dial = ctx.action.get("decided_via") == "dial"
    if by_dial:
        # The dial re-checks the gate at execution: consent withdrawn or a
        # re-analysis that found a complaint stops the post here.
        if auto_reasons(*_review_and_derived(ctx.cur, ctx.hotel_slug, reply["review_id"])):
            return ExecResult(ok=False, error="auto_reply_not_allowed")
    from clefs.modules.reviews.http import VendorError

    review = replies._review_for(ctx.cur, ctx.hotel_slug, reply)
    try:
        if replies.gbp_target(ctx.cur, ctx.hotel_slug, review):
            row = replies.post(ctx.cur, ctx.hotel_slug, reply["id"], user_id=ctx.action.get("decided_by"),
                               via="dial" if by_dial else "gbp")
            return ExecResult(ok=True, data={"reply_id": row["id"], "status": row["status"], "posted": True},
                              reversal_handle={"kind": "manual",
                                               "reason": "delete the reply on Google Business Profile"})
        if by_dial:
            return ExecResult(ok=False, error="not_postable")
        if reply["status"] == "draft":
            replies.approve(ctx.cur, ctx.hotel_slug, reply["id"], user_id=ctx.action.get("decided_by"))
    except VendorError as exc:
        if exc.transient:
            raise  # Google 5xx / 429 / network: the executor's backoff retries
        return ExecResult(ok=False, error=exc.code)
    except ClefsError as exc:
        # A person's edit that breaks the reply policy (422 reply_policy), a
        # revoked Google token (not_connected): final, and the signal reopens.
        return ExecResult(ok=False, error=exc.code)
    return ExecResult(ok=True, data={"reply_id": reply["id"], "status": "approved", "posted": False})


# ─── Registration ───

actions.register_action_type(ActionType(
    key=POSITIVE, module="reviews", label="Reply to a 4-5 star review",
    executor=_execute, default_owner_role="gm", default_level="suggest", max_level="auto",
    gate=_gate, level_check=_level_check, decide_perm="reviews.manage",
    description="Post the drafted reply. Automatic only with the owner's recorded auto-reply consent on the "
                "Google Business Profile connection, for 4-5 star reviews without complaints.",
))

actions.register_action_type(ActionType(
    key=NEGATIVE, module="reviews", label="Reply to a 1-3 star review",
    executor=_execute, default_owner_role="gm", default_level="approve", max_level="approve",
    decide_perm="reviews.manage",
    description="Post or copy the drafted reply after a person approves it. Never automatic.",
))


# ─── Auto-reply consent ───


def set_auto_reply_consent(cur: Any, hotel_slug: str, source_id: int, granted: bool, *, ctx: Any) -> dict:
    """Record (or withdraw) the owner's consent to unattended replies on a
    GBP source: who and when on the row, the consent ledger entry, an audit
    row. Withdrawing also lowers reviews.reply_positive to at most approve,
    so nothing already queued posts on its own afterwards."""
    from clefs import audit
    from clefs.core import clock
    from clefs.core.consent import record_consent
    from clefs.modules.reviews import store

    src = store.get_source(cur, hotel_slug, source_id)
    if src["kind"] != "gbp":
        raise ClefsError(409, "Auto-reply consent applies to a Google Business Profile connection",
                         "consent_needs_gbp")
    user_id = getattr(getattr(ctx, "principal", None), "user_id", None)
    now = clock.now()
    row = store.update_source(cur, hotel_slug, source_id, auto_reply_consent=granted,
                              auto_reply_consent_by=user_id if granted else None,
                              auto_reply_consent_at=now if granted else None)
    record_consent(cur, hotel_slug, f"rev_source:{source_id}", "auto_reply", granted, source="console",
                   subject_type="user", evidence={"user_id": user_id, "source_id": source_id})
    audit.audit(cur, ctx, "reviews.auto_reply_consent." + ("grant" if granted else "withdraw"), "rev_source",
                str(source_id), before={"auto_reply_consent": src["auto_reply_consent"]},
                after={"auto_reply_consent": granted}, hotel_slug=hotel_slug)
    if not granted and not has_consent(cur, hotel_slug):
        res = actions.resolve_level(cur, hotel_slug, POSITIVE)
        if res.configured in AUTO_LEVELS:
            actions.set_autonomy(cur, hotel_slug, POSITIVE, level="approve", actor=actions.SYSTEM,
                                 reason="auto-reply consent withdrawn")
    return row
