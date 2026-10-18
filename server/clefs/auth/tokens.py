"""
Tokens, tickets and signed references.

  new_token() / hash_token()   opaque bearer secrets; only sha256 is stored
  mint_ticket / verify_ticket  short-lived HMAC tickets (SSE streams, direct
                               uploads) for requests that cannot carry the
                               web-origin cookie: b64url(json claims).b64url(mac)
  sign_ref / verify_ref        tamper-evident ids for WhatsApp button payloads
  qr_digest                    short keyed digest for printed QR keys
  new_pairing_code             human-typeable device pairing codes

Tickets and refs use a purpose-specific key derived from SECRET_KEY, so a stream
ticket can never be replayed as an upload ticket or a button ref.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

from clefs.settings import get_settings

PAIRING_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L
REF_TAG_LEN = 12


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _key(purpose: str) -> bytes:
    return hmac.new(get_settings().SECRET_KEY.encode("utf-8"), b"clefs:" + purpose.encode("utf-8"), hashlib.sha256).digest()


def _mac(purpose: str, message: bytes) -> bytes:
    return hmac.new(_key(purpose), message, hashlib.sha256).digest()


# ─── Tickets ───


def mint_ticket(purpose: str, claims: dict[str, Any], ttl_s: int = 60) -> str:
    body = dict(claims)
    body["exp"] = int(time.time()) + ttl_s
    body["p"] = purpose
    payload = _b64e(json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    return f"{payload}.{_b64e(_mac('ticket:' + purpose, payload.encode('ascii')))}"


def verify_ticket(purpose: str, ticket: str) -> dict[str, Any] | None:
    try:
        payload, sig = ticket.split(".", 1)
        expected = _mac("ticket:" + purpose, payload.encode("ascii"))
        if not hmac.compare_digest(_b64d(sig), expected):
            return None
        claims = json.loads(_b64d(payload))
    except (ValueError, TypeError):
        return None
    if claims.get("p") != purpose or int(claims.get("exp", 0)) < int(time.time()):
        return None
    return claims


# ─── Signed references (WhatsApp buttons) ───


def sign_ref(ref: str) -> str:
    """"brief:approve:123" -> "brief:approve:123.Ab3..." (button ids max 256 chars)."""
    tag = _b64e(_mac("ref", ref.encode("utf-8")))[:REF_TAG_LEN]
    return f"{ref}.{tag}"


def verify_ref(signed: str) -> str | None:
    ref, sep, tag = signed.rpartition(".")
    if not sep or not ref:
        return None
    expected = _b64e(_mac("ref", ref.encode("utf-8")))[:REF_TAG_LEN]
    # Bytes: a str compare_digest raises TypeError on non-ASCII input, and the
    # tag arrives from WhatsApp button payloads and public tracking URLs.
    return ref if hmac.compare_digest(tag.encode("utf-8", "replace"), expected.encode("ascii")) else None


# ─── Short keyed digests ───


def qr_digest(message: str, length: int = 10) -> str:
    """base32(HMAC_SHA256(SECRET_KEY, message))[:length], the printed-QR key
    formula from the foundation spec. It uses SECRET_KEY directly (not a derived
    key) so the formula stays reproducible by a QR printing script."""
    digest = hmac.new(get_settings().SECRET_KEY.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii")[:length]


# ─── Pairing codes ───


def new_pairing_code() -> str:
    raw = "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_pairing_code(code: str) -> str:
    raw = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    return f"{raw[:4]}-{raw[4:]}" if len(raw) == 8 else raw
