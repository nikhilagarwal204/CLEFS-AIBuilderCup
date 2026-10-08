"""Guests settings validators (04 spec 3.9 review links, 3.5 marketing window,
and the repo's no-em-dash rule)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from clefs.modules.guests.settings import GuestsSettings, ReviewLinksIn, is_google_review_url, is_tripadvisor_url

GOOD_TA = [
    "https://www.tripadvisor.com/Hotel_Review-g295424-d1-Reviews-X.html",
    "https://tripadvisor.com/Hotel_Review-x",
    "https://www.tripadvisor.ae/Hotel_Review-x",
    "https://ar.tripadvisor.com/Hotel_Review-x",
    "https://www.tripadvisor.co.uk/Hotel_Review-x",
    "https://nl.tripadvisor.be/Hotel_Review-x",
    "https://en.tripadvisor.com.hk/Hotel_Review-x",
]
BAD_TA = [
    "https://tripadvisor.com.evil.example/Hotel_Review-x",   # official name as a label of a foreign domain
    "https://tripadvisor.review-us.io/x",                    # "tripadvisor" label, not Tripadvisor
    "https://evil-tripadvisor.com/x",
    "https://mytripadvisor.com/x",
    "https://www.tripadvisor.com@evil.example/x",            # userinfo trick
    "https://reviews.tripadvisor.com.evil.example/x",
    "https://abc.tripadvisor.com/x",                         # not a www/m/language edition
    "http://www.tripadvisor.com/x",                          # plain http
    "https://www.tripadvisor.com:8443/x",
    "https://tripadvisor.xyz/x",
]
GOOD_G = [
    "https://g.page/r/CabcDEF/review",
    "https://maps.app.goo.gl/AbCdEf",
    "https://search.google.com/local/writereview?placeid=ChIJ123",
]
BAD_G = [
    "https://g.page.evil.example/r/x/review",
    "https://evil.example/g.page/r/x",
    "https://search.google.com/search-console",
    "https://maps.app.goo.gl.evil.example/x",
    "http://g.page/r/x/review",
    "https://g.page@evil.example/r/x",
    "https://www.google.com/maps/place/X",
]


@pytest.mark.parametrize("url", GOOD_TA)
def test_official_tripadvisor_hosts_pass(url):
    assert is_tripadvisor_url(url)
    assert ReviewLinksIn(tripadvisor_url=url).tripadvisor_url == url


@pytest.mark.parametrize("url", BAD_TA)
def test_tripadvisor_lookalikes_are_refused(url):
    assert not is_tripadvisor_url(url)
    with pytest.raises(ValidationError):
        ReviewLinksIn(tripadvisor_url=url)


@pytest.mark.parametrize("url", GOOD_G)
def test_official_google_review_hosts_pass(url):
    assert is_google_review_url(url)
    assert ReviewLinksIn(google_url=url).google_url == url


@pytest.mark.parametrize("url", BAD_G)
def test_google_lookalikes_are_refused(url):
    assert not is_google_review_url(url)
    with pytest.raises(ValidationError):
        ReviewLinksIn(google_url=url)


def test_empty_links_are_none():
    links = ReviewLinksIn(google_url="", tripadvisor_url=None)
    assert links.google_url is None and links.tripadvisor_url is None


def test_marketing_window_narrows_but_never_widens():
    assert GuestsSettings(marketing_window={"start": "10:00", "end": "17:00"}).marketing_window.start == "10:00"
    with pytest.raises(ValidationError):
        GuestsSettings(marketing_window={"start": "08:00", "end": "18:00"})
    with pytest.raises(ValidationError):
        GuestsSettings(marketing_window={"start": "09:00", "end": "18:30"})


def test_template_text_refuses_the_em_dash():
    with pytest.raises(ValidationError):
        GuestsSettings(directions="Turn left \u2014 then right")
    assert GuestsSettings(directions="Turn left, then right").directions == "Turn left, then right"


def test_no_literal_em_dash_in_the_guests_module():
    root = Path(__file__).resolve().parents[3] / "clefs" / "modules" / "guests"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.is_file() and p.suffix in {".py", ".sql", ".md"} and "\u2014" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
