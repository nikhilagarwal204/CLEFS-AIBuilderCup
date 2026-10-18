"""Payment links (04 spec 4.8): sending, the verified-webhook inbox,
fetch_status as the only state change, fees, partial payments, manual
payments, reconciliation and PAYMENT_LINK_EXPIRED."""

from __future__ import annotations

import pytest

from clefs.core.actions import SYSTEM
from clefs.core.errors import ClefsError
from clefs.modules.inquiries import store
from clefs.modules.inquiries.payments import service as pay
from clefs.modules.inquiries.payments.base import PaymentEvent
from tests.modules.inquiries.test_payments_support import connect, send_link, sent_quote, transport  # noqa: F401
from tests.modules.inquiries.test_quotes_support import NOW, demand, hotel, now  # noqa: F401

HDR = {"X-Webhook-Secret": "whsec"}


def body(ref: str, event_id: str = "evt-1") -> bytes:
    import json

    return json.dumps({"eventId": event_id, "eventName": "PURCHASED", "order": {"reference": ref}}).encode()


def link_of(cur, inquiry_id):
    return pay.list_links(cur, inquiry_id)[0]


@pytest.fixture
def linked(cur, hotel, demand, transport, run_jobs):
    acct = connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    send_link(cur, hotel, run_jobs, q)
    return acct, inq, q


def test_link_is_created_and_sent(cur, hotel, linked, outbox):
    _acct, inq, q = linked
    link = link_of(cur, inq["id"])
    assert (link["provider"], link["provider_ref"], link["status"]) == ("ngenius", "ORD-1", "sent")
    assert link["amount_minor"] == 50434 and link["includes_td_minor"] == 1000
    assert store.get_row(cur, inq["id"])["stage"] == "payment_pending"
    assert "https://pay.example/ORD-1" in str(outbox.captured("email")[-1])


def test_webhook_inbox_rules(cur, hotel, linked, transport, run_jobs):
    acct, inq, _q = linked
    key = acct["webhook_key"]
    assert pay.ingest_webhook(cur, "ngenius", "nope", HDR, body("ORD-1"))[0] == 404
    cur.execute("SELECT COUNT(*) AS n FROM inq_payment_events")
    assert cur.fetchone()["n"] == 0
    # Bad signature: stored as rejected, 401, nothing else.
    status, _ = pay.ingest_webhook(cur, "ngenius", key, {"X-Webhook-Secret": "forged"}, body("ORD-1"))
    assert status == 401
    cur.execute("SELECT reason, headers_redacted FROM inq_payment_events_rejected")
    rej = cur.fetchone()
    assert rej["reason"] == "bad_signature" and rej["headers_redacted"]["X-Webhook-Secret"] == "[redacted]"
    # A forged event carried the real id: the real one is still stored.
    assert pay.ingest_webhook(cur, "ngenius", key, HDR, body("ORD-1"))[1]["stored"] == 1
    # Duplicate and replay: stored once.
    assert pay.ingest_webhook(cur, "ngenius", key, HDR, body("ORD-1"))[1]["stored"] == 0
    # Nothing changed yet: only fetch_status moves a link.
    assert link_of(cur, inq["id"])["status"] == "sent"
    transport.state, transport.paid_minor = "STARTED", None
    run_jobs(["inquiries.payment_event_process"])
    assert link_of(cur, inq["id"])["status"] == "sent"  # the body said PURCHASED; the provider did not


def test_paid_after_fetch_status_with_settings_fee(cur, hotel, linked, transport, run_jobs):
    acct, inq, _q = linked
    transport.state, transport.paid_minor = "PURCHASED", 50434
    assert pay.ingest_webhook(cur, "ngenius", acct["webhook_key"], HDR, body("ORD-1", "evt-9"))[0] == 200
    run_jobs(["inquiries.payment_event_process"])
    link = link_of(cur, inq["id"])
    assert (link["status"], link["paid_amount_minor"], link["fee_minor"], link["fee_source"]) == (
        "paid", 50434, 1261, "settings")  # 2.5% of 504.34 = 12.61 (demo N-Genius setting)
    cur.execute("SELECT COUNT(*) AS n FROM job_queue WHERE name = 'inquiries.verify_inquiry'")
    assert cur.fetchone()["n"] == 1


def test_provider_fee_and_partial_payment(cur, hotel, linked):
    _acct, inq, _q = linked
    link = link_of(cur, inq["id"])
    ev = PaymentEvent(provider="ngenius", provider_event_id="x", provider_ref="ORD-1", kind="paid",
                      amount_minor=20000, currency="AED", occurred_at=NOW, fee_minor=777)
    assert pay.apply_status(cur, hotel, link, ev, config={}) is True
    link = link_of(cur, inq["id"])
    assert (link["status"], link["fee_minor"], link["fee_source"]) == ("partially_paid", 777, "provider")
    # A paid link never moves back to failed.
    back = PaymentEvent(**{**ev.model_dump(), "kind": "failed"})
    assert pay.apply_status(cur, hotel, link, back, config={}) is False


def test_reconcile_polls_and_expires(cur, hotel, linked, transport, run_jobs, now):
    _acct, inq, _q = linked
    transport.state = "STARTED"
    assert pay.reconcile(cur, hotel)["polled"] == 1
    now.advance(hours=49)
    out = pay.reconcile(cur, hotel)
    assert out["expired"] == 1 and link_of(cur, inq["id"])["status"] == "expired"
    row = store.get_row(cur, inq["id"])
    assert (row["stage"], row["lost_reason"]) == ("lost", "PAYMENT_LINK_EXPIRED")


def test_manual_mark_paid_only_for_manual(cur, hotel, linked, run_jobs):
    from clefs import files

    _acct, inq, q = linked
    with pytest.raises(ClefsError) as e:
        pay.mark_paid_manual(cur, hotel, link_of(cur, inq["id"])["id"], evidence_file_id="x", actor=SYSTEM)
    assert e.value.code == "not_manual"
    send_link(cur, hotel, run_jobs, q, purpose="balance", provider="manual", url="https://bank.example/pay")
    manual = [lk for lk in pay.list_links(cur, inq["id"]) if lk["provider"] == "manual"][0]
    assert manual["status"] == "sent" and manual["url"] == "https://bank.example/pay"
    with pytest.raises(ClefsError) as e:
        pay.mark_paid_manual(cur, hotel, manual["id"], evidence_file_id="00000000-0000-0000-0000-000000000000",
                             actor=SYSTEM)
    assert e.value.code == "evidence_required"
    ref = files.put_file(cur, hotel.slug, b"transfer slip", filename="slip.pdf", content_type="application/pdf",
                         purpose="inquiries.payment_evidence")
    paid = pay.mark_paid_manual(cur, hotel, manual["id"], evidence_file_id=ref.id, actor=SYSTEM)
    assert (paid["status"], paid["fee_minor"], paid["paid_amount_minor"]) == ("paid", 0, manual["amount_minor"])


def test_cancel_link(cur, hotel, linked):
    _acct, inq, _q = linked
    out = pay.cancel(cur, hotel, link_of(cur, inq["id"])["id"], actor=SYSTEM)
    assert out["status"] == "cancelled"
    with pytest.raises(ClefsError):
        pay.cancel(cur, hotel, out["id"], actor=SYSTEM)


def test_payment_payloads_are_emptied_at_365_days(cur, hotel, linked, now):
    from clefs.modules.inquiries.jobs import purge_payment_events

    acct, _inq, _q = linked
    pay.ingest_webhook(cur, "ngenius", acct["webhook_key"], HDR, body("ORD-1"))
    assert purge_payment_events(cur, hotel.slug) == 0
    now.advance(days=366)
    assert purge_payment_events(cur, hotel.slug) == 1
    cur.execute("SELECT payload, provider_event_id FROM inq_payment_events")
    row = cur.fetchone()
    assert row["payload"] == {} and row["provider_event_id"] == "evt-1"


# ─── Pay URL safety (the Pay button href on the public quote page) ───

BAD_URLS = ["javascript:alert(document.domain)", " javascript:alert(1)", "JaVaScRiPt:alert(1)",
            "data:text/html,<script>alert(1)</script>", "http://bank.example/pay", "https://", "//evil.example/x",
            "https://bank.example/pay\nx", "https://bank.example/\x00", "vbscript:msgbox(1)", ""]


def test_is_safe_pay_url_accepts_only_https_with_a_host():
    from clefs.modules.inquiries.payments.base import is_safe_pay_url

    assert is_safe_pay_url("https://bank.example/pay?ref=Q-1#x")
    assert not any(is_safe_pay_url(u) for u in BAD_URLS)
    assert not is_safe_pay_url(None) and not is_safe_pay_url("https://a.example/" + "x" * 2000)


@pytest.mark.parametrize("url", ["javascript:alert(document.domain)", "data:text/html,x", "http://bank.example/pay"])
def test_request_link_rejects_an_unsafe_pasted_url(cur, hotel, linked, url):
    _acct, _inq, q = linked
    with pytest.raises(ClefsError) as e:
        pay.request_link(cur, hotel, q, purpose="balance", amount_minor=None, actor=SYSTEM, via="system",
                         provider="manual", url=url)
    assert (e.value.status, e.value.code) == (400, "invalid_payment_url")


def test_manual_adapter_rechecks_the_url():
    from datetime import timedelta

    from clefs.modules.inquiries.payments.base import Account, PaymentLinkRequest
    from clefs.modules.inquiries.payments.manual import Manual

    req = PaymentLinkRequest(amount_minor=100, currency="AED", description="d", reference="r",
                             expires_at=NOW + timedelta(days=1), return_url="https://www.clefs.world/q/t",
                             idempotency_key="k")
    acct = Account(id=0, hotel_slug="h", kind="manual", config={"url": "javascript:alert(1)"})
    with pytest.raises(ClefsError) as e:
        Manual().create_link(acct, req)
    assert e.value.code == "invalid_payment_url"
    acct = Account(id=0, hotel_slug="h", kind="manual", config={"url": "https://bank.example/pay"})
    assert Manual().create_link(acct, req).url == "https://bank.example/pay"


def test_open_link_url_never_returns_an_unsafe_stored_url(cur, hotel, linked, run_jobs):
    from clefs.modules.inquiries import quotes

    _acct, inq, q = linked
    token = quotes.public_token(quotes.quote_by_id(cur, q["id"]))
    link = link_of(cur, inq["id"])
    assert pay.open_link_url(cur, token) == link["url"] and link["url"].startswith("https://")
    # A row written before the check existed (or an odd provider response).
    cur.execute("UPDATE inq_payment_links SET url = 'javascript:alert(1)' WHERE id = %s", (link["id"],))
    assert pay.open_link_url(cur, token) is None
