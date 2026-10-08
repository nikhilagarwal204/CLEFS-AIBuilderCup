"""
Notifications (01 spec E6, 04 spec F4): capture mode, idempotency, the
WhatsApp payload limits, 6 s per-recipient pacing, Meta error codes
(131056 / 130429 / 131047 / 131049), the 24 h window, the marketing send
window, the fallback chain, email attachments, SMS rules, documents, and
the magic-link sender.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from clefs import files, jobs, notify
from clefs.auth import tokens
from clefs.notify import outbox as ob
from clefs.notify import validate
from clefs.notify.validate import PayloadError

UTC = timezone.utc
DUBAI = ZoneInfo("Asia/Dubai")
TO = "+971501234567"
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)  # 12:00 in Dubai: inside the marketing window


def _row(cur, oid):
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (oid,))
    return dict(cur.fetchone())


def _tpl(to=TO, key=None, **kw):
    kw.setdefault("hotel_slug", None)
    return notify.send_whatsapp_template(to, "owner_brief_v1", "en", ["3", "AED 4,120"], purpose="brief.daily", idempotency_key=key, **kw)


def test_template_send_is_captured_and_idempotent(cur, outbox, clock):
    clock.freeze(T0)
    r = _tpl(key="brief:2026-10-01:owner", buttons=[{"type": "quick_reply", "payload": tokens.sign_ref("brief:2026-10-01:1:approve")}])
    assert r.ok and r.status == "sent" and r.channel == "whatsapp" and not r.deduped
    [sent] = notify.captured("whatsapp")
    p = sent["payload"]
    assert p["to"] == "971501234567" and p["type"] == "template" and p["template"]["name"] == "owner_brief_v1"
    assert [c["type"] for c in p["template"]["components"]] == ["body", "button"]
    row = _row(cur, r.outbox_id)
    assert row["provider"] == "capture" and row["provider_id"] == sent["provider_id"] and row["sent_at"] == T0
    again = _tpl(key="brief:2026-10-01:owner")
    assert again.deduped and again.outbox_id == r.outbox_id and len(notify.captured()) == 1


@pytest.mark.parametrize(
    "build",
    [
        lambda: validate.template_payload(TO, "t", "en", ["x"], header="h" * 61),
        lambda: validate.template_payload(TO, "t", "en", ["x" * 600, "y" * 425]),
        lambda: validate.template_payload(TO, "t", "en", [""]),
        lambda: validate.template_payload(TO, "t", "en", [], buttons=[{"type": "quick_reply", "payload": "p"}] * 11),
        lambda: validate.template_payload(TO, "t", "en", [], buttons=[{"type": "call"}]),
        lambda: validate.buttons_payload(TO, "Pick", [("a", "A"), ("b", "B"), ("c", "C"), ("d", "D")]),
        lambda: validate.buttons_payload(TO, "Pick", [("a", "x" * 21)]),
        lambda: validate.buttons_payload(TO, "Pick", [("a", "A"), ("a", "B")]),
        lambda: validate.buttons_payload(TO, "b" * 1025, [("a", "A")]),
        lambda: validate.buttons_payload(TO, "Pick", [("a", "A")], footer="f" * 61),
        lambda: validate.list_payload(TO, "Pick", [{"title": "S1", "rows": [{"id": f"r{i}", "title": "t"} for i in range(6)]},
                                                   {"title": "S2", "rows": [{"id": f"s{i}", "title": "t"} for i in range(5)]}]),
        lambda: validate.list_payload(TO, "Pick", [{"rows": [{"id": "r", "title": "x" * 25}]}]),
        lambda: validate.list_payload(TO, "Pick", [{"rows": [{"id": "r", "title": "ok", "description": "d" * 73}]}]),
        lambda: validate.list_payload(TO, "Pick", [{"rows": [{"id": "i" * 201, "title": "ok"}]}]),
        lambda: validate.list_payload(TO, "Pick", [{"rows": [{"id": "r", "title": "ok"}]}], button_text="b" * 21),
        lambda: validate.text_payload(TO, "x" * 4097),
        lambda: validate.document_payload(TO, file_id="f", filename="a.pdf", caption="c" * 1025),
        lambda: validate.validate_template_definition([{"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "q" * 26}]}]),
        lambda: validate.validate_template_definition([{"type": "FOOTER", "text": "f" * 61}]),
        lambda: validate.validate_template_definition([{"type": "HEADER", "format": "TEXT", "text": "h" * 61}]),
        lambda: validate.validate_template_definition([{"type": "BODY", "text": "b" * 1025}]),
    ],
)
def test_payload_limits(build):
    with pytest.raises(PayloadError):
        build()


def test_payloads_at_the_limits_are_accepted():
    validate.template_payload(TO, "t", "en", ["x" * 1024], header="h" * 60, buttons=[{"type": "quick_reply", "payload": "p"}] * 10)
    validate.buttons_payload(TO, "b" * 1024, [("a", "x" * 20), ("b", "B"), ("c", "C")], header="h" * 60, footer="f" * 60)
    validate.list_payload(TO, "Pick", [{"title": "s" * 24, "rows": [{"id": "i" * 200, "title": "t" * 24, "description": "d" * 72}]},
                                       {"title": "Two", "rows": [{"id": f"r{i}", "title": "t"} for i in range(9)]}])
    validate.validate_template_definition([
        {"type": "HEADER", "format": "TEXT", "text": "h" * 60}, {"type": "BODY", "text": "b" * 1024},
        {"type": "FOOTER", "text": "f" * 60}, {"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": "q" * 25}] * 10},
    ])


def test_session_messages_need_the_24h_window(cur, outbox, clock):
    clock.freeze(T0)
    r = notify.send_whatsapp_text(TO, "Hello", hotel_slug=None, purpose="core.test")
    assert not r.ok and r.status == "outside_window" and notify.captured() == []
    fb = notify.send_whatsapp_text(
        TO, "Hello", hotel_slug=None, purpose="core.test", idempotency_key="k1",
        fallback_template={"name": "hello_v1", "lang": "en", "params": ["Sam"]},
    )
    assert fb.status == "outside_window"
    [sent] = notify.captured()
    assert sent["payload"]["template"]["name"] == "hello_v1"
    cur.execute("SELECT status, fallback_of FROM outbound_messages WHERE idempotency_key = 'k1:fallback'")
    assert dict(cur.fetchone()) == {"status": "sent", "fallback_of": fb.outbox_id}
    ob.open_window(cur, "platform", TO, at=T0 - timedelta(hours=23))
    clock.advance(seconds=10)
    ok = notify.send_whatsapp_interactive(TO, "Approve?", buttons=[(tokens.sign_ref("act:1:approve"), "Approve")],
                                          hotel_slug=None, purpose="core.test")
    assert ok.ok and ok.status == "sent"
    clock.advance(hours=2)  # 25 h after the guest's message
    assert notify.send_whatsapp_text(TO, "Late", hotel_slug=None, purpose="core.test").status == "outside_window"


def test_six_seconds_between_messages_to_one_recipient(cur, outbox, clock):
    clock.freeze(T0)
    a = _tpl(key="a")
    b = _tpl(key="b")
    c = _tpl(to="+971509999999", key="c")
    assert (a.status, b.status, c.status) == ("sent", "queued", "sent")
    assert b.ok  # accepted for delivery
    assert _row(cur, b.outbox_id)["not_before"] == T0 + timedelta(seconds=6)
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, b.outbox_id)["status"] == "queued"
    clock.advance(seconds=6)
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, b.outbox_id)["status"] == "sent" and len(notify.captured()) == 3


def test_meta_131056_backs_off(cur, outbox, clock):
    clock.freeze(T0)
    outbox.fail_next(131056, "pair rate limit")
    r = _tpl(key="x")
    row = _row(cur, r.outbox_id)
    assert (row["status"], row["attempts"], row["error_code"]) == ("queued", 1, "131056")
    assert row["not_before"] == T0 + timedelta(seconds=12)
    clock.advance(seconds=12)
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, r.outbox_id)["status"] == "sent"


def test_meta_130429_holds_the_whole_sender(cur, outbox, clock):
    clock.freeze(T0)
    outbox.fail_next(130429, "throughput")
    r1 = _tpl(key="x")
    until = _row(cur, r1.outbox_id)["not_before"]
    assert until == T0 + timedelta(seconds=60)
    r2 = _tpl(to="+971509999999", key="y")
    assert r2.status == "queued" and _row(cur, r2.outbox_id)["not_before"] == until
    clock.advance(seconds=60)
    jobs.run_pending(only=["core.notify.deliver"])
    assert {_row(cur, r1.outbox_id)["status"], _row(cur, r2.outbox_id)["status"]} == {"sent"}


def test_meta_131049_waits_a_day(cur, outbox, clock):
    clock.freeze(T0)
    outbox.fail_next(131049, "marketing cap")
    r = _tpl(key="x", category="marketing")
    assert _row(cur, r.outbox_id)["not_before"] == T0 + timedelta(hours=24)


def test_meta_131047_falls_back_to_the_template(cur, outbox, clock):
    clock.freeze(T0)
    ob.open_window(cur, "platform", TO, at=T0 - timedelta(hours=1))
    outbox.fail_next(131047, "re-engagement required")
    r = notify.send_whatsapp_text(TO, "Hi", hotel_slug=None, purpose="core.test", idempotency_key="t",
                                  fallback_template={"name": "reengage_v1", "lang": "en", "params": ["x"]})
    assert _row(cur, r.outbox_id)["status"] == "failed"
    [sent] = notify.captured()
    assert sent["payload"]["template"]["name"] == "reengage_v1"


def test_other_errors_retry_then_fail(cur, outbox, clock):
    clock.freeze(T0)
    outbox.fail_next(500, "server error", times=ob.MAX_ATTEMPTS)
    r = _tpl(key="x")
    for _ in range(ob.MAX_ATTEMPTS):
        clock.advance(hours=2)
        jobs.run_pending(only=["core.notify.deliver"])
    row = _row(cur, r.outbox_id)
    assert row["status"] == "failed" and row["attempts"] == ob.MAX_ATTEMPTS and row["error_code"] == "500"


def test_marketing_waits_for_dubai_business_hours(cur, outbox, clock):
    clock.freeze(datetime(2026, 10, 1, 20, 0, tzinfo=DUBAI))
    r = _tpl(key="m", category="marketing")
    assert r.status == "queued" and notify.captured() == []
    assert _row(cur, r.outbox_id)["not_before"] == datetime(2026, 10, 2, 9, 0, tzinfo=DUBAI)
    clock.freeze(datetime(2026, 10, 2, 9, 0, tzinfo=DUBAI))
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, r.outbox_id)["status"] == "sent"
    assert ob.next_marketing_slot(datetime(2026, 10, 2, 17, 59, tzinfo=DUBAI)) is None
    assert ob.next_marketing_slot(datetime(2026, 10, 2, 8, 0, tzinfo=DUBAI)) == datetime(2026, 10, 2, 9, 0, tzinfo=DUBAI)


def test_fallback_chain_moves_to_the_next_channel(cur, outbox, clock):
    clock.freeze(T0)
    outbox.unconfigured.add("sms")
    res = notify.notify(
        notify.Recipient(phone=TO, email="gm@hotel.test"),
        notify.Message(purpose="core.test", whatsapp_text="Hi", sms_text="Hi", email_subject="Hi", email_text="Hi there",
                       idempotency_key="chain"),
    )
    assert res.ok and res.channel == "email" and res.status == "sent"
    cur.execute("SELECT channel, status FROM outbound_messages WHERE idempotency_key LIKE 'chain%%' ORDER BY id")
    assert [(r["channel"], r["status"]) for r in cur.fetchall()] == [
        ("whatsapp", "outside_window"), ("sms", "skipped_unconfigured"), ("email", "sent"),
    ]
    tpl = notify.notify(
        notify.Recipient(phone=TO),
        notify.Message(purpose="core.test", whatsapp_text="Hi", whatsapp_template={"name": "hello_v1", "params": ["Sam"]}),
    )
    assert tpl.ok and notify.captured("whatsapp")[-1]["payload"]["type"] == "template"
    none = notify.notify(notify.Recipient(), notify.Message(purpose="core.test", whatsapp_text="x"))
    assert not none.ok and none.status == "no_channel"


def test_email_with_attachments_and_headers(cur, outbox, hotel_uae, clock):
    clock.freeze(T0)
    ref = files.put_file(cur, hotel_uae.slug, b"%PDF-1.4 packet", filename="packet.pdf", content_type="application/pdf",
                         purpose="profit.dispute", source="generated")
    r = notify.send_email("Finance@Hotel.test", "Dispute packet", "<p>Attached</p>", text="Attached", attachments=[ref.id],
                          headers={"List-Unsubscribe": "<mailto:u@clefs.world>"}, hotel_slug=hotel_uae.slug, purpose="profit.dispute")
    assert r.ok and r.status == "sent"
    [sent] = notify.captured("email")
    p = sent["payload"]
    assert p["To"] == "Finance@Hotel.test" and p["Attachments"][0]["Name"] == "packet.pdf" and "AttachmentFileIds" not in p
    assert p["Headers"] == [{"Name": "List-Unsubscribe", "Value": "<mailto:u@clefs.world>"}] and p["MessageStream"] == "outbound"
    assert _row(cur, r.outbox_id)["recipient"] == "finance@hotel.test"
    with pytest.raises(PayloadError):
        notify.send_email("not-an-email", "x", "y", purpose="core.test")


def test_sms_rules_and_providers(cur, outbox):
    with pytest.raises(PayloadError):
        notify.send_sms(TO, "Open https://clefs.world/x", purpose="core.test")
    uae = notify.send_sms(TO, "Clefs: 2 urgent items. Open Clefs.", purpose="brief.urgent")
    india = notify.send_sms("+919812345678", "Towels for 204", purpose="service.alert", template_vars={"room": "204", "task": "Towels"})
    assert uae.ok and india.ok
    by_to = {m["payload"].get("To") or m["payload"]["recipients"][0]["mobiles"]: m for m in notify.captured("sms")}
    assert by_to[TO]["provider"] == "twilio" and by_to[TO]["payload"]["Body"].startswith("Clefs:")
    assert by_to["919812345678"]["provider"] == "msg91" and by_to["919812345678"]["payload"]["recipients"][0]["room"] == "204"


def test_document_send_uses_a_signed_link(cur, outbox, hotel_uae, clock, env):
    clock.freeze(T0)
    env(PUBLIC_API_ORIGIN="https://api.clefs.test")
    ref = files.put_file(cur, hotel_uae.slug, b"%PDF-1.4", filename="report.pdf", content_type="application/pdf",
                         purpose="brief.weekly", source="generated")
    ob.open_window(cur, "platform", TO, at=T0)
    r = notify.send_whatsapp_document(TO, ref.id, "report.pdf", "Weekly report", hotel_slug=hotel_uae.slug, purpose="brief.weekly")
    assert r.ok
    doc = notify.captured("whatsapp")[0]["payload"]["document"]
    assert doc["link"].startswith(f"https://api.clefs.test/hotels/{hotel_uae.slug}/files/{ref.id}?t=") and "file_id" not in doc
    assert doc["filename"] == "report.pdf" and doc["caption"] == "Weekly report"


def test_hotel_sender_needs_a_configured_number(cur, outbox, hotel_uae, clock):
    clock.freeze(T0)
    r = _tpl(key="h", hotel_slug=hotel_uae.slug, sender="hotel")
    assert not r.ok and r.status == "skipped_unconfigured"
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = 'PNID-HOTEL' WHERE slug = %s", (hotel_uae.slug,))
    from clefs.core import hotels

    hotels.invalidate(hotel_uae.slug)
    ok = _tpl(key="h2", hotel_slug=hotel_uae.slug, sender="hotel")
    assert ok.ok and notify.captured()[0]["sender_id"] == "PNID-HOTEL"


def test_outbox_rows_roll_back_with_the_caller(cur, outbox, clock):
    from clefs import db

    clock.freeze(T0)
    with pytest.raises(RuntimeError):
        with db.db() as c:
            _tpl(key="rb", cur=c)
            raise RuntimeError("abort")
    cur.execute("SELECT COUNT(*) AS n FROM outbound_messages WHERE idempotency_key = 'rb'")
    assert cur.fetchone()["n"] == 0


def test_magic_link_sender_picks_the_channel(cur, outbox, make_user, hotel_uae, clock):
    clock.freeze(T0)
    email_only = {"id": 1, "email": "owner@hotel.test", "phone_e164": None}
    assert notify.magic_sender(email_only, hotel_uae.slug, "https://www.clefs.world/auth/magic/abc", "login")
    assert "https://www.clefs.world/auth/magic/abc" in notify.captured("email")[0]["payload"]["HtmlBody"]
    notify.clear_captured()
    phone_only = {"id": 2, "email": None, "phone_e164": "+971507777777"}
    assert notify.magic_sender(phone_only, hotel_uae.slug, "https://x/auth/magic/t1", "brief")
    assert notify.captured("whatsapp")[0]["payload"]["template"]["name"] == "clefs_magic_link"
    notify.clear_captured()
    ob.open_window(cur, "platform", "+971507777777", at=T0)
    clock.advance(seconds=30)
    both = {"id": 3, "email": "o@hotel.test", "phone_e164": "+971507777777"}
    assert notify.magic_sender(both, hotel_uae.slug, "https://x/auth/magic/t2", "brief")
    [sent] = notify.captured()
    assert sent["channel"] == "whatsapp" and sent["payload"]["type"] == "text" and "t2" in sent["payload"]["text"]["body"]
    assert not notify.magic_sender({"id": 4, "email": None, "phone_e164": None}, hotel_uae.slug, "u", "login")


def test_magic_request_route_delivers_through_notify(client, cur, make_user, hotel_uae, outbox):
    u = make_user("owner", hotel_uae.slug)
    r = client.post("/auth/magic/request", json={"email": u["email"], "purpose": "login"})
    assert r.status_code == 202
    [sent] = notify.captured("email")
    assert "/auth/magic/" in sent["payload"]["HtmlBody"]


def test_live_email_never_reaches_reserved_domains(monkeypatch):
    """The production demo hotel's users are @example.com (RFC 2606): live mail
    to them is dropped before Resend, so it can never bounce, and a mixed
    list keeps only the real recipients."""
    import httpx

    from clefs.notify import transport as tp

    calls = []

    class _Ok:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "re-1"}

    monkeypatch.setattr(httpx, "post", lambda url, **kw: calls.append(kw["json"]) or _Ok())
    live = tp.LiveTransport()
    sender = "Clefs <no-reply@clefs.world>"
    assert live.email({"From": sender, "To": "owner+demo-deira-crest@example.com", "Subject": "Brief"}).startswith(
        "suppressed-reserved-")
    assert calls == []
    assert live.email({"From": sender, "To": "Owner <owner@example.com>, gm@hotel.ae", "Cc": "fin@demo.test",
                       "Subject": "Brief"}) == "re-1"
    assert calls[0]["to"] == ["gm@hotel.ae"] and "cc" not in calls[0]
    assert tp.is_reserved_address("a@sub.example.org") and not tp.is_reserved_address("a@example-hotel.ae")


def test_a_demo_hotel_never_delivers_for_real(cur, outbox, hotel_uae, clock):
    """The demo hotel lives in production and its guests are synthetic, but a
    made-up number can be a real person's: in live mode its messages are
    logged and marked sent, and the provider is never called."""
    from clefs.notify.transport import CaptureTransport, set_transport

    class _Live(CaptureTransport):
        name = "live"

    live = _Live()
    set_transport(live)
    clock.freeze(T0)
    real = notify.send_sms(TO, "Clefs: your room is ready.", purpose="core.test", hotel_slug=hotel_uae.slug)
    assert real.ok and len(live.sent) == 1
    cur.execute("UPDATE hotels SET status = 'demo' WHERE slug = %s", (hotel_uae.slug,))
    demo = notify.send_sms(TO, "Clefs: your room is ready.", purpose="core.test", hotel_slug=hotel_uae.slug)
    assert demo.ok and len(live.sent) == 1
    assert _row(cur, demo.outbox_id)["status"] == "sent" and _row(cur, demo.outbox_id)["provider"] == "log"
