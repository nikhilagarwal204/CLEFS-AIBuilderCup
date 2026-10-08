"""
Theme trends (03 spec 4.1, Canon 18 weekly cadence).

Weeks are ISO weeks (Monday start) of the review's posted date in the hotel's
timezone. reviews.theme_trends runs Mondays at 06:00 hotel time and compares
the week just ended with the average of the four weeks before it: a theme
whose negative mentions rose 50% or more, with at least 3 mentions, raises
reviews.theme_trend_up. A theme new this week (baseline 0) with 3 or more
mentions counts as a rise. Comparing with a 4-week average rather than one
week keeps a single noisy week from firing (and from hiding a real rise).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core import signals
from clefs.core.time import local_date, local_range
from clefs.modules.reviews import taxonomy

SIGNAL_TREND = "reviews.theme_trend_up"
MIN_MENTIONS = 3
RISE = 0.5
BASELINE_WEEKS = 4

signals.register_signal_kind(
    SIGNAL_TREND, label="Review theme trending up", default_owner_role="gm",
    section="reviews", cadence="weekly", icon="trending-up",
)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def theme_counts(cur: Any, hotel: Any, start: date, end: date) -> list[dict]:
    """[{week, theme, negative, positive, neutral}] for reviews posted in the
    hotel-local [start, end]."""
    t0, t1 = local_range(hotel, start, end)
    cur.execute(
        """
        SELECT date_trunc('week', r.posted_at AT TIME ZONE %(tz)s)::date AS week, t->>'key' AS theme,
               COUNT(*) FILTER (WHERE t->>'polarity' = 'negative') AS negative,
               COUNT(*) FILTER (WHERE t->>'polarity' = 'positive') AS positive,
               COUNT(*) FILTER (WHERE t->>'polarity' = 'neutral') AS neutral
        FROM rev_reviews r JOIN rev_derived d ON d.review_id = r.id
        CROSS JOIN LATERAL jsonb_array_elements(d.themes) t
        WHERE r.hotel_slug = %(h)s AND r.posted_at >= %(t0)s AND r.posted_at < %(t1)s
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        {"tz": hotel.timezone, "h": hotel.slug, "t0": t0, "t1": t1},
    )
    return [{**dict(r), "negative": int(r["negative"]), "positive": int(r["positive"]),
             "neutral": int(r["neutral"])} for r in cur.fetchall()]


def weekly_series(cur: Any, hotel: Any, *, weeks: int = 12, today: date | None = None) -> dict:
    """The console's trend chart: {weeks[], themes{key: {label, negative[],
    positive[]}}, rating[]} with one slot per week, oldest first."""
    today = today or local_date(hotel)
    last = week_start(today)
    first = last - timedelta(weeks=weeks - 1)
    slots = [first + timedelta(weeks=i) for i in range(weeks)]
    idx = {w: i for i, w in enumerate(slots)}
    themes: dict[str, dict] = {}
    for row in theme_counts(cur, hotel, first, today):
        i = idx.get(row["week"])
        if i is None:
            continue
        th = themes.setdefault(row["theme"], {"label": taxonomy.THEME_LABELS.get(row["theme"], row["theme"]),
                                              "negative": [0] * weeks, "positive": [0] * weeks})
        th["negative"][i] += row["negative"]
        th["positive"][i] += row["positive"]
    t0, t1 = local_range(hotel, first, today)
    cur.execute(
        """SELECT date_trunc('week', posted_at AT TIME ZONE %(tz)s)::date AS week, COUNT(*) AS n,
                  AVG(rating_norm_5) AS avg FROM rev_reviews
           WHERE hotel_slug = %(h)s AND posted_at >= %(t0)s AND posted_at < %(t1)s GROUP BY 1""",
        {"tz": hotel.timezone, "h": hotel.slug, "t0": t0, "t1": t1},
    )
    rating: list[dict] = [{"reviews": 0, "avg_rating": None} for _ in slots]
    for r in cur.fetchall():
        i = idx.get(r["week"])
        if i is not None:
            rating[i] = {"reviews": int(r["n"]),
                         "avg_rating": round(float(r["avg"]), 2) if r["avg"] is not None else None}
    return {"weeks": [w.isoformat() for w in slots], "themes": themes, "rating": rating}


# ─── Weekly detector ───


def detect(cur: Any, hotel: Any, *, today: date | None = None) -> dict:
    """Raise reviews.theme_trend_up for the week that just ended; earlier
    weeks' trend signals resolve (resolve_missing) once they are no longer
    the current week's."""
    today = today or local_date(hotel)
    this_week = week_start(today) - timedelta(weeks=1)  # the full week just ended
    base_start = this_week - timedelta(weeks=BASELINE_WEEKS)
    counts: dict[str, dict[date, int]] = {}
    for row in theme_counts(cur, hotel, base_start, this_week + timedelta(days=6)):
        counts.setdefault(row["theme"], {})[row["week"]] = row["negative"]
    seen: list[str] = []
    for theme, weeks in counts.items():
        cur_n = weeks.get(this_week, 0)
        base = sum(weeks.get(base_start + timedelta(weeks=i), 0) for i in range(BASELINE_WEEKS)) / BASELINE_WEEKS
        if cur_n < MIN_MENTIONS or (base > 0 and (cur_n - base) / base < RISE):
            continue
        key = f"{SIGNAL_TREND}:{theme}:{this_week.isoformat()}"
        seen.append(key)
        label = taxonomy.THEME_LABELS.get(theme, theme)
        rise = "new this week" if base == 0 else f"up {round((cur_n - base) / base * 100)}%"
        dept = taxonomy.THEME_DEPARTMENT.get(theme, "general")
        signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
            module="reviews", kind=SIGNAL_TREND, dedupe_key=key,
            title=f"{label} complaints in reviews {rise} ({cur_n} last week)",
            summary=f"{cur_n} negative mentions of {label.lower()} in the week of {this_week.isoformat()}, "
                    f"against {base:.1f} a week over the previous {BASELINE_WEEKS} weeks.",
            severity=3 if cur_n >= 2 * MIN_MENTIONS else 2, confidence=0.8,
            evidence=[{"kind": "series", "label": f"{label}, negative mentions per week",
                       "value": [weeks.get(base_start + timedelta(weeks=i), 0) for i in range(BASELINE_WEEKS + 1)],
                       "ref": {"theme": theme, "department": dept, "week": this_week.isoformat()}}],
            recommended_action={"type": "core.task", "label": f"Ask the {dept.replace('_', ' ')} lead to review "
                                f"{label.lower()} complaints", "owner_role": "gm", "due_in_hours": 72,
                                "params": {"theme": theme, "department": dept}},
            owner_role="gm", subject_type="rev_theme", subject_id=theme,
        ))
    resolved = signals.resolve_missing(cur, hotel.slug, "reviews", SIGNAL_TREND, seen)
    return {"week": this_week.isoformat(), "trending": len(seen), "resolved": resolved}
