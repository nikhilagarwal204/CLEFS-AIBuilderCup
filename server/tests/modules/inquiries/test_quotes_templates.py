"""Template storage, owner approval and the category gate (04 spec 4.6
step 7, 4.10): +1 numbers get email, 17:59 goes and 18:00 moves to 09:00,
the follow-up is always windowed, and 131049 retries once then emails."""

from __future__ import annotations

from datetime import datetime

import pytest

from clefs.core.actions import SYSTEM
from clefs.modules.inquiries import templates
from tests.modules.inquiries.test_quotes_support import DXB, hotel, now  # noqa: F401

UAE = "+971501234567"


@pytest.fixture
def meta(monkeypatch):
    """brief.api.wa_template_info stand-in: {name: category}."""
    from clefs.modules.brief import api as brief_api

    cats: dict[str, str | None] = {templates.QUOTE_READY: "MARKETING", templates.FOLLOWUP: "UTILITY"}

    def info(cur, hotel_slug, *, name, language):
        c = cats.get(name)
        return None if c is None else {"status": "APPROVED", "category": c, "quality": "GREEN"}

    monkeypatch.setattr(brief_api, "wa_template_info", info)
    return cats


def approve_all(cur, slug):
    for t in templates.list_templates(cur, slug):
        templates.approve(cur, slug, t["id"], actor=SYSTEM)


def at(h, m):
    return datetime(2026, 9, 29, h, m, tzinfo=DXB)


def plan(cur, slug, name=templates.QUOTE_READY, phone=UAE, email="g@example.com", when=None, deadline=None):
    return templates.plan_send(cur, slug, name=name, language="en", phone=phone, email=email, at=when,
                               deadline=deadline)


def test_seed_and_bodies(cur, hotel):
    rows = templates.list_templates(cur, hotel.slug)
    assert {(r["name"], r["language"]) for r in rows} == {
        (templates.QUOTE_READY, "en"), (templates.QUOTE_READY, "ar"),
        (templates.FOLLOWUP, "en"), (templates.FOLLOWUP, "ar")}
    assert templates.seed(cur, hotel.slug) == 0  # idempotent
    text = templates.render(templates.QUOTE_READY, "en", ["ABC-v1", "20 Oct", "AED 1,008.68", "22 Oct"])
    assert text == ("Your quote ABC-v1 for 20 Oct is ready: total AED 1,008.68, valid until 22 Oct. "
                    "Reply here with any questions.")


def test_owner_approval_without_whatsapp_business(cur, hotel, monkeypatch):
    from clefs.core.oauth import NotConnected
    from clefs.modules.brief import api as brief_api

    def not_connected(*a, **kw):
        raise NotConnected("no WABA", provider="whatsapp")

    monkeypatch.setattr(brief_api, "submit_template", not_connected)
    row = templates.list_templates(cur, hotel.slug)[0]
    got = templates.approve(cur, hotel.slug, row["id"], actor=SYSTEM)
    assert got["approved_at"] is not None and got["meta_status"] == "not_connected"


def test_unapproved_template_means_email(cur, hotel, meta):
    p = plan(cur, hotel.slug)
    assert (p.channel, p.reason) == ("email", "template_missing")
    p = plan(cur, hotel.slug, email=None)
    assert (p.channel, p.reason) == (None, "template_missing")


def test_marketing_gate_nanp_and_window(cur, hotel, meta):
    approve_all(cur, hotel.slug)
    p = plan(cur, hotel.slug, phone="+14155550100", when=at(10, 0))
    assert (p.channel, p.reason) == ("email", "nanp_no_marketing")
    p = plan(cur, hotel.slug, when=at(17, 59))
    assert (p.channel, p.send_at, p.category) == ("whatsapp", None, "MARKETING")
    p = plan(cur, hotel.slug, when=at(18, 0))
    assert p.channel == "whatsapp" and p.send_at == datetime(2026, 9, 30, 9, 0, tzinfo=DXB)
    p = plan(cur, hotel.slug, when=at(18, 0), deadline=at(23, 0))
    assert (p.channel, p.reason) == ("email", "window_after_deadline")


def test_unknown_category_counts_as_marketing(cur, hotel, meta):
    approve_all(cur, hotel.slug)
    meta[templates.QUOTE_READY] = None  # Brief knows nothing: not even APPROVED
    assert plan(cur, hotel.slug).channel == "email"


def test_utility_quote_goes_any_time_but_followup_is_windowed(cur, hotel, meta):
    approve_all(cur, hotel.slug)
    meta[templates.QUOTE_READY] = "UTILITY"
    p = plan(cur, hotel.slug, when=at(20, 0))
    assert (p.channel, p.send_at) == ("whatsapp", None)
    p = plan(cur, hotel.slug, name=templates.FOLLOWUP, when=at(20, 0))
    assert p.send_at == datetime(2026, 9, 30, 9, 0, tzinfo=DXB)
    # A UTILITY follow-up to a +1 number is not marketing: WhatsApp stays.
    assert plan(cur, hotel.slug, name=templates.FOLLOWUP, phone="+14155550100", when=at(10, 0)).channel == "whatsapp"


def test_131049_retries_once_then_email(cur, hotel, meta, outbox):
    from clefs import notify

    r = notify.send_whatsapp_template(UAE, templates.QUOTE_READY, "en", ["A", "B", "C", "D"], hotel_slug=hotel.slug,
                                      purpose="inquiries.quote_ready", sender="hotel", idempotency_key="t131049",
                                      cur=cur)
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (r.outbox_id,))
    row = dict(cur.fetchone())
    # First failure: the outbox's own 24 h retry is the one retry; nothing else happens.
    templates.on_template_status(cur, row, "failed", "131049")
    assert not outbox.captured("email")
    # The retry failed too: its next copy is cancelled and the message goes by email.
    cur.execute("INSERT INTO outbound_messages (channel, kind, recipient, payload, purpose, hotel_slug, category, "
                "sender, idempotency_key, status) VALUES ('whatsapp', 'template', %s, '{}', 'inquiries.quote_ready', "
                "%s, 'marketing', 'hotel', 'next-copy', 'queued') RETURNING id", (UAE, hotel.slug))
    nxt = cur.fetchone()["id"]
    retry = {**row, "meta": {"retry": 1, "retried_by": nxt}, "action_id": None}
    templates.on_template_status(cur, retry, "failed", "131049")
    cur.execute("SELECT status FROM outbound_messages WHERE id = %s", (nxt,))
    assert cur.fetchone()["status"] == "cancelled"


def test_quote_goes_by_template_then_131049_falls_back_to_email(cur, hotel, meta, outbox, run_jobs, monkeypatch):
    from clefs.core import actions
    from clefs.modules.demand import api as demand_api
    from clefs.modules.inquiries import quotes
    from tests.modules.inquiries.test_quotes_support import FakeDemand, make_inquiry

    monkeypatch.setattr(demand_api, "price_guardrails", FakeDemand().guardrails)
    approve_all(cur, hotel.slug)
    actions.set_autonomy(cur, hotel.slug, "inquiries.send_quote", level="auto_within_limits", actor=SYSTEM)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    quotes.request_send(cur, hotel, q, actor=SYSTEM, via="system")
    run_jobs(["core.actions.execute", "core.notify.deliver"])
    (wa,) = outbox.captured("whatsapp")
    assert wa["payload"]["template"]["name"] == templates.QUOTE_READY
    assert not outbox.captured("email")
    cur.execute("SELECT * FROM outbound_messages WHERE purpose = 'inquiries.quote_ready' AND channel = 'whatsapp'")
    row = dict(cur.fetchone())
    templates.on_template_status(cur, {**row, "meta": {"retry": 1}}, "failed", "131049")
    run_jobs(["core.notify.deliver"])
    (mail,) = outbox.captured("email")
    assert "is ready: total AED 1,008.68" in str(mail)
