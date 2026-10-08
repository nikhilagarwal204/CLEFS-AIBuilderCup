"""
Review analysis (03 spec 4.1): themes mapped to departments, severity,
sentiment, room hint and stay date, then links to Service requests.

clefs.ai.structured does the reading; when AI is off (AI_ENABLED=false, no
key, an outage) the keyword lexicon in taxonomy.py stands in, so a negative
review still raises its signal and gets a draft. rev_derived keeps the model
and prompt version, so a later re-analysis can tell the two apart.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs import ai
from clefs.core import clock
from clefs.db import Json
from clefs.modules.reviews import taxonomy
from clefs.settings import ai_model_for

PROMPT_VERSION = "rev-analyze-v1"
TASK = "reviews.analyze"
MAX_ATTEMPTS = 3

Theme = Literal[taxonomy.THEMES]  # type: ignore[valid-type]
Severity = Literal["none", "low", "medium", "high"]


class ThemeOut(BaseModel):
    key: Theme
    polarity: Literal["positive", "negative", "neutral"]
    summary_en: str = Field(description="One short English clause, e.g. 'AC not cooling for two nights'")


class ReviewAnalysis(BaseModel):
    language: str = Field(description="ISO 639-1 code of the review text")
    sentiment: float = Field(description="-1 (very negative) to 1 (very positive)")
    themes: list[ThemeOut]
    summary_en: str = Field(description="One English sentence for the hotel owner")
    severity: Severity = Field(description="high: safety, discrimination, theft, health or legal risk, or a "
                                           "failure that ruined the stay; medium: a clear service failure; "
                                           "low: minor gripes; none: no complaint")
    room_hint: str | None = Field(description="Room number the guest names, digits only, else null")
    stay_date_guess: str | None = Field(description="YYYY-MM-DD the guest stayed, when the text says; else null")


_SYSTEM = (
    "You analyse one hotel guest review for the hotel's owner. Identify the operational themes it mentions, "
    "using only the given theme keys, each with its polarity. Rate severity. Never invent facts that are not "
    "in the review. Theme keys: " + ", ".join(taxonomy.THEMES) + "."
)


# ─── Analysis ───


def _fallback(review: dict) -> ReviewAnalysis:
    """Deterministic analysis from the lexicon and the rating."""
    n5 = float(review["rating_norm_5"]) if review.get("rating_norm_5") is not None else None
    text = f"{review.get('title') or ''}\n{review.get('raw_text') or ''}"
    themes = taxonomy.keyword_themes(text, n5)
    neg = [t for t in themes if t["polarity"] == "negative"]
    if any(t["key"] == "safety" for t in neg):
        severity = "high"
    elif n5 is not None and n5 <= 2:
        severity = "medium"
    elif neg:
        severity = "low"
    else:
        severity = "none"
    sentiment = 0.0 if n5 is None else max(-1.0, min(1.0, (n5 - 3) / 2))
    return ReviewAnalysis(
        language=review.get("language") or ("ar" if taxonomy.looks_arabic(text) else "en"),
        sentiment=sentiment,
        themes=[ThemeOut(key=t["key"], polarity=t["polarity"], summary_en=t["summary_en"]) for t in themes],
        summary_en="", severity=severity, room_hint=None, stay_date_guess=None,
    )


def run_model(review: dict, hotel_slug: str) -> tuple[ReviewAnalysis, str]:
    """(analysis, model label). Falls back to the lexicon when AI is off or
    fails; a review whose raw text is already purged is not sent anywhere."""
    if not (review.get("raw_text") or review.get("title")):
        return _fallback(review), "lexicon"
    rating = review.get("rating_norm_5")
    prompt = (f"Platform: {review.get('platform')}\nRating (out of 5): {rating if rating is not None else 'none'}\n"
              f"Title: {review.get('title') or ''}\nReview:\n{review.get('raw_text') or ''}")
    try:
        out = ai.structured(TASK, system=_SYSTEM, input=prompt, schema=ReviewAnalysis, hotel_slug=hotel_slug,
                            max_output_tokens=1500)
        return out, ai_model_for(TASK) or "llm"
    except (ai.AIDisabled, ai.AIError):
        return _fallback(review), "lexicon"


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def is_negative(review: dict, severity: str, sentiment: float | None, threshold: float) -> bool:
    """03 spec 4.1: norm 3 or below, or high severity. A review with no score
    (an email without one) is negative on medium severity or sour sentiment."""
    if severity == "high":
        return True
    n5 = review.get("rating_norm_5")
    if n5 is not None:
        return float(n5) <= threshold
    return severity == "medium" or (sentiment is not None and sentiment <= -0.3)


# ─── Links to Service requests ───


def _window(cur: Any, hotel: Any, review: dict, room: str, stay: date | None) -> tuple[datetime, datetime]:
    """The stay the review is about. A reservation (core stays) for that room
    covering the stay date, or the latest one ending in the 30 days before
    the review, gives exact dates; else a few days either side of the stay
    date, else the 14 days before the review."""
    tz = hotel.tz
    posted = review.get("posted_at") or review.get("ingested_at") or clock.now()
    cur.execute("SELECT to_regclass('reservations') IS NOT NULL AS ok")
    if cur.fetchone()["ok"]:
        if stay is not None:
            cur.execute(
                """SELECT check_in, COALESCE(actual_check_out, check_out) AS check_out FROM reservations
                   WHERE hotel_slug = %s AND room_number = %s AND status <> 'CANCELLED'
                     AND check_in <= %s AND COALESCE(actual_check_out, check_out) >= %s
                   ORDER BY check_in DESC LIMIT 1""",
                (hotel.slug, room, stay, stay),
            )
        else:
            posted_day = posted.astimezone(tz).date()
            cur.execute(
                """SELECT check_in, COALESCE(actual_check_out, check_out) AS check_out FROM reservations
                   WHERE hotel_slug = %s AND room_number = %s AND status IN ('IN_HOUSE', 'CHECKED_OUT')
                     AND COALESCE(actual_check_out, check_out) BETWEEN %s AND %s
                   ORDER BY check_out DESC LIMIT 1""",
                (hotel.slug, room, posted_day - timedelta(days=30), posted_day),
            )
        res = cur.fetchone()
        if res:
            return (datetime.combine(res["check_in"], time(0), tzinfo=tz),
                    datetime.combine(res["check_out"] + timedelta(days=1), time(0), tzinfo=tz))
    if stay is not None:
        return (datetime.combine(stay - timedelta(days=3), time(0), tzinfo=tz),
                datetime.combine(stay + timedelta(days=4), time(0), tzinfo=tz))
    return posted - timedelta(days=14), posted


def link_requests(cur: Any, hotel: Any, review: dict, themes: list[dict], stay: date | None) -> list[int]:
    """service_requests_v1 ids in the review's room, in the departments of its
    negative themes, inside the stay window. Without a room there is no link:
    a department match alone would tie a review to strangers' requests."""
    room = review.get("room_hint")
    depts = {t["department"] for t in themes if t.get("polarity") == "negative"}
    if not room or not depts:
        return []
    t0, t1 = _window(cur, hotel, review, room, stay)
    cur.execute(
        """SELECT id FROM service_requests_v1
           WHERE hotel_slug = %s AND upper(room) = %s AND lower(COALESCE(department, '')) = ANY(%s)
             AND created_at >= %s AND created_at < %s
           ORDER BY created_at LIMIT 20""",
        (hotel.slug, room.upper(), taxonomy.department_names(depts), t0, t1),
    )
    return [int(r["id"]) for r in cur.fetchall()]


def linked_requests(cur: Any, hotel_slug: str, ids: list[int]) -> list[dict]:
    """Rows for the console ("Guest reported AC twice, #881 reopened")."""
    if not ids:
        return []
    cur.execute(
        """SELECT id, room, request_type, department, priority, status, created_at, resolved_at
           FROM service_requests_v1 WHERE hotel_slug = %s AND id = ANY(%s) ORDER BY created_at""",
        (hotel_slug, list(ids)),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Persist ───

_ROOM_OK = re.compile(r"^\d{2,5}[A-Z]?$")


def analyze_review(cur: Any, hotel: Any, review: dict, *, threshold: float = 3.0) -> dict:
    """Analyse one review, write rev_derived (replacing an older analysis) and
    stamp rev_reviews.analyzed_at. Returns the derived row plus `negative`."""
    result, model = run_model(review, hotel.slug)
    themes = [{"key": t.key, "department": taxonomy.THEME_DEPARTMENT[t.key], "polarity": t.polarity,
               "summary_en": t.summary_en[:200]} for t in result.themes]
    # The lexicon also reads the text, so a model that missed a safety word
    # still escalates (a deterministic match is never downgraded, 03 spec 0.5).
    if model != "lexicon" and (review.get("raw_text") or review.get("title")):
        n5 = float(review["rating_norm_5"]) if review.get("rating_norm_5") is not None else None
        lex = taxonomy.keyword_themes(f"{review.get('title') or ''}\n{review.get('raw_text') or ''}", n5)
        if any(t["key"] == "safety" and t["polarity"] == "negative" for t in lex):
            result.severity = "high"
            if not any(t["key"] == "safety" for t in themes):
                themes.append(next(t for t in lex if t["key"] == "safety"))
    room = review.get("room_hint")
    model_room = (result.room_hint or "").strip().upper()
    if not room and _ROOM_OK.match(model_room):
        room = model_room
    stay = review.get("stay_date_guess") or _date(result.stay_date_guess)
    review = {**review, "room_hint": room}
    links = link_requests(cur, hotel, review, themes, stay)
    negative = is_negative(review, result.severity, result.sentiment, threshold)
    sentiment = max(-1.0, min(1.0, float(result.sentiment)))
    cur.execute(
        """UPDATE rev_reviews SET analyzed_at = %s, analysis_attempts = analysis_attempts + 1,
                  room_hint = %s, stay_date_guess = %s, language = COALESCE(%s, language)
           WHERE id = %s""",
        (clock.now(), room, stay, (result.language or "")[:12] or None, review["id"]),
    )
    cur.execute(
        """
        INSERT INTO rev_derived (review_id, hotel_slug, sentiment, themes, summary_en, severity, negative,
                                 linked_request_ids, model, prompt_version)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (review_id) DO UPDATE SET sentiment = EXCLUDED.sentiment, themes = EXCLUDED.themes,
            summary_en = EXCLUDED.summary_en, severity = EXCLUDED.severity, negative = EXCLUDED.negative,
            linked_request_ids = EXCLUDED.linked_request_ids, model = EXCLUDED.model,
            prompt_version = EXCLUDED.prompt_version, created_at = now()
        RETURNING *
        """,
        (review["id"], hotel.slug, round(sentiment, 3), Json(themes), (result.summary_en or "")[:500] or None,
         result.severity, negative, links, model, PROMPT_VERSION),
    )
    return dict(cur.fetchone())


def pending(cur: Any, hotel_slug: str, *, limit: int = 25) -> list[dict]:
    """Reviews waiting for analysis, oldest first; a review that failed
    MAX_ATTEMPTS times is left for a person (it shows unanalysed)."""
    cur.execute(
        """SELECT * FROM rev_reviews WHERE hotel_slug = %s AND analyzed_at IS NULL AND analysis_attempts < %s
           ORDER BY id LIMIT %s""",
        (hotel_slug, MAX_ATTEMPTS, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def mark_failed(cur: Any, review_id: int) -> None:
    cur.execute("UPDATE rev_reviews SET analysis_attempts = analysis_attempts + 1 WHERE id = %s", (review_id,))
