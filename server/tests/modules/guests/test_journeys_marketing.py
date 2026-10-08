"""Marketing steps J3 and J8 (04 spec 3.4, 3.5, Canon 19): never to OTA
aliases or OTA-provided contacts, whatever the consent table says."""

from __future__ import annotations

from datetime import date

import pytest

from clefs import files
from clefs.core import actions
from clefs.modules.guests import api as guests_api
from clefs.modules.guests import identity, timeutil
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import dispatch, guards

from tests.modules.guests.test_journeys_support import (
    add_contact, approve_templates, at, event, grant, make_offer, make_res, plans, sends, set_res,
)

CI, CO = date(2026, 10, 20), date(2026, 10, 23)
OTA_CONTACTS = [
    ("email", "a1b2@guest.booking.com", "ota_alias", "BOOKING", "guest.booking.com"),
    ("email", "x9@m.expediapartnercentral.com", "ota_alias", "EXPEDIA", "m.expediapartnercentral.com"),
    ("email", "rsv123@agoda-messaging.com", "ota_alias", "AGODA", "agoda-messaging.com"),
    ("phone", "+971501112222", "pms_ota", "BOOKING", None),
]


def _limits(cur, s, key):
    actions.set_autonomy(cur, s, key, level="auto_within_limits", limits={"max_sends_per_day": 50},
                         actor=actions.SYSTEM)


def _signature(cur, s):
    return files.put_file(cur, s, b"\x89PNG\r\n\x1a\nsig", filename="sig.png", content_type="image/png",
                          purpose="guests.contact_evidence").id


@pytest.mark.parametrize("kind,value,prov,code,domain", OTA_CONTACTS)
def test_no_marketing_to_ota_aliases(cur, hotel_uae, clock, outbox, kind, value, prov, code, domain):
    s = hotel_uae.slug
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s, code=code)
    h = add_contact(cur, s, rid, value, kind=kind, provenance=prov, alias_domain=domain)
    grant(cur, s, h, "marketing_email" if kind == "email" else "marketing_whatsapp", "whatsapp_updates")
    event(cur, s, rid, "created")
    make_offer(cur, s, "j3")
    make_offer(cur, s, "j8", title="10% off", price_minor=None, discount_pct=10)
    approve_templates(cur, s, step_list=("j3", "j8"))
    _limits(cur, s, "guests.journey_j3_offer")
    _limits(cur, s, "guests.journey_j8_rebook")
    clock.freeze(at(2026, 10, 17, 11))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j3", 1)]["skip_reason"] == "no_eligible_contact"
    set_res(cur, rid, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    clock.freeze(at(2026, 10, 23, 11))
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    clock.freeze(at(2026, 11, 22, 11))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j8", 1)]["skip_reason"] in ("no_eligible_contact", "alias_expired")
    assert sends(cur, s, category="marketing") == []


def test_after_registration_card_j8_goes_to_the_direct_point_only(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    clock.freeze(at(2026, 10, 21, 10))
    rid = make_res(cur, s, code="BOOKING", status="IN_HOUSE")
    alias = add_contact(cur, s, rid, "a1b2@guest.booking.com", kind="email", provenance="ota_alias",
                        alias_domain="guest.booking.com")
    grant(cur, s, alias, "marketing_email")
    versions = {p: identity.text_version(cur, s, p) for p in ("transfer_abroad", "marketing_email")}
    point = guests_api.record_own_contact(
        cur, s, reservation_id=rid, kind="email", value="me@direct.example", capture_source="registration_card",
        consents={"transfer_abroad": True, "marketing_email": True}, consent_text_versions=versions,
        evidence={"kind": "staff_attestation", "signature_file_id": _signature(cur, s)}, actor=None,
    )
    make_offer(cur, s, "j8", title="10% off", price_minor=None, discount_pct=10)
    approve_templates(cur, s, step_list=("j8",))
    _limits(cur, s, "guests.journey_j8_rebook")
    set_res(cur, rid, status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    clock.freeze(at(2026, 10, 23, 11))
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    clock.freeze(at(2026, 11, 22, 11))
    dispatch.dispatch_hotel(cur, s)
    p = plans(cur, s, rid)[("j8", 1)]
    assert p["status"] in ("sent", "delivered"), p
    assert p["contact_point_id"] == point
    out = sends(cur, s, category="marketing")
    assert [r["recipient"] for r in out] == ["me@direct.example"]
    assert "List-Unsubscribe" in str(out[0]["payload"])
    assert p["direct_code"] and p["direct_code"].startswith("BACK-")
    a = actions.get_action(cur, s, p["action_id"])
    assert a["consent_basis"].startswith("consent:") and a["template_category"] == "MARKETING"


def test_j1_form_contact_on_ota_booking_never_gets_marketing(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s, code="AGODA")
    v = {"transfer_abroad": identity.text_version(cur, s, "transfer_abroad"),
         "whatsapp_updates": identity.text_version(cur, s, "whatsapp_updates")}
    pid = guests_api.record_own_contact(
        cur, s, reservation_id=rid, kind="email", value="own@example.com", capture_source="j1_form",
        consents={"transfer_abroad": True, "whatsapp_updates": True}, consent_text_versions=v, evidence={}, actor=None,
    )
    cur.execute("SELECT provenance FROM gst_contact_points WHERE id = %s", (pid,))
    assert cur.fetchone()["provenance"] == "guest_stay"
    h = identity.hmac_for(cur, s, "email", "own@example.com")
    grant(cur, s, h, "marketing_email")  # forced in: provenance still wins
    assert guests_api.marketing_audience(cur, s, channel="email") == []
    ok, why = guests_api.can_send_marketing(cur, s, identity_hmacs=[h], channel="email", at=at(2026, 9, 2, 10),
                                            phase="post_stay")
    assert not ok and why == "no_eligible_contact"


@pytest.mark.parametrize("hhmm,inside", [((8, 59), False), ((9, 0), True), ((17, 59), True), ((18, 0), False)])
def test_marketing_window_is_half_open_in_dubai(cur, hotel_uae, hhmm, inside):
    cfg = gsettings.load(cur, hotel_uae.slug)
    t0, t1 = guards.window_for(cfg, "MARKETING")
    assert timeutil.in_window(hotel_uae, at(2026, 10, 17, *hhmm), t0, t1) is inside


def test_nanp_numbers_get_no_whatsapp_marketing(cur, hotel_uae, clock, outbox):
    from tests.modules.guests.test_journeys_support import enable_whatsapp

    s = hotel_uae.slug
    enable_whatsapp(cur, s)
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    h = add_contact(cur, s, rid, "+12125550123")
    grant(cur, s, h, "marketing_whatsapp", "whatsapp_updates")
    event(cur, s, rid, "created")
    make_offer(cur, s, "j3")
    approve_templates(cur, s, step_list=("j3",))
    _limits(cur, s, "guests.journey_j3_offer")
    clock.freeze(at(2026, 10, 17, 11))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j3", 1)]["status"] == "skipped"
    assert sends(cur, s, channel="whatsapp") == []
