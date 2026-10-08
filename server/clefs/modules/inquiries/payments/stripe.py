"""
Stripe Payment Links (04 spec 4.8). UNVERIFIED against Stripe's docs at
build time; the shapes below follow the public v1 API.

- Create: POST {STRIPE_API_BASE}/v1/payment_links (form-encoded) with one
  price_data line and metadata{inquiry_ref, quote_version, clefs_ref}; the
  reconcile job deactivates the link at valid_until (active=false).
- Webhook: `Stripe-Signature: t=<unix>,v1=<hex>` = HMAC-SHA256(secret,
  "<t>.<raw body>") with a 5-minute tolerance, then the Checkout Sessions of
  the link are fetched (fetch_status).
- Fee: settings (2.9% + AED 1 default, inquiry-crm.md).

Secret: {secret_key, webhook_secret}.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from clefs.core import clock
from clefs.modules.inquiries.payments.base import (
    Account,
    PaymentEvent,
    PaymentLink,
    PaymentLinkRequest,
    ProviderError,
    header,
    http,
)

TOLERANCE_S = 300


def _base() -> str:
    from clefs.settings import get_settings

    return str(getattr(get_settings(), "STRIPE_API_BASE", None) or "https://api.stripe.com").rstrip("/")


class Stripe:
    key = "stripe"

    def _headers(self, acct: Account, idem: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {acct.secret.get('secret_key', '')}"}
        if idem:
            h["Idempotency-Key"] = idem
        return h

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink:
        inquiry_ref, _, rest = req.reference.partition("-v")
        form = {
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": req.currency.lower(),
            "line_items[0][price_data][unit_amount]": str(req.amount_minor),
            "line_items[0][price_data][product_data][name]": req.description[:250],
            "metadata[inquiry_ref]": inquiry_ref, "metadata[quote_version]": rest.split("-")[0],
            "metadata[clefs_ref]": req.reference,
            "after_completion[type]": "redirect", "after_completion[redirect][url]": req.return_url,
        }
        r = http("POST", f"{_base()}/v1/payment_links", headers=self._headers(acct, req.idempotency_key), data=form)
        got = r.json_body or {}
        if not got.get("id") or not got.get("url"):
            raise ProviderError("Stripe returned no payment link", transient=False)
        return PaymentLink(provider_ref=got["id"], url=got["url"], expires_at=req.expires_at, raw=got)

    def cancel_link(self, acct: Account, provider_ref: str) -> None:
        http("POST", f"{_base()}/v1/payment_links/{provider_ref}", headers=self._headers(acct), data={"active": "false"})

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None:
        r = http("GET", f"{_base()}/v1/checkout/sessions?payment_link={provider_ref}&limit=10",
                 headers=self._headers(acct))
        for s in (r.json_body or {}).get("data") or []:
            if s.get("payment_status") == "paid":
                return PaymentEvent(
                    provider=self.key, provider_event_id=f"status:{s.get('id')}", provider_ref=provider_ref,
                    kind="paid", amount_minor=int(s.get("amount_total") or 0),
                    currency=str(s.get("currency") or "aed").upper(),
                    occurred_at=datetime.fromtimestamp(int(s.get("created") or 0), timezone.utc)
                    if s.get("created") else clock.now(),
                )
        return None

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool:
        secret = acct.secret.get("webhook_secret")
        sig = header(headers, "Stripe-Signature")
        if not secret or not sig:
            return False
        parts = dict(p.split("=", 1) for p in sig.split(",") if "=" in p)
        t = parts.get("t", "")
        if not t.isdigit() or abs(clock.now().timestamp() - int(t)) > TOLERANCE_S:
            return False
        want = hmac.new(str(secret).encode(), f"{t}.".encode() + raw, hashlib.sha256).hexdigest()
        given = [v for k, v in (p.split("=", 1) for p in sig.split(",") if "=" in p) if k == "v1"]
        return any(hmac.compare_digest(want, g) for g in given)

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]:
        body = json.loads(raw or b"{}")
        obj = (body.get("data") or {}).get("object") or {}
        ref = obj.get("payment_link")
        if not ref:
            return []
        kind = "refunded" if "refund" in str(body.get("type")) else (
            "paid" if obj.get("payment_status") == "paid" else "failed")
        return [PaymentEvent(provider=self.key, provider_event_id=str(body.get("id") or hashlib.sha256(raw).hexdigest()),
                             provider_ref=ref, kind=kind, amount_minor=int(obj.get("amount_total") or 0),
                             currency=str(obj.get("currency") or "aed").upper(), occurred_at=clock.now())]
