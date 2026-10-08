"""
Intake channel accounts (04 spec 4.1 inq_channel_accounts, 4.3).

- web_widget: config {widget_key, allowed_origins[]}. The key is public (it
  sits in the hotel's page); it only ties a form post to the hotel's widget
  and is rotated from the console.
- meta_page: config {page_id, page_name?}; the page access token sealed in
  secret_enc. Saving subscribes the app to the page's `leadgen` field; the
  webhook finds the hotel by page_id (unique across hotels).
- google_leadform: the URL key (only its sha256 is stored, as
  webhook_key_hash) and the `google_key` Google sends in each body (sealed).
"""

from __future__ import annotations

import hashlib
import secrets
from typing import Any

import httpx

from clefs.core import clock, privacy
from clefs.core.errors import ClefsError, bad_request
from clefs.db import Json
from clefs.settings import get_settings

INTAKE_KINDS = ("web_widget", "meta_page", "google_leadform")
GRAPH_BASE = "https://graph.facebook.com"
_transport: httpx.BaseTransport | None = None  # tests swap in a MockTransport


def set_transport(t: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = t


def graph_client() -> httpx.Client:
    return httpx.Client(base_url=GRAPH_BASE, timeout=15.0, transport=_transport)


def graph_version() -> str:
    return get_settings().WA_API_VERSION


def key_hash(key: str) -> bytes:
    return hashlib.sha256((key or "").encode("utf-8")).digest()


# ─── Reads ───

_PUBLIC_COLS = "id, hotel_slug, kind, config, status, connected_by, connected_at"


def get_account(cur: Any, hotel_slug: str, kind: str, *, with_secret: bool = False) -> dict | None:
    cols = "*" if with_secret else _PUBLIC_COLS
    cur.execute(f"SELECT {cols} FROM inq_channel_accounts WHERE hotel_slug = %s AND kind = %s", (hotel_slug, kind))
    row = cur.fetchone()
    return dict(row) if row else None


def list_accounts(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(f"SELECT {_PUBLIC_COLS} FROM inq_channel_accounts WHERE hotel_slug = %s ORDER BY kind", (hotel_slug,))
    return [to_api(dict(r)) for r in cur.fetchall()]


def to_api(row: dict) -> dict:
    """Never a secret; the widget key is shown (it is public by design)."""
    out = {k: row[k] for k in ("id", "kind", "config", "status", "connected_by", "connected_at") if k in row}
    if row["kind"] == "meta_page":
        out["config"] = {k: v for k, v in (row.get("config") or {}).items() if k in ("page_id", "page_name", "subscribe")}
    return out


def _upsert(cur: Any, hotel_slug: str, kind: str, *, config: dict, secret: str | None = None,
            webhook_key: str | None = None, status: str = "active", actor_user_id: int | None = None) -> dict:
    cur.execute(
        """
        INSERT INTO inq_channel_accounts (hotel_slug, kind, config, secret_enc, webhook_key_hash, status,
                                          connected_by, connected_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, kind) DO UPDATE SET
          config = EXCLUDED.config,
          secret_enc = COALESCE(EXCLUDED.secret_enc, inq_channel_accounts.secret_enc),
          webhook_key_hash = COALESCE(EXCLUDED.webhook_key_hash, inq_channel_accounts.webhook_key_hash),
          status = EXCLUDED.status, connected_by = EXCLUDED.connected_by, connected_at = EXCLUDED.connected_at
        RETURNING id, hotel_slug, kind, config, status, connected_by, connected_at
        """,
        (hotel_slug, kind, Json(config), privacy.seal(secret) if secret else None,
         key_hash(webhook_key) if webhook_key else None, status, actor_user_id, clock.now()),
    )
    return dict(cur.fetchone())


# ─── Web widget ───


def ensure_widget(cur: Any, hotel_slug: str, *, actor_user_id: int | None = None) -> dict:
    row = get_account(cur, hotel_slug, "web_widget")
    if row is not None and row["status"] == "active" and (row.get("config") or {}).get("widget_key"):
        return row
    # New, or reconnected after a disconnect: a fresh key (the old one may
    # still sit in a page the hotel took down on purpose).
    return _upsert(cur, hotel_slug, "web_widget", config={"widget_key": secrets.token_urlsafe(18), "allowed_origins": []},
                   actor_user_id=actor_user_id)


def rotate_widget(cur: Any, hotel_slug: str, *, actor_user_id: int | None = None) -> dict:
    cfg = dict((get_account(cur, hotel_slug, "web_widget") or {}).get("config") or {})
    cfg["widget_key"] = secrets.token_urlsafe(18)
    cfg.setdefault("allowed_origins", [])
    return _upsert(cur, hotel_slug, "web_widget", config=cfg, actor_user_id=actor_user_id)


def set_widget_origins(cur: Any, hotel_slug: str, origins: list[str], *, actor_user_id: int | None = None) -> dict:
    clean = []
    for o in origins or []:
        o = (o or "").strip().rstrip("/")
        if not (o.startswith("https://") or o.startswith("http://localhost")) or any(c in o for c in " ;,'\""):
            raise bad_request(f"not an origin: {o!r} (use https://host)")
        clean.append(o)
    row = ensure_widget(cur, hotel_slug, actor_user_id=actor_user_id)
    cfg = {**(row.get("config") or {}), "allowed_origins": sorted(set(clean))[:20]}
    return _upsert(cur, hotel_slug, "web_widget", config=cfg, actor_user_id=actor_user_id)


def widget_matches(cur: Any, hotel_slug: str, key: str | None) -> dict | None:
    row = get_account(cur, hotel_slug, "web_widget")
    want = ((row or {}).get("config") or {}).get("widget_key")
    if not row or row["status"] != "active" or not want or not key:
        return None
    return row if secrets.compare_digest(str(want), str(key)) else None


# ─── Meta page (lightweight, no OAuth) ───


def connect_meta_page(cur: Any, hotel_slug: str, *, page_id: str, page_access_token: str,
                      page_name: str | None = None, actor_user_id: int | None = None) -> dict:
    """Seal the token, subscribe the app to the page's leadgen field and keep
    the answer in `status` (active | subscribe_failed). A page already
    connected to another hotel is 409 page_in_use."""
    page_id = (page_id or "").strip()
    if not page_id.isdigit() or not page_access_token:
        raise bad_request("page_id (digits) and page_access_token are required")
    cur.execute(
        "SELECT hotel_slug FROM inq_channel_accounts WHERE kind = 'meta_page' AND config->>'page_id' = %s",
        (page_id,),
    )
    other = cur.fetchone()
    if other is not None and other["hotel_slug"] != hotel_slug:
        raise ClefsError(409, "this Facebook page is connected to another hotel", "page_in_use")
    status, detail = "active", None
    try:
        with graph_client() as c:
            r = c.post(f"/{graph_version()}/{page_id}/subscribed_apps",
                       params={"subscribed_fields": "leadgen", "access_token": page_access_token})
        if r.status_code >= 400 or not (r.json() or {}).get("success"):
            status, detail = "subscribe_failed", f"http {r.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        status, detail = "subscribe_failed", type(exc).__name__
    cfg = {"page_id": page_id, "page_name": page_name, "subscribe": {"at": clock.now().isoformat(), "error": detail}}
    return _upsert(cur, hotel_slug, "meta_page", config=cfg, secret=page_access_token, status=status,
                   actor_user_id=actor_user_id)


def meta_account_for_page(cur: Any, page_id: str) -> dict | None:
    cur.execute(
        "SELECT * FROM inq_channel_accounts WHERE kind = 'meta_page' AND config->>'page_id' = %s",
        (str(page_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Google Ads lead form ───


def connect_google_leadform(cur: Any, hotel_slug: str, *, google_key: str | None = None,
                            actor_user_id: int | None = None) -> dict:
    """Mint the webhook URL key (shown once) and seal the google_key the
    owner pastes into Google Ads (generated when not given)."""
    url_key = secrets.token_urlsafe(24)
    google_key = (google_key or "").strip() or secrets.token_urlsafe(24)
    if len(google_key) < 16:
        raise bad_request("google_key must be at least 16 characters")
    row = _upsert(cur, hotel_slug, "google_leadform", config={"key_set_at": clock.now().isoformat()},
                  secret=google_key, webhook_key=url_key, actor_user_id=actor_user_id)
    return {**to_api(row), "webhook_path": f"/webhooks/inquiries/google-leadform/{url_key}", "google_key": google_key}


def google_account_for_key(cur: Any, url_key: str) -> dict | None:
    cur.execute(
        "SELECT * FROM inq_channel_accounts WHERE kind = 'google_leadform' AND webhook_key_hash = %s",
        (key_hash(url_key),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def secret_of(row: dict) -> str | None:
    return privacy.unseal(row.get("secret_enc"))


def disconnect(cur: Any, hotel_slug: str, kind: str) -> bool:
    cur.execute(
        "UPDATE inq_channel_accounts SET status = 'disconnected', secret_enc = NULL, webhook_key_hash = NULL "
        "WHERE hotel_slug = %s AND kind = %s",
        (hotel_slug, kind),
    )
    return cur.rowcount > 0
