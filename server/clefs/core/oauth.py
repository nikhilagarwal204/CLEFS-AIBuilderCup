"""
core.oauth: hotel connections to third-party accounts (04 spec F6).

    token = oauth.get_access_token(cur, "demo-dubai", "google_business")   # refreshes when due
    # raises oauth.NotConnected when the hotel never connected, or the
    # provider revoked access (the console then shows "Reconnect")

Providers (PROVIDERS; register_provider adds more):
  google_business  business.manage, shared by Visibility and Reviews (owners
                   connect Google once); PKCE; offline access
  xero             accounting.reports.read offline_access; PKCE; rotating
                   refresh tokens
  qbo              com.intuit.quickbooks.accounting; the callback's realmId
                   is the account_ref
  zoho             Zoho Books read scopes; the token exchange goes to the
                   accounts server the callback names (multi data centre),
                   checked against an allowlist

Flow:
  1. GET /hotels/{slug}/oauth/{provider}/start (require_perm per provider:
     visibility.manage or reviews.manage for Google, finance.connect for the
     accounting providers). Writes an oauth_states row holding sha256(state),
     the hotel, the user, the redirect_uri and the sealed PKCE verifier, and
     redirects (or, with ?format=json, answers {authorize_url}).
  2. GET /public/oauth/{provider}/callback. The state row is deleted on first
     use and refused after 15 minutes; the hotel comes from that row, never
     from the query. The browser completing it must be signed in as the
     user who started the flow (else oauth_error=session_mismatch and no
     code exchange), so an authorize URL sent to someone else connects
     nothing. The code is exchanged outside any transaction, and the
     tokens are sealed (clefs.core.privacy.seal) into oauth_connections.
     The browser is sent back to the web app with
     ?oauth=<provider>&oauth_status=connected|denied|error[&oauth_error=<code>].

Refresh: get_access_token refreshes in its OWN transaction (a second pooled
connection) under FOR UPDATE SKIP LOCKED, because Xero and QuickBooks rotate the
refresh token on every use: if the new one only lived in the caller's
transaction and that rolled back, the connection would be lost. When the row is
locked, the refresh falls back to the caller's cursor (either another worker is
refreshing, and we wait for it and read its token, or the caller itself holds
the lock and the write must go through its transaction anyway).

Tests: oauth.set_transport(httpx.MockTransport(handler)) mocks every token,
refresh and revoke endpoint; set_transport(None) restores the network.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import psycopg2
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse, Response

from clefs import db, ratelimit
from clefs.audit import audit
from clefs.auth import tokens
from clefs.auth.deps import HotelCtx, Principal, current_principal, require_perm
from clefs.auth.magic import safe_next_path
from clefs.core import clock
from clefs.core.errors import ClefsError, NotConfigured, not_found, too_many
from clefs.settings import get_settings

log = logging.getLogger("clefs.oauth")

router = APIRouter(tags=["oauth"])

STATE_TTL = timedelta(minutes=15)
# Refresh a little before the provider's expiry so a token never dies mid-call.
REFRESH_SKEW = timedelta(seconds=120)
DEFAULT_EXPIRES_IN = 3600
# last_used_at is advisory; write it at most this often.
TOUCH_EVERY = timedelta(minutes=10)
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
REVOKE_TIMEOUT = httpx.Timeout(5.0, connect=3.0)
START_LIMIT = 20  # flow starts per user per 10 minutes
# A refresh answered with one of these means the grant is gone for good (the
# user revoked it, or it expired unused): the connection needs a person.
# Zoho answers a dead refresh token with HTTP 200 {"error": "invalid_code"}.
REVOKED_ERRORS = frozenset({"invalid_grant", "invalid_code"})

Status = Literal["connected", "denied", "error"]


# ─── Errors ───


class NotConnected(ClefsError):
    """The hotel has no usable connection to this provider (never connected,
    disconnected, or revoked). Callers degrade to a "connect" state."""

    def __init__(self, detail: str = "This account is not connected", *, provider: str | None = None):
        super().__init__(409, detail, "not_connected")
        self.provider = provider


class OAuthError(ClefsError):
    """A token endpoint failed. `error` is the provider's OAuth error code (or
    http_<status> / network); `transient` means retry later, not reconnect."""

    def __init__(self, detail: str, *, error: str, transient: bool, status_code: int | None = None):
        super().__init__(502, detail, "oauth_error")
        self.error = error
        self.transient = transient
        self.status_code = status_code


# ─── Providers ───


@dataclass(frozen=True)
class OAuthProvider:
    key: str
    label: str
    authorize_url: str
    token_url: str
    scopes: tuple[str, ...]
    client_id_setting: str  # Settings attribute names, never the values
    client_secret_setting: str
    start_perms: tuple[str, ...]  # any one of these may start or remove the connection
    default_next: str  # "{slug}" is filled in
    pkce: bool = False
    client_auth: Literal["basic", "body"] = "body"
    scope_separator: str = " "
    authorize_params: Mapping[str, str] = field(default_factory=dict)
    revoke_url: str | None = None
    revoke_json: bool = False
    # Callback query parameter carrying the provider account (QBO realmId).
    account_ref_param: str | None = None
    # Callback query parameter naming the accounts server to exchange the code
    # with (Zoho), and the hosts it may name.
    accounts_server_param: str | None = None
    accounts_hosts: frozenset[str] = frozenset()
    # Scopes the connection is useless without. Google lets a user untick a
    # scope on the consent screen; a connection missing one is refused.
    required_scopes: tuple[str, ...] = ()


GOOGLE_BUSINESS_SCOPE = "https://www.googleapis.com/auth/business.manage"
ZOHO_ACCOUNTS_HOSTS = frozenset(
    {
        "accounts.zoho.com", "accounts.zoho.eu", "accounts.zoho.in", "accounts.zoho.com.au", "accounts.zoho.jp",
        "accounts.zohocloud.ca", "accounts.zoho.sa", "accounts.zoho.uk", "accounts.zoho.com.cn",
    }
)
ACCOUNTING_PERMS = ("finance.connect",)

PROVIDERS: dict[str, OAuthProvider] = {}


def register_provider(p: OAuthProvider) -> OAuthProvider:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", p.key):
        raise ValueError(f"provider key must be a short identifier, got {p.key!r}")
    if not p.start_perms:
        raise ValueError(f"provider {p.key!r} needs start_perms (who may connect it)")
    PROVIDERS[p.key] = p
    return p


register_provider(
    OAuthProvider(
        key="google_business",
        label="Google Business Profile",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=(GOOGLE_BUSINESS_SCOPE,),
        client_id_setting="GBP_OAUTH_CLIENT_ID",
        client_secret_setting="GBP_OAUTH_CLIENT_SECRET",
        start_perms=("visibility.manage", "reviews.manage"),
        default_next="/app/{slug}/visibility",
        pkce=True,
        # prompt=consent: Google only returns a refresh token on a consent
        # screen, and a reconnect must always get a fresh one.
        authorize_params={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
        revoke_url="https://oauth2.googleapis.com/revoke",
        required_scopes=(GOOGLE_BUSINESS_SCOPE,),
    )
)
register_provider(
    OAuthProvider(
        key="xero",
        label="Xero",
        authorize_url="https://login.xero.com/identity/connect/authorize",
        token_url="https://identity.xero.com/connect/token",
        # UNVERIFIED (04 spec 7.7): Xero is moving new apps to granular report
        # scopes; the spec's broad scope is kept until Finance confirms.
        scopes=("accounting.reports.read", "offline_access"),
        client_id_setting="XERO_CLIENT_ID",
        client_secret_setting="XERO_CLIENT_SECRET",
        start_perms=ACCOUNTING_PERMS,
        default_next="/app/{slug}/finance",
        pkce=True,
        client_auth="basic",
        revoke_url="https://identity.xero.com/connect/revocation",
    )
)
register_provider(
    OAuthProvider(
        key="qbo",
        label="QuickBooks Online",
        authorize_url="https://appcenter.intuit.com/connect/oauth2",
        token_url="https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        scopes=("com.intuit.quickbooks.accounting",),
        client_id_setting="QBO_CLIENT_ID",
        client_secret_setting="QBO_CLIENT_SECRET",
        start_perms=ACCOUNTING_PERMS,
        default_next="/app/{slug}/finance",
        client_auth="basic",
        revoke_url="https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
        revoke_json=True,
        account_ref_param="realmId",
    )
)
register_provider(
    OAuthProvider(
        key="zoho",
        label="Zoho Books",
        authorize_url="https://accounts.zoho.com/oauth/v2/auth",
        token_url="https://accounts.zoho.com/oauth/v2/token",
        # UNVERIFIED: Zoho Books documents no Reports scope (04 spec 6.3), so
        # the P&L is planned from the chart of accounts and transactions.
        scopes=("ZohoBooks.accountants.READ", "ZohoBooks.settings.READ"),
        client_id_setting="ZOHO_CLIENT_ID",
        client_secret_setting="ZOHO_CLIENT_SECRET",
        start_perms=ACCOUNTING_PERMS,
        default_next="/app/{slug}/finance",
        scope_separator=",",
        authorize_params={"access_type": "offline", "prompt": "consent"},
        revoke_url="https://accounts.zoho.com/oauth/v2/token/revoke",
        accounts_server_param="accounts-server",
        accounts_hosts=ZOHO_ACCOUNTS_HOSTS,
    )
)


def get_provider(key: str) -> OAuthProvider:
    p = PROVIDERS.get(key)
    if p is None:
        raise not_found("Unknown OAuth provider", "unknown_provider")
    return p


def client_credentials(p: OAuthProvider) -> tuple[str, str]:
    s = get_settings()
    cid, secret = getattr(s, p.client_id_setting, None), getattr(s, p.client_secret_setting, None)
    if not cid or not secret:
        raise NotConfigured(f"{p.label} OAuth ({p.client_id_setting} / {p.client_secret_setting})")
    return cid, secret


def redirect_uri(p: OAuthProvider) -> str:
    """OAUTH_REDIRECT_BASE (an origin, optionally with the /api/clefs path of
    the web rewrite) or PUBLIC_API_ORIGIN, plus the callback path. Register
    exactly this URL with the provider."""
    s = get_settings()
    base = (s.OAUTH_REDIRECT_BASE or s.PUBLIC_API_ORIGIN).rstrip("/")
    return f"{base}/public/oauth/{p.key}/callback"


# ─── Sealing (lazy: clefs.core.privacy owns the key) ───


def _seal(text: str) -> bytes:
    from clefs.core import privacy

    return privacy.seal(text)


def _unseal(blob: Any) -> str | None:
    from clefs.core import privacy

    return privacy.unseal(blob)


# ─── HTTP ───

_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Route every provider call through `transport` (tests); None = network."""
    global _transport
    _transport = transport


def _http(timeout: httpx.Timeout = TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout, transport=_transport, follow_redirects=False)


def _token_url(p: OAuthProvider, meta: Mapping[str, Any] | None) -> str:
    server = (meta or {}).get("accounts_server")
    if p.accounts_server_param and server:
        return f"{server}/oauth/v2/token"
    return p.token_url


def _token_request(p: OAuthProvider, url: str, data: dict[str, str]) -> dict:
    """POST a form to a token endpoint with the provider's client auth.
    Messages never quote tokens, codes or the client secret."""
    cid, secret = client_credentials(p)
    body = dict(data)
    auth: tuple[str, str] | None = None
    if p.client_auth == "basic":
        auth = (cid, secret)
    else:
        body["client_id"] = cid
        body["client_secret"] = secret
    try:
        with _http() as c:
            r = c.post(url, data=body, auth=auth, headers={"Accept": "application/json"})
    except httpx.HTTPError as exc:
        raise OAuthError(f"{p.label} could not be reached ({type(exc).__name__})", error="network", transient=True) from None
    try:
        payload = r.json()
    except ValueError:
        payload = None
    payload = payload if isinstance(payload, dict) else {}
    if r.status_code >= 500 or r.status_code == 429:
        raise OAuthError(f"{p.label} token endpoint failed (HTTP {r.status_code})", error=f"http_{r.status_code}",
                         transient=True, status_code=r.status_code)
    err = payload.get("error")
    if r.status_code != 200 or err or not payload.get("access_token"):
        code = str(err or f"http_{r.status_code}")[:64]
        raise OAuthError(f"{p.label} refused the token request ({code})", error=code, transient=False,
                         status_code=r.status_code)
    return payload


def _expires_at(payload: Mapping[str, Any], now: datetime) -> datetime:
    try:
        secs = int(float(payload.get("expires_in") or DEFAULT_EXPIRES_IN))
    except (TypeError, ValueError):
        secs = DEFAULT_EXPIRES_IN
    return now + timedelta(seconds=max(60, secs))


def _granted_scopes(p: OAuthProvider, payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("scope")
    if isinstance(raw, list):
        return [str(s) for s in raw if s]
    if isinstance(raw, str) and raw.strip():
        return [s for s in re.split(r"[\s,]+", raw.strip()) if s]
    return list(p.scopes)


def _revoke(p: OAuthProvider, token: str, meta: Mapping[str, Any] | None) -> None:
    """Best effort: a failed revoke never blocks a disconnect (the tokens are
    already gone from our side)."""
    if not p.revoke_url:
        return
    url = p.revoke_url
    server = (meta or {}).get("accounts_server")
    if p.accounts_server_param and server:
        url = f"{server}/oauth/v2/token/revoke"
    try:
        cid, secret = client_credentials(p)
        auth = (cid, secret) if p.client_auth == "basic" else None
        with _http(REVOKE_TIMEOUT) as c:
            if p.revoke_json:
                c.post(url, json={"token": token}, auth=auth, headers={"Accept": "application/json"})
            else:
                c.post(url, data={"token": token}, auth=auth)
    except Exception as exc:  # noqa: BLE001 - best effort by design
        log.warning("%s token revoke failed (%s)", p.key, type(exc).__name__)


# ─── Connections ───

_PUBLIC_COLUMNS = (
    "id, hotel_slug, provider, account_ref, scopes, expires_at, status, meta, last_error, connected_by, "
    "connected_at, last_used_at, last_refreshed_at, updated_at"
)


def get_connection(cur: Any, hotel_slug: str, provider: str) -> dict | None:
    """The connection without its tokens, or None."""
    cur.execute(f"SELECT {_PUBLIC_COLUMNS} FROM oauth_connections WHERE hotel_slug = %s AND provider = %s",
                (hotel_slug, provider))
    row = cur.fetchone()
    return dict(row) if row else None


def list_connections(cur: Any, hotel_slug: str) -> list[dict]:
    """One entry per registered provider: its connection (without tokens), or
    status 'not_connected'; `configured` says whether Clefs holds the app
    credentials for it."""
    cur.execute(f"SELECT {_PUBLIC_COLUMNS} FROM oauth_connections WHERE hotel_slug = %s", (hotel_slug,))
    rows = {r["provider"]: dict(r) for r in cur.fetchall()}
    s = get_settings()
    out = []
    for key, p in PROVIDERS.items():
        row = rows.get(key)
        out.append(
            {
                "provider": key,
                "label": p.label,
                "configured": bool(getattr(s, p.client_id_setting, None) and getattr(s, p.client_secret_setting, None)),
                "status": row["status"] if row else "not_connected",
                "account_ref": row["account_ref"] if row else None,
                "scopes": list(row["scopes"] or []) if row else [],
                "expires_at": row["expires_at"] if row else None,
                "connected_at": row["connected_at"] if row else None,
                "connected_by": row["connected_by"] if row else None,
                "last_used_at": row["last_used_at"] if row else None,
                "last_error": row["last_error"] if row else None,
            }
        )
    return out


def set_account_ref(cur: Any, hotel_slug: str, provider: str, account_ref: str | None, *,
                    meta: Mapping[str, Any] | None = None) -> bool:
    """Record the provider-side account once a module has looked it up (the
    Xero tenant from GET /connections). `meta` is merged into the row's meta;
    never put a secret in it."""
    cur.execute(
        "UPDATE oauth_connections SET account_ref = %s, meta = meta || %s, updated_at = %s "
        "WHERE hotel_slug = %s AND provider = %s",
        (account_ref, db.Json(dict(meta or {})), clock.now(), hotel_slug, provider),
    )
    return cur.rowcount > 0


def disconnect(cur: Any, hotel_slug: str, provider: str, *, actor: Any) -> bool:
    """Forget the tokens (the row stays, status 'disconnected', for the audit
    trail) and, after commit, ask the provider to revoke the grant."""
    p = get_provider(provider)
    cur.execute(
        "SELECT id, status, access_token_enc, refresh_token_enc, meta FROM oauth_connections "
        "WHERE hotel_slug = %s AND provider = %s FOR UPDATE",
        (hotel_slug, p.key),
    )
    row = cur.fetchone()
    if row is None or row["status"] == "disconnected":
        return False
    token: str | None = None
    for col in ("refresh_token_enc", "access_token_enc"):
        if token is None and row[col] is not None:
            try:
                token = _unseal(row[col])
            except (ValueError, ClefsError):
                token = None
    cur.execute(
        "UPDATE oauth_connections SET status = 'disconnected', access_token_enc = NULL, refresh_token_enc = NULL, "
        "expires_at = NULL, updated_at = %s WHERE id = %s",
        (clock.now(), row["id"]),
    )
    audit(cur, actor, "core.oauth.disconnect", "oauth_connection", row["id"], before={"status": row["status"]},
          after={"status": "disconnected"}, meta={"provider": p.key}, hotel_slug=hotel_slug)
    if token:
        meta = dict(row["meta"] or {})
        db.on_commit(cur, lambda: _revoke(p, token, meta))
    return True


# ─── Access tokens ───


def _fresh(row: Mapping[str, Any], now: datetime) -> bool:
    return row["access_token_enc"] is not None and (row["expires_at"] is None or row["expires_at"] > now + REFRESH_SKEW)


def _select_for_refresh(c: Any, hotel_slug: str, provider: str, *, skip_locked: bool) -> dict | None:
    """The row, locked. With skip_locked a row another transaction holds reads
    as None (never an error: a LockNotAvailable would make db() discard its
    pooled connection as broken)."""
    c.execute(
        "SELECT * FROM oauth_connections WHERE hotel_slug = %s AND provider = %s FOR UPDATE"
        + (" SKIP LOCKED" if skip_locked else ""),
        (hotel_slug, provider),
    )
    row = c.fetchone()
    return dict(row) if row else None


def _not_connected(p: OAuthProvider, why: str = "is not connected for this hotel") -> NotConnected:
    return NotConnected(f"{p.label} {why}", provider=p.key)


def _refresh_locked(c: Any, p: OAuthProvider, row: dict | None) -> tuple[str | None, ClefsError | None]:
    """Refresh a locked row. Returns (token, None), or (None, error) after
    committing any status change the caller must keep (so it is returned, not
    raised, while the transaction is still open)."""
    now = clock.now()
    if row is None or row["status"] != "active":
        return None, _not_connected(p)
    if _fresh(row, now):  # another worker refreshed while we waited
        return _unseal(row["access_token_enc"]), None
    refresh = _unseal(row["refresh_token_enc"]) if row["refresh_token_enc"] is not None else None
    if not refresh:
        c.execute(
            "UPDATE oauth_connections SET status = 'expired', access_token_enc = NULL, last_error = %s, updated_at = %s "
            "WHERE id = %s",
            ("access expired and no refresh token was issued", now, row["id"]),
        )
        return None, _not_connected(p, "access has expired; reconnect it")
    try:
        payload = _token_request(p, _token_url(p, row["meta"]), {"grant_type": "refresh_token", "refresh_token": refresh})
    except OAuthError as exc:
        if exc.transient or exc.error not in REVOKED_ERRORS:
            c.execute("UPDATE oauth_connections SET last_error = %s, updated_at = %s WHERE id = %s",
                      (f"refresh failed: {exc.error}", now, row["id"]))
            return None, exc
        c.execute(
            "UPDATE oauth_connections SET status = 'revoked', access_token_enc = NULL, refresh_token_enc = NULL, "
            "last_error = %s, updated_at = %s WHERE id = %s",
            (f"refresh refused: {exc.error}", now, row["id"]),
        )
        audit(c, None, "core.oauth.revoked", "oauth_connection", row["id"], before={"status": "active"},
              after={"status": "revoked"}, meta={"provider": p.key, "error": exc.error}, hotel_slug=row["hotel_slug"])
        log.warning("%s connection of %s was revoked (%s)", p.key, row["hotel_slug"], exc.error)
        return None, _not_connected(p, "access was revoked or has lapsed; reconnect it")
    access = str(payload["access_token"])
    new_refresh = payload.get("refresh_token")
    c.execute(
        """
        UPDATE oauth_connections SET access_token_enc = %s, refresh_token_enc = COALESCE(%s, refresh_token_enc),
               expires_at = %s, last_error = NULL, last_refreshed_at = %s, last_used_at = %s, updated_at = %s
        WHERE id = %s
        """,
        (_seal(access), _seal(str(new_refresh)) if new_refresh else None, _expires_at(payload, now), now, now, now,
         row["id"]),
    )
    return access, None


def _refresh(cur: Any, p: OAuthProvider, hotel_slug: str) -> str:
    """Refresh in a transaction of our own, so a rotated refresh token is
    committed whatever the caller's transaction does next. When another
    transaction holds the row, wait on the caller's cursor instead: either a
    concurrent refresher (we then read its fresh token) or the caller itself
    (the write must then go through its transaction, or it would deadlock)."""
    token: str | None = None
    err: ClefsError | None = None
    with db.db() as w:
        row = _select_for_refresh(w, hotel_slug, p.key, skip_locked=True)
        if row is not None:
            token, err = _refresh_locked(w, p, row)
    if row is None:
        token, err = _refresh_locked(cur, p, _select_for_refresh(cur, hotel_slug, p.key, skip_locked=False))
    if err is not None:
        raise err
    assert token is not None
    return token


def _touch(row: Mapping[str, Any], now: datetime) -> None:
    """Throttled last_used_at, in its own short transaction and SKIP LOCKED,
    so it never holds a lock in (or waits on) the caller's transaction."""
    if row["last_used_at"] is not None and row["last_used_at"] > now - TOUCH_EVERY:
        return
    try:
        with db.db() as w:
            w.execute(
                "UPDATE oauth_connections SET last_used_at = %s WHERE id = "
                "(SELECT id FROM oauth_connections WHERE id = %s FOR UPDATE SKIP LOCKED)",
                (now, row["id"]),
            )
    except (psycopg2.Error, ClefsError):  # advisory only: a busy pool must not fail the caller
        log.debug("last_used_at not recorded for connection %s", row["id"])


def get_access_token(cur: Any, hotel_slug: str, provider: str) -> str:
    """A live access token for the hotel's connection, refreshed when it is
    within two minutes of expiry. Raises NotConnected (no connection, or the
    grant is gone) and OAuthError (the provider is down: retry later)."""
    p = get_provider(provider)
    cur.execute(
        "SELECT id, status, access_token_enc, expires_at, last_used_at FROM oauth_connections "
        "WHERE hotel_slug = %s AND provider = %s",
        (hotel_slug, p.key),
    )
    row = cur.fetchone()
    if row is None or row["status"] != "active":
        raise _not_connected(p)
    now = clock.now()
    if _fresh(row, now):
        try:
            token = _unseal(row["access_token_enc"])
        except ValueError:
            raise _not_connected(p, "token cannot be read (the encryption key changed); reconnect it") from None
        _touch(row, now)
        assert token is not None
        return token
    return _refresh(cur, p, hotel_slug)


# ─── Authorisation flow ───


def _pkce_pair() -> tuple[str, str]:
    verifier = tokens.new_token(48)  # 64 url-safe characters (RFC 7636: 43-128)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def start_flow(cur: Any, hotel_slug: str, provider: str, *, user_id: int, next_path: str | None = None) -> dict:
    """Create a single-use state and return {authorize_url, expires_in,
    provider}. Raises NotConfigured when the app credentials or the sealing
    key are missing (checked here, before the user leaves for the provider)."""
    p = get_provider(provider)
    cid, _secret = client_credentials(p)
    _seal("probe")  # fail now, not after the consent screen, when PII_ENC_KEY is missing
    now = clock.now()
    if not ratelimit.hit(cur, f"oauth:start:{user_id}", limit=START_LIMIT, window_s=600):
        raise too_many()
    # Opportunistic cleanup keeps the table tiny without a scheduled job.
    cur.execute("DELETE FROM oauth_states WHERE expires_at < %s", (now - timedelta(hours=1),))

    state = tokens.new_token(32)
    verifier, challenge = _pkce_pair() if p.pkce else (None, None)
    uri = redirect_uri(p)
    nxt = safe_next_path(next_path) or p.default_next.format(slug=hotel_slug)
    cur.execute(
        """
        INSERT INTO oauth_states (state_hash, hotel_slug, provider, user_id, next_path, redirect_uri,
                                  code_verifier_enc, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (tokens.hash_token(state), hotel_slug, p.key, user_id, nxt, uri, _seal(verifier) if verifier else None, now,
         now + STATE_TTL),
    )
    query: dict[str, str] = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": uri,
        "scope": p.scope_separator.join(p.scopes),
        "state": state,
        **dict(p.authorize_params),
    }
    if challenge:
        query["code_challenge"] = challenge
        query["code_challenge_method"] = "S256"
    return {
        "provider": p.key,
        "authorize_url": f"{p.authorize_url}?{urlencode(query)}",
        "expires_in": int(STATE_TTL.total_seconds()),
    }


@dataclass(frozen=True)
class CallbackResult:
    provider: str
    status: Status
    hotel_slug: str | None = None
    next_path: str | None = None
    error: str | None = None
    connection_id: int | None = None

    @property
    def redirect_url(self) -> str:
        """Back to the web app, with the outcome in the query string."""
        base = get_settings().web_base_url.rstrip("/")
        parts = urlsplit(safe_next_path(self.next_path) or "/app")
        q = parse_qsl(parts.query, keep_blank_values=True)
        q += [("oauth", self.provider), ("oauth_status", self.status)]
        if self.error:
            q.append(("oauth_error", self.error))
        return base + urlunsplit(("", "", parts.path, urlencode(q), parts.fragment))


def _accounts_server(p: OAuthProvider, params: Mapping[str, str]) -> str | None:
    """The Zoho data centre's accounts server, only when its host is on the
    allowlist: the code (and our client secret) must never go elsewhere."""
    if not p.accounts_server_param:
        return None
    raw = (params.get(p.accounts_server_param) or "").strip()
    if not raw:
        return urlsplit(p.token_url)._replace(path="", query="").geturl()
    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.hostname not in p.accounts_hosts or parts.port or parts.path not in ("", "/"):
        raise ValueError("accounts server not allowed")
    return f"https://{parts.hostname}"


def _same_user(principal: Principal | None, user_id: Any) -> bool:
    return (
        principal is not None
        and principal.kind == "user"
        and principal.user_id is not None
        and user_id is not None
        and int(principal.user_id) == int(user_id)
    )


def complete_flow(provider: str, params: Mapping[str, str], *, principal: Principal | None) -> CallbackResult:
    """Handle the provider's redirect. Never raises for a bad request: every
    outcome is a CallbackResult the route turns into a redirect.

    `principal` is whoever the browser completing the callback is signed in
    as. It must be the user who started the flow (RFC 6749 10.12, RFC 9700
    4.7: state bound to the user agent). WHY: the authorize URL is portable.
    Without this check, a manager at hotel A could start a flow, send the
    link to another hotel's owner, and have that owner's Google listing or
    Xero books sealed into hotel A the moment they click Allow on the
    genuine consent screen; a leaked in-flight link would equally let an
    outsider feed their own account into the victim hotel. PKCE does not
    help, because the verifier is ours and keyed only by the state."""
    p = get_provider(provider)
    state = params.get("state") or ""
    now = clock.now()
    row = None
    if state and len(state) <= 256:
        with db.db() as cur:
            # Consumed even on a session mismatch: a state that reached the
            # wrong browser is burnt, so the same link cannot be retried
            # against anyone else.
            cur.execute("DELETE FROM oauth_states WHERE state_hash = %s RETURNING *", (tokens.hash_token(state),))
            row = cur.fetchone()
    if row is None or row["provider"] != p.key:
        return CallbackResult(p.key, "error", error="invalid_state")
    if not _same_user(principal, row["user_id"]):
        # Before any code exchange, and with no hotel or next path in the
        # redirect: the browser holding this callback is not the one that
        # asked, so it learns nothing about the hotel that did.
        log.warning("%s callback for %s completed by a different or anonymous session; nothing connected",
                    p.key, row["hotel_slug"])
        return CallbackResult(p.key, "error", error="session_mismatch")
    base = {"provider": p.key, "hotel_slug": row["hotel_slug"], "next_path": row["next_path"]}
    if row["expires_at"] <= now:
        return CallbackResult(**base, status="error", error="state_expired")
    if params.get("error"):
        denied = params.get("error") == "access_denied"
        return CallbackResult(**base, status="denied" if denied else "error", error="access_denied" if denied else "provider_error")
    code = params.get("code")
    if not code or len(code) > 4096:
        return CallbackResult(**base, status="error", error="missing_code")

    meta: dict[str, Any] = {}
    try:
        server = _accounts_server(p, params)
    except ValueError:
        log.warning("%s callback named an accounts server outside the allowlist", p.key)
        return CallbackResult(**base, status="error", error="invalid_accounts_server")
    if server:
        meta["accounts_server"] = server
    if p.key == "qbo":
        meta["env"] = get_settings().QBO_ENV
    account_ref: str | None = None
    if p.account_ref_param:
        account_ref = (params.get(p.account_ref_param) or "").strip()[:200] or None

    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": row["redirect_uri"]}
    try:
        if row["code_verifier_enc"] is not None:
            data["code_verifier"] = _unseal(row["code_verifier_enc"]) or ""
        payload = _token_request(p, _token_url(p, meta), data)
    except OAuthError as exc:
        log.warning("%s code exchange for %s failed: %s", p.key, row["hotel_slug"], exc.error)
        return CallbackResult(**base, status="error", error="exchange_failed")
    except NotConfigured:
        return CallbackResult(**base, status="error", error="not_configured")
    except ValueError:  # the sealed verifier no longer opens (PII_ENC_KEY rotated mid-flow)
        return CallbackResult(**base, status="error", error="state_unreadable")

    scopes = _granted_scopes(p, payload)
    if any(s not in scopes for s in p.required_scopes):
        return CallbackResult(**base, status="error", error="scope_denied")
    if payload.get("api_domain"):
        meta["api_domain"] = str(payload["api_domain"])[:200]

    refresh = payload.get("refresh_token")
    try:
        with db.db() as cur:
            cur.execute(
                """
                INSERT INTO oauth_connections (hotel_slug, provider, account_ref, scopes, access_token_enc,
                    refresh_token_enc, expires_at, status, meta, last_error, connected_by, connected_at,
                    last_used_at, last_refreshed_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, NULL, %s, %s, NULL, %s, %s)
                ON CONFLICT (hotel_slug, provider) DO UPDATE SET
                    account_ref = EXCLUDED.account_ref, scopes = EXCLUDED.scopes,
                    access_token_enc = EXCLUDED.access_token_enc, refresh_token_enc = EXCLUDED.refresh_token_enc,
                    expires_at = EXCLUDED.expires_at, status = 'active', meta = EXCLUDED.meta, last_error = NULL,
                    connected_by = EXCLUDED.connected_by, connected_at = EXCLUDED.connected_at,
                    last_refreshed_at = EXCLUDED.last_refreshed_at, updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                (row["hotel_slug"], p.key, account_ref, scopes, _seal(str(payload["access_token"])),
                 _seal(str(refresh)) if refresh else None, _expires_at(payload, now), db.Json(meta), row["user_id"],
                 now, now, now),
            )
            conn_id = int(cur.fetchone()["id"])
            actor = Principal(kind="user", user_id=row["user_id"])
            audit(cur, actor, "core.oauth.connect", "oauth_connection", conn_id,
                  after={"provider": p.key, "scopes": scopes, "account_ref": account_ref, "has_refresh": bool(refresh)},
                  hotel_slug=row["hotel_slug"])
    except NotConfigured:
        return CallbackResult(**base, status="error", error="not_configured")
    return CallbackResult(**base, status="connected", connection_id=conn_id)


# ─── Routes ───


def _start_ctx(request: Request, provider: str) -> HotelCtx:
    """require_perm with the provider's own permissions (Google: visibility or
    reviews managers; accounting: finance.connect). require_perm reads {slug}
    from the path itself."""
    return require_perm(*get_provider(provider).start_perms)(request)


_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/hotels/{slug}/oauth")
def list_oauth_connections(slug: str, ctx: HotelCtx = Depends(require_perm("core.settings.view"))) -> list[dict]:
    with db.db() as cur:
        return list_connections(cur, ctx.slug)


@router.get("/hotels/{slug}/oauth/{provider}/start", response_model=None)
def oauth_start(
    slug: str,
    provider: str,
    next: str | None = Query(default=None, max_length=512),  # noqa: A002 - the public query name
    format: Literal["redirect", "json"] = "redirect",  # noqa: A002
    ctx: HotelCtx = Depends(_start_ctx),
) -> Response | dict:
    user_id = ctx.principal.user_id
    if ctx.principal.kind != "user" or user_id is None:
        raise ClefsError(403, "Connecting an account needs a signed-in person", "forbidden")
    with db.db() as cur:
        out = start_flow(cur, ctx.slug, provider, user_id=user_id, next_path=next)
    if format == "json":
        return out
    return RedirectResponse(out["authorize_url"], status_code=302, headers=_NO_STORE)


@router.delete("/hotels/{slug}/oauth/{provider}", status_code=204, response_model=None)
def oauth_disconnect(slug: str, provider: str, ctx: HotelCtx = Depends(_start_ctx)) -> Response:
    with db.db() as cur:
        if not disconnect(cur, ctx.slug, provider, actor=ctx):
            raise not_found("Not connected", "not_connected")
    return Response(status_code=204)


@router.get("/public/oauth/{provider}/callback", response_model=None)
def oauth_callback(provider: str, request: Request) -> Response:
    """Public (the provider redirects here), but bound to the session that
    started the flow. The redirect URI is served through the web origin
    (OAUTH_REDIRECT_BASE = <web>/api/clefs), so the provider's top-level GET
    carries the SameSite=Lax clefs_session cookie."""
    get_provider(provider)
    result = complete_flow(provider, dict(request.query_params), principal=current_principal(request))
    return RedirectResponse(result.redirect_url, status_code=302, headers=_NO_STORE)
