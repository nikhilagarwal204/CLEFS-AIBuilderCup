"""
Calls into other modules' api.py, guarded.

Another module may be switched off, not migrated on this database (a
CLEFS_ONLY_MODULES test run), or mid-build in a parallel lane. A failure
there must degrade Visibility to that function's documented stub default
(04 spec 7.2 "Stub returns"), never abort our transaction: each call runs
inside a SAVEPOINT, so a SQL error in the other module rolls back only its
own statements and our job keeps its work.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

log = logging.getLogger("clefs.visibility")
T = TypeVar("T")


def call(cur: Any, default: T, fn: Callable[..., T], *args: Any, **kw: Any) -> T:
    """fn(cur, *args, **kw), or `default` if it raises (logged, not raised).
    None from fn is returned as is: that is the other module's own answer."""
    cur.execute("SAVEPOINT vis_xmod")
    try:
        out = fn(cur, *args, **kw)
    except Exception as exc:  # noqa: BLE001  (any failure in another module degrades to its stub default)
        cur.execute("ROLLBACK TO SAVEPOINT vis_xmod")
        log.warning("visibility: %s.%s failed (%s); using its default",
                    getattr(fn, "__module__", "?"), getattr(fn, "__name__", "?"), type(exc).__name__)
        return default
    cur.execute("RELEASE SAVEPOINT vis_xmod")
    return out


# ─── Reviews: unanswered Google reviews for "Draft replies" ───


def unanswered_google_review_ids(cur: Any, hotel_slug: str, *, days: int, limit: int) -> list[int]:
    """Newest unanswered Google review ids in the trailing `days` (the same
    window and "unanswered" rule as reviews.api.reply_stats, so the button
    drafts exactly what the tile counts). WHY a guarded read of rev_reviews:
    reviews.api exposes draft_reply(review_id) but no id listing (a request
    for `unanswered_review_ids` is filed with the Reviews lane). The read is
    SELECT-only, inside a SAVEPOINT, and a missing table (Reviews off or not
    migrated) degrades to [] like any other xmod default."""
    from datetime import timedelta

    from clefs.core import clock

    def _ids(c: Any) -> list[int]:
        c.execute("SELECT to_regclass('rev_reviews') IS NOT NULL AS ok")
        if not c.fetchone()["ok"]:
            return []
        c.execute("SELECT id FROM rev_reviews WHERE hotel_slug = %s AND platform = 'google' "
                  "AND NOT has_owner_response AND posted_at >= %s ORDER BY posted_at DESC, id DESC LIMIT %s",
                  (hotel_slug, clock.now() - timedelta(days=days), limit))
        return [int(r["id"]) for r in c.fetchall()]

    return call(cur, [], _ids)
