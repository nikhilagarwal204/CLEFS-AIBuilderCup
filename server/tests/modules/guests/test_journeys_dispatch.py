"""Dispatch, channels and guards (04 spec 3.4, 3.5, 3.7), including the
Canon 19 test: no marketing to OTA aliases."""

from __future__ import annotations

from datetime import date

from clefs import notify
from clefs.core import actions
from clefs.modules.guests.journeys import dispatch

from tests.modules.guests.test_journeys_support import (
    EMAIL, PHONE, add_contact, approve_templates, at, enable_whatsapp, event, grant, make_offer, make_res, plans,
    sends, set_res,
)

CI, CO = date(2026, 10, 20), date(2026, 10, 23)


def _j1(cur, s, clock, **kw):
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s, booked_at=at(2026, 9, 1, 10), **kw)
    return rid


def test_j1_goes_out_on_email_when_whatsapp_has_no_meta_approval(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    enable_whatsapp(cur, s)
    rid = _j1(cur, s, clock)
    h = add_contact(cur, s, rid, PHONE)
    add_contact(cur, s, rid, EMAIL, kind="email")
    grant(cur, s, h, "whatsapp_updates")
    event(cur, s, rid, "created")
    approve_templates(cur, s, channels=("whatsapp",), meta="PENDING")
    approve_templates(cur, s, channels=("email",))
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j1", 1)]
    assert p["status"] == "delivered" and p["channel"] == "email"  # email: the outbox accept counts as delivered
    a = actions.get_action(cur, s, p["action_id"])
    assert a["status"] == "done" and a["template_id"] == "gst_j1_confirm_v1@1"
    assert a["consent_basis"] == f"contract:reservation:{rid}"


def test_no_approved_template_anywhere_drops_to_observe(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid = _j1(cur, s, clock)
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j1", 1)]
    assert (p["status"], p["skip_reason"]) == ("observed", "template_missing")
    assert actions.get_action(cur, s, p["action_id"])["status"] == "observed"
    assert sends(cur, s) == []


def test_no_whatsapp_without_optin(cur, hotel_uae, clock, outbox, monkeypatch):
    s = hotel_uae.slug
    enable_whatsapp(cur, s)
    approve_templates(cur, s)
    rid = _j1(cur, s, clock)
    add_contact(cur, s, rid, PHONE)  # pms_direct, no whatsapp_updates
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j1", 1)]["channel"] == "email"
    # A Booking.com real phone (pms_ota) with no email: nothing, until the
    # guest writes first and opens a window.
    ota = make_res(cur, s, code="BOOKING", booked_at=at(2026, 9, 1, 10))
    add_contact(cur, s, ota, "+971509999999", provenance="pms_ota")
    event(cur, s, ota, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, ota)[("j1", 1)]["skip_reason"] == "no_eligible_contact"
    monkeypatch.setattr(notify, "wa_window_open_until", lambda *a, **k: at(2026, 9, 2))
    ota2 = make_res(cur, s, code="BOOKING", booked_at=at(2026, 9, 1, 10))
    add_contact(cur, s, ota2, "+971508888888", provenance="pms_ota")
    event(cur, s, ota2, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, ota2)[("j1", 1)]["channel"] == "whatsapp"


def test_billing_not_ready_sends_email_instead_of_whatsapp(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    enable_whatsapp(cur, s, attested=False)
    approve_templates(cur, s)
    rid = _j1(cur, s, clock)
    h = add_contact(cur, s, rid, PHONE)
    add_contact(cur, s, rid, EMAIL, kind="email")
    grant(cur, s, h, "whatsapp_updates")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j1", 1)]["channel"] == "email"


def test_dial_off_skips_without_sending(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    approve_templates(cur, s)
    rid = _j1(cur, s, clock)
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    actions.set_autonomy(cur, s, "guests.journey_j1_confirm", level="off", actor=actions.SYSTEM)
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j1", 1)]["skip_reason"] == "dial_off"
    assert sends(cur, s) == []


def test_open_complaint_pauses_marketing_but_never_j7(cur, hotel_uae, clock, outbox, monkeypatch):
    from clefs.modules.service import api as service_api
    from tests.modules.guests.test_journeys_support import make_offer

    s = hotel_uae.slug
    monkeypatch.setattr(service_api, "has_open_complaint", lambda *a, **k: True)
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    h = add_contact(cur, s, rid, EMAIL, kind="email")
    grant(cur, s, h, "marketing_email")
    event(cur, s, rid, "created")
    make_offer(cur, s, "j3")
    approve_templates(cur, s, step_list=("j3", "j7"))
    clock.freeze(at(2026, 10, 17, 11))
    dispatch.dispatch_hotel(cur, s)
    j3 = plans(cur, s, rid)[("j3", 1)]
    assert (j3["status"], j3["skip_reason"]) == ("deferred", "complaint_open")
    set_res(cur, rid, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    clock.freeze(at(2026, 10, 23, 11))
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    clock.freeze(at(2026, 10, 25, 11))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j7", 1)]["status"] in ("sent", "delivered")


def test_meta_recategorised_utility_step_follows_marketing_rules(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    approve_templates(cur, s, channels=("email",))
    cur.execute("UPDATE gst_journey_templates SET category_meta = 'MARKETING' WHERE hotel_slug = %s AND step = 'j1'", (s,))
    rid = _j1(cur, s, clock)
    add_contact(cur, s, rid, EMAIL, kind="email")  # pms_direct, no marketing consent
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j1", 1)]["skip_reason"] == "no_eligible_contact"
    assert sends(cur, s) == []


def test_multi_room_reservation_sends_once(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    approve_templates(cur, s)
    rid = _j1(cur, s, clock)
    set_res(cur, rid, rooms_count=3)
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    dispatch.dispatch_hotel(cur, s)
    dispatch.dispatch_hotel(cur, s)
    assert len([m for m in sends(cur, s) if m["purpose"] == "guests.journey.j1"]) == 1
