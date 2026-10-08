"""Email intake (04 spec 4.3): +inq addresses, thread joining by ref and by
In-Reply-To, quoted history stripped, OTA and auto mail ignored."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    ALIAS,
    hotel,
    inquiries,
    now,
    send_email,
    thread,
)


def test_email_creates_an_inquiry_and_strips_quotes(cur, hotel):
    out = send_email(text="We need 2 rooms for 3 nights.\n\nOn Mon, Sep 28, 2026 Hotel wrote:\n> old stuff\n> more",
                     message_id="m-1")
    assert out["status"] == "handled", out
    r = inquiries(cur, hotel.slug)[0]
    assert r["source"] == "email" and r["consent_basis"] == "pre_contract_notice"
    body = thread(cur, r["id"])[0]["body"]
    assert "old stuff" not in body and body.startswith("Rooms for next week")


def test_plus_ref_joins_the_thread_from_another_address(cur, hotel, now):
    send_email(sender="a@example.com", message_id="m-1")
    ref = inquiries(cur, hotel.slug)[0]["ref"]
    now.advance(minutes=5)
    send_email(sender="colleague@example.com", to=f"{ALIAS}+inq-{ref}@in.clefs.world", message_id="m-2",
               text="Adding my colleague's dates")
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1 and len(thread(cur, rows[0]["id"])) == 2


def test_in_reply_to_joins_the_thread(cur, hotel, now):
    send_email(sender="a@example.com", message_id="m-1")
    now.advance(minutes=5)
    send_email(sender="b@example.com", message_id="m-2", headers=[{"Name": "In-Reply-To", "Value": "<m-1>"}])
    assert len(inquiries(cur, hotel.slug)) == 1


def test_ota_and_auto_submitted_mail_is_ignored(cur, hotel):
    assert send_email(sender="x7k2@guest.booking.com", message_id="m-alias")["status"] == "handled"
    assert send_email(sender="noreply@booking.com", message_id="m-ota")["status"] == "unhandled"
    send_email(sender="reservations@agoda.com", message_id="m-agoda")
    send_email(sender="bot@example.com", message_id="m-auto", headers=[{"Name": "Auto-Submitted", "Value": "auto-replied"}])
    sources = [r["contact_email_hmac"] is not None for r in inquiries(cur, hotel.slug)]
    # booking.com's guest alias domain is a guest writing through Booking.com (not an OTA notification),
    # agoda.com and auto-submitted mail are dropped.
    assert len(sources) == 1


def test_duplicate_message_id_is_dropped(cur, hotel):
    send_email(message_id="m-dup")
    assert send_email(message_id="m-dup")["status"] == "duplicate"
    assert len(inquiries(cur, hotel.slug)) == 1
