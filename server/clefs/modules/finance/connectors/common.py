"""
Shared connector plumbing: the HTTP client (mockable), amounts, sections.

Tokens come from clefs.core.oauth (sealed in oauth_connections, refreshed
there); finance keeps only its view of the link in fin_connections.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from clefs.core import money
from clefs.core.oauth import NotConnected, OAuthError

TIMEOUT = httpx.Timeout(30.0, connect=5.0)
_transport: httpx.BaseTransport | None = None


def set_transport(t: httpx.BaseTransport | None) -> None:
    """Tests: route every connector request through httpx.MockTransport."""
    global _transport
    _transport = t


def http() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=_transport, follow_redirects=False)


def get_json(url: str, *, token: str, params: dict | None = None, headers: dict | None = None,
             provider: str) -> dict:
    """GET a report. 401 means the link needs re-consent (NotConnected); 429
    and 5xx are transient OAuthErrors the job retries next run."""
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
    try:
        with http() as c:
            r = c.get(url, params=params, headers=h)
    except httpx.HTTPError as exc:
        raise OAuthError(f"{provider} request failed: {exc.__class__.__name__}", error="network",
                         transient=True) from exc
    if r.status_code == 401:
        raise NotConnected(f"{provider} refused the token: reconnect", provider=provider)
    if r.status_code == 429 or r.status_code >= 500:
        raise OAuthError(f"{provider} is unavailable ({r.status_code})", error=str(r.status_code), transient=True)
    if r.status_code >= 400:
        raise OAuthError(f"{provider} rejected the report request ({r.status_code})", error=str(r.status_code),
                         transient=False)
    return r.json()


def to_minor(v: Any, currency: str) -> int | None:
    s = str(v if v is not None else "").replace(",", "").strip()
    if not s:
        return None
    try:
        return money.to_minor(Decimal(s), currency)
    except (InvalidOperation, ValueError):
        return None


INCOME_WORDS = ("income", "revenue", "sales", "trading")


def section_sign(title: str) -> tuple[str, int]:
    """('income'|'expense', sign applied to printed amounts). Reports print
    expenses as positive numbers under 'Less ...' / 'Expenses' sections."""
    t = (title or "").lower()
    if any(w in t for w in ("cost of sales", "cogs", "expense", "overhead", "less ")):
        return "expense", -1
    if any(w in t for w in INCOME_WORDS):
        return "income", 1
    return "expense", -1


@dataclass
class Report:
    """A parsed statement for one month."""

    lines: list[dict] = field(default_factory=list)   # {code, name, group_raw, type_raw, amount_minor, row_ref}
    net_profit_minor: int | None = None
    debit_minor: int = 0
    credit_minor: int = 0
    meta: dict = field(default_factory=dict)
