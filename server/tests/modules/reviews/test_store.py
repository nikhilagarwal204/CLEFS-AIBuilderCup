"""Ingest, normalisation and the 30-day raw purge."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.modules.reviews import store


def test_norm_5_halves_ten_point_scores():
    assert store.norm_5("8.4", 10) == Decimal("4.2")
    assert store.norm_5(7.5, 10) == Decimal("3.8")  # half up
    assert store.norm_5(4, 5) == Decimal("4.0")
    assert store.norm_5(None, 10) is None
    assert store.norm_5(12, 10) == Decimal("5.0")


def test_ingest_is_idempotent_and_merges_owner_response(cur, slug, now):
    src = store.create_source(cur, slug, "csv", label="Booking export", platform="booking")
    r = store.ReviewIn(platform="booking", external_id="b1", rating="6", rating_scale=10,
                       author_display="Omar", text="The AC in room 504 was broken.")
    row, created = store.ingest(cur, slug, src["id"], r)
    assert created and row["rating_norm_5"] == Decimal("3.0") and row["room_hint"] == "504"
    assert row["language"] == "en" and row["raw_expires_at"] == now.now() + timedelta(days=30)
    r.has_owner_response = True
    row2, created2 = store.ingest(cur, slug, src["id"], r)
    assert not created2 and row2["id"] == row["id"] and row2["has_owner_response"]


def test_raw_purge_nulls_text_and_author_but_keeps_rating(cur, slug, now):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g1", rating=2,
                                                          author_display="Anna", text="Dirty room"))
    now.advance(days=29)
    assert store.purge_raw(cur, hotel_slug=slug) == 0
    now.advance(days=2)
    assert store.purge_raw(cur, hotel_slug=slug) == 1
    got = store.get_review(cur, slug, row["id"])
    assert got["raw_text"] is None and got["author_display"] is None and got["rating"] == Decimal("2.0")
    # A re-sync of the same review does not bring the purged text back.
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g1", rating=2,
                                                 author_display="Anna", text="Dirty room"))
    assert store.get_review(cur, slug, row["id"])["raw_text"] is None


def test_one_source_per_kind_except_csv(cur, slug):
    import pytest

    from clefs.core.errors import ClefsError

    store.create_source(cur, slug, "email")
    with pytest.raises(ClefsError) as exc:
        store.create_source(cur, slug, "email")
    assert exc.value.code == "source_exists"
    store.create_source(cur, slug, "csv", platform="booking")
    store.create_source(cur, slug, "csv", platform="agoda")


# ─── Purge vs re-sync (Trust's retention job redacts first) ───


def _trust_redact(cur, slug):
    # What trust.retention_purge does to rev_reviews (registry strategy
    # redact): the three columns go NULL and raw_purged_at is left unset.
    cur.execute("UPDATE rev_reviews SET raw_text = NULL, author_display = NULL, title = NULL "
                "WHERE hotel_slug = %s", (slug,))


def test_resync_after_trust_redaction_does_not_restore_text(cur, slug, now):
    r = store.ReviewIn(platform="google", external_id="g-trust", rating=2, author_display="Anna",
                       title="Meh", text="Dirty room")
    row, _ = store.ingest(cur, slug, None, r)
    cur.execute("UPDATE rev_reviews SET analyzed_at = %s WHERE id = %s", (now.now(), row["id"]))
    now.advance(days=31)
    _trust_redact(cur, slug)
    # The daily sync re-ingests the same review with its text: nothing comes back
    # and the review is not queued for the AI again.
    store.ingest(cur, slug, None, r)
    got = store.get_review(cur, slug, row["id"])
    assert got["raw_text"] is None and got["author_display"] is None and got["title"] is None
    assert got["analyzed_at"] is not None
    # reviews.raw_purge still stamps the row, so the console reports it purged.
    assert store.purge_raw(cur, hotel_slug=slug) == 1
    assert store.get_review(cur, slug, row["id"])["raw_purged_at"] == now.now()
    assert store.purge_raw(cur, hotel_slug=slug) == 0
    store.ingest(cur, slug, None, r)
    assert store.get_review(cur, slug, row["id"])["raw_text"] is None


def test_expired_but_unpurged_review_takes_no_new_text(cur, slug, now):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g-exp", rating=3,
                                                          text="Old words"))
    cur.execute("UPDATE rev_reviews SET analyzed_at = %s WHERE id = %s", (now.now(), row["id"]))
    now.advance(days=10)
    # Inside the 30 days an edited review is refreshed and re-queued.
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g-exp", rating=3,
                                                 text="Edited words"))
    got = store.get_review(cur, slug, row["id"])
    assert got["raw_text"] == "Edited words" and got["analyzed_at"] is None
    cur.execute("UPDATE rev_reviews SET analyzed_at = %s WHERE id = %s", (now.now(), row["id"]))
    now.advance(days=21)
    store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g-exp", rating=3,
                                                 text="Edited again"))
    got = store.get_review(cur, slug, row["id"])
    assert got["raw_text"] == "Edited words" and got["analyzed_at"] is not None


def test_console_reports_expired_rows_as_purged(cur, slug, now):
    from types import SimpleNamespace

    from clefs.modules.reviews import queries

    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="g-ser", rating=4,
                                                          author_display="Anna", title="Nice", text="Lovely"))
    fresh = queries.serialize(dict(store.get_review(cur, slug, row["id"])), SimpleNamespace(role="owner"))
    assert fresh["text"] == "Lovely" and fresh["purged"] is False
    now.advance(days=31)
    _trust_redact(cur, slug)
    out = queries.serialize(dict(store.get_review(cur, slug, row["id"])), SimpleNamespace(role="owner"))
    assert out["purged"] is True and out["text"] is None and out["title"] is None
