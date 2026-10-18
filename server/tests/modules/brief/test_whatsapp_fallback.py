"""Delivery fallback: no DELIVERED within 30 minutes, or a failure, sends the
email brief (with a magic link) and, for urgent money, an SMS without a URL.
Also Meta's 131056 pair-rate backoff and the status-webhook trigger."""

from __future__ import annotations

import pytest

from clefs import db, notify
from clefs.modules.brief import api as brief_api
from clefs.modules.brief import fallback

from .test_whatsapp_flow import OWNER_PHONE, PLATFORM, _only_owner, env, owner_p, wa_ready  # noqa: F401


@pytest.fixture
def sent(cur, hotel_uae, frozen, wa_ready, owner_p, sig):  # noqa: F811
    """A WhatsApp brief with one urgent item (due in 10 h, AED 4,120)."""
    _only_owner(cur, hotel_uae, owner_p)
    sig("leak", section="money_lost", aed=4120, hours=10, title="VCC not charged")
    out = brief_api.push_now(cur, hotel_uae.slug)
    assert out["sent_to"][0]["channel"] == "whatsapp"
    cur.execute("SELECT * FROM brf_deliveries WHERE run_id = %s", (out["brief_id"],))
    return dict(cur.fetchone())


def _emails() -> list[dict]:
    return db.fetch_all("SELECT payload, purpose FROM outbound_messages WHERE channel = 'email' ORDER BY id")


def _sms() -> list[dict]:
    return db.fetch_all("SELECT payload, purpose FROM outbound_messages WHERE channel = 'sms' ORDER BY id")


def _status(cur, delivery_id: int) -> str:
    cur.execute("SELECT status FROM brf_deliveries WHERE id = %s", (delivery_id,))
    return cur.fetchone()["status"]


def test_not_delivered_in_30_minutes_falls_back_to_email_and_sms(cur, sent, frozen):
    assert fallback.process(cur, "test-dubai")["waiting"] == 1
    frozen.advance(minutes=31)
    assert fallback.process(cur, "test-dubai")["fell_back"] == 1
    assert _status(cur, sent["id"]) == "fell_back"
    mails = [m for m in _emails() if m["purpose"] == "brief.fallback"]
    assert len(mails) == 1 and "/auth/magic/" in str(mails[0]["payload"])
    sms = [m for m in _sms() if m["purpose"] == "brief.fallback_sms"]
    assert len(sms) == 1
    text = str(sms[0]["payload"])
    assert "1 urgent item, AED 4,120 at stake" in text and "http" not in text and "clefs.world" not in text
    cur.execute("SELECT delivery FROM brf_runs WHERE id = %s", (sent["run_id"],))
    assert any(d.get("fallback_of") == "whatsapp" for d in cur.fetchone()["delivery"])
    # resolved once: a later sweep sends nothing more
    assert fallback.process(cur, "test-dubai") == {"delivered": 0, "fell_back": 0, "waiting": 0}


def test_delivered_brief_needs_no_fallback(cur, sent, frozen):
    cur.execute("UPDATE outbound_messages SET status = 'delivered' WHERE id = %s", (sent["outbox_id"],))
    frozen.advance(minutes=45)
    assert fallback.process(cur, "test-dubai")["delivered"] == 1
    assert _status(cur, sent["id"]) == "delivered" and not [m for m in _emails() if m["purpose"] == "brief.fallback"]


def test_send_failure_falls_back_without_waiting(cur, sent):
    cur.execute("UPDATE outbound_messages SET status = 'failed' WHERE id = %s", (sent["outbox_id"],))
    assert fallback.process(cur, "test-dubai")["fell_back"] == 1
    cur.execute("SELECT reason FROM brf_deliveries WHERE id = %s", (sent["id"],))
    assert cur.fetchone()["reason"] == "failed"


def test_sms_only_for_urgent_money(cur, hotel_uae, frozen, wa_ready, owner_p, sig):  # noqa: F811
    _only_owner(cur, hotel_uae, owner_p)
    sig("slow", section="money_lost", aed=4120, hours=24 * 10, title="Commission check")  # U < 1
    brief_api.push_now(cur, hotel_uae.slug)
    frozen.advance(minutes=31)
    fallback.process(cur, "test-dubai")
    assert [m for m in _emails() if m["purpose"] == "brief.fallback"] and not _sms()


def test_meta_status_failure_triggers_the_fallback_at_once(cur, sent, env):  # noqa: F811
    env(WA_APP_SECRET="s3cret")
    wamid = db.fetch_one("SELECT provider_id FROM outbound_messages WHERE id = %s", (sent["outbox_id"],))["provider_id"]
    assert wamid
    from clefs.core import clock

    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PLATFORM},
        "statuses": [{"id": wamid, "status": "failed", "timestamp": str(int(clock.now().timestamp())),
                      "recipient_id": OWNER_PHONE.lstrip("+"), "errors": [{"code": 131026, "title": "undeliverable"}]}],
    }}]}]}
    notify.process_whatsapp(body)
    assert _status(cur, sent["id"]) == "fell_back"


def test_meta_status_delivered_resolves_the_ledger(cur, sent):
    from clefs.core import clock

    wamid = db.fetch_one("SELECT provider_id FROM outbound_messages WHERE id = %s", (sent["outbox_id"],))["provider_id"]
    body = {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": PLATFORM},
        "statuses": [{"id": wamid, "status": "delivered", "timestamp": str(int(clock.now().timestamp())),
                      "recipient_id": OWNER_PHONE.lstrip("+")}],
    }}]}]}
    notify.process_whatsapp(body)
    assert _status(cur, sent["id"]) == "delivered"


def test_pair_rate_limit_backs_off_and_stays_pending(cur, hotel_uae, frozen, wa_ready, owner_p, sig):  # noqa: F811
    _only_owner(cur, hotel_uae, owner_p)
    sig("leak", section="money_lost", aed=900, hours=10)
    wa_ready.fail_next(131056, channel="whatsapp")
    brief_api.push_now(cur, hotel_uae.slug)
    cur.execute("SELECT * FROM brf_deliveries")
    d = cur.fetchone()
    assert fallback.chain_state(cur, d["outbox_id"]) == "in_flight"
    # notify owns the backoff (6 s x 2^n): the send waits, in the row itself
    # or in a retry row that points back at it; either way it is in flight.
    rows = db.fetch_all("SELECT id, status, not_before, attempts, fallback_of FROM outbound_messages "
                        "WHERE channel = 'whatsapp' ORDER BY id")
    waiting = [r for r in rows if r["status"] == "queued" and r["not_before"] and r["not_before"] > frozen.now()]
    assert waiting and waiting[0]["id"] in (d["outbox_id"], *[r["id"] for r in rows if r["fallback_of"] == d["outbox_id"]])
    assert fallback.process(cur, "test-dubai")["waiting"] == 1
