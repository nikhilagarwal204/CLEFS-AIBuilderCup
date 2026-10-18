"""
Reviews metrics (03 spec 4.1), registered at import (module.py imports this).

The period is the metric query's [start, end] on the review's posted date in
hotel time. `reviews.avg_rating_30d` keeps the spec's key: the brief and the
console tile ask it for the trailing 30 days; any other range works too.
Percent metrics are 0-100. Ratings are on the /5 scale (Booking.com and
Agoda /10 scores are halved at ingest).
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

_REVIEWS = "FROM rev_reviews r LEFT JOIN rev_derived d ON d.review_id = r.id"
_DIMS = (
    Dimension("platform", "Platform", "r.platform",
              "SELECT DISTINCT platform FROM rev_reviews WHERE hotel_slug = %(hotel)s"),
    Dimension("severity", "Severity", "COALESCE(d.severity, 'unanalysed')"),
)
_COMMON = dict(source=_REVIEWS, hotel_column="r.hotel_slug", time_column="r.posted_at", time_kind="timestamptz",
               dimensions=_DIMS, permission="reviews.view")

register_metric(Metric(
    key="reviews.count", label="New reviews", description="Reviews posted in the period, all platforms",
    unit="count", measure="COUNT(*)", higher_is_better=None, synonyms=("review volume", "number of reviews"),
    **_COMMON,
))

register_metric(Metric(
    key="reviews.avg_rating_30d", label="Average rating",
    description="Mean guest rating out of 5 for reviews posted in the period (usually the last 30 days)",
    unit="ratio", measure="ROUND(AVG(r.rating_norm_5), 2)", where="r.rating_norm_5 IS NOT NULL",
    higher_is_better=True, synonyms=("review score", "guest rating", "star rating"), **_COMMON,
))

register_metric(Metric(
    key="reviews.response_rate", label="Review response rate",
    description="Share of reviews posted in the period that have an owner reply",
    unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE r.has_owner_response) / NULLIF(COUNT(*), 0)",
    higher_is_better=True, synonyms=("reply rate", "answered reviews"), **_COMMON,
))

register_metric(Metric(
    key="reviews.negative_share", label="Negative review share",
    description="Share of analysed reviews posted in the period that are negative (3 stars or below, or high "
                "severity)",
    unit="percent", measure="100.0 * COUNT(*) FILTER (WHERE d.negative) / NULLIF(COUNT(d.review_id), 0)",
    higher_is_better=False, synonyms=("bad reviews", "complaint share"), **_COMMON,
))
