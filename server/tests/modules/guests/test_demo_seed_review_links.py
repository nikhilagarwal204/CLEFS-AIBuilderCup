"""The demo hotel has its review links configured (QA GS-6): the J7 page
of any seeded stay shows the same Google and Tripadvisor links at every
rating, and the seed keeps every other guests setting."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import tokens as jtokens
from clefs.seed import demo_uae
from clefs.seed.demo_uae.ops import demo_review_links

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-gst"
PARTS = ["core", "core_catalog", "core_stays", "ops"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def test_demo_links_pass_the_settings_validators():
    links = gsettings.ReviewLinksIn(**demo_review_links("any-hotel"))
    assert gsettings.is_google_review_url(links.google_url) and gsettings.is_tripadvisor_url(links.tripadvisor_url)


def test_seeded_feedback_page_shows_identical_links(cur, seeded, client):
    want = demo_review_links(SLUG)
    cfg = gsettings.load(cur, SLUG)
    assert cfg.review_links.model_dump() == want
    # Only review_links changed: the rest are still the model defaults.
    assert cfg.model_dump(exclude={"review_links"}) == gsettings.GuestsSettings().model_dump(exclude={"review_links"})

    cur.execute("SELECT id FROM reservations WHERE hotel_slug = %s ORDER BY id LIMIT 2", (SLUG,))
    rids = [r["id"] for r in cur.fetchall()]
    assert len(rids) == 2
    far = datetime(2027, 6, 1, tzinfo=timezone.utc)
    got = []
    for rid, body in zip(rids, ({"overall": 1, "improve": "dirty room"}, {"overall": 5})):
        token = jtokens.mint(cur, SLUG, rid, "feedback", expires_at=far)
        page = client.get(f"/public/journeys/{token}")
        assert page.status_code == 200, page.text
        assert page.json()["review_links"] == want
        r = client.post(f"/public/journeys/{token}/feedback", json=body)
        assert r.status_code == 200, r.text
        got.append(r.json())
    # A 1-star and a 5-star answer get the very same response and links.
    assert got[0] == got[1] and got[0]["review_links"] == want
