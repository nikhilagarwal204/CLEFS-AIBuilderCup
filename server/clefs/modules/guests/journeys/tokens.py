"""
Public page tokens for /g/[token] (prefs, consent) and /g/f/[token]
(feedback), 04 spec 3.2 gst_public_tokens.

Only the sha256 of a token is stored. Expiry (3.2):
- prefs    = check-in day 23:59 hotel time
- feedback = the J7 plan's valid_until
- consent  = sent_at + 24 h (the J5 transfer tick, 3.4)
A consent token is single-use; prefs and feedback tokens may be reopened
until they expire (the guest may edit their arrival time), but feedback is
stored once per stay (gst_feedback UNIQUE).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clefs.auth import tokens as auth_tokens
from clefs.core import clock
from clefs.db import Json

PURPOSES = ("prefs", "feedback", "consent")


def mint(cur: Any, hotel_slug: str, reservation_id: int, purpose: str, *, expires_at: datetime,
         ref: dict | None = None) -> str:
    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {PURPOSES}")
    raw = auth_tokens.new_token(18)
    cur.execute(
        "INSERT INTO gst_public_tokens (token_hash, hotel_slug, reservation_id, purpose, expires_at, ref, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (auth_tokens.hash_token(raw), hotel_slug, reservation_id, purpose, expires_at, Json(ref or {}), clock.now()),
    )
    return raw


def lookup(cur: Any, token: str, *, lock: bool = False) -> dict | None:
    """The token row whatever its state (the page says "expired"); None
    for an unknown token."""
    if not token or len(token) > 200:
        return None
    cur.execute(
        "SELECT * FROM gst_public_tokens WHERE token_hash = %s" + (" FOR UPDATE" if lock else ""),
        (auth_tokens.hash_token(token),),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def state(row: dict | None, *, now: datetime | None = None) -> str:
    """valid | expired | used | invalid."""
    if row is None:
        return "invalid"
    if row.get("used_at") is not None:
        return "used" if row["purpose"] == "consent" else "expired"
    if row["expires_at"] <= (now or clock.now()):
        return "expired"
    return "valid"


def mark_used(cur: Any, token_hash: bytes) -> None:
    cur.execute("UPDATE gst_public_tokens SET used_at = %s WHERE token_hash = %s AND used_at IS NULL",
                (clock.now(), token_hash))
