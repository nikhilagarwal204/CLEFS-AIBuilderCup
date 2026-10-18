"""
Tap Payments invoices (04 spec 4.8; research inquiry-crm.md).

- Create: POST {TAP_API_BASE}/v2/invoices with due, expiry,
  notifications.channels and post.url.
- Webhook: the `hashstring` header, here HMAC-SHA256 of the raw body with
  the account's webhook secret (UNVERIFIED: Tap signs selected fields), then
  GET /v2/invoices/{id} decides.
- Fee: settings.

Secret: {secret_key, webhook_secret}.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from clefs.core import clock, money
from clefs.modules.inquiries.payments.base import (
    Account,
    PaymentEvent,
    PaymentLink,
    PaymentLinkRequest,
    ProviderError,
    header,
    http,
)

_STATUS = {"PAID": "paid", "EXPIRED": "expired", "CANCELLED": "cancelled", "FAILED": "failed", "REFUNDED": "refunded"}


def _base() -> str:
    from clefs.settings import get_settings

    return str(getattr(get_settings(), "TAP_API_BASE", None) or "https://api.tap.company").rstrip("/")


class Tap:
    key = "tap"

    def _headers(self, acct: Account) -> dict:
        return {"Authorization": f"Bearer {acct.secret.get('secret_key', '')}", "Content-Type": "application/json"}

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink:
        amount = money.money_str(req.amount_minor, req.currency)
        ms = int(req.expires_at.timestamp() * 1000)
        body = {
            "due": ms, "expiry": ms, "description": req.description, "mode": "INVOICE",
            "currencies": [req.currency], "notifications": {"channels": [], "dispatch": False},
            "customer": {"first_name": req.customer_name or "Guest", "email": req.customer_email or None},
            "order": {"amount": amount, "currency": req.currency,
                      "items": [{"amount": amount, "currency": req.currency, "description": req.description,
                                 "quantity": 1}]},
            "post": {"url": acct.config.get("post_url") or req.return_url}, "redirect": {"url": req.return_url},
            "reference": {"invoice": req.reference, "order": req.idempotency_key},
        }
        r = http("POST", f"{_base()}/v2/invoices", headers=self._headers(acct), json=body)
        got = r.json_body or {}
        if not got.get("id") or not got.get("url"):
            raise ProviderError("Tap returned no invoice", transient=False)
        return PaymentLink(provider_ref=got["id"], url=got["url"], expires_at=req.expires_at, raw=got)

    def cancel_link(self, acct: Account, provider_ref: str) -> None:
        http("DELETE", f"{_base()}/v2/invoices/{provider_ref}", headers=self._headers(acct))

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None:
        r = http("GET", f"{_base()}/v2/invoices/{provider_ref}", headers=self._headers(acct))
        got = r.json_body or {}
        kind = _STATUS.get(str(got.get("status") or "").upper())
        if kind is None:
            return None
        order = got.get("order") or {}
        ccy = str(order.get("currency") or got.get("currency") or "AED")
        return PaymentEvent(provider=self.key, provider_event_id=f"status:{provider_ref}:{kind}",
                            provider_ref=provider_ref, kind=kind,
                            amount_minor=money.to_minor(money.D(order.get("amount") or got.get("amount") or 0), ccy),
                            currency=ccy, occurred_at=clock.now())

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool:
        secret = acct.secret.get("webhook_secret")
        got = header(headers, "hashstring")
        if not secret or not got:
            return False
        want = hmac.new(str(secret).encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, got)

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]:
        body = json.loads(raw or b"{}")
        ref = body.get("id") if str(body.get("id", "")).startswith("inv_") else (body.get("invoice") or {}).get("id")
        if not ref:
            return []
        ccy = str(body.get("currency") or "AED")
        return [PaymentEvent(provider=self.key, provider_event_id=hashlib.sha256(raw).hexdigest(), provider_ref=ref,
                             kind=_STATUS.get(str(body.get("status") or "").upper(), "paid"),
                             amount_minor=money.to_minor(money.D(body.get("amount") or 0), ccy), currency=ccy,
                             occurred_at=clock.now())]
