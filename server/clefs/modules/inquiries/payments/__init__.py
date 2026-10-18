"""
Payment links behind one adapter (04 spec 4.8): N-Genius, Telr, Stripe, Tap
and manual. `PROVIDERS` maps the provider key (the channel-account kind
without `pay_`) to its adapter; `service` holds the flow and `routes` the
HTTP API.
"""

from __future__ import annotations

from clefs.modules.inquiries.payments.base import (
    Account,
    PaymentEvent,
    PaymentLink,
    PaymentLinkRequest,
    PaymentProvider,
    ProviderError,
    set_transport,
)
from clefs.modules.inquiries.payments.manual import Manual
from clefs.modules.inquiries.payments.ngenius import NGenius
from clefs.modules.inquiries.payments.stripe import Stripe
from clefs.modules.inquiries.payments.tap import Tap
from clefs.modules.inquiries.payments.telr import Telr

PROVIDERS: dict[str, PaymentProvider] = {p.key: p for p in (NGenius(), Telr(), Stripe(), Tap(), Manual())}
# Settings defaults when the account config names no fee (inquiry-crm.md; N-Genius is a demo assumption).
DEFAULT_FEES: dict[str, tuple[str, int]] = {
    "ngenius": ("2.5", 0), "telr": ("2.25", 50), "stripe": ("2.9", 100), "tap": ("2.5", 0), "manual": ("0", 0),
}


def get_provider(key: str) -> PaymentProvider:
    from clefs.core.errors import ClefsError

    p = PROVIDERS.get(key)
    if p is None:
        raise ClefsError(404, f"unknown payment provider {key!r}", "unknown_provider")
    return p


__all__ = [
    "DEFAULT_FEES", "PROVIDERS", "Account", "PaymentEvent", "PaymentLink", "PaymentLinkRequest", "PaymentProvider",
    "ProviderError", "get_provider", "set_transport",
]
