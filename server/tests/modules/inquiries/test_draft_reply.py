"""POST /hotels/{slug}/inquiries/{id}/draft-reply (04 spec 4.11): an AI
draft that becomes an `inquiries.respond` action the dial governs. Consent
gate (ai_on_unconsented_inbound), price lint on the draft and on an
approver's edit, redacted model input, and the approved draft being sent."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_wa,
    set_settings,
    thread,
)

BASE = "/hotels/test-dubai/inquiries"
CLEAN = {"body": "Thank you for your message. How many rooms do you need, and for which dates? "
                 "We will send you a quote.", "language": "en"}
PRICED = {"body": "Our rate is AED 450 per night for those dates.", "language": "en"}


def _respond_actions(cur, inquiry_id):
    cur.execute("SELECT id, status, params FROM actions WHERE type = 'inquiries.respond' "
                "AND (params->>'inquiry_id')::bigint = %s ORDER BY id", (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def _wa_inquiry(cur, hotel, text="Hi, do you have rooms, call me on +971 50 765 4321"):
    send_wa(text)
    return inquiries(cur, hotel.slug)[0]


def test_unconsented_inquiry_never_reaches_the_model(cur, hotel, now, fake_ai, client_as):
    row = _wa_inquiry(cur, hotel)
    r = client_as("front_desk", hotel.slug).post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 409 and r.json()["code"] == "ai_not_allowed", r.text
    assert fake_ai.calls == [] and _respond_actions(cur, row["id"]) == []


def test_draft_becomes_a_proposed_respond_action(cur, hotel, now, fake_ai, client_as, set_settings):
    set_settings(ai_on_unconsented_inbound=True)
    row = _wa_inquiry(cur, hotel)
    fake_ai.push(CLEAN)
    r = client_as("front_desk", hotel.slug).post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["body"] == CLEAN["body"] and out["channel"] == "whatsapp" and out["language"] == "en"
    assert out["action"]["type"] == "inquiries.respond" and out["action"]["status"] == "proposed"
    sent = str(fake_ai.calls[-1].input)
    assert "[phone]" in sent and "765 4321" not in sent and fake_ai.calls[-1].store is False
    # Nothing leaves until a person approves.
    assert all(m["author_type"] != "ai" for m in thread(cur, row["id"]))
    assert inquiries(cur, hotel.slug)[0]["first_response_at"] is None


def test_a_price_is_retried_then_refused(cur, hotel, now, fake_ai, client_as, set_settings):
    set_settings(ai_on_unconsented_inbound=True)
    row = _wa_inquiry(cur, hotel)
    c = client_as("front_desk", hotel.slug)
    fake_ai.push(PRICED, CLEAN)  # the retry is clean: accepted
    r = c.post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 201 and "AED" not in r.json()["body"] and len(fake_ai.calls) == 2
    assert "STATED A PRICE" in str(fake_ai.calls[1].input)
    fake_ai.push(PRICED, PRICED)  # still priced after the retry: refused, no new action
    r = c.post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 422 and r.json()["code"] == "draft_contains_price"
    assert len(_respond_actions(cur, row["id"])) == 1


def test_approved_draft_is_sent_as_an_ai_reply(cur, hotel, now, fake_ai, client_as, set_settings, run_jobs):
    set_settings(ai_on_unconsented_inbound=True)
    row = _wa_inquiry(cur, hotel)
    c = client_as("front_desk", hotel.slug)
    fake_ai.push(CLEAN)
    act = c.post(f"{BASE}/{row['id']}/draft-reply", json={}).json()["action"]
    # An approver's edit that adds a price is refused (validate_params).
    r = c.post(f"/hotels/{hotel.slug}/actions/{act['id']}/decide",
               json={"decision": "adjust", "params_patch": {"body": "It is 500 AED a night."}})
    assert r.status_code == 422, r.text
    r = c.post(f"/hotels/{hotel.slug}/actions/{act['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 200, r.text
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    last = thread(cur, row["id"])[-1]
    assert (last["author_type"], last["direction"], last["body"]) == ("ai", "out", CLEAN["body"])
    after = inquiries(cur, hotel.slug)[0]
    assert after["stage"] == "replied" and after["first_response_at"] is not None
    assert _respond_actions(cur, row["id"])[0]["status"] == "done"


def test_staff_reply_or_a_newer_draft_closes_the_old_one(cur, hotel, now, fake_ai, client_as, set_settings):
    set_settings(ai_on_unconsented_inbound=True)
    row = _wa_inquiry(cur, hotel)
    c = client_as("front_desk", hotel.slug)
    fake_ai.push(CLEAN, CLEAN)
    c.post(f"{BASE}/{row['id']}/draft-reply", json={})
    c.post(f"{BASE}/{row['id']}/draft-reply", json={"channel": "whatsapp"})
    first, second = _respond_actions(cur, row["id"])
    assert (first["status"], second["status"]) == ("cancelled", "proposed")
    assert c.post(f"{BASE}/{row['id']}/messages", json={"body": "Hello!", "channel": "whatsapp"}).status_code == 201
    assert _respond_actions(cur, row["id"])[1]["status"] == "cancelled"


def test_dial_off_and_closed_inquiry_spend_no_model_call(cur, hotel, now, fake_ai, client_as, set_settings):
    from clefs.core.actions import SYSTEM, set_autonomy

    set_settings(ai_on_unconsented_inbound=True)
    row = _wa_inquiry(cur, hotel)
    c = client_as("front_desk", hotel.slug)
    set_autonomy(cur, hotel.slug, "inquiries.respond", level="off", actor=SYSTEM)
    r = c.post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 409 and r.json()["code"] == "dial_off"
    cur.execute("UPDATE inq_inquiries SET stage = 'spam' WHERE id = %s", (row["id"],))
    r = c.post(f"{BASE}/{row['id']}/draft-reply", json={})
    assert r.status_code == 409 and r.json()["code"] == "illegal_stage"
    assert fake_ai.calls == []
    assert client_as("staff", hotel.slug).post(f"{BASE}/{row['id']}/draft-reply", json={}).status_code == 403


def test_price_lint_catches_amounts_not_dates():
    from clefs.modules.inquiries.replies import price_findings

    for bad in ("AED 450", "450 AED", "1,200 dirhams", "$120", "10% off", "400 per night", "٤٥٠ درهم", "15 percent"):
        assert price_findings(bad), bad
    for ok in ("2 rooms on 12 Oct", "3 adults, 2 nights", "12/10 to 15/10", "2026-10-12", "room 405"):
        assert price_findings(ok) == [], ok
