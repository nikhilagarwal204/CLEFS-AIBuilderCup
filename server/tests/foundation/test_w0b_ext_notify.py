"""
Extension lane "notify-jobs-privacy-ai" (04 spec 0.4 F3, F4, F8):

  F3  monthly_at / nth_weekday_at run once per slot through tick(), catch up a
      missed tick on the same hotel-local day and never the day after.
  F4  the frozen guest priority table and the handled=True rule; sender_hmac
      on inbound rows (migration 0014) and wa_window_open_until; the inbound
      email grammar (inbound_address <-> parse_address, the in.clefs.world
      default); send_email headers; the attempt-key convention; the
      per-recipient pacing lock across senders and workers; on_outbound_status
      for failures at send time, email "sent", and retry / fallback links.
  F8  contact_hmac defaulting a national phone to the hotel's country; sealing
      with a version byte; the PII registry entry for sender_hmac; ai cost in
      micros (Canon K3) on ai_calls and in responses_raw; store=False on every
      ai.* call.
"""

from __future__ import annotations

import inspect
import logging
import os
import pathlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
import pytest

from clefs import ai, jobs, notify
from clefs.core import hotels, privacy
from clefs.core.errors import ClefsError
from clefs.notify import inbound
from clefs.notify import outbox as ob
from clefs.notify.validate import PayloadError
from tests.foundation import resend_mail

UTC = timezone.utc
DUBAI = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)  # 12:00 Dubai
GUEST = "+971501112222"
MIGRATION = pathlib.Path(__file__).resolve().parents[2] / "clefs" / "migrations" / "0014_core_inbound_sender.sql"


def _row(cur, oid):
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (oid,))
    return dict(cur.fetchone())


def _msg(mid: str, *, frm: str = "971501112222", ts: int = 1790000000, **body) -> dict:
    m = {"from": frm, "id": mid, "timestamp": str(ts)}
    m.update(body)
    if "type" not in m:
        m.update(type="text", text={"body": "Hello"})
    return m


# `handlers` lives in tests/foundation/conftest.py (module handlers set aside).


@pytest.fixture
def hotel_number(cur, hotel_uae):
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = 'PNID-HOTEL' WHERE slug = %s", (hotel_uae.slug,))
    hotels.invalidate(hotel_uae.slug)
    return "PNID-HOTEL"


# ─── F3: monthly_at and nth_weekday_at through tick() ───


@pytest.fixture
def calendar_jobs():
    calls: dict[str, list] = {"monthly": [], "nth": []}

    @jobs.scheduled("core.test_ext_monthly", jobs.monthly_at(31, "06:00"))
    def _monthly(ctx: jobs.JobCtx) -> dict:
        calls["monthly"].append((ctx.hotel_slug, ctx.scheduled_for))
        return {"ok": True}

    @jobs.scheduled("core.test_ext_last_friday", jobs.nth_weekday_at(-1, 5, "10:00"))
    def _nth(ctx: jobs.JobCtx) -> dict:
        calls["nth"].append((ctx.hotel_slug, ctx.scheduled_for))
        return {"ok": True}

    yield calls
    jobs.unregister("core.test_ext_monthly")
    jobs.unregister("core.test_ext_last_friday")


def test_monthly_at_clamps_runs_once_and_catches_up_only_that_day(cur, hotel_uae, calendar_jobs, clock):
    mine = lambda: [c for c in calendar_jobs["monthly"] if c[0] == hotel_uae.slug]  # noqa: E731
    only = ["core.test_ext_monthly"]
    clock.freeze(datetime(2027, 2, 27, 23, 0, tzinfo=DUBAI))  # day 31 clamps to Feb 28
    jobs.tick(only=only)
    assert mine() == []
    clock.freeze(datetime(2027, 2, 28, 5, 59, tzinfo=DUBAI))
    jobs.tick(only=only)
    assert mine() == []
    clock.freeze(datetime(2027, 2, 28, 21, 30, tzinfo=DUBAI))  # every tick since 06:00 was missed
    jobs.tick(only=only)
    slot = datetime(2027, 2, 28, 6, 0, tzinfo=DUBAI).astimezone(UTC)
    assert mine() == [(hotel_uae.slug, slot)]
    clock.freeze(datetime(2027, 2, 28, 23, 59, tzinfo=DUBAI))
    jobs.tick(only=only)
    assert len(mine()) == 1  # claimed once
    clock.freeze(datetime(2027, 3, 1, 7, 0, tzinfo=DUBAI))
    jobs.tick(only=only)
    assert len(mine()) == 1  # never replayed the next day
    clock.freeze(datetime(2027, 3, 31, 6, 1, tzinfo=DUBAI))
    jobs.tick(only=only)
    assert mine()[-1] == (hotel_uae.slug, datetime(2027, 3, 31, 6, 0, tzinfo=DUBAI).astimezone(UTC))


def test_nth_weekday_at_last_friday_catches_up_the_same_day_only(cur, hotel_uae, calendar_jobs, clock):
    mine = lambda: [c for c in calendar_jobs["nth"] if c[0] == hotel_uae.slug]  # noqa: E731
    only = ["core.test_ext_last_friday"]
    clock.freeze(datetime(2026, 10, 23, 12, 0, tzinfo=DUBAI))  # a Friday, but not the last one
    jobs.tick(only=only)
    assert mine() == []
    clock.freeze(datetime(2026, 10, 30, 23, 45, tzinfo=DUBAI))  # the last Friday, late
    jobs.tick(only=only)
    assert mine() == [(hotel_uae.slug, datetime(2026, 10, 30, 10, 0, tzinfo=DUBAI).astimezone(UTC))]
    clock.freeze(datetime(2026, 10, 31, 0, 15, tzinfo=DUBAI))
    jobs.tick(only=only)
    assert len(mine()) == 1
    cur.execute("SELECT status FROM job_runs WHERE job = %s AND hotel_slug = %s", (only[0], hotel_uae.slug))
    assert [r["status"] for r in cur.fetchall()] == ["done"]


# ─── F4: guest dispatch order ───


def test_guest_priority_table_is_frozen():
    assert notify.GUEST_PRIORITY == {
        "guests.optout_consent": 5,
        "guests.journey_replies": 10,
        "inquiries.whatsapp_intake": 20,
        "concierge.whatsapp_chat": 30,
        "service.whatsapp_fallback": 90,
    }


def test_handled_false_passes_the_message_on(client, wa, cur, hotel_number, handlers):
    order = []

    class Result:
        def __init__(self, handled):
            self.handled = handled

    @handlers
    @notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["guests.journey_replies"])
    def journeys(ctx):
        order.append("journeys")
        return {"handled": False, "reason": "not a journey reply"}

    @handlers
    @notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["inquiries.whatsapp_intake"])
    def intake(ctx):
        order.append("intake")
        return Result(False)

    @handlers
    @notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["concierge.whatsapp_chat"])
    def concierge(ctx):
        order.append("concierge")
        return {}  # falsy: not handled

    @handlers
    @notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["service.whatsapp_fallback"])
    def fallback(ctx):
        order.append("fallback")
        return Result(True)

    wa.post(client, wa.message_payload([_msg("wamid.X1")], phone_number_id=hotel_number))
    assert order == ["journeys", "intake", "concierge", "fallback"]
    cur.execute("SELECT status, handled_by FROM inbound_messages WHERE provider_id = 'wamid.X1'")
    row = cur.fetchone()
    assert row["status"] == "handled" and row["handled_by"].endswith("fallback")


def test_a_module_guest_handler_off_its_slot_is_logged(caplog):
    def handler(ctx):
        return True

    handler.__module__ = "clefs.modules.concierge.whatsapp"
    with caplog.at_level(logging.WARNING, logger="clefs.notify.inbound"):
        notify.on_whatsapp("guest", priority=31)(handler)
    try:
        assert any("frozen slots for concierge are [30]" in r.getMessage() for r in caplog.records)
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="clefs.notify.inbound"):
            notify.on_whatsapp("guest", priority=30)(handler)
        assert not caplog.records
    finally:
        inbound.unregister_handler(handler)


# ─── F4: sender_hmac and the window lookup ───


def test_migration_0014_is_idempotent(cur):
    sql = MIGRATION.read_text()
    assert chr(0x2014) not in sql and "%" not in sql
    cur.execute(sql)
    cur.execute(sql)
    cur.execute(
        "SELECT data_type FROM information_schema.columns WHERE table_name = 'inbound_messages' AND column_name = 'sender_hmac'"
    )
    assert cur.fetchone()["data_type"] == "bytea"
    cur.execute("SELECT indexdef FROM pg_indexes WHERE indexname = 'inbound_messages_sender_hmac_idx'")
    assert "(hotel_slug, sender_hmac, received_at DESC)" in cur.fetchone()["indexdef"]


def test_inbound_rows_carry_the_sender_hmac_and_the_window_is_found_by_it(client, wa, cur, hotel_uae, hotel_number, clock, handlers):
    seen = []

    @handlers
    @notify.on_whatsapp("guest", priority=90)
    def capture_ctx(ctx):
        seen.append((ctx.sender_hmac, ctx.window_open_until, ctx.context_id, ctx.referral))
        return True

    ts = 1790000000
    at = datetime.fromtimestamp(ts, tz=UTC)
    clock.freeze(at + timedelta(minutes=5))
    referral = {"source_type": "ad", "source_id": "120", "ctwa_clid": "abc"}
    wa.post(client, wa.message_payload([_msg("wamid.H1", ts=ts, context={"id": "wamid.OUT1"}, referral=referral)],
                                       phone_number_id=hotel_number))
    h = privacy.contact_hmac(hotel_uae.slug, "phone", GUEST)
    cur.execute("SELECT sender_hmac FROM inbound_messages WHERE provider_id = 'wamid.H1'")
    assert bytes(cur.fetchone()["sender_hmac"]) == h
    assert seen == [(h, at + timedelta(hours=24), "wamid.OUT1", referral)]

    assert notify.wa_window_open_until(cur, hotel_uae.slug, sender_hmac=h) == at + timedelta(hours=24)
    assert notify.wa_window_open_until(cur, hotel_uae.slug, sender_hmac=privacy.contact_hmac(hotel_uae.slug, "phone", "+971509990000")) is None
    assert notify.wa_window_open_until(cur, "hotel-nandan", sender_hmac=h) is None  # the hash is per hotel
    assert notify.wa_window_open_until(cur, hotel_uae.slug, sender_hmac=b"") is None
    # A later message extends the window; after it closes the answer is None.
    wa.post(client, wa.message_payload([_msg("wamid.H2", ts=ts + 3600)], phone_number_id=hotel_number))
    assert notify.wa_window_open_until(cur, hotel_uae.slug, sender_hmac=h) == at + timedelta(hours=25)
    clock.freeze(at + timedelta(hours=25, seconds=1))
    assert notify.wa_window_open_until(cur, hotel_uae.slug, sender_hmac=h) is None


def test_a_sender_without_a_hotel_has_no_hmac(client, wa, cur):
    wa.post(client, wa.message_payload([_msg("wamid.U1", frm="971508887777")]))  # a stranger on the platform number
    cur.execute("SELECT audience, hotel_slug, sender_hmac FROM inbound_messages WHERE provider_id = 'wamid.U1'")
    assert dict(cur.fetchone()) == {"audience": "unknown", "hotel_slug": None, "sender_hmac": None}


# ─── F4: inbound email grammar ───


def test_inbound_address_round_trips_through_the_parser(env):
    env(INBOUND_EMAIL_DOMAIN=None)
    assert notify.inbound_domain() == "in.clefs.world"
    assert notify.inbound_address("Demo", "inquiries", 42) == "demo+inq-42@in.clefs.world"
    assert notify.inbound_address("demo", "inq", "q-7f") == "demo+inq-q-7f@in.clefs.world"
    assert notify.inbound_address("demo", "reviews") == "demo+reviews@in.clefs.world"
    assert notify.inbound_address("demo") == notify.inbound_address("demo", "ingest") == "demo@in.clefs.world"
    for purpose, ref in (("inquiries", 42), ("reviews", None), (None, None), ("inq", "q-7f-b")):
        alias, token, parsed_ref = inbound.parse_address(notify.inbound_address("demo-dubai", purpose, ref), "in.clefs.world")
        assert alias == "demo-dubai"
        assert notify.purpose_for_token(token) == {"inq": "inquiries", None: "ingest"}.get(purpose, purpose or "ingest")
        assert parsed_ref == (None if ref is None else str(ref))
    assert notify.purpose_for_token("inquiries") == "inquiries" and notify.purpose_for_token("custom") == "custom"
    for bad in (("de+mo",), ("demo", "in-q"), ("demo", "inq", "a b"), ("demo", None, "7"), ("demo", "inq", "x" * 60)):
        with pytest.raises(ValueError):
            notify.inbound_address(*bad)
    env(INBOUND_EMAIL_DOMAIN="In.Staging.Clefs.World")
    assert notify.inbound_address("demo", "reviews") == "demo+reviews@in.staging.clefs.world"


def test_hotel_inbound_address_needs_an_alias(cur, hotel_uae, env):
    env(INBOUND_EMAIL_DOMAIN=None)
    assert notify.hotel_inbound_address(cur, hotel_uae.slug, "inquiries", 9) is None
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))
    assert notify.hotel_inbound_address(cur, hotel_uae.slug, "inquiries", 9) == "testdubai+inq-9@in.clefs.world"


def _mail(to: str, message_id: str, *, cc: str | None = None) -> dict:
    return {
        "From": "guest@example.com",
        "FromFull": {"Email": "Guest@Example.com", "Name": "A Guest"},
        "To": to,
        "ToFull": [{"Email": to, "Name": ""}],
        "CcFull": [{"Email": cc, "Name": ""}] if cc else [],
        "Subject": "Group booking",
        "MessageID": message_id,
        "TextBody": "12 rooms",
        "Headers": [],
        "Attachments": [],
    }


def test_inbound_email_routes_only_the_inbound_domain_and_hashes_the_sender(client, cur, hotel_uae, env, handlers):
    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET, INBOUND_EMAIL_DOMAIN=None)
    cur.execute("UPDATE hotels SET inbound_email_alias = 'testdubai' WHERE slug = %s", (hotel_uae.slug,))
    got = []

    @handlers
    @notify.on_inbound_email("inquiries")
    def inquiry_mail(mail):
        got.append((mail.purpose, mail.ref, mail.sender_hmac))
        return True

    # Unset INBOUND_EMAIL_DOMAIN no longer means "any domain": a Cc to another
    # company's "testdubai@" mailbox must not route to this hotel.
    r = resend_mail.post(client, _mail("testdubai+inq@example.org", "e-1"))
    assert r.json()["status"] == "unrouted" and got == []
    r = resend_mail.post(client, _mail("someone@example.org", "e-2", cc="testdubai+inquiries-77@in.clefs.world"))
    assert r.json() == {"ok": True, "status": "handled", "purpose": "inquiries", "hotel_slug": hotel_uae.slug, "attachments": 0}
    h = privacy.contact_hmac(hotel_uae.slug, "email", "guest@example.com")
    assert got == [("inquiries", "77", h)]
    cur.execute("SELECT sender_hmac FROM inbound_messages WHERE provider_id = 'email:e-2'")
    assert bytes(cur.fetchone()["sender_hmac"]) == h


# ─── F4: send_email headers and the attempt-key convention ───


def test_send_email_headers_are_checked(cur, outbox, clock):
    clock.freeze(T0)
    r = notify.send_email(
        "guest@example.com", "Offer", "<p>x</p>", purpose="guests.journey.j8", category="marketing",
        headers={"List-Unsubscribe": "<https://clefs.world/u/abc>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
    )
    assert r.ok
    [sent] = notify.captured("email")
    assert sent["payload"]["Headers"] == [
        {"Name": "List-Unsubscribe", "Value": "<https://clefs.world/u/abc>"},
        {"Name": "List-Unsubscribe-Post", "Value": "List-Unsubscribe=One-Click"},
    ]
    assert sent["payload"]["MessageStream"] == "broadcast"
    for bad in ({"Bcc": "x@y.z"}, {"reply-to": "x@y.z"}, {"X-Evil": "a\r\nBcc: x@y.z"}, {"Bad Name": "v"}, {"X-Long": "v" * 999}):
        with pytest.raises(PayloadError):
            notify.send_email("guest@example.com", "s", "<p>x</p>", purpose="core.test", headers=bad)


def test_attempt_key_makes_a_retry_a_new_row(cur, outbox, clock):
    clock.freeze(T0)
    assert notify.attempt_key("gst:17", 2) == "gst:17:a2"
    for bad in (("", 1), ("gst:1", 0), ("gst:1", "2")):
        with pytest.raises(ValueError):
            notify.attempt_key(*bad)
    first = notify.send_whatsapp_template(GUEST, "gst_j1_confirm_v1", "en", ["Sam"], hotel_slug=None, purpose="guests.journey.j1",
                                          idempotency_key=notify.attempt_key("gst:17", 1))
    again = notify.send_whatsapp_template(GUEST, "gst_j1_confirm_v1", "en", ["Sam"], hotel_slug=None, purpose="guests.journey.j1",
                                          idempotency_key=notify.attempt_key("gst:17", 1))
    assert again.deduped and again.outbox_id == first.outbox_id
    clock.advance(seconds=10)
    retry = notify.send_whatsapp_template(GUEST, "gst_j1_confirm_v1", "en", ["Sam"], hotel_slug=None, purpose="guests.journey.j1",
                                          idempotency_key=notify.attempt_key("gst:17", 2))
    assert not retry.deduped and retry.outbox_id != first.outbox_id and len(notify.captured()) == 2


# ─── F4: pacing owned by core.notify.deliver ───


def test_six_second_spacing_holds_across_our_two_numbers(cur, outbox, hotel_number, hotel_uae, clock):
    clock.freeze(T0)
    a = notify.send_whatsapp_template(GUEST, "owner_brief_v1", "en", ["1"], hotel_slug=None, purpose="brief.daily", sender="platform")
    b = notify.send_whatsapp_template(GUEST, "gst_j1_confirm_v1", "en", ["Sam"], hotel_slug=hotel_uae.slug,
                                      purpose="guests.journey.j1", sender="hotel")
    assert (a.status, b.status) == ("sent", "queued")
    assert _row(cur, b.outbox_id)["not_before"] == T0 + timedelta(seconds=6)
    clock.advance(seconds=6)
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, b.outbox_id)["status"] == "sent" and notify.captured()[-1]["sender_id"] == hotel_number


def test_a_recipient_being_delivered_elsewhere_is_deferred_not_doubled(cur, outbox, clock):
    """Another worker holds the per-recipient lock (it is mid-send): this
    delivery must come back one spacing later instead of sending alongside."""
    clock.freeze(T0)
    other = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        with other.cursor() as oc:
            oc.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", (ob.PACE_LOCK_CLASS, GUEST))
        r = notify.send_whatsapp_template(GUEST, "owner_brief_v1", "en", ["1"], hotel_slug=None, purpose="brief.daily")
        assert r.ok and r.status == "queued" and notify.captured() == []
        row = _row(cur, r.outbox_id)
        assert row["not_before"] == T0 + ob.PAIR_SPACING and row["attempts"] == 0
        with other.cursor() as oc:
            oc.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", (ob.PACE_LOCK_CLASS, GUEST))
    finally:
        other.close()
    clock.advance(seconds=6)
    jobs.run_pending(only=["core.notify.deliver"])
    assert _row(cur, r.outbox_id)["status"] == "sent" and len(notify.captured()) == 1


# ─── F4: on_outbound_status beyond the webhook ───


def test_status_hooks_hear_send_time_failures_and_email_sends(cur, outbox, clock, handlers):
    clock.freeze(T0)
    seen = []

    @handlers
    @notify.on_outbound_status("guests.journey")
    def hook(cur_, row, status, code):
        seen.append((row["id"], row["channel"], status, code, dict(row.get("meta") or {})))

    ob.open_window(cur, "platform", GUEST, at=T0 - timedelta(hours=1))
    outbox.fail_next(131047, "re-engagement required")
    r = notify.send_whatsapp_text(GUEST, "Your room is ready", hotel_slug=None, purpose="guests.journey.j5",
                                  idempotency_key="gst:5:a1", fallback_template={"name": "gst_j5_daycheck_v1", "lang": "en", "params": ["Sam"]})
    assert r.status == "failed"
    fb = _row(cur, _row(cur, r.outbox_id)["meta"]["fallback_id"])
    assert fb["template_name"] == "gst_j5_daycheck_v1" and fb["fallback_of"] == r.outbox_id
    assert seen == [(r.outbox_id, "whatsapp", "failed", "131047", {"fallback_template": seen[0][4]["fallback_template"],
                                                                     "fallback_id": fb["id"]})]
    seen.clear()
    e = notify.send_email("guest@example.com", "Your stay", "<p>Hi</p>", hotel_slug=None, purpose="guests.journey.j7")
    assert e.status == "sent" and [(s[0], s[1], s[2], s[3]) for s in seen] == [(e.outbox_id, "email", "sent", None)]
    seen.clear()
    notify.send_email("gm@example.com", "Brief", "<p>Hi</p>", hotel_slug=None, purpose="brief.daily")
    assert seen == []  # another purpose


def test_a_failed_status_being_retried_says_so(client, wa, cur, outbox, clock, handlers):
    clock.freeze(T0)
    seen = []

    @handlers
    @notify.on_outbound_status("guests.")
    def hook(cur_, row, status, code):
        seen.append((row["id"], status, code, (row.get("meta") or {}).get("retried_by")))

    r = notify.send_whatsapp_template(GUEST, "gst_j1_confirm_v1", "en", ["Sam"], hotel_slug=None, purpose="guests.journey.j1",
                                      idempotency_key="gst:9:a1")
    wamid = notify.captured()[0]["provider_id"]
    wa.post(client, wa.status_payload([{"id": wamid, "status": "failed", "timestamp": "1790000000",
                                        "errors": [{"code": 131056, "title": "Pair rate limit"}]}]))
    cur.execute("SELECT id, status, fallback_of FROM outbound_messages WHERE idempotency_key = 'gst:9:a1:retry:1'")
    retry = dict(cur.fetchone())
    assert retry["status"] == "queued" and retry["fallback_of"] == r.outbox_id
    assert seen == [(r.outbox_id, "failed", "131056", retry["id"])]


# ─── F8: contact hmac, sealing, registry ───


def test_contact_hmac_defaults_a_national_phone_to_the_hotels_country(cur, hotel_uae):
    e164 = privacy.contact_hmac(hotel_uae.slug, "phone", "+971501234567")
    assert privacy.contact_hmac(hotel_uae.slug, "phone", "050 123 4567") == e164
    assert privacy.contact_hmac(hotel_uae.slug, "phone", "050-123-4567", cur=cur) == e164
    assert privacy.contact_ref(hotel_uae.slug, "phone", "0501234567", cur=cur) == e164.hex()
    # An explicit country code still wins over the hotel's.
    assert privacy.contact_hmac(hotel_uae.slug, "phone", "98200 12345", default_country_code="91") == privacy.contact_hmac(
        hotel_uae.slug, "phone", "+919820012345"
    )
    india = hotels.create_hotel(cur, slug="test-mumbai", name="Test Mumbai", country="IN", emirate=None, timezone="Asia/Kolkata",
                                currency="INR")
    assert privacy.hotel_calling_code(india.slug, cur=cur) == "91"
    assert privacy.contact_hmac(india.slug, "phone", "098200 12345", cur=cur) == privacy.contact_hmac(india.slug, "phone", "+919820012345")
    us = hotels.create_hotel(cur, slug="test-nyc", name="Test NYC", country="US", emirate=None, timezone="America/New_York",
                             currency="USD")
    assert privacy.hotel_calling_code(us.slug, cur=cur) is None  # NANP trunk rules are not guessed
    for slug in (us.slug, "no-such-hotel"):
        with pytest.raises(ClefsError) as exc:
            privacy.contact_hmac(slug, "phone", "212 555 0100", cur=cur)
        assert exc.value.code == "invalid_phone"
    assert privacy.contact_hmac("no-such-hotel", "email", " A@B.co ") == privacy.contact_hmac("no-such-hotel", "email", "a@b.co")


def test_international_input_never_looks_the_hotel_up(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("no hotel lookup for an E.164 number")

    monkeypatch.setattr(privacy, "hotel_calling_code", boom)
    assert len(privacy.contact_hmac("any", "phone", "+971501234567")) == 32
    assert len(privacy.contact_hmac("any", "phone", "00971501234567")) == 32


def test_sealed_values_carry_the_version_byte():
    blob = privacy.seal("guest@example.com")
    assert blob[0] == privacy.SEAL_VERSION == 1 and len(blob) == 1 + 12 + len("guest@example.com") + 16
    assert privacy.unseal(memoryview(blob)) == "guest@example.com"
    with pytest.raises(ValueError):
        privacy.unseal(bytes([2]) + blob[1:])  # an unknown version is refused, not guessed


def test_registry_covers_the_inbound_sender_hmac():
    spec = next(s for s in privacy.pii_registry() if (s.module, s.table) == ("core", "inbound_messages"))
    assert spec.subject_columns["sender_hmac"] == "hmac" and "sender_hmac" in spec.redact_columns
    assert "where" in inspect.signature(privacy.register_pii).parameters


# ─── F8: AI cost (Canon K3) and store=False ───


def test_cost_micros_prices_tokens_cache_and_web_search(env):
    # 800 fresh + 200 cached input, 500 output, 2 web searches on gemini-2.5-flash:
    # 800 x 0.30 + 200 x 0.03 + 500 x 2.50 + 2 x 14,000 = 29,496 micro-USD.
    assert ai.cost_micros("gemini-2.5-flash", tokens_in=1000, tokens_out=500, cached_tokens_in=200, web_search_calls=2) == 29_496
    assert ai.cost_micros("gemini-2.5-flash-preview-09-2025", tokens_in=1000, tokens_out=0) == 300  # dated preview -> base price
    assert ai.cost_micros("gemini-2.5-flash-lite", tokens_in=3, tokens_out=0) == 0  # 0.3 rounds half up to 0
    assert ai.cost_micros("gemini-2.5-flash-lite", tokens_in=5, tokens_out=0) == 1  # 0.5 rounds up
    assert ai.cost_micros("some-new-model", tokens_in=1000, tokens_out=1000) is None  # unpriced: NULL, not 0
    env(AI_PRICE_SOME_NEW_MODEL="1.00,3.00")
    assert ai.cost_micros("some-new-model", tokens_in=1000, tokens_out=1000) == 4_000
    env(AI_PRICE_GEMINI_2_5_FLASH="0.50,0.05,4.00")
    assert ai.cost_micros("gemini-2.5-flash", tokens_in=100, tokens_out=100, cached_tokens_in=100) == 405


def test_responses_raw_logs_its_cost_and_returns_it(cur, fake_ai, env):
    env(AI_MODEL_VISIBILITY_GEMINI="gemini-2.5-flash")
    body = {
        "id": "resp_9",
        "model": "gemini-2.5-flash-preview-09-2025",
        "output": [
            {"type": "web_search_call", "id": "ws_1", "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "text": "Hotel A", "annotations": []}]},
        ],
        "usage": {"input_tokens": 1200, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 300},
    }
    fake_ai.push(body)
    out = ai.responses_raw("visibility.gemini", input="best hotel in Deira", tools=[{"type": "web_search"}],
                           include=["web_search_call.action.sources"], model="gemini-2.5-flash", hotel_slug=None, timeout_s=15)
    expected = 1200 * 0.30 + 300 * 2.50 + 14_000  # 15,110 micro-USD
    assert out["id"] == "resp_9" and out["output"] == body["output"] and "clefs" not in body
    assert out["clefs"]["cost_micros"] == expected and out["clefs"]["cost_currency"] == "USD"
    req = fake_ai.calls[-1]
    assert (req.kind, req.store, req.timeout_s, req.include) == ("raw", False, 15, ["web_search_call.action.sources"])
    cur.execute("SELECT id, kind, status, tokens_in, tokens_out, cost_micros, cost_currency, response_id FROM ai_calls ORDER BY id DESC LIMIT 1")
    row = dict(cur.fetchone())
    assert row == {"id": out["clefs"]["ai_call_id"], "kind": "raw", "status": "ok", "tokens_in": 1200, "tokens_out": 300,
                   "cost_micros": expected, "cost_currency": "USD", "response_id": "resp_9"}


def test_every_call_is_costed_and_store_defaults_off(cur, fake_ai, caplog):
    fake_ai.push("hello")
    ai.text("core.test", system="s", input="x", model="gemini-2.5-flash-lite")  # FakeBackend reports 10 in / 10 out
    cur.execute("SELECT cost_micros, cost_currency FROM ai_calls ORDER BY id DESC LIMIT 1")
    assert dict(cur.fetchone()) == {"cost_micros": 5, "cost_currency": "USD"}  # 10 x 0.10 + 10 x 0.40 = 5
    for fn in (ai.structured, ai.text, ai.tool_call, ai.web_search, ai.responses_raw):
        p = inspect.signature(fn).parameters["store"]
        assert p.default is False and p.kind is inspect.Parameter.KEYWORD_ONLY, fn.__name__
    fake_ai.push("hello")
    with caplog.at_level(logging.WARNING, logger="clefs.ai"):
        ai.text("core.test", system="s", input="x", store=True)
    assert fake_ai.calls[-1].store is True and any("store=True" in r.getMessage() for r in caplog.records)
