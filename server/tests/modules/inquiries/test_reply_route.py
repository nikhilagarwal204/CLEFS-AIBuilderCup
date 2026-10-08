"""POST /hotels/{slug}/inquiries/{id}/messages: a person's reply from the
console thread (integrator, Wave 2; the ThreadPanel calls it)."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_email,
    send_wa,
    thread,
)

BASE = "/hotels/test-dubai/inquiries"


def test_whatsapp_reply_inside_the_window_is_the_first_response(cur, hotel, now, outbox, client_as):
    send_wa("Hi, do you have 2 rooms 12-14 Oct?")
    row = inquiries(cur, hotel.slug)[0]
    c = client_as("front_desk", hotel.slug)
    r = c.post(f"{BASE}/{row['id']}/messages", json={"body": "Yes, we do. Shall I send a quote?", "channel": "whatsapp"})
    assert r.status_code == 201, r.text
    assert r.json()["author_type"] == "staff" and r.json()["direction"] == "out"
    after = inquiries(cur, hotel.slug)[0]
    assert after["stage"] == "replied" and after["first_response_at"] is not None
    assert [m["author_type"] for m in thread(cur, row["id"])][-1] == "staff"


def test_whatsapp_reply_after_the_window_is_refused(cur, hotel, now, client_as):
    send_wa("Hello")
    row = inquiries(cur, hotel.slug)[0]
    now.advance(hours=25)
    r = client_as("front_desk", hotel.slug).post(f"{BASE}/{row['id']}/messages", json={"body": "Hi", "channel": "whatsapp"})
    assert r.status_code == 409 and r.json()["code"] == "wa_window_closed"


def test_email_reply_needs_an_address_and_revenue_cannot_reply(cur, hotel, now, client_as):
    send_wa("Hello")
    row = inquiries(cur, hotel.slug)[0]
    c = client_as("front_desk", hotel.slug)
    r = c.post(f"{BASE}/{row['id']}/messages", json={"body": "Hi", "channel": "email"})
    assert r.status_code == 409 and r.json()["code"] == "no_email"
    send_email()
    mail = [i for i in inquiries(cur, hotel.slug) if i["source"] == "email"][0]
    assert c.post(f"{BASE}/{mail['id']}/messages", json={"body": "Dear guest", "channel": "email"}).status_code == 201
    r = client_as("staff", hotel.slug).post(f"{BASE}/{mail['id']}/messages", json={"body": "x", "channel": "email"})
    assert r.status_code == 403
