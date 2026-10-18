"""Inquiry intake test support (no tests here): a Dubai hotel wired for every
channel, a frozen clock, and helpers that feed the real WhatsApp / email
dispatch. Test files import the fixtures they use from this module."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import clefs.modules.inquiries.module  # noqa: F401  (registers handlers, jobs, action types)

DXB = ZoneInfo("Asia/Dubai")
# Tuesday 2026-09-29 10:00 Dubai, inside business hours.
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)
HOTEL_PNID = "PNID-HOTEL"
ALIAS = "testdxb"


@pytest.fixture
def now(clock):
    clock.freeze(NOW)
    return clock


@pytest.fixture
def hotel(cur, hotel_uae, now, outbox):
    """hotel_uae with a WhatsApp number and an inbound email alias."""
    from clefs.core.hotels import get_hotel

    cur.execute(
        "UPDATE hotels SET whatsapp_phone_number_id = %s, inbound_email_alias = %s WHERE slug = %s",
        (HOTEL_PNID, ALIAS, hotel_uae.slug),
    )
    return get_hotel(cur, hotel_uae.slug, fresh=True)


@pytest.fixture
def set_settings(cur, hotel):
    def _set(**kw):
        from clefs.core.hotels import set_module_settings

        set_module_settings(cur, hotel.slug, "inquiries", kw, actor=None)

    return _set


def wa_message(text: str | None, *, sender: str = "971501234567", mid: str = "wamid.1", referral: dict | None = None,
               ts: datetime | None = None, name: str = "Sara") -> dict:
    """A Meta webhook payload with one inbound message to the hotel number."""
    m = {"from": sender, "id": mid, "timestamp": str(int((ts or NOW).timestamp())), "type": "text",
         "text": {"body": text or ""}}
    if referral:
        m["referral"] = referral
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "metadata": {"display_phone_number": "97140000000", "phone_number_id": HOTEL_PNID},
            "contacts": [{"wa_id": sender, "profile": {"name": name}}],
            "messages": [m],
        }}]}],
    }


def send_wa(text: str | None, **kw) -> dict:
    from clefs import notify

    return notify.process_whatsapp(wa_message(text, **kw))


def email_payload(*, sender: str = "lead@example.com", to: str | None = None, subject: str = "Rooms for next week",
                  text: str = "Hello, we need 2 rooms.", message_id: str = "mail-1", headers: list | None = None,
                  stripped: str | None = None) -> dict:
    return {
        "MessageID": message_id,
        "FromFull": {"Email": sender, "Name": "Lead Person"},
        "ToFull": [{"Email": to or f"{ALIAS}+inq@in.clefs.world"}],
        "To": to or f"{ALIAS}+inq@in.clefs.world",
        "Subject": subject,
        "TextBody": text,
        "StrippedTextReply": stripped,
        "Headers": headers or [],
        "Attachments": [],
    }


def send_email(**kw) -> dict:
    from clefs import notify

    return notify.process_email(email_payload(**kw))


def inquiries(cur, slug: str) -> list[dict]:
    cur.execute("SELECT * FROM inq_inquiries WHERE hotel_slug = %s ORDER BY id", (slug,))
    return [dict(r) for r in cur.fetchall()]


def thread(cur, inquiry_id: int) -> list[dict]:
    cur.execute("SELECT * FROM inq_messages WHERE inquiry_id = %s ORDER BY id", (inquiry_id,))
    return [dict(r) for r in cur.fetchall()]


def queued(cur, name: str) -> list[dict]:
    cur.execute("SELECT * FROM job_queue WHERE name = %s AND status = 'queued' ORDER BY id", (name,))
    return [dict(r) for r in cur.fetchall()]
