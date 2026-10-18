"""Marketing eligibility (03 spec 2.7 rebooking rules, 04 spec 3.4/3.5):
can_send_marketing, record_external_send, marketing_audience, opt_out."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.core import consent
from clefs.modules.guests import api, identity

from .test_identity import EMAIL, PHONE, add_contact, capture, grant, make_res

DUBAI = ZoneInfo("Asia/Dubai")
AT = datetime(2026, 10, 5, 11, 0, tzinfo=DUBAI)  # inside 09:00-18:00


def _h(cur, slug, value=PHONE, kind="phone"):
    return identity.hmac_for(cur, slug, kind, value)


def _ok_guest(cur, slug, value=PHONE):
    capture(cur, slug, value=value, consents={"marketing_whatsapp": True})
    return _h(cur, slug, value)


def test_no_consent_no_marketing(cur, hotel_uae):
    s = hotel_uae.slug
    capture(cur, s, consents={"whatsapp_updates": True})
    assert api.can_send_marketing(cur, s, identity_hmacs=[_h(cur, s)], channel="whatsapp", at=AT, phase="post_stay") == (
        False, "no_consent")
    assert api.can_send_marketing(cur, s, identity_hmacs=[], channel="whatsapp", at=AT, phase="post_stay") == (
        False, "no_identity")


def test_consented_direct_guest_is_allowed_inside_the_window(cur, hotel_uae):
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (True, "ok")
    # whatsapp consent is not email consent
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="email", at=AT, phase="post_stay")[1] == "no_consent"


@pytest.mark.parametrize(
    "hhmm, allowed", [((8, 59), False), ((9, 0), True), ((17, 59), True), ((18, 0), False)]
)
def test_marketing_window_is_half_open_in_hotel_time(cur, hotel_uae, hhmm, allowed):
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    at = datetime(2026, 10, 5, *hhmm, tzinfo=DUBAI)
    ok, reason = api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=at, phase="post_stay")
    assert ok is allowed and (allowed or reason == "outside_window")


def test_ota_provided_contacts_are_never_eligible_even_with_a_consent_row(cur, hotel_uae):
    s = hotel_uae.slug
    rid = make_res(cur, s, code="BOOKING")
    ota_phone = add_contact(cur, s, rid, "+971509999999", provenance="pms_ota")
    alias = add_contact(cur, s, rid, "x1@guest.booking.com", kind="email", provenance="ota_alias")
    from clefs.modules.guests import contacts

    contacts.mirror_reservation_contacts(cur, s, rid)
    grant(cur, s, ota_phone, "marketing_whatsapp")  # forced in
    grant(cur, s, alias, "marketing_email")
    for h, ch in ((ota_phone, "whatsapp"), (alias, "email")):
        assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel=ch, at=AT, phase="post_stay") == (
            False, "no_eligible_contact")
    assert api.marketing_audience(cur, s, channel="whatsapp") == []
    assert api.marketing_audience(cur, s, channel="email") == []


def test_nanp_numbers_get_no_whatsapp_marketing(cur, hotel_uae):
    s = hotel_uae.slug
    h = _ok_guest(cur, s, value="+14155550100")
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (
        False, "nanp_blocked")
    assert api.marketing_audience(cur, s) == []


def test_audience_country_segment_uses_the_calling_code(cur, hotel_uae):
    # Demand passes {"countries": [...]} (an event's source markets); a UAE
    # number matches AE, not IN, and an empty country list does not narrow.
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    assert [r["contact_ref"] for r in api.marketing_audience(cur, s, segment={"countries": ["AE"]})] == [h.hex()]
    assert api.marketing_audience(cur, s, segment={"countries": ["in"]}) == []
    assert len(api.marketing_audience(cur, s, segment={"countries": []})) == 1


def test_caps_daily_and_two_post_stay_per_30_days_across_senders(cur, hotel_uae):
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    api.record_external_send(cur, s, identity_hmacs=[h], channel="whatsapp", source="demand", sent_at=AT - timedelta(days=20), ref="c1")
    api.record_external_send(cur, s, identity_hmacs=[h], channel="whatsapp", source="demand", sent_at=AT - timedelta(days=20), ref="c1")
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (True, "ok")
    api.record_external_send(cur, s, identity_hmacs=[h], channel="whatsapp", source="journey_j8", sent_at=AT - timedelta(hours=1), ref="p9")
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (
        False, "cap_daily")
    tomorrow = AT + timedelta(days=1)
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=tomorrow, phase="post_stay") == (
        False, "cap_post_stay")
    # the duplicate (source, ref) was counted once, so 11 days on the oldest has aged out
    later = AT + timedelta(days=11)
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=later, phase="post_stay") == (True, "ok")
    cur.execute("SELECT source, phase FROM gst_marketing_sends WHERE hotel_slug = %s ORDER BY id", (s,))
    assert [tuple(r.values()) for r in cur.fetchall()] == [("demand_export", "post_stay"), ("journey_j8", "post_stay")]


def test_opt_out_suppresses_and_withdraws_marketing(cur, hotel_uae):
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    assert len(api.marketing_audience(cur, s)) == 1
    assert api.opt_out(cur, s, kind="phone", value="+971 50 123 4567", source="whatsapp") is None
    assert api.opt_out(cur, s, kind="phone", value=PHONE, source="whatsapp") is None  # idempotent
    assert api.opt_out(cur, s, kind="phone", value="not a phone", source="whatsapp") is None
    assert not consent.has_consent(cur, s, h.hex(), "marketing_whatsapp")
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (
        False, "suppressed")
    assert api.marketing_audience(cur, s) == []
    cur.execute("SELECT COUNT(*) AS n FROM gst_suppressions WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 1
    # a fresh evidenced opt-in lifts the STOP
    capture(cur, s, consents={"marketing_whatsapp": True})
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="post_stay") == (True, "ok")


def test_audience_returns_consented_direct_contacts_with_locale(cur, hotel_uae):
    s = hotel_uae.slug
    h = _ok_guest(cur, s)
    capture(cur, s, value=EMAIL, kind="email", consents={"marketing_email": True})
    capture(cur, s, value="+971507777777", consents={"whatsapp_updates": True})  # no marketing consent
    wa = api.marketing_audience(cur, s, channel="whatsapp")
    assert wa == [{"contact_ref": h.hex(), "channel": "whatsapp", "locale": "en"}]
    em = api.marketing_audience(cur, s, channel="email")
    assert [r["contact_ref"] for r in em] == [_h(cur, s, EMAIL, "email").hex()]
    assert api.marketing_audience(cur, s, channel="sms") == []


def test_pre_stay_cap_is_one_per_reservation(cur, hotel_uae):
    s = hotel_uae.slug
    rid = make_res(cur, s, code="DIRECT_WEB", check_in=AT.date() + timedelta(days=5), check_out=AT.date() + timedelta(days=8))
    from clefs.modules.guests import linking

    linking.link_reservation(cur, s, rid)
    capture(cur, s, reservation_id=rid, consents={"marketing_whatsapp": True})
    h = _h(cur, s)
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="pre_stay") == (True, "ok")
    from clefs.modules.guests import marketing

    marketing.record_send(cur, s, identity_hmacs=[h], channel="whatsapp", source="journey_j3",
                          sent_at=AT - timedelta(days=2), ref="j3", phase="pre_stay", reservation_id=rid)
    assert api.can_send_marketing(cur, s, identity_hmacs=[h], channel="whatsapp", at=AT, phase="pre_stay") == (
        False, "cap_pre_stay")
