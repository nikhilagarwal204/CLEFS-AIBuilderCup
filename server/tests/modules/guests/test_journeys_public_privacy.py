"""Regression tests for the public journey pages' privacy fixes:
- a J1 prefs link cannot lift a third party's STOP (security-privacy-4);
- posted consent text versions must be the ones the page could show;
- an expired link returns no preferences and no stay dates (security-privacy-7)."""

from __future__ import annotations

import json
from itertools import count

from clefs.core import consent, privacy
from clefs.modules.guests import api, contacts, identity
from clefs.modules.guests.journeys import tokens as jtokens

from .test_identity import PHONE, capture
from .test_journeys_support import at, event, make_res

VICTIM = "+971509876543"
_ids = count(1)


def _stopped_victim(cur, slug):
    """A number that opted in once (staff entry) and later replied STOP."""
    capture(cur, slug, value=VICTIM, consents={"whatsapp_updates": True, "marketing_whatsapp": True})
    api.opt_out(cur, slug, kind="phone", value=VICTIM, source="whatsapp")
    h = identity.hmac_for(cur, slug, "phone", VICTIM)
    assert contacts.is_suppressed(cur, slug, [h], marketing=False)
    return h


def _prefs_link(cur, slug, clock):
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, slug)
    event(cur, slug, rid, "created")
    return rid, jtokens.mint(cur, slug, rid, "prefs", expires_at=at(2026, 10, 20, 23, 59))


def _inbound_from(cur, slug, sender):
    n = next(_ids)
    cur.execute(
        "INSERT INTO inbound_messages (channel, provider_id, kind, hotel_slug, sender, audience, payload, sender_hmac) "
        "VALUES ('whatsapp', %s, 'text', %s, %s, 'guest', %s, %s) RETURNING id",
        (f"wamid.gpp{n}", slug, sender, json.dumps({"type": "text"}), privacy.contact_hmac(slug, "phone", sender, cur=cur)),
    )
    return cur.fetchone()["id"]


def _statuses(cur, slug, h):
    cur.execute("SELECT status FROM gst_contact_points WHERE hotel_slug = %s AND value_hmac = %s", (slug, h))
    return {r["status"] for r in cur.fetchall()}


# ─── STOP cannot be lifted from an unverified form ───


def test_j1_form_cannot_lift_a_third_partys_stop(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    h = _stopped_victim(cur, s)
    _, tok = _prefs_link(cur, s, clock)
    page = client.get(f"/public/journeys/{tok}").json()
    texts = page["consent_texts"]
    ticks = ("transfer_abroad", "whatsapp_updates", "marketing_whatsapp")
    r = client.post(f"/public/journeys/{tok}/prefs", json={
        "arrival_time": "22:00", "language": "en", "contact": {"kind": "phone", "value": VICTIM},
        "consents": {p: True for p in ticks}, "consent_text_versions": {p: texts[p]["version"] for p in ticks}})
    assert r.status_code == 200, r.text
    # The STOP stands, every point of the number stays suppressed (the new
    # one included), and the ticks it covers were never recorded.
    assert contacts.is_suppressed(cur, s, [h], marketing=False)
    assert _statuses(cur, s, h) == {"suppressed"}
    assert not consent.has_consent(cur, s, h.hex(), "whatsapp_updates")
    assert not consent.has_consent(cur, s, h.hex(), "marketing_whatsapp")
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=at(2026, 10, 5, 11),
                                  phase="post_stay") == (False, "suppressed")
    cur.execute("SELECT after FROM audit_log WHERE action = 'guests.contact.capture' ORDER BY id DESC LIMIT 1")
    assert cur.fetchone()["after"]["held_by_suppression"] == ["marketing_whatsapp", "whatsapp_updates"]


def test_j1_form_still_opts_in_a_number_that_never_said_stop(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    _, tok = _prefs_link(cur, s, clock)
    texts = client.get(f"/public/journeys/{tok}").json()["consent_texts"]
    ticks = ("transfer_abroad", "whatsapp_updates")
    r = client.post(f"/public/journeys/{tok}/prefs", json={
        "language": "en", "contact": {"kind": "phone", "value": PHONE},
        "consents": {p: True for p in ticks}, "consent_text_versions": {p: texts[p]["version"] for p in ticks}})
    assert r.status_code == 200, r.text
    h = identity.hmac_for(cur, s, "phone", PHONE)
    assert consent.has_consent(cur, s, h.hex(), "whatsapp_updates")
    assert _statuses(cur, s, h) == {"active"}


def test_a_message_from_the_number_itself_lifts_the_stop(cur, hotel_uae):
    s = hotel_uae.slug
    h = _stopped_victim(cur, s)
    grant = {"transfer_abroad": True, "whatsapp_updates": True}
    # An inbound message from ANOTHER number vouches for nothing.
    other = _inbound_from(cur, s, PHONE)
    capture(cur, s, value=VICTIM, source="concierge", consents=grant, evidence={"inbound_message_id": other})
    assert contacts.is_suppressed(cur, s, [h], marketing=False)
    assert not consent.has_consent(cur, s, h.hex(), "whatsapp_updates")
    # The number messaging in is proof of control: the STOP is lifted.
    own = _inbound_from(cur, s, VICTIM)
    capture(cur, s, value=VICTIM, source="concierge", consents=grant, evidence={"inbound_message_id": own})
    assert not contacts.is_suppressed(cur, s, [h], marketing=False)
    assert consent.has_consent(cur, s, h.hex(), "whatsapp_updates")
    assert _statuses(cur, s, h) == {"active"}


def test_a_marketing_only_stop_still_takes_utility_updates_from_the_form(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    capture(cur, s, value=VICTIM, consents={"marketing_whatsapp": True})
    api.opt_out(cur, s, kind="phone", value=VICTIM, source="email_link", scope="marketing")
    h = identity.hmac_for(cur, s, "phone", VICTIM)
    _, tok = _prefs_link(cur, s, clock)
    texts = client.get(f"/public/journeys/{tok}").json()["consent_texts"]
    ticks = ("transfer_abroad", "whatsapp_updates", "marketing_whatsapp")
    r = client.post(f"/public/journeys/{tok}/prefs", json={
        "language": "en", "contact": {"kind": "phone", "value": VICTIM},
        "consents": {p: True for p in ticks}, "consent_text_versions": {p: texts[p]["version"] for p in ticks}})
    assert r.status_code == 200, r.text
    assert contacts.is_suppressed(cur, s, [h], marketing=True)
    assert not consent.has_consent(cur, s, h.hex(), "marketing_whatsapp")
    assert consent.has_consent(cur, s, h.hex(), "whatsapp_updates")


# ─── Posted wording versions ───


def test_posted_text_version_must_be_one_the_page_showed(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    _, tok = _prefs_link(cur, s, clock)
    shown = client.get(f"/public/journeys/{tok}").json()["consent_texts"]["transfer_abroad"]["version"]
    body = {"language": "en", "contact": {"kind": "phone", "value": PHONE}, "consents": {"transfer_abroad": True}}
    r = client.post(f"/public/journeys/{tok}/prefs", json={**body, "consent_text_versions": {"transfer_abroad": "forged.v9"}})
    assert r.status_code == 422 and r.json()["code"] == "consent_not_allowed"
    cur.execute("SELECT COUNT(*) AS n FROM gst_contact_points WHERE hotel_slug = %s AND capture_source = 'j1_form'", (s,))
    assert cur.fetchone()["n"] == 0
    r = client.post(f"/public/journeys/{tok}/prefs", json={**body, "consent_text_versions": {"transfer_abroad": shown}})
    assert r.status_code == 200 and r.json()["contact_saved"] is True
    h = identity.hmac_for(cur, s, "phone", PHONE)
    cur.execute("SELECT text_version FROM consents WHERE hotel_slug = %s AND subject_ref = %s AND purpose = 'transfer_abroad'",
                (s, h.hex()))
    assert {r["text_version"] for r in cur.fetchall()} == {shown}


def test_j5_transfer_tick_refuses_a_forged_version(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    rid, _ = _prefs_link(cur, s, clock)
    mid = _inbound_from(cur, s, PHONE)
    tok = jtokens.mint(cur, s, rid, "consent", expires_at=at(2026, 9, 2), ref={"inbound_message_id": mid})
    r = client.post(f"/public/journeys/{tok}/consent", json={"transfer_abroad": True, "text_version": "forged.v9"})
    assert r.status_code == 422 and r.json()["code"] == "consent_not_allowed"
    r = client.post(f"/public/journeys/{tok}/consent", json={"transfer_abroad": True})
    assert r.status_code == 200 and r.json()["point_created"] is True


# ─── Expired links show nothing about the stay ───


def test_expired_prefs_link_returns_no_prefs_and_no_stay_dates(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    rid, tok = _prefs_link(cur, s, clock)
    r = client.post(f"/public/journeys/{tok}/prefs", json={"arrival_time": "22:00", "requests": "No nuts, severe allergy"})
    assert r.status_code == 200
    live = client.get(f"/public/journeys/{tok}").json()
    assert live["prefs"]["requests"] == "No nuts, severe allergy" and live["stay"]["check_in"]
    clock.freeze(at(2026, 10, 21, 0, 5))  # past check-in day 23:59
    dead = client.get(f"/public/journeys/{tok}").json()
    assert dead["state"] == "expired"
    assert set(dead) == {"purpose", "state", "language", "hotel"}
    assert "No nuts" not in json.dumps(dead)
    # the prefs still exist until purge_after; only the link stopped reading them
    cur.execute("SELECT requests FROM gst_stay_prefs WHERE hotel_slug = %s AND reservation_id = %s", (s, rid))
    assert cur.fetchone()["requests"] == "No nuts, severe allergy"


def test_expired_feedback_link_keeps_the_review_line_but_not_the_stay(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    rid, _ = _prefs_link(cur, s, clock)
    tok = jtokens.mint(cur, s, rid, "feedback", expires_at=at(2026, 9, 2))
    clock.freeze(at(2026, 9, 3))
    dead = client.get(f"/public/journeys/{tok}").json()
    assert dead["state"] == "expired" and "stay" not in dead
    assert "review_links" in dead and dead["submitted"] is False
