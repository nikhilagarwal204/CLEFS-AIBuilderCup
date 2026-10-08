"""reviews.api: draft_reply, review_counts, reply_stats (frozen signatures)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from clefs.modules.reviews import api, store


def test_nothing_measured_keeps_the_documented_defaults(cur, slug):
    assert api.review_counts(cur, slug, start=date(2026, 9, 1), end=date(2026, 9, 30)) == {}
    assert api.reply_stats(cur, slug) is None
    assert api.draft_reply(cur, slug, 999) is None


def test_review_counts_by_platform_and_rounded_stars(cur, slug, now):
    at = datetime(2026, 9, 20, 10, tzinfo=timezone.utc)
    for ext, platform, rating, scale in [("a", "google", 5, 5), ("b", "google", 2, 5), ("c", "booking", 7.5, 10),
                                         ("d", "booking", None, 10)]:
        store.ingest(cur, slug, None, store.ReviewIn(platform=platform, external_id=ext, rating=rating,
                                                     rating_scale=scale, text="x", posted_at=at))
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="old", rating=1, text="x",
                                                 posted_at=at - timedelta(days=60)))
    out = api.review_counts(cur, slug, start=date(2026, 9, 1), end=date(2026, 9, 30))
    assert out == {"total": 4, "by_platform": {"google": 2, "booking": 2},
                   "by_rating": {"1": 0, "2": 1, "3": 0, "4": 1, "5": 1}}
    assert api.review_counts(cur, slug, start=date(2026, 9, 1), end=date(2026, 9, 30), platform="google")["total"] == 2
    # A platform with a connected source but no reviews is measured: zero, not {}.
    store.create_source(cur, slug, "tripadvisor", config={"location_id": "1"})
    assert api.review_counts(cur, slug, start=date(2026, 9, 1), end=date(2026, 9, 30),
                             platform="tripadvisor")["total"] == 0


def test_reply_stats_unanswered_rate_and_median(cur, slug, now):
    t = now.now()
    rows = [("a", t - timedelta(days=5), t - timedelta(days=5) + timedelta(hours=10)),
            ("b", t - timedelta(days=4), t - timedelta(days=4) + timedelta(hours=30)),
            ("c", t - timedelta(days=3), None), ("d", t - timedelta(days=200), None)]
    for ext, posted, resp in rows:
        store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id=ext, rating=4, text="x",
                                                     posted_at=posted, has_owner_response=resp is not None,
                                                     owner_response_at=resp))
    out = api.reply_stats(cur, slug, platform="google", days=90)
    assert out["unanswered"] == 1 and out["response_rate"] == 66.7 and out["median_reply_h"] == 20.0
    assert out["total"] == 3
    assert api.reply_stats(cur, slug, platform="tripadvisor") is None


def test_draft_reply_analyses_drafts_and_is_stable(cur, slug, now):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="z", rating=2,
                                                          author_display="Sara Ali", text="Noisy room, slow check-in."))
    first = api.draft_reply(cur, slug, row["id"])
    assert set(first) >= {"review_id", "text", "language", "needs_approval"}
    assert first["needs_approval"] is True and first["language"] == "en" and "Sara" in first["text"]
    assert "\u2014" not in first["text"] and len(first["text"].split()) < 120
    assert api.draft_reply(cur, slug, row["id"])["reply_id"] == first["reply_id"]


def test_defaults_when_the_module_tables_are_absent(cur, slug):
    """Another lane's scoped run (reviews not migrated) must get the defaults,
    not an UndefinedTable error that poisons its transaction."""
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="q", rating=4, text="x"))
    cur.execute("SAVEPOINT gone")
    cur.execute("ALTER TABLE rev_reviews RENAME TO rev_reviews_gone")
    try:
        assert api.review_counts(cur, slug, start=date(2026, 9, 1), end=date(2026, 9, 30)) == {}
        assert api.reply_stats(cur, slug) is None and api.draft_reply(cur, slug, 1) is None
        cur.execute("SELECT 1 AS ok")  # the transaction is still usable
        assert cur.fetchone()["ok"] == 1
    finally:
        cur.execute("ROLLBACK TO SAVEPOINT gone")
