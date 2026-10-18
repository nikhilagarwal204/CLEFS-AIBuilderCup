"""Public tracker (03 spec 1.7) and Service on WhatsApp: confirm yes/no with
the recovery case, the post-checkout path, the WhatsApp opt-in (consent
first, hashed and sealed phone, the utility template now and on each state
change), the dial giving a default gesture once per stay, and the priority
90 fallback handler (room from the sender's own subscription, idempotent on
the message id, STOP)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clefs.core import actions, clock as clock_mod, consent, hotels, privacy
from clefs.modules.service import api, config_api, public_api, recovery, state, whatsapp

PHONE = "+971501234567"


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _done_request(cur, slug, room="101", rtype="towels"):
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) "
                "VALUES (%s, 'Amina', 'A', 'Housekeeping', TRUE, TRUE) ON CONFLICT DO NOTHING", (slug,))
    row = api.create_request(cur, slug, room=room, type=rtype, description=rtype, source="web")
    state.transition(cur, slug, row["id"], "complete", state.SYSTEM)
    return row


def _wa_hotel(cur, slug):
    hotels.update_hotel(cur, slug, {"whatsapp_phone_number_id": "1098765"})


def test_not_resolved_reopens_and_opens_a_case(client, cur, hotel_uae):
    s = hotel_uae.slug
    row = _done_request(cur, s)
    t = row["tracking_token"]
    got = client.get(f"/public/r/{t}").json()
    assert got["state"] == "done" and got["can_confirm"] is True
    assert got["subscribed"] is False and got["whatsapp_available"] is False and "assigned_to" not in got
    r = client.post(f"/public/r/{t}/confirm", json={"resolved": False, "comment": "still no towels"})
    assert r.status_code == 200, r.text
    assert r.json()["state"] in ("received", "assigned") and r.json()["guest_confirmation"] == "no"
    cases = recovery.list_cases(cur, s)
    assert len(cases) == 1 and cases[0]["request_id"] == row["id"] and cases[0]["reason"] == "not_resolved"
    assert client.post(f"/public/r/{t}/confirm", json={"resolved": True}).status_code == 409



def test_guest_is_asked_again_after_a_reopen_and_second_completion(client, cur, hotel_uae, clock):
    """QA GS-7: an answer belongs to one completion. After "No" (reopen,
    reassign) and a second completion the tracker asks again, and a "Yes"
    then records the request as resolved (it was stuck at "no")."""
    s = hotel_uae.slug
    clock.freeze(clock_mod.now())
    row = _done_request(cur, s)
    t = row["tracking_token"]
    clock.advance(minutes=5)
    assert client.post(f"/public/r/{t}/confirm", json={"resolved": False}).status_code == 200
    clock.advance(minutes=20)
    state.transition(cur, s, row["id"], "complete", state.SYSTEM)
    got = client.get(f"/public/r/{t}").json()
    assert got["state"] == "done" and got["can_confirm"] is True and got["guest_confirmation"] is None
    clock.advance(minutes=2)
    r = client.post(f"/public/r/{t}/confirm", json={"resolved": True})
    assert r.status_code == 200, r.text
    assert r.json()["guest_confirmation"] == "yes" and r.json()["can_confirm"] is False
    assert state.load(cur, s, row["id"])["guest_confirmation"] == "yes"
    assert client.post(f"/public/r/{t}/confirm", json={"resolved": False}).status_code == 409
    # unanswered, the stored "no" of the first round stands (the last thing the guest said)
    row2 = _done_request(cur, s, room="102")
    clock.advance(minutes=1)
    client.post(f"/public/r/{row2['tracking_token']}/confirm", json={"resolved": False})
    clock.advance(minutes=10)
    state.transition(cur, s, row2["id"], "complete", state.SYSTEM)
    assert state.load(cur, s, row2["id"])["guest_confirmation"] == "no"
    clock.advance(hours=25)
    assert client.get(f"/public/r/{row2['tracking_token']}").json()["can_confirm"] is False

def test_post_checkout_no_opens_case_without_reopen(client, cur, hotel_uae, monkeypatch):
    s = hotel_uae.slug
    row = _done_request(cur, s)
    monkeypatch.setattr(public_api, "_checked_out", lambda *_: True)
    r = client.post(f"/public/r/{row['tracking_token']}/confirm", json={"resolved": False})
    assert r.status_code == 200 and r.json()["state"] == "done" and r.json()["guest_confirmation"] == "no"
    assert state.load(cur, s, row["id"])["reopen_count"] == 0
    assert recovery.list_cases(cur, s)[0]["reason"] == "post_checkout"


def test_dial_gives_default_gesture_once_per_stay(client, cur, hotel_uae, run_jobs):
    s = hotel_uae.slug
    hotels.set_module_settings(cur, s, "service", {"gesture_default_minor": 5000, "gesture_default_kind": "amenity"}, actor=None)
    actions.set_autonomy(cur, s, recovery.ACTION_TYPE, level="auto_within_limits",
                         limits={"max_amount_minor": 5000, "once_per_stay": True}, actor=actions.SYSTEM)
    first = _done_request(cur, s)
    client.post(f"/public/r/{first['tracking_token']}/confirm", json={"resolved": False})
    run_jobs("core.actions.execute")
    g1 = recovery.gestures_for(cur, s, recovery.list_cases(cur, s)[0]["id"])
    assert g1[0]["status"] == "given" and g1[0]["authorized_by"]["via"] == "dial"
    # the reopened first request is open again, so a second towels request
    # would merge into it: the second complaint is about something else
    second = _done_request(cur, s, rtype="water")
    assert second["id"] != first["id"]
    client.post(f"/public/r/{second['tracking_token']}/confirm", json={"resolved": False})
    run_jobs("core.actions.execute")
    case2 = next(c for c in recovery.list_cases(cur, s) if c["request_id"] == second["id"])
    g2 = recovery.gestures_for(cur, s, case2["id"])
    assert g2[0]["status"] == "pending_approval"  # the second one this stay waits for a person
    a = actions.get_action(cur, s, g2[0]["action_id"])
    assert a["status"] == "proposed"


def test_whatsapp_optin_consent_and_updates(client, cur, hotel_uae, outbox, clock):
    s = hotel_uae.slug
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) "
                "VALUES (%s, 'Amina', 'A', 'Housekeeping', TRUE, TRUE)", (s,))
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    t = row["tracking_token"]
    assert client.post(f"/public/r/{t}/whatsapp", json={"phone": PHONE, "consent": True}).json()["code"] == "whatsapp_unavailable"
    _wa_hotel(cur, s)
    assert client.post(f"/public/r/{t}/whatsapp", json={"phone": PHONE, "consent": False}).json()["code"] == "consent_required"
    r = client.post(f"/public/r/{t}/whatsapp", json={"phone": "050 123 4567", "consent": True, "language": "ar"})
    assert r.status_code == 200, r.text
    assert r.json()["subscribed"] is True and r.json()["whatsapp"]["subscribed"] is True
    ref = privacy.contact_ref(s, "phone", PHONE)
    assert consent.has_consent(cur, s, ref, "whatsapp_updates")
    cur.execute("SELECT phone_enc, last_state_sent FROM svc_tracker_subscriptions WHERE request_id = %s", (row["id"],))
    sub = cur.fetchone()
    assert privacy.unseal(bytes(sub["phone_enc"])) == PHONE and PHONE.encode() not in bytes(sub["phone_enc"])
    first = [m for m in outbox.captured("whatsapp") if "request_status_v1" in str(m)]
    assert len(first) == 1
    assert state.load(cur, s, row["id"])["state"] == "assigned"
    state.transition(cur, s, row["id"], "ack", state.SYSTEM)
    state.transition(cur, s, row["id"], "start", state.SYSTEM)
    clock.freeze(clock_mod.now() + timedelta(seconds=61))
    out = whatsapp.push_tracker_updates(cur, s)
    assert out["sent"] == 1
    assert whatsapp.push_tracker_updates(cur, s)["sent"] == 0  # nothing new
    consent.withdraw(cur, s, ref, "whatsapp_updates", source="test")
    state.transition(cur, s, row["id"], "complete", state.SYSTEM)
    clock.freeze(clock_mod.now() + timedelta(seconds=61))
    assert whatsapp.push_tracker_updates(cur, s) == {"sent": 0, "skipped": 1}


def _inbound(cur, slug, text, wamid):
    from clefs.notify.inbound import InboundCtx

    return InboundCtx(cur=cur, inbound_id=1, provider_id=wamid, kind="text", audience="guest", sender=PHONE,
                      recipient_id="1098765", hotel_slug=slug, text=text,
                      sender_hmac=privacy.contact_hmac(slug, "phone", PHONE, cur=cur))


def test_fallback_files_a_request_with_the_subscribed_room(client, cur, hotel_uae, outbox):
    s = hotel_uae.slug
    _wa_hotel(cur, s)
    row = api.create_request(cur, s, room="502", type="towels", description="towels", source="web")
    client.post(f"/public/r/{row['tracking_token']}/whatsapp", json={"phone": PHONE, "consent": True})
    out = whatsapp.fallback(_inbound(cur, s, "Could we get two extra pillows please", "wamid.A1"))
    assert out["handled"] is True
    req = state.load(cur, s, out["request_id"])
    assert (req["room"], req["source"]) == ("502", "whatsapp")
    again = whatsapp.fallback(_inbound(cur, s, "Could we get two extra pillows please", "wamid.A1"))
    assert again["request_id"] == out["request_id"]
    assert whatsapp.fallback(_inbound(cur, s, "   ", "wamid.A2")) == {"handled": False}
    stop = whatsapp.fallback(_inbound(cur, s, "STOP", "wamid.A3"))
    assert stop["stopped"] is True
    assert not consent.has_consent(cur, s, privacy.contact_ref(s, "phone", PHONE), "whatsapp_updates")


def test_fallback_over_the_room_limit_still_answers(client, cur, hotel_uae, outbox):
    s = hotel_uae.slug
    _wa_hotel(cur, s)
    row = api.create_request(cur, s, room="503", type="towels", description="towels", source="web")
    client.post(f"/public/r/{row['tracking_token']}/whatsapp", json={"phone": PHONE, "consent": True})
    outs = [whatsapp.fallback(_inbound(cur, s, f"message number {i} about something new {i}", f"wamid.R{i}")) for i in range(6)]
    assert outs[-1] == {"handled": True, "rate_limited": True}
    assert all(o["handled"] for o in outs)
