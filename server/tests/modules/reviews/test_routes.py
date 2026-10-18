"""Console HTTP routes under /hotels/{slug}/reviews."""

from __future__ import annotations

import pytest

from clefs.modules.reviews import store
from tests.modules.reviews.test_sources import BOOKING_CSV


@pytest.fixture
def owner(client_as):
    return client_as("owner")


def _base(slug):
    return f"/hotels/{slug}/reviews"


def test_permissions(client_as, slug):
    fd = client_as("front_desk")
    assert fd.get(_base(slug)).status_code == 200
    assert fd.post(f"{_base(slug)}/sources", json={"kind": "tripadvisor"}).status_code == 403
    assert client_as("revenue").get(_base(slug)).status_code == 403


def test_import_analyse_edit_approve_copy(owner, slug, now, run_jobs, cur):
    r = owner.post(f"{_base(slug)}/import", json={"preset": "booking", "csv": BOOKING_CSV})
    assert r.status_code == 200 and r.json()["created"] == 2
    feed = owner.get(_base(slug)).json()
    omar = next(x for x in feed if x["author_display"] == "Omar")
    assert omar["rating_norm_5"] == 3.8 and omar["analyzed_at"] is None
    res = owner.post(f"{_base(slug)}/{omar['id']}/analyze").json()
    assert res["reply_id"]
    d = owner.get(f"{_base(slug)}/{omar['id']}").json()
    assert d["reply"]["status"] == "draft" and d["postable"] is False and d["themes"]
    rid = d["reply"]["id"]
    bad = owner.patch(f"{_base(slug)}/replies/{rid}", json={"text": "Sorry \u2014 we will refund you."}).json()
    assert "\u2014" not in bad["draft_text"] and bad["checks"]["compensation"] == ["refund"] and bad["edited"]
    r = owner.post(f"{_base(slug)}/replies/{rid}/approve")
    assert r.status_code == 422 and r.json()["code"] == "reply_policy"
    good = "Dear Omar, thank you for your feedback. Please contact us directly so we can talk. The team"
    owner.patch(f"{_base(slug)}/replies/{rid}", json={"text": good})
    assert owner.post(f"{_base(slug)}/replies/{rid}/approve").json()["status"] == "approved"
    run_jobs(only=["core.actions.execute"])
    assert owner.post(f"{_base(slug)}/replies/{rid}/post").json()["code"] == "not_postable"
    copied = owner.post(f"{_base(slug)}/replies/{rid}/copied").json()
    assert copied["status"] == "copied" and copied["posted_via"] == "copy"
    assert store.get_review(cur, slug, omar["id"])["has_owner_response"]
    summary = owner.get(f"{_base(slug)}/summary").json()
    assert summary["count"] == 2 and summary["response_rate"] == 100.0
    assert owner.get(f"{_base(slug)}/themes").status_code == 200
    trends = owner.get(f"{_base(slug)}/trends?weeks=4").json()
    assert len(trends["weeks"]) == 4 and sum(w["reviews"] for w in trends["rating"]) == 2


def test_discard_ignores_the_action(owner, slug, now, cur):
    row, _ = store.ingest(cur, slug, None, store.ReviewIn(platform="google", external_id="x", rating=1,
                                                          text="Rude staff"))
    rid = owner.post(f"{_base(slug)}/{row['id']}/analyze").json()["reply_id"]
    out = owner.post(f"{_base(slug)}/replies/{rid}/discard").json()
    assert out["status"] == "discarded"
    cur.execute("SELECT status FROM actions WHERE id = %s", (out["action_id"],))
    assert cur.fetchone()["status"] == "rejected"


def test_sources_crud_sync_now_and_consent(client_as, owner, slug, now, run_jobs, env, http_fake):
    env(TRIPADVISOR_API_KEY="k")
    http_fake.on("GET", "/location/77/reviews", {"data": [{"id": 1, "rating": 5, "text": "Great",
                                                           "published_date": "2026-09-28T00:00:00Z"}]})
    r = owner.post(f"{_base(slug)}/sources", json={"kind": "tripadvisor", "config": {"location_id": "77"}})
    assert r.status_code == 201
    sid = r.json()["id"]
    assert owner.post(f"{_base(slug)}/sources", json={"kind": "tripadvisor"}).json()["code"] == "source_exists"
    bad = owner.patch(f"{_base(slug)}/sources/{sid}", json={"config": {"api_key": "x"}})
    assert bad.status_code == 400
    q = owner.post(f"{_base(slug)}/sync", json={"source_id": sid})
    assert q.status_code == 202 and q.json()["queued"]
    run_jobs(only=["reviews.sync_now"])
    feed = owner.get(_base(slug)).json()
    assert len(feed) == 1 and feed[0]["platform"] == "tripadvisor" and feed[0]["analyzed_at"]
    listing = owner.get(f"{_base(slug)}/sources").json()
    assert listing["sources"][0]["last_synced_at"] and listing["auto_reply_consent"] is False
    gbp = owner.post(f"{_base(slug)}/sources", json={"kind": "gbp"}).json()
    assert gbp["google"]["status"] == "not_connected"
    gm = client_as("gm")
    assert gm.put(f"{_base(slug)}/sources/{gbp['id']}/auto-reply-consent",
                  json={"granted": True}).json()["code"] == "owner_only"
    ok = owner.put(f"{_base(slug)}/sources/{gbp['id']}/auto-reply-consent", json={"granted": True}).json()
    assert ok["auto_reply_consent"] is True
    assert gm.put(f"{_base(slug)}/sources/{gbp['id']}/auto-reply-consent", json={"granted": False}).status_code == 200
    assert owner.delete(f"{_base(slug)}/sources/{sid}").status_code == 204
    assert len(owner.get(_base(slug)).json()) == 1  # reviews outlive their source


def test_competitors_are_fetched_live_and_never_stored(owner, slug, env, http_fake, cur):
    env(GOOGLE_API_KEY="gk")
    http_fake.on("GET", "places.googleapis.com/v1/places/ChIJcompetitor01",
                 {"displayName": {"text": "Rival Hotel"}, "rating": 4.6, "userRatingCount": 1203})
    bad = owner.put(f"{_base(slug)}/competitors", json={"place_ids": ["not a place"]})
    assert bad.status_code == 400
    assert owner.put(f"{_base(slug)}/competitors", json={"place_ids": ["ChIJcompetitor01"]}).status_code == 200
    out = owner.get(f"{_base(slug)}/competitors").json()
    assert out["places"] == [{"place_id": "ChIJcompetitor01", "name": "Rival Hotel", "rating": 4.6,
                              "user_rating_count": 1203, "maps_url": None, "own": False}]
    req = http_fake.requests[-1]
    assert req.headers["x-goog-api-key"] == "gk" and "rating" in req.headers["x-goog-fieldmask"]
    cur.execute("SELECT * FROM rev_competitors WHERE hotel_slug = %s", (slug,))
    row = cur.fetchone()
    assert set(row) == {"hotel_slug", "place_ids", "own_place_id", "updated_by", "updated_at"}


@pytest.mark.parametrize("metric", ["reviews.count", "reviews.avg_rating_30d", "reviews.response_rate",
                                    "reviews.negative_share"])
def test_metrics_run(owner, slug, now, metric):
    owner.post(f"{_base(slug)}/import", json={"preset": "booking", "csv": BOOKING_CSV})
    r = owner.post(f"/hotels/{slug}/metrics/query", json={"metric": metric, "start": "2026-09-01",
                                                          "end": "2026-09-30", "grain": "total"})
    assert r.status_code == 200, r.text
    body = r.json()
    if metric == "reviews.count":
        assert body["total"] == 2
    if metric == "reviews.avg_rating_30d":
        assert body["total"] == 4.4  # (3.8 + 5.0) / 2
    grouped = owner.post(f"/hotels/{slug}/metrics/query", json={"metric": metric, "start": "2026-09-01",
                                                                "end": "2026-09-30", "grain": "total",
                                                                "group_by": ["platform"]})
    assert grouped.status_code == 200 and grouped.json()["rows"][0]["group"] == {"platform": "booking"}
