"""Journeys jobs, caps and the J8 verifier (04 spec 3.5, 3.7, 3.10)."""

from __future__ import annotations

from datetime import date, timedelta

from clefs.core import actions, clock as clock_mod
from clefs.modules.guests.journeys import dispatch, maintenance
from clefs.modules.guests.journeys import tokens as jtokens

from tests.modules.guests.test_journeys_support import (
    EMAIL, add_contact, approve_templates, at, event, make_offer, make_res, plans,
)


def test_contact_purge_after_usable_until_and_expedia_j7_alias_expired(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    clock.freeze(at(2026, 10, 23, 11))
    rid = make_res(cur, s, code="EXPEDIA", status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    add_contact(cur, s, rid, "x9@m.expediapartnercentral.com", kind="email", provenance="ota_alias",
                alias_domain="m.expediapartnercentral.com", usable_until=at(2026, 10, 23, 23, 59))
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    approve_templates(cur, s, step_list=("j7",))
    from clefs.core.hotels import set_module_settings

    set_module_settings(cur, s, "guests", {"j7_for_ota_provided_contacts": True}, actor=None)
    clock.freeze(at(2026, 10, 25, 11))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j7", 1)]["skip_reason"] == "alias_expired"
    out = maintenance.purge_contacts(cur, s)
    assert out["expired"] == 1
    cur.execute("SELECT status, value_hmac FROM gst_contact_points WHERE hotel_slug = %s AND reservation_id = %s", (s, rid))
    row = cur.fetchone()
    assert row["status"] == "purged" and row["value_hmac"] is None


def test_daily_cap_defers_the_lower_priority_step(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    approve_templates(cur, s)
    clock.freeze(at(2026, 10, 12, 10))
    rid = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    add_contact(cur, s, rid, EMAIL, kind="email")
    event(cur, s, rid, "created")
    # J1 went out today at 10:00; J2 falls due at 10:00 on the 13th, then J4 and J2 compete
    clock.freeze(at(2026, 10, 12, 10, 1))
    dispatch.dispatch_hotel(cur, s)
    assert plans(cur, s, rid)[("j1", 1)]["status"] in ("sent", "delivered")
    assert plans(cur, s, rid)[("j2", 1)]["status"] == "planned"
    cur.execute("UPDATE gst_journey_plans SET due_at = %s WHERE reservation_id = %s AND step = 'j2'",
                (at(2026, 10, 12, 10), rid))
    dispatch.dispatch_hotel(cur, s)
    j2 = plans(cur, s, rid)[("j2", 1)]
    assert (j2["status"], j2["skip_reason"]) == ("deferred", "cap_daily")
    assert j2["due_at"] == at(2026, 10, 13, 8)  # next day's utility window start


def test_optout_spike_demotes_j3_alone(cur, hotel_uae, clock):
    s = hotel_uae.slug
    now = at(2026, 10, 20, 12)
    clock.freeze(now)
    rid = make_res(cur, s)
    for i in range(40):
        cur.execute("INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, seq, due_at, valid_until, status, "
                    "last_attempt_at) VALUES (%s, %s, 'j3', %s, %s, %s, 'delivered', %s) RETURNING id",
                    (s, rid, i + 1, now, now, now - timedelta(days=1)))
        pid = cur.fetchone()["id"]
        if i < 4:
            cur.execute("INSERT INTO gst_journey_optouts (hotel_slug, value_hmac, step, plan_id, basis, source, opted_out_at) "
                        "VALUES (%s, %s, 'j3', %s, 'context', 'stop', %s)", (s, bytes([i]) * 8, pid, now - timedelta(hours=i + 1)))
    for i in range(40):
        cur.execute("INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, seq, due_at, valid_until, status, "
                    "last_attempt_at) VALUES (%s, %s, 'j1', %s, %s, %s, 'delivered', %s)",
                    (s, rid, i + 1, now, now, now - timedelta(days=1)))
    out = maintenance.monitor_optouts(cur, s, now=now)
    assert out["demoted"] == ["guests.journey_j3_offer"]
    assert actions.get_autonomy(cur, s, "guests.journey_j3_offer")["level"] == "approve"
    assert actions.get_autonomy(cur, s, "guests.journey_j1_confirm")["level"] == "auto"
    assert maintenance.monitor_optouts(cur, s, now=now)["demoted"] == []  # once per 7 days


def test_feedback_links_expire(cur, hotel_uae, clock, client):
    s = hotel_uae.slug
    clock.freeze(at(2026, 10, 30, 12))
    rid = make_res(cur, s)
    token = jtokens.mint(cur, s, rid, "feedback", expires_at=at(2026, 10, 30, 11))
    assert maintenance.expire_links(cur, s) == 1
    assert client.get(f"/public/journeys/{token}").json()["state"] == "expired"
    assert client.post(f"/public/journeys/{token}/feedback", json={"overall": 4}).status_code == 410


def test_billing_signal_when_whatsapp_has_no_payment_method(cur, hotel_uae):
    from clefs.core import signals
    from tests.modules.guests.test_journeys_support import enable_whatsapp

    s = hotel_uae.slug
    enable_whatsapp(cur, s, attested=False)
    assert maintenance.check_billing(cur, s) == {"configured": True, "ready": False}
    kinds = [x["kind"] for x in signals.list_signals(cur, s, modules=["guests"])]
    assert "guests.wa_billing_not_ready" in kinds


def _j8_sent(cur, s, clock, *, promo=None, utm=None, other_guest=False):
    clock.freeze(at(2026, 10, 23, 11))
    rid = make_res(cur, s, code="BOOKING", status="CHECKED_OUT", checked_out_at=at(2026, 10, 23, 11))
    from clefs.modules.guests import api as guests_api
    from clefs.modules.guests import identity

    v = {p: identity.text_version(cur, s, p) for p in ("transfer_abroad", "marketing_email")}
    guests_api.record_own_contact(cur, s, reservation_id=rid, kind="email", value="me@direct.example",
                                  capture_source="console", consents={"transfer_abroad": True, "marketing_email": True},
                                  consent_text_versions=v, evidence={}, actor=None)
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])
    make_offer(cur, s, "j8", title="10% off", price_minor=None, discount_pct=10)
    approve_templates(cur, s, step_list=("j8",))
    actions.set_autonomy(cur, s, "guests.journey_j8_rebook", level="auto_within_limits",
                         limits={"max_sends_per_day": 10}, actor=actions.SYSTEM)
    clock.freeze(at(2026, 11, 22, 11))
    dispatch.dispatch_hotel(cur, s)
    j8 = plans(cur, s, rid)[("j8", 1)]
    clock.freeze(at(2026, 11, 25, 11))
    new = make_res(cur, s, code="DIRECT_WEB", booked_at=at(2026, 11, 25, 10), check_in=date(2027, 1, 5),
                   check_out=date(2027, 1, 8), promo_code=promo(j8) if promo else None, utm=utm(j8) if utm else None)
    email = "someone@else.example" if other_guest else "me@direct.example"
    add_contact(cur, s, new, email, kind="email")
    return j8, new


def test_j8_code_redeemed_by_the_same_guest_counts(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    j8, new = _j8_sent(cur, s, clock, promo=lambda p: p["direct_code"])
    assert actions.run_verifier(cur, j8["action_id"])["status"] == "verified"
    a = actions.get_action(cur, s, j8["action_id"])
    assert a["attribution"] == "caused" and a["counts_toward_verified_profit"] is True
    cur.execute("SELECT redeemed_reservation_id FROM gst_direct_codes WHERE plan_id = %s", (j8["id"],))
    assert cur.fetchone()["redeemed_reservation_id"] == new


def test_j8_hmac_only_match_is_influenced(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    j8, new = _j8_sent(cur, s, clock)
    actions.run_verifier(cur, j8["action_id"])
    a = actions.get_action(cur, s, j8["action_id"])
    assert a["status"] == "verified" and a["counts_toward_verified_profit"] is False
    assert a["attribution"] == "influenced"


def test_j8_code_used_by_another_guest_does_not_count(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    j8, new = _j8_sent(cur, s, clock, promo=lambda p: p["direct_code"], other_guest=True)
    actions.run_verifier(cur, j8["action_id"])
    a = actions.get_action(cur, s, j8["action_id"])
    assert a["status"] == "done"  # still pending: no booking by this guest
    assert clock_mod.now() == at(2026, 11, 25, 11)
