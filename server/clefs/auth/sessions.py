"""
User sessions: opaque tokens in an httpOnly cookie on the web origin.

The browser reaches the API through the Next rewrite /api/clefs/*, so the
cookie FastAPI sets is first-party on www, previews and localhost alike, and
XSS cannot read it. Scripts may send the same token as `Authorization: Bearer`.

Lookups are one indexed query, cached in-process for CACHE_TTL_S. Revocation
evicts the local cache immediately; other processes notice within 30 s.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from fastapi import Response

from clefs import db
from clefs.auth.tokens import hash_token, new_token
from clefs.core import clock
from clefs.db import Json
from clefs.settings import get_settings

SESSION_COOKIE = "clefs_session"
DEVICE_COOKIE = "clefs_device"
CACHE_TTL_S = 30.0
TOUCH_EVERY = timedelta(minutes=5)
MAGIC_SESSION_TTL = timedelta(hours=12)
# Browsers cap cookie lifetime at 400 days; devices stay paired until revoked.
DEVICE_COOKIE_MAX_AGE_S = 400 * 24 * 3600


@dataclass(frozen=True)
class SessionInfo:
    session_id: int
    user_id: int
    kind: str  # web | magic | api
    expires_at: datetime
    name: str
    email: str | None
    phone_e164: str | None
    locale: str | None
    is_clefs_admin: bool
    last_seen_at: datetime | None = None
    scope: dict = field(default_factory=dict)

    def user_json(self) -> dict[str, Any]:
        return {
            "id": self.user_id,
            "name": self.name,
            "email": self.email,
            "is_clefs_admin": self.is_clefs_admin,
            "phone_e164": self.phone_e164,
            "locale": self.locale,
        }


_cache: dict[bytes, tuple[float, SessionInfo | None]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _evict(pred) -> None:
    with _cache_lock:
        for k in [k for k, (_, v) in _cache.items() if v is not None and pred(v)]:
            _cache.pop(k, None)


# ─── Create / look up / revoke ───


def create_session(
    cur: Any,
    user_id: int,
    *,
    kind: str = "web",
    scope: dict | None = None,
    ttl: timedelta | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> tuple[str, int, datetime]:
    """Returns (token, session_id, expires_at). Only the token's hash is stored."""
    if ttl is None:
        ttl = MAGIC_SESSION_TTL if kind == "magic" else timedelta(days=get_settings().SESSION_TTL_DAYS)
    token = new_token()
    expires_at = clock.now() + ttl
    cur.execute(
        """
        INSERT INTO auth_sessions (token_hash, user_id, kind, scope, expires_at, last_seen_at, ip, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (hash_token(token), user_id, kind, Json(scope or {}), expires_at, clock.now(), ip, (user_agent or "")[:400] or None),
    )
    return token, cur.fetchone()["id"], expires_at


def lookup_session(token: str | None) -> SessionInfo | None:
    if not token:
        return None
    key = hash_token(token)
    now_mono = _time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] > now_mono:
        info = hit[1]
        if info is None or info.expires_at > clock.now():
            return info
    with db.db() as cur:
        cur.execute(
            """
            SELECT s.id, s.user_id, s.kind, s.scope, s.expires_at, s.last_seen_at,
                   u.name, u.email, u.phone_e164, u.locale, u.is_clefs_admin
            FROM auth_sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.revoked_at IS NULL AND s.expires_at > %s AND u.is_active
            """,
            (key, clock.now()),
        )
        row = cur.fetchone()
        info = None
        if row is not None:
            info = SessionInfo(
                session_id=row["id"],
                user_id=row["user_id"],
                kind=row["kind"],
                expires_at=row["expires_at"],
                name=row["name"],
                email=row["email"],
                phone_e164=row["phone_e164"],
                locale=row["locale"],
                is_clefs_admin=row["is_clefs_admin"],
                last_seen_at=row["last_seen_at"],
                scope=row["scope"] or {},
            )
            # Throttled so a busy dashboard does not write on every request.
            if row["last_seen_at"] is None or clock.now() - row["last_seen_at"] > TOUCH_EVERY:
                cur.execute("UPDATE auth_sessions SET last_seen_at = %s WHERE id = %s", (clock.now(), row["id"]))
    with _cache_lock:
        _cache[key] = (now_mono + CACHE_TTL_S, info)
    return info


def revoke_session(cur: Any, session_id: int) -> None:
    cur.execute("UPDATE auth_sessions SET revoked_at = %s WHERE id = %s AND revoked_at IS NULL", (clock.now(), session_id))
    _evict(lambda s: s.session_id == session_id)


def revoke_user_sessions(cur: Any, user_id: int, *, except_session_id: int | None = None) -> int:
    cur.execute(
        "UPDATE auth_sessions SET revoked_at = %s WHERE user_id = %s AND revoked_at IS NULL AND id IS DISTINCT FROM %s",
        (clock.now(), user_id, except_session_id),
    )
    _evict(lambda s: s.user_id == user_id and s.session_id != except_session_id)
    return cur.rowcount


def evict_user(user_id: int) -> None:
    """Drop cached sessions for a user whose flags changed (admin, active)."""
    _evict(lambda s: s.user_id == user_id)


# ─── Cookies ───


def _cookie_kwargs() -> dict[str, Any]:
    # No Domain attribute: the cookie belongs to whichever web origin the
    # browser used (www, a preview, localhost) via the Next rewrite.
    return {"httponly": True, "samesite": "lax", "secure": get_settings().COOKIE_SECURE, "path": "/"}


def set_session_cookie(response: Response, token: str, expires_at: datetime) -> None:
    max_age = max(0, int((expires_at - clock.now()).total_seconds()))
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, **_cookie_kwargs())


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, **_cookie_kwargs())


def set_device_cookie(response: Response, token: str) -> None:
    response.set_cookie(DEVICE_COOKIE, token, max_age=DEVICE_COOKIE_MAX_AGE_S, **_cookie_kwargs())


def clear_device_cookie(response: Response) -> None:
    response.delete_cookie(DEVICE_COOKIE, **_cookie_kwargs())
