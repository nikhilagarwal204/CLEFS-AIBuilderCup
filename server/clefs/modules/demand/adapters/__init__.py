"""
Demand Radar source adapters. Event adapters turn a vendor payload into
event candidates (see events.py) and never write rows themselves; sync
functions hand candidates to events.ingest().

Outbound HTTP for the event vendors goes through vendor_json() so tests swap
the transport once (set_transport) and every call carries short timeouts: a
slow vendor must not hold a job slot. SerpApi never goes through here (one
path, clefs.core.serpapi, Canon K3).
"""

from __future__ import annotations

from typing import Any

import httpx

from clefs.core.errors import ClefsError

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: route every vendor call through `transport`; None = network."""
    global _transport
    _transport = transport


class VendorError(ClefsError):
    """A vendor call failed (502 vendor_error). `.transient` says whether a
    later retry may help (network error, 429, 5xx)."""

    def __init__(self, vendor: str, status: int | None, detail: str) -> None:
        super().__init__(502, f"{vendor}: {detail}", "vendor_error")
        self.vendor = vendor
        self.status = status
        self.transient = status is None or status == 429 or status >= 500


def vendor_json(vendor: str, method: str, url: str, **kw: Any) -> dict:
    try:
        with httpx.Client(timeout=TIMEOUT, transport=_transport) as c:
            resp = c.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise VendorError(vendor, None, type(exc).__name__) from exc
    if resp.status_code >= 400:
        # Never echo the body or URL: both can carry the API key.
        raise VendorError(vendor, resp.status_code, f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise VendorError(vendor, resp.status_code, "invalid JSON") from exc
    return body if isinstance(body, dict) else {"data": body}
