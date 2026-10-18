"""
Console read models: the feed, one review in full, the summary tiles and the
theme table. Every row leaves through `serialize`, which masks the reviewer's
name by role (core.pii guest_name) and never returns purged text.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs.core import clock
from clefs.core.pii import GUEST_NAME, mask_for_ctx
from clefs.modules.reviews import analyze, replies, taxonomy

_FEED_COLS = """r.id, r.platform, r.source_id, r.external_id, r.author_display, r.title, r.rating, r.rating_scale,
    r.rating_norm_5, r.language, r.posted_at, r.raw_text, r.raw_purged_at, r.raw_expires_at,
    r.has_owner_response,
    r.owner_response_at, r.room_hint, r.stay_date_guess, r.ingested_at, r.analyzed_at,
    d.sentiment, d.themes, d.summary_en, d.severity, d.negative, d.linked_request_ids,
    p.id AS reply_id, p.status AS reply_status, p.needs_approval AS reply_needs_approval"""


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def serialize(row: dict, ctx: Any) -> dict:
    out = dict(row)
    for k in ("rating", "rating_norm_5", "sentiment"):
        out[k] = _f(out.get(k))
    out["linked_request_ids"] = list(out.get("linked_request_ids") or [])
    out["themes"] = [{**t, "label": taxonomy.THEME_LABELS.get(t.get("key"), t.get("key"))}
                     for t in (out.get("themes") or [])]
    # Past raw_expires_at the text is gone whoever nulled it (reviews.raw_purge
    # or Trust's retention job, which runs first and leaves raw_purged_at
    # unset), and between expiry and the purge run it must not leak either.
    expires = out.pop("raw_expires_at", None)
    purged = out.pop("raw_purged_at", None) is not None or (expires is not None and expires <= clock.now())
    text = out.pop("raw_text", None)
    out["text"] = None if purged else text
    if purged:
        out["author_display"] = out["title"] = None
    out["purged"] = purged
    return mask_for_ctx(out, ctx, field_classes={"author_display": GUEST_NAME})


def feed(cur: Any, hotel_slug: str, ctx: Any, *, platform: str | None = None, negative: bool | None = None,
         unanswered: bool | None = None, theme: str | None = None, max_rating: float | None = None,
         days: int = 90, limit: int = 50, before_id: int | None = None) -> list[dict]:
    where = ["r.hotel_slug = %(h)s", "COALESCE(r.posted_at, r.ingested_at) >= %(since)s"]
    params: dict[str, Any] = {"h": hotel_slug, "since": clock.now() - timedelta(days=days),
                              "limit": max(1, min(limit, 200))}
    if platform:
        where.append("r.platform = %(platform)s")
        params["platform"] = platform
    if negative is not None:
        where.append("COALESCE(d.negative, FALSE) = %(neg)s")
        params["neg"] = negative
    if unanswered:
        where.append("NOT r.has_owner_response")
    if theme:
        where.append("d.themes @> %(theme)s::jsonb")
        params["theme"] = f'[{{"key": "{theme}"}}]' if theme in taxonomy.THEMES else '[{"key": "?"}]'
    if max_rating is not None:
        where.append("r.rating_norm_5 <= %(maxr)s")
        params["maxr"] = max_rating
    if before_id:
        where.append("r.id < %(before)s")
        params["before"] = before_id
    cur.execute(
        f"""SELECT {_FEED_COLS} FROM rev_reviews r LEFT JOIN rev_derived d ON d.review_id = r.id
            LEFT JOIN rev_replies p ON p.review_id = r.id AND p.status <> 'discarded'
            WHERE {' AND '.join(where)}
            ORDER BY COALESCE(r.posted_at, r.ingested_at) DESC, r.id DESC LIMIT %(limit)s""",
        params,
    )
    return [serialize(dict(r), ctx) for r in cur.fetchall()]


def detail(cur: Any, hotel_slug: str, review_id: int, ctx: Any) -> dict | None:
    cur.execute(
        f"""SELECT {_FEED_COLS}, d.model, d.prompt_version, s.kind AS source_kind, s.label AS source_label
            FROM rev_reviews r LEFT JOIN rev_derived d ON d.review_id = r.id
            LEFT JOIN rev_replies p ON p.review_id = r.id AND p.status <> 'discarded'
            LEFT JOIN rev_sources s ON s.id = r.source_id
            WHERE r.hotel_slug = %s AND r.id = %s""",
        (hotel_slug, review_id),
    )
    row = cur.fetchone()
    if not row:
        return None
    out = serialize(dict(row), ctx)
    out["linked_requests"] = analyze.linked_requests(cur, hotel_slug, out["linked_request_ids"])
    out["reply"] = replies.live_reply(cur, hotel_slug, review_id)
    out["postable"] = bool(out["reply"]) and replies.gbp_target(
        cur, hotel_slug, {"platform": row["platform"], "source_kind": row["source_kind"],
                          "platform_ref": _platform_ref(cur, review_id)}) is not None
    return out


def _platform_ref(cur: Any, review_id: int) -> str | None:
    cur.execute("SELECT platform_ref FROM rev_reviews WHERE id = %s", (review_id,))
    row = cur.fetchone()
    return row["platform_ref"] if row else None


# ─── Tiles and themes ───


def summary(cur: Any, hotel_slug: str, *, days: int = 30) -> dict:
    """The Reviews tab header: {days, count, avg_rating, response_rate,
    negative_share, unanswered, drafts_waiting, by_platform{p: {count,
    avg_rating}}}. Percent values are 0-100."""
    since = clock.now() - timedelta(days=days)
    cur.execute(
        """SELECT r.platform, COUNT(*) AS n, AVG(r.rating_norm_5) AS avg,
                  COUNT(*) FILTER (WHERE r.has_owner_response) AS answered,
                  COUNT(d.review_id) AS analysed, COUNT(*) FILTER (WHERE d.negative) AS neg
           FROM rev_reviews r LEFT JOIN rev_derived d ON d.review_id = r.id
           WHERE r.hotel_slug = %s AND COALESCE(r.posted_at, r.ingested_at) >= %s GROUP BY 1""",
        (hotel_slug, since),
    )
    rows = cur.fetchall()
    n = sum(int(r["n"]) for r in rows)
    answered = sum(int(r["answered"]) for r in rows)
    analysed = sum(int(r["analysed"]) for r in rows)
    neg = sum(int(r["neg"]) for r in rows)
    rated = [(float(r["avg"]), int(r["n"])) for r in rows if r["avg"] is not None]
    cur.execute(
        """SELECT COUNT(*) AS n FROM rev_replies WHERE hotel_slug = %s AND status IN ('draft', 'approved')""",
        (hotel_slug,),
    )
    drafts = int(cur.fetchone()["n"])
    return {
        "days": days, "count": n,
        "avg_rating": round(sum(a * c for a, c in rated) / sum(c for _, c in rated), 2) if rated else None,
        "response_rate": round(100.0 * answered / n, 1) if n else None,
        "negative_share": round(100.0 * neg / analysed, 1) if analysed else None,
        "unanswered": n - answered, "drafts_waiting": drafts,
        "by_platform": {r["platform"]: {"count": int(r["n"]),
                                        "avg_rating": round(float(r["avg"]), 2) if r["avg"] is not None else None}
                        for r in rows},
    }


def themes(cur: Any, hotel_slug: str, *, days: int = 30) -> list[dict]:
    """[{key, label, department, negative, positive, neutral, reviews}] for
    reviews posted in the trailing `days`, most complained-about first."""
    since = clock.now() - timedelta(days=days)
    cur.execute(
        """SELECT t->>'key' AS key,
                  COUNT(*) FILTER (WHERE t->>'polarity' = 'negative') AS negative,
                  COUNT(*) FILTER (WHERE t->>'polarity' = 'positive') AS positive,
                  COUNT(*) FILTER (WHERE t->>'polarity' = 'neutral') AS neutral,
                  COUNT(DISTINCT r.id) AS reviews
           FROM rev_reviews r JOIN rev_derived d ON d.review_id = r.id
           CROSS JOIN LATERAL jsonb_array_elements(d.themes) t
           WHERE r.hotel_slug = %s AND COALESCE(r.posted_at, r.ingested_at) >= %s
           GROUP BY 1 ORDER BY 2 DESC, 5 DESC""",
        (hotel_slug, since),
    )
    return [{"key": r["key"], "label": taxonomy.THEME_LABELS.get(r["key"], r["key"]),
             "department": taxonomy.THEME_DEPARTMENT.get(r["key"], "general"),
             "negative": int(r["negative"]), "positive": int(r["positive"]), "neutral": int(r["neutral"]),
             "reviews": int(r["reviews"])} for r in cur.fetchall()]
