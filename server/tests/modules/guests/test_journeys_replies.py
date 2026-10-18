"""Journey replies (04 spec 3.8): the priority-10 WhatsApp handler, YES
disambiguation, the J5 consent flow and the no-LLM-without-consent rule."""

from __future__ import annotations

import pytest

from clefs.core import actions, consent
from clefs.modules.guests import identity
from clefs.modules.guests.journeys import dispatch, replies
from clefs.modules.service import api as service_api

from tests.modules.guests.test_journeys_support import (
    PHONE, at, event, in_house_j5, inbound, make_offer, plans, set_res,
)


@pytest.fixture
def filed(monkeypatch):
    calls = {"request": [], "followup": []}

    def fake_request(cur, hotel_slug, **kw):
        calls["request"].append(kw)
        return {"id": 991, "tracking_token": "t"}

    def fake_followup(cur, hotel_slug, **kw):
        calls["followup"].append(kw)
        return {"id": 992, "action_id": 992, "kind": kw["kind"], "status": "proposed"}

    monkeypatch.setattr(service_api, "create_request", fake_request)
    monkeypatch.setattr(service_api, "create_followup", fake_followup)
    return calls


def _replies(cur, s, rid):
    cur.execute("SELECT * FROM gst_journey_replies WHERE hotel_slug = %s AND reservation_id = %s ORDER BY id", (s, rid))
    return [dict(r) for r in cur.fetchall()]


def _texts(cur, s, kind=None):
    cur.execute("SELECT purpose, payload FROM outbound_messages WHERE hotel_slug = %s AND purpose LIKE 'guests.journey_msg%%' "
                "ORDER BY id", (s,))
    rows = [dict(r) for r in cur.fetchall()]
    return [r for r in rows if kind is None or r["purpose"].endswith(kind)]


def test_j5_sent_on_whatsapp_with_wamid(cur, hotel_uae, clock, outbox):
    rid, h, j5 = in_house_j5(cur, hotel_uae.slug, clock)
    assert j5["channel"] == "whatsapp" and j5["status"] in ("sent", "delivered") and j5["wamid"]


def test_negative_reply_in_house_opens_a_service_request_and_acks_once(cur, hotel_uae, clock, outbox, filed, fake_ai):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, "The AC is not working", context=j5["wamid"])
    assert len(filed["request"]) == 1
    req = filed["request"][0]
    assert req["room"] == "501" and req["source"] == "whatsapp_journey" and req["type"] == "maintenance"
    r = _replies(cur, s, rid)[-1]
    assert r["outcome"] == "service_request" and r["deterministic_hit"] == "not working" and r["llm_used"] is False
    assert len(_texts(cur, s, "ack")) == 1
    assert fake_ai.calls == []  # no transfer consent and the setting is off: no LLM


def test_negative_reply_after_checkout_is_a_followup(cur, hotel_uae, clock, outbox, filed):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    set_res(cur, rid, status="CHECKED_OUT")
    inbound(s, PHONE, "room was dirty", context=j5["wamid"])
    assert filed["request"] == [] and filed["followup"][0]["kind"] == "guest_issue"
    assert _replies(cur, s, rid)[-1]["outcome"] == "followup"


def test_signed_fix_button_counts_as_negative(cur, hotel_uae, clock, outbox, filed):
    from clefs.auth import tokens

    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, button=tokens.sign_ref(f"gstj:{j5['id']}:gst_j5_fix"), button_text="Something to fix")
    assert _replies(cur, s, rid)[-1]["deterministic_hit"] == "button:fix"
    assert len(filed["request"]) == 1


def test_llm_runs_only_with_transfer_consent_and_never_downgrades(cur, hotel_uae, clock, outbox, filed, fake_ai):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    consent.record_consent(cur, s, h.hex(), "transfer_abroad", True, source="test", subject_type="contact",
                           text_version=identity.text_version(cur, s, "transfer_abroad"))
    fake_ai.push(replies.ReplyClass(sentiment="positive", is_request=False, summary_en="Guest says the AC is broken.",
                                    department_hint="general", severity="L3", is_opt_out=False,
                                    is_complaint_about_messaging=False, is_wrong_number=False, language="en"))
    inbound(s, PHONE, "the ac is broken", context=j5["wamid"])
    r = _replies(cur, s, rid)[-1]
    assert r["llm_used"] is True and r["outcome"] == "service_request"  # the lexicon's negative stands
    assert r["summary_en"] == "Guest says the AC is broken."


def test_positive_j5_reply_asks_for_consent_then_yes_and_tick_make_a_direct_point(cur, hotel_uae, clock, outbox,
                                                                                    client):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, "All good, thanks", context=j5["wamid"])
    assert len(_texts(cur, s, "consent_ask")) == 1
    inbound(s, PHONE, "YES")
    assert consent.has_consent(cur, s, h.hex(), "marketing_whatsapp")
    cur.execute("SELECT COUNT(*) AS n FROM gst_contact_points WHERE hotel_slug = %s AND provenance = 'guest_direct'", (s,))
    assert cur.fetchone()["n"] == 0  # no point until the transfer tick
    link = _texts(cur, s, "consent_link")[-1]["payload"]["text"]["body"]
    token = link.rsplit("/g/", 1)[1]
    page = client.get(f"/public/journeys/{token}").json()
    assert page["purpose"] == "consent" and list(page["consent_texts"]) == ["transfer_abroad"]
    r = client.post(f"/public/journeys/{token}/consent", json={"transfer_abroad": True, "language": "en"})
    assert r.status_code == 200, r.text
    cur.execute("SELECT capture_source FROM gst_contact_points WHERE hotel_slug = %s AND provenance = 'guest_direct'", (s,))
    assert cur.fetchone()["capture_source"] == "j5_reply"
    assert client.post(f"/public/journeys/{token}/consent", json={"transfer_abroad": True}).status_code == 410


def test_yes_to_j3_creates_the_offer_accept_task(cur, hotel_uae, clock, outbox):
    from tests.modules.guests.test_journeys_support import (
        add_contact, approve_templates, enable_whatsapp, grant, make_res,
    )

    s = hotel_uae.slug
    enable_whatsapp(cur, s)
    clock.freeze(at(2026, 9, 1, 10, 5))
    rid = make_res(cur, s)
    h = add_contact(cur, s, rid, PHONE, provenance="pms_direct")
    grant(cur, s, h, "marketing_whatsapp", "whatsapp_updates")
    event(cur, s, rid, "created")
    offer = make_offer(cur, s, "j3")
    approve_templates(cur, s, step_list=("j3",))
    actions.set_autonomy(cur, s, "guests.journey_j3_offer", level="auto_within_limits",
                         limits={"max_sends_per_day": 10}, actor=actions.SYSTEM)
    clock.freeze(at(2026, 10, 17, 11))
    dispatch.dispatch_hotel(cur, s)
    j3 = plans(cur, s, rid)[("j3", 1)]
    assert j3["channel"] == "whatsapp", j3
    inbound(s, PHONE, "Yes", context=j3["wamid"])
    cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND type = 'guests.offer_accept'", (s,))
    task = dict(cur.fetchone())
    assert task["status"] == "proposed" and task["params"]["offer_price_minor"] == offer["price_minor"]


def test_wrong_number_suppresses_everything(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, "wrong number", context=j5["wamid"])
    cur.execute("SELECT reason, scope FROM gst_suppressions WHERE hotel_slug = %s AND value_hmac = %s", (s, h))
    assert dict(cur.fetchone()) == {"reason": "wrong_number", "scope": "all"}
    cur.execute("SELECT status FROM gst_contact_points WHERE hotel_slug = %s AND value_hmac = %s", (s, h))
    assert {r["status"] for r in cur.fetchall()} == {"suppressed"}


def test_messaging_complaint_demotes_the_step(cur, hotel_uae, clock, outbox):
    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, "this is spam", context=j5["wamid"])
    assert actions.get_autonomy(cur, s, "guests.journey_j5_daycheck")["level"] == "approve"


@pytest.mark.parametrize("word", ["STOP", "إيقاف"])
def test_stop_is_handled_first_and_attributed_to_the_step(cur, hotel_uae, clock, outbox, word):
    from clefs.modules.guests.journeys import maintenance

    s = hotel_uae.slug
    rid, h, j5 = in_house_j5(cur, s, clock)
    inbound(s, PHONE, word, context=j5["wamid"])
    cur.execute("SELECT reason FROM gst_suppressions WHERE hotel_slug = %s AND value_hmac = %s", (s, h))
    assert cur.fetchone()["reason"] == "stop"
    assert _replies(cur, s, rid) == []  # slot 5 took it; the journey handler never saw it
    assert maintenance.attribute_optouts(cur, s, now=at(2026, 10, 21, 10)) == 1
    cur.execute("SELECT step, basis FROM gst_journey_optouts WHERE hotel_slug = %s", (s,))
    assert dict(cur.fetchone()) == {"step": "j5", "basis": "context"}
