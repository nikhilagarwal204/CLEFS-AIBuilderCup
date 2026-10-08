"""Guests console routes (03 spec 4.2), the registration-card capture route
(04 spec 3.10), unsubscribe links and the WhatsApp STOP handler."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from clefs import notify
from clefs.core import consent
from clefs.modules.guests import api, identity, optout

from .test_identity import PHONE, add_contact, grant, make_res

NOON = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)
ALL_CONSENTS = {"transfer_abroad": True, "profile_memory": True, "marketing_whatsapp": True}
PREFS = {"tags": ["extra pillows"], "health": {"allergies": ["nuts"]}}


def _modal(c, slug, room="101", **kw):
    body = {"phone": PHONE, "first_name": "Aisha", "consents": ALL_CONSENTS, "preferences": PREFS, **kw}
    return c.post(f"/hotels/{slug}/guests/rooms/{room}/identity", json=body)


def test_guestmodal_capture_then_profiles_masked_by_role(client_as, hotel_uae):
    s = hotel_uae.slug
    fd = client_as("front_desk")
    r = _modal(fd, s)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["profile"]["first_name"] == "Aisha" and out["profile"]["preferences"]["health"]["allergies"] == ["nuts"]
    assert out["contact"]["phone"] != PHONE and out["contact"]["phone"].endswith("67")
    items = fd.get(f"/hotels/{s}/guests/profiles").json()["items"]
    assert len(items) == 1 and items[0]["contacts"][0]["value"] == PHONE  # front desk: full contact
    pid = items[0]["id"]
    detail = fd.get(f"/hotels/{s}/guests/profiles/{pid}").json()
    assert detail["consents"][0]["purposes"]["profile_memory"]["granted"] is True
    assert PHONE not in repr(detail["consents"])
    # roles without guests.view never reach profiles
    assert client_as("revenue").get(f"/hotels/{s}/guests/profiles").status_code == 403
    assert client_as("finance").get(f"/hotels/{s}/guests/profiles/{pid}").status_code == 403
    found = fd.get(f"/hotels/{s}/guests/profiles", params={"contact": "+971 50 123 4567"}).json()
    assert found["total"] == 1
    assert fd.get(f"/hotels/{s}/guests/overview").json()["consents"]["profile_memory"] == 1


def test_capture_without_transfer_consent_is_refused(client_as, hotel_uae):
    r = _modal(client_as("front_desk"), hotel_uae.slug, consents={"profile_memory": True})
    assert r.status_code == 422 and r.json()["code"] == "consent_not_allowed"


def test_patch_and_forget_profile(client_as, hotel_uae):
    s = hotel_uae.slug
    gm = client_as("gm")
    pid = _modal(gm, s).json()["profile_id"]
    r = gm.patch(f"/hotels/{s}/guests/profiles/{pid}", json={"vip": True, "preferences": {"tags": ["late checkout"]}})
    assert r.status_code == 200 and r.json()["vip"] is True and r.json()["preferences"]["tags"] == ["late checkout"]
    assert gm.delete(f"/hotels/{s}/guests/profiles/{pid}").status_code == 200
    assert gm.get(f"/hotels/{s}/guests/profiles/{pid}").status_code == 404
    look = gm.post(f"/hotels/{s}/guests/consents/lookup", json={"kind": "phone", "value": PHONE}).json()
    assert look["purposes"]["profile_memory"]["granted"] is False and look["profile_id"] is None


def test_opt_out_route_and_rebooking_pipeline(client_as, hotel_uae, cur, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    gm = client_as("gm")
    rid = make_res(cur, s, check_in=date(2026, 8, 20), check_out=date(2026, 9, 1), status="CHECKED_OUT")
    h = add_contact(cur, s, rid, PHONE)
    grant(cur, s, h, "marketing_whatsapp")
    from clefs.modules.guests import linking

    linking.link_reservation(cur, s, rid)
    items = gm.get(f"/hotels/{s}/guests/rebooking").json()["items"]
    assert items[0]["reservation_id"] == rid and items[0]["marketing"]["whatsapp"] is True and items[0]["eligible"]
    assert items[0]["days_since"] == 34
    assert gm.post(f"/hotels/{s}/guests/opt-out", json={"kind": "phone", "value": PHONE}).json()["created"] is True
    items = gm.get(f"/hotels/{s}/guests/rebooking").json()["items"]
    assert items[0]["suppressed"] is True and items[0]["eligible"] is False


def test_codes_routes(client_as, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    gm = client_as("gm")
    r = gm.post(f"/hotels/{s}/guests/codes", json={"valid_to": "2026-12-31", "discount_pct": "10", "prefix": "vip"})
    assert r.status_code == 200 and r.json()["code"].startswith("VIP-")
    assert gm.post(f"/hotels/{s}/guests/codes", json={"valid_to": "2026-01-01"}).json()["code"] == "invalid_valid_to"
    assert len(gm.get(f"/hotels/{s}/guests/codes").json()) == 1


def test_registration_card_capture_needs_a_front_office_device(device_client, client_as, hotel_uae, cur):
    from clefs import files

    s = hotel_uae.slug
    sig = files.put_file(cur, s, b"\x89PNG sig", filename="sig.png", content_type="image/png", purpose="guests.contact_evidence").id
    body = {"kind": "phone", "value": PHONE, "consents": {"transfer_abroad": True, "marketing_whatsapp": True},
            "evidence": {"kind": "staff_attestation", "signature_file_id": sig}}
    hk = device_client(departments=["Housekeeping"])
    assert hk.post(f"/hotels/{s}/guests/contacts", json=body).status_code == 403
    fo = device_client(departments=["Front Office"])
    r = fo.post(f"/hotels/{s}/guests/contacts", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["contact"]["value"] is None  # devices see no contact values (5.2)
    r = client_as("front_desk").post(f"/hotels/{s}/guests/contacts", json={**body, "consents": {"marketing_whatsapp": True}})
    assert r.status_code == 422 and r.json()["code"] == "consent_not_allowed"
    assert client_as("revenue").post(f"/hotels/{s}/guests/contacts", json=body).status_code == 403


def test_unsubscribe_link(client, cur, hotel_uae):
    s = hotel_uae.slug
    h = identity.hmac_for(cur, s, "email", "guest@example.com")
    path = api.unsubscribe_path(s, identity_hmac=h, channel="email")
    assert "example.com" not in path and "%40" not in path
    assert client.get(path).json()["status"] == "subscribed"
    assert client.post(path).json()["status"] == "unsubscribed"
    assert client.get(path).json()["status"] == "unsubscribed"
    assert client.post(path[:-3] + "xyz").status_code == 404


def test_whatsapp_stop_handler(cur, hotel_uae, outbox):
    s = hotel_uae.slug
    h = identity.hmac_for(cur, s, "phone", PHONE)
    grant(cur, s, h, "marketing_whatsapp", "whatsapp_updates")
    ctx = notify.InboundCtx(cur=cur, inbound_id=1, provider_id="wamid.1", kind="text", audience="guest",
                            sender=PHONE, recipient_id="PNID", hotel_slug=s, text="إيقاف", sender_hmac=h)
    assert optout._stop_match(ctx)
    assert optout.whatsapp_stop(ctx)["handled"] is True
    assert not consent.has_consent(cur, s, h.hex(), "whatsapp_updates")
    chatty = notify.InboundCtx(cur=cur, inbound_id=2, provider_id="wamid.2", kind="text", audience="guest",
                               sender=PHONE, recipient_id="PNID", hotel_slug=s, text="please don't stop the towels")
    assert not optout._stop_match(chatty)


def test_journey_timeline_and_arrival_prefs_read_the_journey_tables(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    rid = make_res(cur, s, check_in=date(2026, 10, 5), check_out=date(2026, 10, 7))
    from clefs.modules.guests import linking

    linking.link_reservation(cur, s, rid)
    cur.execute(
        "INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, due_at, valid_until, status) VALUES "
        "(%s, %s, 'j1', %s, %s, 'sent'), (%s, %s, 'j4', %s, %s, 'cancelled')",
        (s, rid, NOON - timedelta(days=5), NOON, s, rid, NOON, NOON + timedelta(days=1)),
    )
    assert [t["step"] for t in api.journey_timeline(cur, s, rid)] == ["j1"]
    cur.execute(
        "INSERT INTO gst_stay_prefs (hotel_slug, reservation_id, arrival_time, requests, purge_after) VALUES (%s, %s, '14:00', 'feather-free pillows', %s)",
        (s, rid, NOON + timedelta(days=30)),
    )
    prefs = api.arrival_prefs(cur, s, day=date(2026, 10, 5))
    assert prefs[0]["room"] == "101" and prefs[0]["arrival_time"] == "14:00"
    assert api.arrival_prefs(cur, s, day=date(2026, 10, 6)) == []


def test_a_strangers_first_stop_is_passed_on(cur, hotel_uae):
    s = hotel_uae.slug
    ctx = notify.InboundCtx(cur=cur, inbound_id=3, provider_id="wamid.3", kind="text", audience="guest",
                            sender="+971508888888", recipient_id="PNID", hotel_slug=s, text="STOP")
    assert not optout._stop_match(ctx)
