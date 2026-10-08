"""Provider adapters (04 spec 4.8): webhook signatures (Stripe 5-minute
tolerance, Tap hashstring, N-Genius header secret, Telr hint-only advice),
parse mapping, create payloads, and the HTTP webhook route."""

from __future__ import annotations

import hashlib
import hmac
import json

from clefs.core import clock
from clefs.modules.inquiries.payments import PROVIDERS
from clefs.modules.inquiries.payments.base import Account, PaymentLinkRequest
from tests.modules.inquiries.test_payments_support import connect, send_link, sent_quote, transport  # noqa: F401
from tests.modules.inquiries.test_quotes_support import NOW, demand, hotel, now  # noqa: F401


def acct(kind: str, **secret) -> Account:
    return Account(id=1, hotel_slug="x", kind=f"pay_{kind}", config={"outlet_ref": "OUT"}, secret=secret)


def stripe_sig(secret: str, raw: bytes, t: int) -> str:
    mac = hmac.new(secret.encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def test_stripe_signature_and_tolerance(now):
    a = acct("stripe", webhook_secret="whsec_1")
    raw = json.dumps({"id": "evt_1", "type": "checkout.session.completed",
                      "data": {"object": {"payment_link": "plink_1", "payment_status": "paid",
                                          "amount_total": 50434, "currency": "aed"}}}).encode()
    t = int(clock.now().timestamp())
    s = PROVIDERS["stripe"]
    assert s.verify_webhook(a, {"stripe-signature": stripe_sig("whsec_1", raw, t)}, raw)
    assert not s.verify_webhook(a, {"Stripe-Signature": stripe_sig("other", raw, t)}, raw)
    assert not s.verify_webhook(a, {"Stripe-Signature": stripe_sig("whsec_1", raw, t - 301)}, raw)
    assert not s.verify_webhook(a, {}, raw)
    ev = s.parse_webhook(a, {}, raw)[0]
    assert (ev.provider_event_id, ev.provider_ref, ev.kind, ev.amount_minor, ev.currency) == (
        "evt_1", "plink_1", "paid", 50434, "AED")


def test_tap_hashstring():
    a = acct("tap", webhook_secret="tapsec")
    raw = json.dumps({"id": "inv_1", "status": "PAID", "amount": "504.34", "currency": "AED"}).encode()
    good = hmac.new(b"tapsec", raw, hashlib.sha256).hexdigest()
    t = PROVIDERS["tap"]
    assert t.verify_webhook(a, {"hashstring": good}, raw)
    assert not t.verify_webhook(a, {"hashstring": "0" * 64}, raw)
    ev = t.parse_webhook(a, {}, raw)[0]
    assert (ev.provider_ref, ev.kind, ev.amount_minor) == ("inv_1", "paid", 50434)
    assert ev.provider_event_id == hashlib.sha256(raw).hexdigest()  # no id: the body's sha256


def test_ngenius_header_secret_and_telr_hint():
    raw = b'{"eventName": "PURCHASED", "order": {"reference": "ORD-1"}}'
    n = PROVIDERS["ngenius"]
    assert n.verify_webhook(acct("ngenius", webhook_secret="s"), {"x-webhook-secret": "s"}, raw)
    assert not n.verify_webhook(acct("ngenius", webhook_secret="s"), {"x-webhook-secret": "t"}, raw)
    assert not n.verify_webhook(acct("ngenius"), {"x-webhook-secret": ""}, raw)
    telr = PROVIDERS["telr"]
    advice = b"tran_ref=1&order_ref=TR-9&tran_status=A&tran_amount=504.34&tran_currency=AED"
    assert telr.verify_webhook(acct("telr"), {}, advice)  # a hint: nothing changes until `check`
    assert not telr.verify_webhook(acct("telr", advice_secret="a"), {"X-Telr-Secret": "b"}, advice)
    ev = telr.parse_webhook(acct("telr"), {}, advice)[0]
    assert (ev.provider_ref, ev.amount_minor) == ("TR-9", 50434)


def test_ngenius_create_payload(transport):
    req = PaymentLinkRequest(amount_minor=50434, currency="AED", description="Hotel ABC-v1", reference="ABC-v1-1",
                             expires_at=NOW, customer_name="Sara Khan", customer_email="g@example.com",
                             return_url="https://www.clefs.world/q/t", idempotency_key="k1")
    link = PROVIDERS["ngenius"].create_link(acct("ngenius", api_key="k"), req)
    assert link.provider_ref == "ORD-1" and link.url == "https://pay.example/ORD-1"
    method, url, body, _ = transport.calls[-1]
    assert url.endswith("/invoices/outlets/OUT/invoice")
    assert body["total"] == {"currencyCode": "AED", "value": 50434} and body["skipInvoiceCreatedEmailNotification"]
    assert body["invoiceExpiryDate"] == NOW.date().isoformat() and body["paymentAttempts"] == 3


def test_webhook_route_over_http(cur, client, hotel, demand, transport, run_jobs):
    acct_row = connect(cur, hotel.slug)
    inq, q = sent_quote(cur, hotel, run_jobs)
    send_link(cur, hotel, run_jobs, q)
    path = acct_row["webhook_path"]
    raw = json.dumps({"eventId": "e-1", "order": {"reference": "ORD-1"}}).encode()
    r = client.post(path, content=raw, headers={"X-Webhook-Secret": "whsec"})
    assert r.status_code == 200 and r.json() == {"ok": True, "stored": 1}
    r = client.post(path, content=raw, headers={"X-Webhook-Secret": "bad"})
    assert r.status_code == 401 and r.json()["code"] == "bad_signature"
    assert client.post("/webhooks/inquiries/payments/ngenius/unknown", content=raw).status_code == 404


# ─── N-Genius api_base allowlist (SSRF) ───


def test_ngenius_api_base_allowlist(transport):
    """A manager-supplied api_base is fetched server-side, so only https
    *.ngenius-payments.com gateways pass; anything else is refused at
    connect and ignored (default gateway) on an old row."""
    import pytest

    from clefs.core.errors import ClefsError
    from clefs.modules.inquiries.payments.ngenius import DEFAULT_BASE, clean_api_base, safe_api_base

    assert safe_api_base("https://api-gateway.sandbox.ngenius-payments.com/") == \
        "https://api-gateway.sandbox.ngenius-payments.com"
    for bad in ("http://api-gateway.ngenius-payments.com", "https://169.254.169.254", "https://localhost:8000",
                "https://ngenius-payments.com.evil.io", "https://api-gateway.ngenius-payments.com@10.0.0.1",
                "https://api-gateway.ngenius-payments.com:8443", "https://evilngenius-payments.com",
                "file:///etc/passwd", 42):
        assert safe_api_base(bad) is None, bad
        with pytest.raises(ClefsError) as exc:
            clean_api_base(bad)
        assert exc.value.code == "invalid_api_base"
    assert clean_api_base("") is None and clean_api_base(None) is None
    n = PROVIDERS["ngenius"]
    old = Account(id=1, hotel_slug="x", kind="pay_ngenius", config={"outlet_ref": "OUT", "api_base": "http://10.0.0.5"},
                  secret={"api_key": "k"})
    assert n._base(old) == DEFAULT_BASE


def test_connect_rejects_internal_api_base(cur, hotel):
    import pytest

    from clefs.core.errors import ClefsError

    with pytest.raises(ClefsError) as exc:
        connect(cur, hotel.slug, api_base="http://127.0.0.1:5432")
    assert exc.value.code == "invalid_api_base"
    ok = connect(cur, hotel.slug, api_base="https://api-gateway.sandbox.ngenius-payments.com")
    assert ok["config"]["api_base"] == "https://api-gateway.sandbox.ngenius-payments.com"
    # Other providers never fetch api_base: it is dropped, not stored.
    assert "api_base" not in connect(cur, hotel.slug, provider="stripe", api_base="https://x.example")["config"]
