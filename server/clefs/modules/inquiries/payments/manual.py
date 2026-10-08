"""
Manual payments (04 spec 4.8): staff paste a link from any provider, or
record a bank transfer with evidence (`mark-paid`, manual provider only).
There is no status call and no webhook; the fee is 0.
"""

from __future__ import annotations

from clefs.core.errors import ClefsError
from clefs.modules.inquiries.payments.base import Account, PaymentEvent, PaymentLink, PaymentLinkRequest, safe_pay_url


class Manual:
    key = "manual"

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink:
        url = (acct.config or {}).get("url")
        if not url:
            raise ClefsError(400, "paste the payment link (url) for a manual payment", "url_required")
        # Re-checked here, not only at request time: an action created before
        # the check existed still carries its pasted url in params.
        return PaymentLink(provider_ref=f"manual:{req.idempotency_key}", url=safe_pay_url(url),
                           expires_at=req.expires_at)

    def cancel_link(self, acct: Account, provider_ref: str) -> None:
        return None

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None:
        return None

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool:
        return False

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]:
        return []
