"""
What happens to a review after it lands (reviews.analyze, every 15 minutes,
and the console's "analyse now"):

1. analyze.analyze_review writes rev_derived (themes, severity, links);
2. a negative review raises reviews.negative_review (section reviews) with the
   evidence and the drafted reply;
3. an unanswered recent review gets a draft (replies.compose) and one reply
   action through the autonomy dial, which decides who sees it and whether
   it may run unattended (autonomy.py).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.core import actions, clock, signals
from clefs.core.errors import ClefsError
from clefs.modules.reviews import analyze, autonomy, replies, taxonomy
from clefs.modules.reviews.settings import ReviewsSettings, get_settings

SIGNAL_NEGATIVE = "reviews.negative_review"
signals.register_signal_kind(
    SIGNAL_NEGATIVE, label="Negative guest review", default_owner_role="gm",
    section="reviews", cadence="weekly", icon="star",
)
PLATFORM_LABELS = {"google": "Google", "tripadvisor": "Tripadvisor", "booking": "Booking.com", "agoda": "Agoda",
                   "expedia": "Expedia", "email": "Email", "other": "Other"}


def _stars(review: dict) -> str:
    n5 = review.get("rating_norm_5")
    return f"{float(n5):g}\u2605" if n5 is not None else "Unscored"


def _recent(review: dict, cfg: ReviewsSettings) -> bool:
    at = review.get("posted_at") or review.get("ingested_at")
    return at is None or at >= clock.now() - timedelta(days=cfg.act_on_days)


def reply_type(review: dict, derived: dict) -> str:
    n5 = review.get("rating_norm_5")
    if derived.get("negative") or n5 is None or float(n5) < 4:
        return autonomy.NEGATIVE
    return autonomy.POSITIVE


# ─── Negative review signal ───


def raise_negative(cur: Any, hotel: Any, review: dict, derived: dict, reply: dict | None) -> dict:
    neg = [t for t in derived.get("themes") or [] if t.get("polarity") == "negative"]
    labels = ", ".join(taxonomy.THEME_LABELS.get(t["key"], t["key"]) for t in neg[:3]) or "guest unhappy"
    platform = PLATFORM_LABELS.get(review["platform"], review["platform"])
    high = derived.get("severity") == "high"
    evidence = [
        {"kind": "metric", "label": "Rating", "value": _stars(review)},
        {"kind": "row", "label": f"{platform} review", "value": f"#{review['id']}",
         "ref": {"review_id": review["id"], "platform": review["platform"]}},
    ]
    if derived.get("summary_en"):
        evidence.append({"kind": "quote", "label": "Summary", "value": derived["summary_en"]})
    if derived.get("linked_request_ids"):
        ids = ", ".join(f"#{i}" for i in derived["linked_request_ids"][:5])
        evidence.append({"kind": "row", "label": "Service requests in that stay", "value": ids,
                         "ref": {"request_ids": list(derived["linked_request_ids"])}})
    if reply:
        evidence.append({"kind": "quote", "label": "Drafted reply", "value": reply["draft_text"],
                         "ref": {"reply_id": reply["id"]}})
    n5 = review.get("rating_norm_5")
    severity = 4 if high else (3 if n5 is not None and float(n5) <= 2 else 2)
    rec = None
    if reply:
        rec = {"type": autonomy.NEGATIVE, "label": "Review and approve the drafted reply", "owner_role": "gm",
               "params": {"review_id": review["id"], "reply_id": reply["id"]}, "due_in_hours": 48}
    return signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
        module="reviews", kind=SIGNAL_NEGATIVE, dedupe_key=f"{SIGNAL_NEGATIVE}:{review['id']}",
        title=f"{_stars(review)} {platform} review: {labels}",
        summary=derived.get("summary_en") or None,
        cadence="daily" if high else None, severity=severity, confidence=0.9 if derived.get("model") != "lexicon"
        else 0.6, evidence=evidence, recommended_action=rec, owner_role="gm",
        subject_type="rev_review", subject_id=str(review["id"]),
    ))


# ─── Drafts and actions ───


def ensure_draft(cur: Any, hotel: Any, review: dict, derived: dict, cfg: ReviewsSettings, *,
                 replace_edited: bool = False) -> dict:
    """Compose and store the live draft (a person's edit or an approved reply
    is kept unless replace_edited)."""
    draft = replies.compose(cur, hotel, review, derived, cfg.reply_style)
    return replies.save_draft(cur, hotel.slug, review["id"], draft, needs_approval=True,
                              replace_edited=replace_edited)


def ensure_action(cur: Any, hotel: Any, review: dict, derived: dict, reply: dict,
                  signal_id: int | None = None) -> dict:
    """One reply action per review (dedupe key), created through the dial;
    the reply records it and whether a person must approve."""
    if reply["status"] != "draft" or reply.get("action_id"):
        return reply
    platform = PLATFORM_LABELS.get(review["platform"], review["platform"])
    act = actions.create_action(
        cur, hotel.slug, type=reply_type(review, derived),
        title=f"Reply to the {_stars(review)} {platform} review #{review['id']}",
        params={"review_id": review["id"], "reply_id": reply["id"]}, signal_id=signal_id, owner_role="gm",
        dedupe_key=f"reviews.reply:{review['id']}",
    )
    needs = act.get("level") not in actions.AUTO_LEVELS
    cur.execute(
        f"UPDATE rev_replies SET action_id = %s, needs_approval = %s WHERE id = %s RETURNING {replies.REPLY_COLS}",
        (act.get("id"), needs, reply["id"]),
    )
    return dict(cur.fetchone())


def process_review(cur: Any, hotel: Any, review: dict, cfg: ReviewsSettings | None = None) -> dict:
    """Analyse one review, then draft, signal and act on it. Returns
    {review_id, negative, severity, reply_id, signal_id}."""
    cfg = cfg or get_settings(cur, hotel.slug)
    derived = analyze.analyze_review(cur, hotel, review, threshold=cfg.negative_threshold)
    cur.execute("SELECT * FROM rev_reviews WHERE id = %s", (review["id"],))
    review = dict(cur.fetchone())
    out = {"review_id": review["id"], "negative": derived["negative"], "severity": derived["severity"],
           "reply_id": None, "signal_id": None}
    if not _recent(review, cfg):
        return out
    reply = None
    if not review["has_owner_response"] and (derived["negative"] or cfg.draft_positive):
        reply = ensure_draft(cur, hotel, review, derived, cfg)
    if derived["negative"]:
        sig = raise_negative(cur, hotel, review, derived, reply)
        out["signal_id"] = sig.get("id")
    if reply:
        reply = ensure_action(cur, hotel, review, derived, reply, out["signal_id"])
        out["reply_id"] = reply["id"]
    return out


def run_pending(cur: Any, hotel: Any, *, limit: int = 25) -> dict:
    """Process waiting reviews, each in a savepoint: one bad review (a model
    error, a malformed row) never blocks the rest."""
    cfg = get_settings(cur, hotel.slug)
    done = negative = failed = 0
    for review in analyze.pending(cur, hotel.slug, limit=limit):
        cur.execute("SAVEPOINT rev_one")
        try:
            res = process_review(cur, hotel, review, cfg)
            cur.execute("RELEASE SAVEPOINT rev_one")
            done += 1
            negative += int(res["negative"])
        except (ClefsError, ValueError, KeyError, TypeError) as exc:
            cur.execute("ROLLBACK TO SAVEPOINT rev_one")
            analyze.mark_failed(cur, review["id"])
            failed += 1
            import logging

            logging.getLogger(__name__).warning("reviews.analyze failed for review %s: %s", review["id"], exc)
    return {"analyzed": done, "negative": negative, "failed": failed}
