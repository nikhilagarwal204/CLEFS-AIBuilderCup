"""Payment test support (no tests here): a fake provider transport and a
helper that takes an inquiry to a sent quote with an N-Genius account."""

from __future__ import annotations

import pytest

from clefs.core.actions import SYSTEM
from clefs.modules.inquiries.payments.base import Response
from tests.modules.inquiries.test_quotes_support import make_inquiry

RUN = ["core.actions.execute", "core.notify.deliver"]


class FakeTransport:
    """Answers provider HTTP calls by URL; `state` is what fetch_status sees
    (N-Genius order state), `calls` what was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None, dict | None]] = []
        self.state = "STARTED"
        self.paid_minor: int | None = None
        self.n = 0

    def __call__(self, method, url, *, headers, json=None, data=None, timeout=20):
        self.calls.append((method, url, json, data))
        if url.endswith("/identity/auth/access-token"):
            return Response(status=200, json_body={"access_token": "tok"})
        if "/invoices/outlets/" in url:
            self.n += 1
            return Response(status=201, json_body={"orderReference": f"ORD-{self.n}",
                                                   "_links": {"payment": {"href": f"https://pay.example/ORD-{self.n}"}}})
        if "/transactions/outlets/" in url:
            amount = {"value": self.paid_minor or 0, "currencyCode": "AED"}
            return Response(status=200, json_body={"state": self.state, "amount": amount})
        if "api.stripe.com/v1/payment_links" in url and method == "POST" and url.endswith("payment_links"):
            return Response(status=200, json_body={"id": "plink_1", "url": "https://buy.stripe.com/x"})
        if "/v1/checkout/sessions" in url:
            data_ = [{"id": "cs_1", "payment_status": "paid", "amount_total": self.paid_minor, "currency": "aed",
                      "created": 1790000000}] if self.state == "PURCHASED" else []
            return Response(status=200, json_body={"data": data_})
        return Response(status=404, json_body={})


@pytest.fixture
def transport():
    from clefs.modules.inquiries.payments import set_transport

    t = FakeTransport()
    set_transport(t)
    yield t
    set_transport(None)


def connect(cur, slug, provider="ngenius", **config):
    from clefs.modules.inquiries.payments import service as pay

    secret = {"api_key": "k", "webhook_secret": "whsec", "secret_key": "sk"}
    return pay.connect(cur, slug, provider=provider, config={"outlet_ref": "OUT-1", **config}, secret=secret,
                       actor=SYSTEM)


def sent_quote(cur, hotel, run_jobs, **inquiry_kw):
    """A FIT inquiry with its v1 quote sent (dial auto_within_limits)."""
    from clefs.core import actions
    from clefs.modules.inquiries import quotes

    actions.set_autonomy(cur, hotel.slug, "inquiries.send_quote", level="auto_within_limits", actor=SYSTEM)
    inq = make_inquiry(cur, hotel, **inquiry_kw)
    q = quotes.build(cur, hotel, inq)
    quotes.request_send(cur, hotel, q, actor=SYSTEM, via="system")
    run_jobs(RUN)
    return inq, quotes.quote_by_id(cur, q["id"])


def send_link(cur, hotel, run_jobs, q, **kw):
    """Request and approve the first-deposit link, then run the executor."""
    from clefs.core import actions
    from clefs.modules.inquiries.payments import service as pay

    # A system "approval" is not a human one (the executor revokes it), so the
    # helper lets the dial approve: the quote met the auto-send rule.
    actions.set_autonomy(cur, hotel.slug, "inquiries.send_payment_link", level="auto_within_limits", actor=SYSTEM)
    act = pay.request_link(cur, hotel, q, purpose=kw.pop("purpose", "deposit"), amount_minor=kw.pop("amount_minor", None),
                           actor=SYSTEM, via="system", **kw)
    assert act["status"] == "approved", act
    run_jobs(RUN)
    return act


# ─── Verification helpers ───


def reservation(cur, hotel, *, pms="R1", channel="DIRECT_WEB", name="Sara Khan", check_in=None, nights=2,
                rate_aed=400, booked_at=None, status="CONFIRMED", phone="+971501234567", email=None, rooms=1,
                room_type="STD") -> dict:
    """A PMS reservation with nightly rates (so its tax breakdown equals
    the quote's) and its contacts."""
    from datetime import timedelta

    from clefs.core.stays import upsert_reservation
    from tests.modules.inquiries.test_quotes_support import CHECK_IN, NOW

    ci = check_in or CHECK_IN
    nights_rows = [{"stay_date": ci + timedelta(days=i), "rate_minor": rate_aed * 100, "rooms": rooms,
                    "room_type_code": room_type} for i in range(nights)]
    contacts = [c for c in ({"kind": "phone", "value": phone} if phone else None,
                            {"kind": "email", "value": email} if email else None) if c]
    gross = {400: 100868}.get(rate_aed) if nights == 2 and rooms == 1 else None
    out = upsert_reservation(cur, hotel.slug, {
        "pms_res_id": pms, "channel_code": channel, "guest_name": name, "booked_at": booked_at or NOW + timedelta(hours=2),
        "check_in": ci, "check_out": ci + timedelta(days=nights), "room_type_code": room_type, "status": status,
        "currency": "AED", "gross_minor": gross, "rooms_count": rooms,
    }, nights=nights_rows, contacts=contacts or None)
    cur.execute("SELECT * FROM reservations WHERE id = %s", (out["id"],))
    return dict(cur.fetchone())


class FakeProfitGuard:
    """profit_guard.api stand-in: Booking 15% on room + SC + VAT (Canon K2)."""

    def __init__(self) -> None:
        self.leaks: list[dict] = []

    def reference_ota(self, cur, hotel_slug, *, as_of):
        from decimal import Decimal

        return ("BOOKING", Decimal("15"))

    def expected_commission(self, cur, hotel_slug, *, ota_code, breakdown, on):
        from decimal import ROUND_HALF_UP, Decimal

        base = breakdown.room + breakdown.sc + breakdown.vat
        c = int((Decimal(base) * Decimal("0.15")).quantize(Decimal(1), ROUND_HALF_UP))
        return {"commission_minor": c, "base_minor": base, "pct": Decimal("15"), "currency": "AED", "basis": "default"}

    def record_ota_leak(self, cur, hotel_slug, **kw):
        self.leaks.append(kw)
        return None  # Profit Guard has no statement line: Inquiry Guard keeps its own expected commission


@pytest.fixture
def pg(monkeypatch):
    from clefs.modules.profit_guard import api as pg_api

    fake = FakeProfitGuard()
    for name in ("reference_ota", "expected_commission", "record_ota_leak"):
        monkeypatch.setattr(pg_api, name, getattr(fake, name))
    return fake


def pay_link(cur, hotel, inquiry_id, *, amount=None, fee=None):
    """Mark the inquiry's newest open link paid as the provider would."""
    from clefs.modules.inquiries.payments import service as pay
    from clefs.modules.inquiries.payments.base import PaymentEvent
    from tests.modules.inquiries.test_quotes_support import NOW

    link = [lk for lk in pay.list_links(cur, inquiry_id) if lk["status"] == "sent"][0]
    ev = PaymentEvent(provider=link["provider"], provider_event_id=f"t{link['id']}", provider_ref=link["provider_ref"],
                      kind="paid", amount_minor=amount or link["amount_minor"], currency="AED", occurred_at=NOW,
                      fee_minor=fee)
    assert pay.apply_status(cur, hotel, link, ev, config={})
    return link
