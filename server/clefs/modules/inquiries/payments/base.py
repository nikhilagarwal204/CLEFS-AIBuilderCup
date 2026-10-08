"""
Payment-link adapter interface (04 spec 4.8).

Every provider sits behind one Protocol. The flow never trusts a webhook
body: a verified webhook is only stored, then `fetch_status` (the
authoritative read) decides what changes on inq_payment_links.

HTTP goes through `http()`, a replaceable transport, so tests install a fake
(`set_transport`) and no test ever reaches a provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from clefs.core.errors import ClefsError

EventKind = Literal["paid", "partially_paid", "failed", "expired", "cancelled", "refunded"]


class PaymentLinkRequest(BaseModel):
    amount_minor: int
    currency: str
    description: str
    reference: str  # "<inquiry ref>-v<version>-<seq>": what the provider shows and echoes back
    expires_at: datetime
    customer_name: str | None = None
    customer_email: str | None = None
    customer_phone: str | None = None
    language: str = "en"
    return_url: str
    idempotency_key: str


class PaymentLink(BaseModel):
    provider_ref: str
    url: str
    expires_at: datetime | None = None
    raw: dict = {}


class PaymentEvent(BaseModel):
    provider: str
    provider_event_id: str
    provider_ref: str
    kind: EventKind
    amount_minor: int
    currency: str
    occurred_at: datetime
    fee_minor: int | None = None


@dataclass(frozen=True)
class Account:
    """An inq_channel_accounts pay_* row with its secret unsealed (a dict:
    api keys, webhook secret). Never serialised."""

    id: int
    hotel_slug: str
    kind: str
    config: dict
    secret: dict = field(default_factory=dict)

    @property
    def provider(self) -> str:
        return self.kind.removeprefix("pay_")


class PaymentProvider(Protocol):
    key: str

    def create_link(self, acct: Account, req: PaymentLinkRequest) -> PaymentLink: ...

    def cancel_link(self, acct: Account, provider_ref: str) -> None: ...

    def fetch_status(self, acct: Account, provider_ref: str) -> PaymentEvent | None: ...

    def verify_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> bool: ...

    def parse_webhook(self, acct: Account, headers: dict[str, str], raw: bytes) -> list[PaymentEvent]: ...


class ProviderError(ClefsError):
    """A provider call failed (502 payment_provider_error). `transient` asks
    the caller to retry later."""

    def __init__(self, detail: str, *, transient: bool = True) -> None:
        super().__init__(502, detail, "payment_provider_error")
        self.transient = transient


# ─── Pay URL safety ───


def is_safe_pay_url(url: str | None) -> bool:
    """An absolute https URL with a host and no control characters or
    whitespace. WHY this strict: the URL becomes the Pay button's href on the
    public quote page, which shares the console's origin. A `javascript:`
    (or `data:`) URL there runs script as Clefs when the guest, GM or owner
    taps Pay; React 18 only warns on such hrefs, it still renders them."""
    from urllib.parse import urlsplit

    if not url or not isinstance(url, str) or len(url) > 2000:
        return False
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname)


def safe_pay_url(url: str | None) -> str:
    """The URL unchanged, or 400 invalid_payment_url."""
    if not is_safe_pay_url(url):
        raise ClefsError(400, "the payment link must be an https:// URL", "invalid_payment_url")
    return str(url)


# ─── Transport ───


class Response(BaseModel):
    status: int
    json_body: Any = None
    text: str = ""


Transport = Any  # callable(method, url, *, headers, json=None, data=None, timeout=20) -> Response
_transport: Transport | None = None


def set_transport(t: Transport | None) -> None:
    """Tests install a fake; None restores the real httpx transport."""
    global _transport
    _transport = t


def _httpx(method: str, url: str, *, headers: dict, json: Any = None, data: Any = None, timeout: float = 20) -> Response:
    import httpx

    r = httpx.request(method, url, headers=headers, json=json, data=data, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = None
    return Response(status=r.status_code, json_body=body, text=r.text[:2000])


def http(method: str, url: str, *, headers: dict | None = None, json: Any = None, data: Any = None) -> Response:
    """One provider call. 5xx and network errors are transient; 4xx is not."""
    t = _transport or _httpx
    try:
        r = t(method, url, headers=headers or {}, json=json, data=data, timeout=20)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 - network errors of any client
        raise ProviderError(f"{method} {url.split('?')[0]} failed: {type(exc).__name__}") from exc
    if r.status >= 500 or r.status == 429:
        raise ProviderError(f"provider returned {r.status}")
    if r.status >= 400:
        raise ProviderError(f"provider refused the request ({r.status})", transient=False)
    return r


def header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header read."""
    low = name.lower()
    for k, v in (headers or {}).items():
        if k.lower() == low:
            return v
    return None
