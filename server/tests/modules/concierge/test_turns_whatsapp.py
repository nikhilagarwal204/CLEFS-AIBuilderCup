"""The concierge WhatsApp chat handler (GUEST_PRIORITY 30): known guests only,
consent first, the reply written by a worker task with the same tools."""

from __future__ import annotations

import itertools

import pytest

from clefs.auth import tokens
from clefs.core import privacy
from clefs.modules.concierge import whatsapp
from clefs.notify.inbound import InboundCtx
from tests.modules.concierge.test_tools import fake_service  # noqa: F401 (fixture)
from tests.modules.concierge.test_turns import fn_call, said

PHONE = "+971501234567"
_ids = itertools.count(1)


@pytest.fixture
def known_guest(monkeypatch, cur, hotel_uae, clock):
    """A guest on the hotel's own number with the 24 h window open (the
    webhook opens it on every inbound message)."""
    from clefs.modules.guests import api as guests_api
    from clefs.notify import outbox as ob

    clock.freeze(clock.now())
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = 'PNID-HOTEL' WHERE slug = %s", (hotel_uae.slug,))
    ob.open_window(cur, "PNID-HOTEL", PHONE, at=clock.now(), hotel_slug=hotel_uae.slug)
    known = {"yes": True}
    monkeypatch.setattr(guests_api, "is_known_guest_sender", lambda cur, slug, **kw: known["yes"])
    return known


def inbound(cur, slug, text=None, *, kind="text", button_id=None):
    n = next(_ids)
    hmac = privacy.contact_hmac(slug, "phone", PHONE, cur=cur)
    payload = {"type": kind, "text": {"body": text}} if kind == "text" else {"type": "interactive"}
    cur.execute(
        "INSERT INTO inbound_messages (channel, provider_id, kind, hotel_slug, sender, audience, payload, sender_hmac) "
        "VALUES ('whatsapp', %s, %s, %s, %s, 'guest', %s, %s) RETURNING id",
        (f"wamid.cc{n}", kind, slug, PHONE, __import__("json").dumps(payload), hmac),
    )
    iid = cur.fetchone()["id"]
    ref = tokens.verify_ref(button_id) if button_id else None
    return InboundCtx(cur=cur, inbound_id=iid, provider_id=f"wamid.cc{n}", kind=kind, audience="guest", sender=PHONE,
                      recipient_id=None, hotel_slug=slug, text=text, button_id=button_id, button_ref=ref, sender_hmac=hmac)


def drain(run_jobs):
    """The reply task, then the outbox delivery it queued (the worker sends
    through core.notify.deliver, 6 s apart per recipient)."""
    from clefs.core import clock

    run_jobs(only=[whatsapp.REPLY_TASK])
    clock.advance(__import__("datetime").timedelta(seconds=7))
    run_jobs(only=["core.notify.deliver"])


def consent_buttons(outbox):
    msg = [m for m in outbox.captured("whatsapp") if m["payload"].get("type") == "interactive"][-1]
    return [b["reply"]["id"] for b in msg["payload"]["interactive"]["action"]["buttons"]]


def test_unknown_senders_pass_through(cur, hotel_uae, known_guest):
    known_guest["yes"] = False
    assert whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "Hi, do you have rooms?")) == {"handled": False}


def test_first_message_asks_consent_then_answers_after_yes(cur, hotel_uae, known_guest, fake_service, fake_ai,
                                                           outbox, run_jobs):
    out = whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "What time is checkout?"))
    assert out["handled"] is True and out["awaiting_consent"] is True
    whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "And is there parking?"))
    sent = outbox.captured("whatsapp")
    assert len(sent) == 1  # asked once
    yes_id, _no_id = consent_buttons(outbox)
    fake_ai.push(said("Checkout is at 12:00 and parking is free."))
    done = whatsapp.whatsapp_consent_button(inbound(cur, hotel_uae.slug, "Yes", kind="button_reply", button_id=yes_id))
    assert done == {"handled": True, "consent": True}
    drain(run_jobs)
    user_msg = fake_ai.calls[0].input[-1]["content"]
    assert "checkout" in user_msg and "parking" in user_msg
    replies = [m for m in outbox.captured("whatsapp") if "Checkout is at 12:00" in str(m)]
    assert replies
    cur.execute("SELECT surface, channel, sender_ref, consents FROM cc_sessions WHERE hotel_slug = %s", (hotel_uae.slug,))
    row = cur.fetchone()
    assert row["surface"] == "whatsapp" and row["channel"] == "whatsapp" and row["consents"]["ai_processing"] is True
    assert PHONE not in str(row)


def test_consented_guest_gets_a_queued_reply_with_tools(cur, hotel_uae, known_guest, fake_service, fake_ai,
                                                         outbox, run_jobs):
    ctx = inbound(cur, hotel_uae.slug, "hello")
    whatsapp.whatsapp_chat(ctx)
    yes_id, _ = consent_buttons(outbox)
    fake_ai.push(said("Hello! How can I help?"))
    whatsapp.whatsapp_consent_button(inbound(cur, hotel_uae.slug, "Yes", kind="button_reply", button_id=yes_id))
    drain(run_jobs)
    fake_ai.push(fn_call("notify_staff", {"request_type": "towels", "description_en": "Towels", "room": "205"}),
                 said("Towels are on their way to room 205."))
    out = whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "Towels to room 205 please"))
    assert out["handled"] is True and "awaiting_consent" not in out
    drain(run_jobs)
    req = fake_service.requests[0]
    assert req["room"] == "205" and req["source"] == "whatsapp" and "Room not verified" in req["description"]
    assert any("Towels are on their way" in str(m) for m in outbox.captured("whatsapp"))


def test_a_japanese_message_gets_the_question_and_the_wording_in_japanese(cur, hotel_uae, known_guest, outbox):
    out = whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "チェックアウトは何時ですか？"))
    assert out["awaiting_consent"] is True
    msg = [m for m in outbox.captured("whatsapp") if m["payload"].get("type") == "interactive"][-1]["payload"]["interactive"]
    assert msg["body"]["text"].startswith("こんにちは。Test Hotel DubaiのAIアシスタント、")
    assert "Google CloudのGemini AIモデル" in msg["body"]["text"]
    assert [b["reply"]["title"] for b in msg["action"]["buttons"]] == list(whatsapp.BUTTONS["ja"])
    yes_id = msg["action"]["buttons"][0]["reply"]["id"]
    whatsapp.whatsapp_consent_button(inbound(cur, hotel_uae.slug, "はい", kind="button_reply", button_id=yes_id))
    cur.execute("SELECT c.purpose, c.text_version, t.language FROM consents c JOIN consent_texts t ON t.id = c.text_id "
                "WHERE c.hotel_slug = %s", (hotel_uae.slug,))
    rows = cur.fetchall()
    assert {r["purpose"] for r in rows} == {"ai_processing", "transfer_abroad"}
    assert {(r["text_version"], r["language"]) for r in rows} == {("concierge-2", "ja")}


def test_no_hands_the_guest_to_a_person(cur, hotel_uae, known_guest, fake_service, outbox):
    whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "Can I extend my stay?"))
    _yes, no_id = consent_buttons(outbox)
    done = whatsapp.whatsapp_consent_button(inbound(cur, hotel_uae.slug, "No", kind="button_reply", button_id=no_id))
    assert done == {"handled": True, "consent": False}
    esc = fake_service.escalations[0]
    assert esc["channel"] == "whatsapp" and "extend my stay" in esc["excerpt"]
    # Later messages go to Service's fallback (a person), not the AI.
    assert whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "Hello?")) == {"handled": False}


def test_not_live_concierge_leaves_whatsapp_to_staff(cur, hotel_uae, known_guest):
    """The readiness gate (live.py): a hotel not switched live never has the
    AI answer; the message goes on to Service's staff fallback."""
    from tests.modules.concierge.conftest import switch_live

    switch_live(cur, hotel_uae.slug, False)
    assert whatsapp.whatsapp_chat(inbound(cur, hotel_uae.slug, "What time is checkout?")) == {"handled": False}
