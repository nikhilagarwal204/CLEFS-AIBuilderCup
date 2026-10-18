"""WhatsApp intake (04 spec 4.3, 4.12): priority-20 handler, CTWA referral,
append / reopen, the ack and the SLA timer."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    HOTEL_PNID,
    NOW,
    hotel,
    inquiries,
    now,
    queued,
    send_wa,
    set_settings,
    thread,
)


def test_new_sender_becomes_an_inquiry(cur, hotel):
    out = send_wa("Hi, do you have 2 rooms 12-14 Oct?")
    assert out.get("handled") == 1, out
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1
    r = rows[0]
    assert r["source"] == "whatsapp" and r["stage"] == "new"
    assert r["consent_basis"] == "pre_contract_notice"
    assert r["contact_phone_hmac"] is not None and r["contact_phone_enc"] is not None
    assert r["channel_ref"] == HOTEL_PNID
    assert r["currency"] == "AED"
    assert r["wa_window_expires_at"] is not None
    # FIT/unknown: 15 business minutes from 10:00.
    assert r["first_response_due_at"].astimezone(NOW.tzinfo).strftime("%H:%M") == "10:15"
    assert queued(cur, "inquiries.sla_check")
    assert queued(cur, "inquiries.extract")
    msgs = thread(cur, r["id"])
    assert msgs[0]["direction"] == "in" and msgs[0]["provider_message_id"] == "wamid.1"


def test_ctwa_referral_is_captured(cur, hotel):
    send_wa("Interested in the offer", referral={
        "source_url": "https://fb.me/ad", "source_id": "ad-77", "source_type": "ad", "headline": "Autumn deal",
        "body": "Stay 3 pay 2", "media_type": "image", "ctwa_clid": "ARAkLk-clid",
    })
    r = inquiries(cur, hotel.slug)[0]
    assert r["ctwa_clid"] == "ARAkLk-clid"
    assert r["ad_referral"]["source_id"] == "ad-77" and r["ad_referral"]["headline"] == "Autumn deal"


def test_duplicate_wamid_adds_nothing(cur, hotel):
    send_wa("Hello", mid="wamid.dup")
    send_wa("Hello", mid="wamid.dup")
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1 and len(thread(cur, rows[0]["id"])) == 1


def test_second_message_appends_to_the_open_inquiry(cur, hotel, now):
    send_wa("Hello", mid="wamid.a")
    now.advance(minutes=3)
    send_wa("For 3 adults please", mid="wamid.b")
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1
    assert [m["provider_message_id"] for m in thread(cur, rows[0]["id"]) if m["direction"] == "in"] == ["wamid.a", "wamid.b"]


def test_ack_is_sent_free_form_inside_the_window(cur, hotel, outbox, run_jobs):
    send_wa("Hello there")
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    r = inquiries(cur, hotel.slug)[0]
    sent = outbox.captured("whatsapp")
    assert sent, "the ack goes out on WhatsApp"
    assert "Thank you for contacting Test Hotel Dubai" in str(sent[-1])
    assert r["notice_sent_at"] is not None
    # The ack is not a first response: the SLA clock keeps running.
    assert r["first_response_at"] is None and r["stage"] == "new"
