"""Public web form (04 spec 4.3, 4.12): rate limit, honeypot, fill time,
widget key, both required consents enforced separately, consent rows."""

from __future__ import annotations

import pytest

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    thread,
)


@pytest.fixture
def widget(cur, hotel):
    from clefs.modules.inquiries import channels

    return channels.ensure_widget(cur, hotel.slug)["config"]["widget_key"]


def _config(client, hotel, widget):
    r = client.get(f"/public/inquiries/{hotel.slug}/widget", params={"k": widget, "lang": "en"})
    assert r.status_code == 200, r.text
    return r.json()


def _form(cfg, widget, **kw):
    body = {
        "name": "Sara Ali", "email": "sara@example.com", "phone": "+971501234567", "check_in": "2026-10-12",
        "check_out": "2026-10-15", "rooms": 2, "adults": 4, "message": "Family trip, 2 rooms please",
        "language": "en", "consent_contact": True, "consent_transfer_abroad": True, "widget_key": widget,
        "hp": "", "rendered_at": cfg["rendered_at"], "utm": {"utm_source": "google", "evil": "x"}, "gclid": "g-123",
        "page_url": "https://hotel.example/rooms",
    }
    body.update(kw)
    return body


def test_widget_config_shows_the_consent_lines(client, hotel, widget):
    cfg = _config(client, hotel, widget)
    keys = {c["key"]: c for c in cfg["consents"]}
    assert keys["consent_contact"]["required"] and keys["consent_transfer_abroad"]["required"]
    assert not keys["consent_marketing_email"]["required"]
    assert "Test Hotel Dubai may contact me about this request." == keys["consent_contact"]["text"]
    assert client.get(f"/public/inquiries/{hotel.slug}/widget", params={"k": "wrong"}).status_code == 404


def test_form_creates_an_express_inquiry_with_consents(client, cur, hotel, widget, now):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=10)
    r = client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, consent_marketing_email=True))
    assert r.status_code == 202 and r.json() == {"ok": True}
    row = inquiries(cur, hotel.slug)[0]
    assert row["source"] == "web_form" and row["consent_basis"] == "express_web_form"
    assert (row["rooms"], row["adults"], row["nights"]) == (2, 4, 3)
    assert row["utm"] == {"utm_source": "google"} and row["gclid"] == "g-123"
    cur.execute("SELECT purpose, subject_type, text_version FROM consents WHERE id = ANY(%s)", (row["consent_ids"],))
    purposes = sorted(c["purpose"] for c in cur.fetchall())
    # contact + transfer_abroad for both identities, marketing for the email only.
    assert purposes == ["inquiry_contact", "inquiry_contact", "marketing", "transfer_abroad", "transfer_abroad"]


@pytest.mark.parametrize("missing", ["consent_contact", "consent_transfer_abroad"])
def test_each_required_consent_is_enforced_separately(client, cur, hotel, widget, now, missing):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=10)
    r = client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, **{missing: False}))
    assert r.status_code == 422 and r.json()["code"] == f"{missing}_required"
    assert inquiries(cur, hotel.slug) == []


def test_honeypot_and_fast_fill_store_nothing(client, cur, hotel, widget, now):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=10)
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, hp="http://spam")).status_code == 202
    cfg = _config(client, hotel, widget)
    now.advance(seconds=1)
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget)).status_code == 202
    assert inquiries(cur, hotel.slug) == []


def test_wrong_widget_key_is_refused(client, cur, hotel, widget, now):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=10)
    r = client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, "not-the-key"))
    assert r.status_code == 403 and r.json()["code"] == "invalid_widget_key"


def test_rate_limit_is_five_per_hour_per_ip(client, cur, hotel, widget, now):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=10)
    codes = [client.post(f"/public/inquiries/{hotel.slug}",
                         json=_form(cfg, widget, email=f"g{i}@example.com", phone=None)).status_code for i in range(6)]
    assert codes == [202] * 5 + [429]


# ─── Render stamp: signed only, fresh only (bot bypass regression) ───


@pytest.mark.parametrize("stamp", [0, "0", "1700000000000", None, "", "inqform:test-dubai:0", "not-a-stamp"])
def test_unsigned_render_stamp_is_treated_as_a_bot(client, cur, hotel, widget, now, stamp):
    cfg = _config(client, hotel, widget)
    now.advance(seconds=30)
    r = client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, rendered_at=stamp))
    assert r.status_code == 202 and r.json() == {"ok": True}
    assert inquiries(cur, hotel.slug) == []
    cur.execute("SELECT COUNT(*) AS n FROM consents WHERE hotel_slug = %s", (hotel.slug,))
    assert cur.fetchone()["n"] == 0


def test_stale_or_foreign_render_stamp_is_treated_as_a_bot(client, cur, hotel, widget, now):
    from clefs.auth import tokens
    from clefs.modules.inquiries.router_public import RENDER_MAX_AGE_MS, RENDER_PREFIX, render_stamp

    cfg = _config(client, hotel, widget)
    now.advance(seconds=RENDER_MAX_AGE_MS // 1000 + 60)  # harvested, replayed hours later
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget)).status_code == 202
    assert inquiries(cur, hotel.slug) == []
    other = render_stamp("another-hotel")  # signed, but for another hotel's form
    now.advance(seconds=30)
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, rendered_at=other)).status_code == 202
    future = tokens.sign_ref(f"{RENDER_PREFIX}:{hotel.slug}:{10**14}")
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget, rendered_at=future)).status_code == 202
    assert inquiries(cur, hotel.slug) == []
    # A fresh signed stamp still works.
    cfg = _config(client, hotel, widget)
    now.advance(seconds=30)
    assert client.post(f"/public/inquiries/{hotel.slug}", json=_form(cfg, widget)).status_code == 202
    assert len(inquiries(cur, hotel.slug)) == 1
