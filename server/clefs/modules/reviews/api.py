"""
Reviews public API (frozen signatures; other modules import only this file).

Policy: review requests go to every guest with the same link regardless of
sentiment (no review gating); 1-3 star replies always need approval.

"Nothing measured" stays the documented default: a hotel with no review
source and no stored review gets {} / None, so Journeys and Visibility hide
their tiles instead of showing a zero they cannot explain.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

from clefs.core import clock
from clefs.core.hotels import get_hotel
from clefs.core.time import local_range


def _tables_ready(cur: Any) -> bool:
    """False when this module's migrations have not run in this database (a
    caller's scoped test run, a deploy with reviews switched off): every
    function then answers its "nothing measured" default instead of failing
    the caller's transaction."""
    cur.execute("SELECT to_regclass('rev_reviews') IS NOT NULL AND to_regclass('rev_sources') IS NOT NULL AS ok")
    return bool(cur.fetchone()["ok"])


def _has_data(cur: Any, hotel_slug: str, platform: str | None = None) -> bool:
    if not _tables_ready(cur):
        return False
    cur.execute(
        """SELECT EXISTS (SELECT 1 FROM rev_sources WHERE hotel_slug = %(h)s
                          AND (%(p)s::text IS NULL OR platform = %(p)s OR (%(p)s = 'google' AND kind IN ('gbp', 'serpapi_google'))))
               OR EXISTS (SELECT 1 FROM rev_reviews WHERE hotel_slug = %(h)s AND (%(p)s::text IS NULL OR platform = %(p)s))
               AS ok""",
        {"h": hotel_slug, "p": platform},
    )
    return bool(cur.fetchone()["ok"])


def draft_reply(cur: Any, hotel_slug: str, review_id: int) -> dict | None:
    """{review_id, text, language, needs_approval} or None when unavailable."""
    from clefs.modules.reviews import analyze, pipeline, replies, store
    from clefs.modules.reviews.settings import get_settings

    if not _tables_ready(cur):
        return None
    review = store.get_review(cur, hotel_slug, int(review_id))
    if review is None:
        return None
    reply = replies.live_reply(cur, hotel_slug, review["id"])
    if reply is None:
        hotel = get_hotel(cur, hotel_slug)
        cfg = get_settings(cur, hotel_slug)
        cur.execute("SELECT * FROM rev_derived WHERE review_id = %s", (review["id"],))
        derived = cur.fetchone()
        derived = dict(derived) if derived else analyze.analyze_review(cur, hotel, review,
                                                                      threshold=cfg.negative_threshold)
        review = store.get_review(cur, hotel_slug, review["id"])
        reply = pipeline.ensure_draft(cur, hotel, review, derived, cfg)
        if not review["has_owner_response"]:
            reply = pipeline.ensure_action(cur, hotel, review, derived, reply)
    return {"review_id": review["id"], "reply_id": reply["id"], "text": reply["draft_text"],
            "language": reply["language"], "needs_approval": bool(reply["needs_approval"]),
            "status": reply["status"]}


def review_counts(cur: Any, hotel_slug: str, *, start: date, end: date, platform: str | None = None) -> dict:
    """New reviews in [start, end] (hotel-local dates), as {total, by_platform:
    {platform: n}, by_rating: {"1".."5": n}}; one platform when `platform` is
    given. {} while nothing is measured (no source and no review, on that
    platform when given): Journeys and Visibility then hide the velocity
    tile ("measured, not targeted", 04 spec 3.10)."""
    if not _has_data(cur, hotel_slug, platform):
        return {}
    t0, t1 = local_range(get_hotel(cur, hotel_slug), start, end)
    cur.execute(
        # GREATEST ignores NULLs, so an unscored review needs the CASE to stay unscored.
        """SELECT platform, CASE WHEN rating_norm_5 IS NOT NULL
                                 THEN LEAST(5, GREATEST(1, ROUND(rating_norm_5)))::int END AS stars, COUNT(*) AS n
           FROM rev_reviews WHERE hotel_slug = %s AND posted_at >= %s AND posted_at < %s
             AND (%s::text IS NULL OR platform = %s)
           GROUP BY 1, 2""",
        (hotel_slug, t0, t1, platform, platform),
    )
    by_platform: dict[str, int] = {}
    by_rating = {str(i): 0 for i in range(1, 6)}
    total = 0
    for r in cur.fetchall():
        n = int(r["n"])
        total += n
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + n
        if r["stars"] is not None:
            by_rating[str(r["stars"])] += n
    return {"total": total, "by_platform": by_platform, "by_rating": by_rating}


def reply_stats(cur: Any, hotel_slug: str, *, platform: str = "google", days: int = 90) -> dict | None:
    """{unanswered, response_rate, median_reply_h} over the trailing `days`
    for one platform, or None while nothing is measured on that platform
    (Visibility hides the tile).

    response_rate is a percent (0-100, one decimal) of reviews posted in the
    window that carry an owner reply; median_reply_h is the median hours from
    review to reply where both times are known (None when none are)."""
    if not _has_data(cur, hotel_slug, platform):
        return None
    since = clock.now() - timedelta(days=days)
    cur.execute(
        """SELECT has_owner_response, posted_at, owner_response_at FROM rev_reviews
           WHERE hotel_slug = %s AND platform = %s AND posted_at >= %s""",
        (hotel_slug, platform, since),
    )
    rows = cur.fetchall()
    total = len(rows)
    answered = sum(1 for r in rows if r["has_owner_response"])
    hours = [(r["owner_response_at"] - r["posted_at"]).total_seconds() / 3600 for r in rows
             if r["has_owner_response"] and r["owner_response_at"] and r["posted_at"]
             and r["owner_response_at"] >= r["posted_at"]]
    return {
        "unanswered": total - answered,
        "response_rate": round(100.0 * answered / total, 1) if total else None,
        "median_reply_h": round(median(hours), 1) if hours else None,
        "total": total, "platform": platform, "days": days,
    }
