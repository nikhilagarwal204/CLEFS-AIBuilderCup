"""Keyed service.api.create_request: an idempotency_key makes a call exactly
replayable (tool executors, Upsell), including after a semantic merge."""

from __future__ import annotations

from clefs.modules.service import api


def _file(cur, slug, room, key=None, **kw):
    return api.create_request(cur, slug, room=room, type=kw.pop("type", "towels"), description=kw.pop("description", "two towels"),
                              source=kw.pop("source", "web"), idempotency_key=key, **kw)


def _count(cur, slug, room):
    cur.execute("SELECT COUNT(*) AS n FROM staff_requests WHERE hotel_slug = %s AND room = %s", (slug, room))
    return cur.fetchone()["n"]


def test_same_key_replays_the_first_outcome(cur, hotel_uae):
    slug = hotel_uae.slug
    first = _file(cur, slug, "301", "cc:tool:abc")
    assert first["replayed"] is False and first["idempotency_key"] == "cc:tool:abc"
    again = _file(cur, slug, "301", "cc:tool:abc", description="something else entirely")
    assert again["replayed"] is True and again["id"] == first["id"] and again["merged"] is False
    assert again["tracking_token"] and _count(cur, slug, "301") == 1
    cur.execute("SELECT description FROM staff_requests WHERE id = %s", (first["id"],))
    assert "something else" not in cur.fetchone()["description"]


def test_replay_survives_the_request_closing(cur, hotel_uae):
    slug = hotel_uae.slug
    first = _file(cur, slug, "302", "ups:order:9")
    cur.execute("UPDATE staff_requests SET state = 'completed', status = 'completed' WHERE id = %s", (first["id"],))
    again = _file(cur, slug, "302", "ups:order:9")
    assert again["replayed"] is True and again["id"] == first["id"] and again["state"] == "completed"


def test_different_keys_still_merge_and_a_merged_key_replays_without_appending(cur, hotel_uae):
    slug = hotel_uae.slug
    first = _file(cur, slug, "303", "k1", description="two towels")
    second = _file(cur, slug, "303", "k2", description="and a bath mat")
    assert second["merged"] is True and second["id"] == first["id"] and second["replayed"] is False
    cur.execute("SELECT description, merged_count FROM staff_requests WHERE id = %s", (first["id"],))
    before = dict(cur.fetchone())
    again = _file(cur, slug, "303", "k2", description="and a bath mat")
    assert again["replayed"] is True and again["merged"] is True and again["id"] == first["id"]
    cur.execute("SELECT description, merged_count FROM staff_requests WHERE id = %s", (first["id"],))
    assert dict(cur.fetchone()) == before  # nothing appended twice


def test_keys_are_per_hotel_and_blank_means_unkeyed(cur, hotel_uae, legacy_slug):
    slug = hotel_uae.slug
    a = _file(cur, slug, "304", "same-key")
    b = _file(cur, legacy_slug, "304", "same-key")
    assert a["id"] != b["id"] and b["replayed"] is False
    blank = _file(cur, slug, "305", "   ", type="ice")
    assert blank["idempotency_key"] is None and blank["replayed"] is False


def test_keyed_replay_is_not_rate_limited(cur, hotel_uae):
    slug = hotel_uae.slug
    first = _file(cur, slug, "306", "burst", type="ice", description="ice bucket")
    for i in range(12):  # well past the per-room guest limit
        again = _file(cur, slug, "306", "burst", type="ice", description="ice bucket")
        assert again["id"] == first["id"] and again["replayed"] is True
