"""
Inbound webhooks (01 spec E6, 04 spec F4): the Meta verification handshake,
X-Hub-Signature-256 checks, messages[].id dedupe, the 24 h window, audience
resolution, signed button dispatch with priorities, status updates and their
error-code follow-ups, and the Resend inbound email route (Svix signatures,
sender verdicts taken from Resend's API only).
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

import pytest

from clefs import notify
from clefs.auth import tokens
from clefs.core import clock, hotels
from clefs.core.errors import ClefsError
from clefs.notify import inbound
from clefs.notify import outbox as ob
from tests.foundation import resend_mail

UTC = timezone.utc
GUEST = "+971501112222"


def _msg(mid: str, *, frm: str = "971501112222", ts: int = 1790000000, **body) -> dict:
    m = {"from": frm, "id": mid, "timestamp": str(ts)}
    m.update(body)
    if "type" not in m:
        m.update(type="text", text={"body": "Hello"})
    return m


# `handlers` lives in tests/foundation/conftest.py (module handlers set aside).


def _inbound(cur, provider_id):
    cur.execute("SELECT * FROM inbound_messages WHERE provider_id = %s", (provider_id,))
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Handshake and signatures ───


def test_verification_handshake(client, wa):
    ok = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": wa.verify_token, "hub.challenge": "1158201444"})
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "x"})
    assert bad.status_code == 403


def test_signatures_are_checked_on_the_raw_body(client, wa, cur):
    payload = wa.message_payload([_msg("wamid.sig")])
    assert wa.post(client, payload, signature=None).status_code == 401
    assert wa.post(client, payload, signature="sha256=" + "0" * 64).status_code == 401
    assert wa.post(client, payload, signature="md5=abc").status_code == 401
    assert _inbound(cur, "wamid.sig") is None
    # A body re-serialized differently from what was signed must fail too.
    import json

    body = json.dumps(payload).encode()
    sig = wa.sign(body)
    tampered = body.replace(b"Hello", b"Hullo")
    r = client.post("/webhooks/whatsapp", content=tampered, headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"})
    assert r.status_code == 401
    assert wa.post(client, payload).status_code == 200


def test_unconfigured_webhooks_refuse(client, env):
    env(WA_APP_SECRET=None, META_APP_SECRET=None, WA_VERIFY_TOKEN=None)
    assert client.post("/webhooks/whatsapp", content=b"{}", headers={"X-Hub-Signature-256": "sha256=00"}).status_code == 503
    assert client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "x"}).status_code == 503


# ─── Messages ───


def test_messages_are_deduplicated_and_open_the_window(client, wa, cur, outbox):
    payload = wa.message_payload([_msg("wamid.A1", ts=1790000000)], contacts=[{"wa_id": "971501112222", "profile": {"name": "Sam"}}])
    first = wa.post(client, payload)
    assert first.status_code == 200 and first.json()["processed"] == {"unhandled": 1}
    again = wa.post(client, payload)
    assert again.json()["processed"] == {"duplicate": 1}
    cur.execute("SELECT COUNT(*) AS n FROM inbound_messages WHERE provider_id = 'wamid.A1'")
    assert cur.fetchone()["n"] == 1
    row = _inbound(cur, "wamid.A1")
    assert row["audience"] == "unknown" and row["sender"] == GUEST and row["status"] == "unhandled"
    exp = ob.window_expires_at(cur, wa.platform_id, GUEST)
    assert exp == datetime.fromtimestamp(1790000000, tz=UTC) + timedelta(hours=24)


def test_signed_button_taps_reach_the_prefix_handler(client, wa, cur, make_user, hotel_uae, handlers):
    owner = make_user("owner", hotel_uae.slug)
    cur.execute("UPDATE users SET phone_e164 = '+971504445555' WHERE id = %s", (owner["id"],))
    seen = []

    @handlers
    @notify.on_whatsapp("staff", button_prefix="brief:")
    def tap(ctx: notify.InboundCtx) -> bool:
        seen.append((ctx.button_ref, ctx.audience, ctx.hotel_slug, ctx.user_id, ctx.kind, ctx.button_title))
        return True

    good_id = tokens.sign_ref("brief:2026-10-01:1:approve")
    msg = _msg("wamid.B1", frm="971504445555", type="interactive",
               interactive={"type": "button_reply", "button_reply": {"id": good_id, "title": "Approve #1"}},
               context={"from": "971500000000", "id": "wamid.OUT1"})
    r = wa.post(client, wa.message_payload([msg]))
    assert r.json()["processed"] == {"handled": 1}
    assert seen == [("brief:2026-10-01:1:approve", "staff", hotel_uae.slug, owner["id"], "button_reply", "Approve #1")]
    assert _inbound(cur, "wamid.B1")["handled_by"].endswith("tap")

    forged = good_id.replace(":1:approve", ":2:approve")  # a valid tag for another ref
    msg2 = _msg("wamid.B2", frm="971504445555", type="button", button={"payload": forged, "text": "Approve #2"})
    assert wa.post(client, wa.message_payload([msg2])).json()["processed"] == {"unhandled": 1}
    assert len(seen) == 1


def test_guest_handlers_run_by_priority_until_one_handles(client, wa, cur, hotel_uae, handlers):
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = 'PNID-HOTEL' WHERE slug = %s", (hotel_uae.slug,))
    hotels.invalidate(hotel_uae.slug)
    order = []

    @handlers
    @notify.on_whatsapp("guest", priority=30)
    def concierge(ctx):
        order.append("concierge")
        return {"handled": True}

    @handlers
    @notify.on_whatsapp("guest", priority=5, kind="text", match=lambda c: (c.text or "").strip().upper() == "STOP")
    def optout(ctx):
        order.append("optout")
        return True

    @handlers
    @notify.on_whatsapp("guest", priority=10)
    def journeys(ctx):
        order.append("journeys")
        assert ctx.sender_hmac is not None and len(ctx.sender_hmac) == 32
        return False

    @handlers
    @notify.on_whatsapp("guest", priority=90)
    def fallback(ctx):
        order.append("fallback")
        return True

    wa.post(client, wa.message_payload([_msg("wamid.G1")], phone_number_id="PNID-HOTEL"))
    assert order == ["journeys", "concierge"]
    row = _inbound(cur, "wamid.G1")
    assert row["audience"] == "guest" and row["hotel_slug"] == hotel_uae.slug and row["handled_by"].endswith("concierge")
    order.clear()
    wa.post(client, wa.message_payload([_msg("wamid.G2", type="text", text={"body": "stop"})], phone_number_id="PNID-HOTEL"))
    assert order == ["optout"]


def test_a_crashing_handler_is_recorded_and_meta_still_gets_200(client, wa, cur, handlers):
    @handlers
    @notify.on_whatsapp("*")
    def broken(ctx):
        raise RuntimeError("bug in a module")

    r = wa.post(client, wa.message_payload([_msg("wamid.E1")]))
    assert r.status_code == 200 and r.json()["processed"] == {"error": 1}
    row = _inbound(cur, "wamid.E1")
    assert row["status"] == "error" and "bug in a module" in row["error"]


def test_staff_members_are_recognised(client, wa, cur, hotel_uae):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, whatsapp_e164) VALUES (%s, 'Ravi', 'Attendant', 'Housekeeping', '+971506667777')",
        (hotel_uae.slug,),
    )
    wa.post(client, wa.message_payload([_msg("wamid.S1", frm="971506667777")]))
    row = _inbound(cur, "wamid.S1")
    assert row["audience"] == "staff" and row["hotel_slug"] == hotel_uae.slug


# ─── Statuses ───


def test_status_updates_move_forward_only(client, wa, cur, outbox, handlers):
    seen = []

    @handlers
    @notify.on_outbound_status("brief.")
    def hook(cur_, row, status, code):
        seen.append((row["id"], status, code))

    r = notify.send_whatsapp_template(GUEST, "owner_brief_v1", "en", ["1"], hotel_slug=None, purpose="brief.daily")
    wamid = notify.captured()[0]["provider_id"]
    ts = 1790000100
    statuses = [
        {"id": wamid, "status": "delivered", "timestamp": str(ts), "recipient_id": "971501112222",
         "pricing": {"billable": True, "category": "utility"}},
        {"id": wamid, "status": "read", "timestamp": str(ts + 60), "recipient_id": "971501112222"},
    ]
    res = wa.post(client, wa.status_payload(statuses))
    assert res.json()["processed"] == {"status_delivered": 1, "status_read": 1}
    late = wa.post(client, wa.status_payload([{"id": wamid, "status": "delivered", "timestamp": str(ts + 5)}]))
    assert late.json()["processed"] == {"status_duplicate": 1}
    wa.post(client, wa.status_payload([{"id": wamid, "status": "sent", "timestamp": str(ts - 5)}]))
    cur.execute("SELECT status, delivered_at, read_at, billable, pricing_category FROM outbound_messages WHERE id = %s", (r.outbox_id,))
    row = dict(cur.fetchone())
    assert row["status"] == "read" and row["billable"] is True and row["pricing_category"] == "utility"
    assert row["read_at"] == datetime.fromtimestamp(ts + 60, tz=UTC)
    assert [s[1] for s in seen] == ["delivered", "read", "sent"]


def test_failed_statuses_trigger_the_right_follow_up(client, wa, cur, outbox, clock):
    clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=UTC))
    ob.open_window(cur, wa.platform_id, GUEST, at=clock.now())
    text = notify.send_whatsapp_text(GUEST, "Hi", hotel_slug=None, purpose="core.test", idempotency_key="t1",
                                     fallback_template={"name": "reengage_v1", "lang": "en", "params": ["x"]})
    wamid = notify.captured()[0]["provider_id"]
    wa.post(client, wa.status_payload([{"id": wamid, "status": "failed", "timestamp": "1790000000",
                                        "errors": [{"code": 131047, "title": "Re-engagement message"}]}]))
    cur.execute("SELECT status, error_code FROM outbound_messages WHERE id = %s", (text.outbox_id,))
    assert dict(cur.fetchone()) == {"status": "failed", "error_code": "131047"}
    cur.execute("SELECT template_name, fallback_of FROM outbound_messages WHERE idempotency_key = 't1:fallback'")
    assert dict(cur.fetchone()) == {"template_name": "reengage_v1", "fallback_of": text.outbox_id}

    clock.advance(minutes=5)
    mk = notify.send_whatsapp_template("+971508889999", "offer_v1", "en", ["x"], hotel_slug=None, purpose="core.test",
                                       category="marketing", idempotency_key="m1")
    wamid2 = notify.captured()[-1]["provider_id"]
    wa.post(client, wa.status_payload([{"id": wamid2, "status": "failed", "timestamp": "1790000000",
                                        "errors": [{"code": 131049, "title": "Healthy ecosystem"}]}]))
    cur.execute("SELECT status, not_before, fallback_of FROM outbound_messages WHERE idempotency_key = 'm1:retry:1'")
    retry = dict(cur.fetchone())
    assert retry["status"] == "queued" and retry["fallback_of"] == mk.outbox_id
    assert retry["not_before"] >= clock.now() + timedelta(hours=24)


# ─── Email ───


@pytest.fixture
def mail_env(env, cur, hotel_uae):
    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_DOMAIN="in.clefs.world")
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))


def _mail(to: str, message_id: str = "m-1", attachments: list | None = None) -> dict:
    return {
        "From": "Guest <guest@example.com>",
        "FromFull": {"Email": "Guest@Example.com", "Name": "A Guest"},
        "To": to,
        "ToFull": [{"Email": to, "Name": "", "MailboxHash": ""}],
        "Subject": "Group booking for GITEX",
        "MessageID": message_id,
        "TextBody": "12 rooms, 7-11 Dec",
        "HtmlBody": "<p>12 rooms</p>",
        "StrippedTextReply": "",
        "Headers": [{"Name": "X-Spam-Score", "Value": "0"}],
        "Attachments": attachments or [],
    }


def test_parse_address():
    assert inbound.parse_address("testdubai+inq-42@in.clefs.world", "in.clefs.world") == ("testdubai", "inq", "42")
    assert inbound.parse_address("<TestDubai+reviews@IN.clefs.world>", "in.clefs.world") == ("testdubai", "reviews", None)
    assert inbound.parse_address("testdubai@in.clefs.world", "in.clefs.world") == ("testdubai", None, None)
    assert inbound.parse_address("testdubai+inq@other.com", "in.clefs.world") is None
    assert inbound.parse_address("nonsense", None) is None


def test_inbound_email_routes_by_alias_and_purpose(client, cur, hotel_uae, mail_env, handlers):
    got = []

    @handlers
    @notify.on_inbound_email(purpose="inquiries")
    def inquiry(mail: notify.InboundEmail) -> bool:
        got.append((mail.hotel_slug, mail.purpose, mail.ref, mail.sender, [a.filename for a in mail.attachments]))
        return True

    csv = base64.b64encode(b"room,nights\n101,3\n").decode()
    body = _mail("testdubai+inq-42@in.clefs.world", attachments=[{"Name": "rooming.csv", "Content": csv, "ContentType": "text/csv", "ContentLength": 18}])
    r = resend_mail.post(client, body)
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "status": "handled", "purpose": "inquiries", "hotel_slug": hotel_uae.slug, "attachments": 1}
    assert got == [(hotel_uae.slug, "inquiries", "42", "guest@example.com", ["rooming.csv"])]
    cur.execute("SELECT source, purpose, hotel_slug FROM files WHERE filename = 'rooming.csv'")
    assert dict(cur.fetchone()) == {"source": "email", "purpose": "email.inquiries", "hotel_slug": hotel_uae.slug}
    stored = _inbound(cur, "email:m-1")
    assert stored["payload"]["Attachments"] == [{"Name": "rooming.csv", "ContentType": "text/csv", "ContentLength": 18}]
    assert resend_mail.post(client, body).json()["status"] == "duplicate"
    other = resend_mail.post(client, _mail("testdubai@in.clefs.world", "m-2")).json()
    assert other["purpose"] == "ingest" and other["status"] == "unhandled"
    unknown = resend_mail.post(client, _mail("nobody+inq@in.clefs.world", "m-3"))
    assert unknown.json() == {"ok": True, "status": "unrouted"}


def test_resend_webhook_needs_its_signature_and_the_switch(client, env, mail_env):
    body = _mail("testdubai+inq@in.clefs.world")
    raw = json.dumps(resend_mail.event()).encode()
    other_secret = "whsec_" + base64.b64encode(b"x" * 32).decode()
    r = resend_mail.post(client, body, headers=resend_mail.sign(raw, secret=other_secret))
    assert r.status_code == 401 and r.json()["code"] == "invalid_signature"
    assert resend_mail.post(client, body, headers={}).status_code == 401
    # Other event types, and an email Resend no longer has, are acknowledged (no redelivery).
    r = resend_mail.post(client, body, body=resend_mail.event("email.delivered"))
    assert r.status_code == 200 and r.json() == {"ok": True, "status": "ignored"}
    assert resend_mail.post(client, None).json() == {"ok": True, "status": "ignored"}
    env(RESEND_WEBHOOK_SECRET=None)
    r = resend_mail.post(client, body)
    assert r.status_code == 503 and r.json()["code"] == "not_configured"
    env(RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_ENABLED="false")
    assert resend_mail.post(client, body).status_code == 503


def test_odd_purpose_tokens_do_not_break_attachment_storage(client, cur, hotel_uae, mail_env):
    att = [{"Name": "a.txt", "Content": base64.b64encode(b"hello").decode(), "ContentType": "text/plain"}]
    r = resend_mail.post(client, _mail("testdubai+we!rd@in.clefs.world", "m-9", attachments=att))
    assert r.status_code == 200 and r.json()["attachments"] == 1
    cur.execute("SELECT purpose FROM files WHERE filename = 'a.txt'")
    assert cur.fetchone()["purpose"] == "email.we_rd"


# ─── Resend: the signature and the sender verdicts ───


def test_resend_signature_is_an_hmac_of_id_timestamp_and_body(env):
    env(RESEND_WEBHOOK_SECRET=resend_mail.SECRET)
    raw = json.dumps(resend_mail.event()).encode()

    def ok(h: dict, body: bytes = raw) -> bool:
        return inbound.verify_resend_signature(body, h.get("svix-id"), h.get("svix-timestamp"), h.get("svix-signature"))

    good = resend_mail.sign(raw)
    assert ok(good)
    rotated = resend_mail.sign(raw, secret="whsec_" + base64.b64encode(b"y" * 32).decode())
    assert not ok(rotated)  # wrong secret
    assert not ok(good, raw.replace(b"email.received", b"email.receivex"))  # tampered body
    assert not ok({**good, "svix-id": "msg_other"})  # the id is signed too
    now = int(clock.now().timestamp())
    assert not ok(resend_mail.sign(raw, ts=now - 301)) and not ok(resend_mail.sign(raw, ts=now + 301))  # replay window
    assert ok(resend_mail.sign(raw, ts=now - 240))
    # Several signatures (Svix sends two while a secret rotates): any v1 match counts.
    assert ok({**good, "svix-signature": f"{rotated['svix-signature']} {good['svix-signature']}"})
    assert not ok({**good, "svix-signature": f"{rotated['svix-signature']} v1,{'A' * 44}"})
    assert not ok({**good, "svix-signature": good["svix-signature"].replace("v1,", "v2,")})
    assert not ok({**good, "svix-signature": "v1,é"})  # non-ASCII: a refusal, not a 500
    env(RESEND_WEBHOOK_SECRET=None)
    with pytest.raises(ClefsError) as unset:
        ok(good)
    assert unset.value.status == 503


def test_resend_sender_verdicts_come_from_the_api_never_from_the_message():
    """The sender writes every header of their message, a dmarc=pass naming
    our own verifier included. from_resend drops them all and writes exactly
    one Received-SPF and one Authentication-Results from the API's
    authentication object, which Resend's receiving server computed."""
    headers = {
        "from": "GM <gm@hotel.ae>",
        "x-mailer": "Mail",
        "received-spf": "pass receiver=inbound.resend.com",
        "authentication-results": ["inbound.resend.com; spf=pass; dkim=pass; dmarc=pass",
                                   "x; dmarc=pass header.from=hotel.ae"],
    }
    msg = {"object": "email", "id": "e-1", "from": "gm@hotel.ae", "to": ["testdubai@in.clefs.world"],
           "headers": headers, "message_id": "<a1@hotel.ae>",
           "authentication": {"spf": "fail", "dkim": "gray", "dmarc": "fail"}}
    passed = {"spf": "pass", "dkim": "pass", "dmarc": "pass"}

    def verdicts(p: dict) -> list[tuple[str, str]]:
        return [(h["Name"], h["Value"]) for h in p["Headers"] if h["Name"].lower() in inbound.AUTH_VERDICT_HEADERS]

    def mail(p: dict) -> notify.InboundEmail:
        return notify.InboundEmail(cur=None, inbound_id=1, hotel_slug="h", purpose="ingest", purpose_token=None, ref=None,
                                   alias="testdubai", sender=p["FromFull"]["Email"], sender_name=None, to="x",
                                   subject=None, text=None, html=None, stripped_reply=None, message_id=p["MessageID"],
                                   header_list=[(h["Name"], h["Value"]) for h in p["Headers"]])

    p = inbound.from_resend(msg)
    assert verdicts(p) == [("Received-SPF", "fail receiver=inbound.resend.com"),
                           ("Authentication-Results", "inbound.resend.com; spf=fail; dkim=gray; dmarc=fail")]
    m = mail(p)
    assert (m.spf, m.dkim) == ("fail", "gray") and "dmarc=pass" not in m.headers["Authentication-Results"]
    assert (p["From"], p["FromFull"], p["MessageID"]) == ("GM <gm@hotel.ae>", {"Email": "gm@hotel.ae", "Name": "GM"},
                                                          "a1@hotel.ae")
    m = mail(inbound.from_resend({**msg, "authentication": passed}))
    assert (m.spf, m.dkim) == ("pass", "pass") and "dmarc=pass" in m.headers["Authentication-Results"]
    # Results about a From header naming someone else, or two people, prove
    # nothing about the API's sender: none are written.
    for other in ("gm@hotel.ae, ceo@hotel.ae", "Boss <ceo@hotel.ae>"):
        p = inbound.from_resend({**msg, "headers": {**headers, "from": other}, "authentication": passed})
        assert verdicts(p) == [] and p["FromFull"]["Email"] == "gm@hotel.ae", other
