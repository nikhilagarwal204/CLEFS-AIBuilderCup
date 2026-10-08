"""
Network International N-Genius invoices (04 spec 4.8; research inquiry-crm.md).

- Create: POST {base}/invoices/outlets/{outletRef}/invoice with
  Content-Type application/vnd.ni-invoice.v1+json; the link is
  _links.payment.href. The access-token flow is UNVERIFIED: POST
  {base}/identity/auth/access-token with `Authorization: Basic <api_key>`.
- Webhook: a header secret configured in the portal (UNVERIFIED; header name
  in config.webhook_header), then the order is re-fetched.
- Fee: bank-negotiated (UNVERIFIED), so always taken from settings.

Config: {outlet_ref, api_base? (https *.ngenius-payments.com only), webhook_header?, fee_pct,
fee_fixed_minor}.
Secret: {api_key, webhook_secret}.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.modules.inquiries.payments.base import (
    Account,
    PaymentEvent,
    PaymentLink,
    PaymentLinkRequest,
    ProviderError,
    header,
    http,
)

DEFAULT_BASE = "https://api-gateway.ngenius-payments.com"
DEFAULT_HEADER = "X-Webhook-Secret"
# WHY an allowlist: api_base comes from a manager's connect form and is
# fetched server-side (token and order status), so a free-form URL let an
# owner or GM probe internal or link-local addresses from the API or the
# worker and read the answer back as "provider refused (NNN)". Only
# Network International's own https gateways (production, sandbox and the
# regional hosts, all under ngenius-payments.com) are accepted.
ALLOWED_HOST_SUFFIX = ".ngenius-payments.com"
_STATES = {"PURCHASED": "paid", "CAPTURED": "paid", "PARTIALLY_CAPTURED": "partially_paid", "FAILED": "failed",
           "DECLINED": "failed", "REVERSED": "refunded", "PARTIALLY_REFUNDED": "refunded", "REFUNDED": "refunded",
           "CANCELLED": "cancelled", "EXPIRED": "expired"}


def safe_api_base(value: Any) -> str | None:
    """The normalised base when `value` is an https N-Genius gateway, else
    None. No userinfo, port or query: `https://x.ngenius-payments.com@evil`
    parses with hostname evil, and a port could reach another service."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        u = urlsplit(value.strip())
        port = u.port
    except ValueError:
        return None
    host = (u.hostname or "").lower().rstrip(".")
    if u.scheme != "https" or not host.endswith(ALLOWED_HOST_SUFFIX) or port is not None:
        return None
    if u.username or u.password or u.query or u.fragment or "@" in u.netloc:
        return None
    return f"https://{host}{u.path.rstrip('/')}"


def clean_api_base(value: Any) -> str | None:
    """Connect-time check: empty means the default gateway; anything else
    must pass safe_api_base (400 invalid_api_base)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    got = safe_api_base(value)
    if got is None:
        raise ClefsError(400, "api_base must be an https://*.ngenius-payments.com gateway", "invalid_api_base")
    return got


class NGenius:
    key = "ngenius"

    def _base(self, acct: Account) -> str:
        # A row saved before the allowlist (or edited in the database) falls
        # back to the default gateway rather than being fetched.
        return safe_api_base(acct.config.get("api_base")) or DEFAULT_BASE

    def _token(self, acct: Account) -> str:
        r = http("POST", f"{self._base(acct)}/identity/auth/access-token", headers={
            "Authorization": f"Basic {acct.secret.get('api_key', '')}",
            "Content-Type": "application/vnd.ni-identity.v1+json", "Accept": "application/vnd.ni-identity.v1+json"})
        token = (r.json_body or {}).get("access_token")
        if not token:
            raise ProviderError("N-Genius returned no access token", transient=False)
        return token

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink:
        amount = {"currencyCode": req.currency, "value": req.amount_minor}
        first, _, last = (req.customer_name or "Guest").partition(" ")
        body = {
            "firstName": first, "lastName": last or "-", "email": req.customer_email or "",
            "transactionType": "PURCHASE", "emailSubject": req.description,
            "invoiceExpiryDate": req.expires_at.date().isoformat(),
            "items": [{"description": req.description, "totalPrice": amount, "quantity": 1}],
            "total": amount, "message": req.reference, "paymentAttempts": 3,
            "skipInvoiceCreatedEmailNotification": True, "redirectUrl": req.return_url,
            "merchantOrderReference": req.reference,
        }
        r = http("POST", f"{self._base(acct)}/invoices/outlets/{acct.config.get('outlet_ref')}/invoice", headers={
            "Authorization": f"Bearer {self._token(acct)}", "Content-Type": "application/vnd.ni-invoice.v1+json",
            "Accept": "application/vnd.ni-invoice.v1+json"}, json=body)
        got = r.json_body or {}
        ref = got.get("orderReference") or got.get("reference")
        url = ((got.get("_links") or {}).get("payment") or {}).get("href")
        if not ref or not url:
            raise ProviderError("N-Genius returned no payment link", transient=False)
        return PaymentLink(provider_ref=ref, url=url, expires_at=req.expires_at, raw=got)

    def cancel_link(self, acct: Account, provider_ref: str) -> None:
        # UNVERIFIED: no documented invoice cancel; the invoice lapses at
        # invoiceExpiryDate and the link row is cancelled on our side.
        return None

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None:
        r = http("GET", f"{self._base(acct)}/transactions/outlets/{acct.config.get('outlet_ref')}/orders/{provider_ref}",
                 headers={"Authorization": f"Bearer {self._token(acct)}", "Accept": "application/vnd.ni-payment.v2+json"})
        got = r.json_body or {}
        state = str(got.get("state") or "").upper()
        kind = _STATES.get(state)
        if kind is None:
            return None  # STARTED / AWAITING_3DS: nothing final yet
        amount = got.get("amount") or {}
        return PaymentEvent(provider=self.key, provider_event_id=f"status:{provider_ref}:{state}", provider_ref=provider_ref,
                            kind=kind, amount_minor=int(amount.get("value") or 0),
                            currency=str(amount.get("currencyCode") or acct.config.get("currency") or "AED"),
                            occurred_at=clock.now(), fee_minor=None)

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool:
        secret = acct.secret.get("webhook_secret")
        got = header(headers, acct.config.get("webhook_header") or DEFAULT_HEADER)
        return bool(secret and got) and hmac.compare_digest(str(got).encode(), str(secret).encode())

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]:
        body = json.loads(raw or b"{}")
        order = body.get("order") or {}
        ref = order.get("reference") or body.get("orderReference")
        if not ref:
            return []
        state = str(body.get("eventName") or order.get("state") or "").upper()
        amount = order.get("amount") or {}
        return [PaymentEvent(
            provider=self.key, provider_event_id=str(body.get("eventId") or hashlib.sha256(raw).hexdigest()),
            provider_ref=ref, kind=_STATES.get(state, "paid"), amount_minor=int(amount.get("value") or 0),
            currency=str(amount.get("currencyCode") or "AED"), occurred_at=datetime.now(timezone.utc),
        )]

