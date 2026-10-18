"""
Visibility's outbound HTTP (Places, GBP Performance / Business Information /
Lodging, CrUX, PSI, the hotel's own website). SerpApi never goes through
here: its one path is clefs.core.serpapi.search (Canon K3).

One client factory so tests swap the transport once (set_transport) and every
call carries the same short timeouts: a slow vendor must not hold a job slot
past the 25 s tick budget.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from clefs.core.errors import ClefsError

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
USER_AGENT = "ClefsBot/1.0 (+https://www.clefs.world/bot)"
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Tests: route every call through `transport`; None = the network."""
    global _transport
    _transport = transport


def client(*, follow_redirects: bool = False) -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=_transport, follow_redirects=follow_redirects,
                        headers={"User-Agent": USER_AGENT})


class VendorError(ClefsError):
    """A vendor call failed (502 vendor_error). `.http_status` is the vendor's
    HTTP status (None for a network error; `.status` stays the 502 this error
    answers with); `.transient` says whether a retry may help. A 404 is kept
    distinct because CrUX uses it for "insufficient data"."""

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
        # Never echo the body: Google puts request details (the key) in errors.
        raise VendorError(vendor, resp.status_code, f"HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise VendorError(vendor, resp.status_code, "invalid JSON") from exc
    return body if isinstance(body, dict) else {"data": body}


# ─── Owner website fetch (SSRF guard) ───
#
# The website URL is owner-typed and fetched from our worker, so every hop
# must reach only public addresses (no loopback, private, CGNAT, link-local
# such as cloud metadata, or reserved ranges). WHY a network backend and not a
# pre-check: checking the name with getaddrinfo and then letting httpx resolve
# it again at connect time is a DNS-rebinding hole (a short-TTL name answers a
# public IP to the check and 127.0.0.1 to the connect). The backend below
# resolves ONCE inside connect_tcp, vets every answer, and dials the vetted IP
# itself; TLS still verifies the certificate and sends SNI for the hostname
# (httpcore passes the URL host as server_hostname), so HTTPS stays sound.

MAX_REDIRECTS = 5
# Web ports only: a website check has no reason to reach :22, :5432, :6379 or
# an internal admin port on a public IP.
ALLOWED_PORTS = frozenset({80, 443})
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


def _blocked_ip(raw: str) -> bool:
    """True unless `raw` is a globally routable unicast address. IPv6 forms
    that embed an IPv4 address (mapped, 6to4, Teredo, NAT64) are judged by the
    embedded address: ::ffff:127.0.0.1 is loopback, whatever is_global says."""
    try:
        ip = ipaddress.ip_address(raw.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip.sixtofour is not None:
            ip = ip.sixtofour
        elif ip.teredo is not None:
            ip = ip.teredo[1]
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return not ip.is_global or ip.is_multicast


def _resolve(host: str, port: int) -> list[str]:
    """Every address `host` resolves to (the single DNS lookup of a hop)."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _url_allowed(url: str) -> bool:
    """Shape-only check (no DNS): http(s), a host, and a web port."""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname) and port in ALLOWED_PORTS


class _PinnedBackend(httpcore.SyncBackend):
    """Resolve, vet, then dial the vetted IP: one resolution per connection,
    so nothing can change between the check and the connect."""

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None,
                    socket_options: Iterable[Any] | None = None) -> httpcore.NetworkStream:
        if port not in ALLOWED_PORTS:
            raise VendorError("website", None, "address not allowed")
        try:
            addrs = _resolve(host, port)
        except OSError as exc:
            raise httpcore.ConnectError(f"resolve failed: {type(exc).__name__}") from exc
        # Any non-public answer rejects the host outright (a name that mixes
        # public and private records is a rebinding setup, not a real site).
        if not addrs or any(_blocked_ip(a) for a in addrs):
            raise VendorError("website", None, "address not allowed")
        last: Exception | None = None
        for addr in addrs:
            try:
                return super().connect_tcp(addr, port, timeout=timeout, local_address=local_address,
                                           socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last = exc
        assert last is not None
        raise last


class _PinnedTransport(httpx.HTTPTransport):
    """httpx's own transport (request/response mapping, TLS, exception
    translation) with the connection pool swapped for one that dials through
    _PinnedBackend. No proxy: an env proxy would do its own resolution."""

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        # WHY the guard: HTTPTransport has no network_backend parameter, so we
        # replace its pool. If a future httpx renamed the attribute, assigning
        # it would silently leave the unpinned pool in charge; fail loudly.
        if not isinstance(getattr(self, "_pool", None), httpcore.ConnectionPool):
            raise RuntimeError("httpx transport layout changed; SSRF pin cannot be installed")
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(),
            network_backend=_PinnedBackend(),
        )


def fetch_text(url: str, *, max_bytes: int = 2_000_000) -> tuple[int, str]:
    """GET a web page as ClefsBot (status, text). Redirects are followed by
    hand (at most 5) so each hop passes the URL-shape check, and every
    connection is dialled through _PinnedBackend (public IPs only, ports
    80/443). The body is capped so a hostile page cannot exhaust the worker.
    Tests (a transport installed) bypass the pinned transport: their hosts do
    not resolve."""
    transport = _transport if _transport is not None else _PinnedTransport()
    try:
        with httpx.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False,
                          headers={"User-Agent": USER_AGENT}) as c:
            for _ in range(MAX_REDIRECTS + 1):
                if not _url_allowed(url):
                    raise VendorError("website", None, "address not allowed")
                with c.stream("GET", url) as resp:
                    if resp.is_redirect and resp.headers.get("location"):
                        url = urljoin(url, resp.headers["location"])
                        continue
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            break
                        chunks.append(chunk)
                    enc = resp.encoding or "utf-8"
                    return resp.status_code, b"".join(chunks).decode(enc, errors="replace")
            raise VendorError("website", None, "too many redirects")
    except httpx.HTTPError as exc:
        raise VendorError("website", None, type(exc).__name__) from exc
