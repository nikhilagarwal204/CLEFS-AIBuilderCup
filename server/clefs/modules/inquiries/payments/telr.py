"""
Telr hosted payment page (04 spec 4.8). The hosted payment-link API is
UNVERIFIED (research inquiry-crm.md has pricing only); this follows Telr's
public order.json gateway: ivp_method create / check.

- Status is POLLED (`check`); the advice URL call is only a hint that makes
  us poll sooner, so an unsigned advice is accepted but never trusted.
  When the account has an `advice_secret`, the advice must carry it
  (header X-Telr-Secret) or it is rejected.
- Fee: settings default 2.25% + AED 0.50 (Medium plan, inquiry-crm.md).

Config: {store_id, test_mode?}. Secret: {auth_key, advice_secret?}.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from urllib.parse import parse_qsl

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

# order.status.code: 1 pending, 2 authorised, 3 paid, -1 expired, -2 cancelled, -3 declined (UNVERIFIED)
_CODES = {3: "paid", -1: "expired", -2: "cancelled", -3: "failed"}


def _base() -> str:
    from clefs.settings import get_settings

    return str(getattr(get_settings(), "TELR_API_BASE", None) or "https://secure.telr.com").rstrip("/")


class Telr:
    key = "telr"

    def _auth(self, acct: Account) -> dict:
        return {"ivp_store": str(acct.config.get("store_id") or ""), "ivp_authkey": acct.secret.get("auth_key", "")}

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink:
        form = {
            **self._auth(acct), "ivp_method": "create", "ivp_test": "1" if acct.config.get("test_mode") else "0",
            "ivp_amount": money.money_str(req.amount_minor, req.currency), "ivp_currency": req.currency,
            "ivp_cart": req.reference, "ivp_desc": req.description[:60], "ivp_lang": req.language,
            "return_auth": req.return_url, "return_decl": req.return_url, "return_can": req.return_url,
            "bill_email": req.customer_email or "",
        }
        r = http("POST", f"{_base()}/gateway/order.json", data=form)
        order = (r.json_body or {}).get("order") or {}
        if not order.get("ref") or not order.get("url"):
            raise ProviderError("Telr returned no order", transient=False)
        return PaymentLink(provider_ref=order["ref"], url=order["url"], expires_at=req.expires_at, raw=r.json_body)

    def cancel_link(self, acct: Account, provider_ref: str) -> None:
        return None  # UNVERIFIED: no cancel call; the order lapses and our row is cancelled

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None:
        r = http("POST", f"{_base()}/gateway/order.json", data={**self._auth(acct), "ivp_method": "check",
                                                                "order_ref": provider_ref})
        order = (r.json_body or {}).get("order") or {}
        code = int(((order.get("status") or {}).get("code")) or 0)
        kind = _CODES.get(code)
        if kind is None:
            return None
        ccy = str(order.get("currency") or acct.config.get("currency") or "AED")
        return PaymentEvent(provider=self.key, provider_event_id=f"status:{provider_ref}:{code}",
                            provider_ref=provider_ref, kind=kind,
                            amount_minor=money.to_minor(money.D(order.get("amount") or 0), ccy), currency=ccy,
                            occurred_at=clock.now())

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool:
        secret = acct.secret.get("advice_secret")
        if not secret:
            return True  # a hint only: nothing changes until fetch_status says so
        got = header(headers, "X-Telr-Secret") or ""
        return hmac.compare_digest(got.encode(), str(secret).encode())

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]:
        try:
            fields = dict(parse_qsl(raw.decode("utf-8"))) if b"=" in raw and not raw.strip().startswith(b"{") \
                else json.loads(raw or b"{}")
        except (UnicodeDecodeError, ValueError):
            return []
        ref = fields.get("order_ref") or fields.get("tran_order")
        if not ref:
            return []
        ccy = str(fields.get("tran_currency") or "AED")
        return [PaymentEvent(provider=self.key, provider_event_id=hashlib.sha256(raw).hexdigest(), provider_ref=ref,
                             kind="paid" if fields.get("tran_status") == "A" else "failed",
                             amount_minor=money.to_minor(money.D(fields.get("tran_amount") or 0), ccy),
                             currency=ccy, occurred_at=clock.now())]
