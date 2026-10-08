"""
The module's outbound HTTP (GBP reviews API, Tripadvisor Content API, Google
Places). SerpApi never goes through here: it has one path, core.serpapi.

One client factory so tests swap the transport once (set_transport) and every
call carries the same short timeouts: a slow vendor must not hold a request
or a job slot.
"""

from __future__ import annotations

from typing import Any

import httpx

from clefs.core.errors import ClefsError

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: route every call through `transport`; None = the network."""
    global _transport
    _transport = transport


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=_transport)


class VendorError(ClefsError):
    """A vendor call failed (502 vendor_error). `.http_status` is the vendor's
    HTTP status (None for a network error), `.transient` says whether a retry
    may help. `.status` stays 502: ClefsError's handler answers with it, so a
    vendor 404 (or a network error's None) must never overwrite it."""

    def __init__(self, vendor: str, status: int | None, detail: str) -> None:
        super().__init__(502, f"{vendor}: {detail}", "vendor_error")
        self.vendor = vendor
        self.http_status = status
        self.transient = status is None or status == 429 or status >= 500


def request_json(vendor: str, method: str, url: str, **kw: Any) -> dict:
    try:
        with client() as c:
            resp = c.request(method, url, **kw)
    except httpx.HTTPError as exc:
        raise VendorError(vendor, None, type(exc).__name__) from exc
    if resp.status_code >= 400:
        # Never echo the body: vendors put request details (keys) in errors.
        raise VendorError(vendor, resp.status_code, f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise VendorError(vendor, resp.status_code, "invalid JSON") from exc
    return body if isinstance(body, dict) else {"data": body}
