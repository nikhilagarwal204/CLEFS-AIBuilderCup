"""
Magic links: one-tap access from WhatsApp or email to the brief and actions.

    token = create_link(cur, user_id=7, hotel_slug="demo-dubai", purpose="brief",
                        next_path="/app/demo-dubai/brief")
    url = f"{settings.web_base_url}{link_path(token)}"

Rules (foundation spec C):
  - opaque, stored hashed, valid 24 h, at most 3 uses;
  - exchanged ONLY by POST from the landing page. WhatsApp's link-preview
    fetcher GETs every URL in a message; a GET exchange would burn the token
    before the owner ever taps it;
  - the resulting session is kind='magic', scoped to that hotel with purposes
    brief + actions, and lasts 12 h. Full console access needs a password.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from clefs.audit import audit
from clefs.auth import sessions
from clefs.auth.tokens import hash_token, new_token
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.settings import get_settings

log = logging.getLogger("clefs.auth.magic")

LINK_TTL = timedelta(hours=24)
MAX_USES = 3
PURPOSES = ("brief", "login", "action")
SESSION_PURPOSES = ["brief", "actions"]
INVALID = "This link has expired or has already been used. Ask for a new one."


def link_path(token: str) -> str:
    return f"/auth/magic/{token}"


# ─── Delivery ───
# The notify package (contracts lane) registers a sender that sends the link
# by WhatsApp or email: fn(user, hotel_slug, url, purpose) -> delivered?
# Without one, a development process (Settings.is_dev: ENV explicitly
# development/test, or unset on a local database) logs the link so local
# testing works; nothing else ever logs it (it is a credential).

MagicSender = Callable[[dict, str, str, str], bool]
_senders: list[MagicSender] = []


def register_sender(fn: MagicSender) -> MagicSender:
    if fn not in _senders:
        _senders.append(fn)
    return fn


def deliver(user: dict, hotel_slug: str, url: str, purpose: str) -> bool:
    for fn in list(_senders):
        try:
            if fn(user, hotel_slug, url, purpose):
                return True
        except Exception:
            log.exception("magic link sender %r failed", fn)
    if get_settings().is_dev:
        log.warning("[dev] magic link for user %s at %s: %s", user.get("id"), hotel_slug, url)
    return False


def request_link(
    cur: Any,
    *,
    email: str | None,
    phone: str | None,
    purpose: str = "login",
    hotel_slug: str | None = None,
    next_path: str | None = None,
) -> tuple[dict, str, str] | None:
    """Find an active user with an active membership and mint a link.
    Returns (user, hotel_slug, token) or None. Callers answer the same either
    way, so the endpoint never reveals whether an account exists."""
    if email:
        cur.execute("SELECT id, name, email, phone_e164 FROM users WHERE email = %s AND is_active", (email.strip().lower(),))
    elif phone:
        cur.execute("SELECT id, name, email, phone_e164 FROM users WHERE phone_e164 = %s AND is_active", (phone,))
    else:
        return None
    user = cur.fetchone()
    if user is None:
        return None
    cur.execute(
        """
        SELECT m.hotel_slug FROM hotel_memberships m JOIN hotels h ON h.slug = m.hotel_slug
        WHERE m.user_id = %s AND m.is_active AND (%s::TEXT IS NULL OR m.hotel_slug = %s)
        ORDER BY h.name LIMIT 1
        """,
        (user["id"], hotel_slug, hotel_slug),
    )
    m = cur.fetchone()
    if m is None:
        return None
    token = create_link(cur, user_id=user["id"], hotel_slug=m["hotel_slug"], purpose=purpose, next_path=next_path)
    return dict(user), m["hotel_slug"], token


# C0 controls and DEL. The WHATWG URL parser (browsers, Next's router) strips
# tab and newline, so "/\t/evil.example" passes a prefix check yet resolves
# to https://evil.example.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_PROBE_ORIGIN = "https://clefs.invalid"


def safe_next_path(next_path: str | None) -> str | None:
    """A same-origin relative path, or None. Validated by parsing, not by
    prefix: the path is resolved against a probe origin and must stay on it."""
    if not next_path or not next_path.startswith("/") or next_path.startswith("//"):
        return None  # "//host" and "///host" are network paths in browsers
    if "\\" in next_path or _CONTROL_CHARS.search(next_path):
        return None
    try:
        resolved = urlsplit(urljoin(_PROBE_ORIGIN + "/", next_path))
    except ValueError:
        return None
    if f"{resolved.scheme}://{resolved.netloc}" != _PROBE_ORIGIN:
        return None
    return next_path


def _safe_next(next_path: str | None, hotel_slug: str) -> str:
    return safe_next_path(next_path) or f"/app/{hotel_slug}"


def create_link(
    cur: Any,
    *,
    user_id: int,
    hotel_slug: str,
    purpose: str,
    next_path: str | None = None,
    scope: dict | None = None,
    ttl: timedelta = LINK_TTL,
    max_uses: int = MAX_USES,
) -> str:
    """Store a new link and return its raw token (build the URL with link_path)."""
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}")
    token = new_token()
    cur.execute(
        """
        INSERT INTO magic_links (token_hash, user_id, hotel_slug, purpose, next_path, scope, expires_at, max_uses)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            hash_token(token),
            user_id,
            hotel_slug,
            purpose,
            _safe_next(next_path, hotel_slug),
            Json(scope or {}),
            clock.now() + ttl,
            max_uses,
        ),
    )
    return token


def peek(cur: Any, token: str) -> dict:
    """Validate a link WITHOUT using it up: not expired, user active and
    (unless a Clefs admin) an active member of the link's hotel. Returns
    {id, user_id, hotel_slug, purpose, next_path, scope, expires_at, uses,
    max_uses}; 401 magic_link_invalid otherwise. For pages that render from
    a link on every open (Brief's mobile brief); `uses` is not checked,
    because the page is re-read after the one exchange."""
    if not token or len(token) > 200:
        raise ClefsError(401, INVALID, "magic_link_invalid")
    cur.execute(
        """
        SELECT ml.id, ml.user_id, ml.hotel_slug, ml.purpose, ml.next_path, ml.scope, ml.expires_at,
               ml.max_uses, ml.uses, u.is_clefs_admin, u.is_active,
               EXISTS (SELECT 1 FROM hotel_memberships m WHERE m.user_id = ml.user_id
                       AND m.hotel_slug = ml.hotel_slug AND m.is_active) AS is_member
        FROM magic_links ml JOIN users u ON u.id = ml.user_id
        WHERE ml.token_hash = %s
        """,
        (hash_token(token),),
    )
    link = cur.fetchone()
    if (link is None or not link["is_active"] or link["expires_at"] <= clock.now()
            or not (link["is_member"] or link["is_clefs_admin"])):
        raise ClefsError(401, INVALID, "magic_link_invalid")
    keys = ("id", "user_id", "hotel_slug", "purpose", "next_path", "scope", "expires_at", "uses", "max_uses")
    return {k: link[k] for k in keys}


def exchange(cur: Any, token: str, *, ip: str | None = None, user_agent: str | None = None) -> tuple[str, Any, dict]:
    """Consume one use. Returns (session_token, expires_at, response_body)."""
    cur.execute(
        """
        SELECT ml.id, ml.user_id, ml.hotel_slug, ml.purpose, ml.next_path, ml.scope, ml.expires_at,
               ml.max_uses, ml.uses, u.name, u.email, u.phone_e164, u.locale, u.is_clefs_admin, u.is_active
        FROM magic_links ml JOIN users u ON u.id = ml.user_id
        WHERE ml.token_hash = %s
        FOR UPDATE OF ml
        """,
        (hash_token(token or ""),),
    )
    link = cur.fetchone()
    if link is None or not link["is_active"] or link["expires_at"] <= clock.now() or link["uses"] >= link["max_uses"]:
        raise ClefsError(401, INVALID, "magic_link_invalid")
    cur.execute(
        "SELECT 1 FROM hotel_memberships WHERE user_id = %s AND hotel_slug = %s AND is_active",
        (link["user_id"], link["hotel_slug"]),
    )
    if cur.fetchone() is None and not link["is_clefs_admin"]:
        raise ClefsError(401, INVALID, "magic_link_invalid")
    cur.execute(
        "UPDATE magic_links SET uses = uses + 1, last_used_at = %s WHERE id = %s",
        (clock.now(), link["id"]),
    )
    scope = {**(link["scope"] or {}), "hotel": link["hotel_slug"], "purposes": SESSION_PURPOSES, "magic_link_id": link["id"]}
    session_token, session_id, expires_at = sessions.create_session(
        cur, link["user_id"], kind="magic", scope=scope, ip=ip, user_agent=user_agent
    )
    user = {
        "id": link["user_id"],
        "name": link["name"],
        "email": link["email"],
        "is_clefs_admin": link["is_clefs_admin"],
        "phone_e164": link["phone_e164"],
        "locale": link["locale"],
    }
    audit(
        cur,
        _UserActor(link["user_id"], link["name"]),
        "auth.magic.exchange",
        "magic_link",
        link["id"],
        meta={"purpose": link["purpose"], "use": link["uses"] + 1, "session_id": session_id},
        hotel_slug=link["hotel_slug"],
    )
    # Checked again on the way out: a row written before safe_next_path
    # existed, or by a script that bypassed create_link, must not turn a
    # one-tap sign-in into an open redirect.
    body = {"next_path": _safe_next(link["next_path"], link["hotel_slug"]), "user": user, "hotel_slug": link["hotel_slug"]}
    return session_token, expires_at, body


class _UserActor:
    kind = "user"

    def __init__(self, user_id: int, label: str):
        self.user_id = user_id
        self.device_id = None
        self.label = label
