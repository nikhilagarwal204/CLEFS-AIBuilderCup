"""
WhatsApp button nonces (02 spec 6.2).

    nonce = base32(HMAC(key, action_id|verb|run_id|user_id|exp))[:12]

Every button id Brief sends carries one, inside a clefs.auth.tokens.sign_ref
envelope (the foundation trusts only signed ids, and button_prefix handlers
see verified refs only). The signature proves WE minted the id; the nonce
adds what the signature cannot:

- **Recipient binding.** The HMAC input names the user the button was sent
  to. A forwarded message tapped by someone else recomputes to a different
  value and is refused (`other_recipient`), even though its sign_ref is valid.
- **Expiry.** `exp` is in the HMAC input, so a stored row cannot be stretched.
- **Single use** for state-changing verbs: the caller passes the nonce to
  core actions.decide(nonce=...), whose unique index on action_events.nonce
  makes a replay 409 nonce_replayed. Navigation verbs (list, open, more)
  stay reusable until they expire.

Rows live in brf_wa_nonces so a short id (`act:{id}:{verb}:{nonce}`, well
under the 200-character list id limit) finds its run, item and recipient.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from clefs.core import clock
from clefs.settings import get_settings

NONCE_LEN = 12
DEFAULT_TTL = timedelta(hours=72)  # the brief's magic links live as long (02 spec 6.1)
# Verbs that change an action: single-use through core decide(nonce=...).
STATE_VERBS = frozenset({"approve", "adjust", "assign", "dispute", "remind", "snooze", "ignore"})


class NonceError(Exception):
    """code: unknown | other_recipient | expired | replayed | mismatch."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Nonce:
    nonce: str
    hotel_slug: str
    user_id: int
    run_id: int | None
    action_id: int | None
    item_no: int | None
    verb: str
    expires_at: datetime
    used_at: datetime | None


# ─── Formula ───


def _key() -> bytes:
    # Derived, so a leaked nonce says nothing about SECRET_KEY or other HMACs
    # built from it (QR keys, sign_ref tags).
    return hmac.new(get_settings().SECRET_KEY.encode("utf-8"), b"clefs.brief.wa_nonce", hashlib.sha256).digest()


def compute(*, action_id: int | None, verb: str, run_id: int | None, user_id: int, exp: datetime) -> str:
    msg = f"{action_id or ''}|{verb}|{run_id or ''}|{user_id}|{int(exp.timestamp())}"
    digest = hmac.new(_key(), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b32encode(digest).decode("ascii")[:NONCE_LEN]


def base_verb(verb: str) -> str:
    """'assign-u12' -> 'assign', 'snooze7' -> 'snooze', 'ignore-dup' -> 'ignore'."""
    head = verb.split("-", 1)[0]
    return head.rstrip("0123456789") or head


# ─── Mint and verify ───


def mint(cur: Any, *, hotel_slug: str, user_id: int, verb: str, run_id: int | None = None,
         action_id: int | None = None, item_no: int | None = None, ttl: timedelta = DEFAULT_TTL) -> str:
    """Store and return a nonce for one button. The expiry is truncated to
    the second (it is an HMAC input as an integer epoch)."""
    exp = (clock.now() + ttl).replace(microsecond=0)
    n = compute(action_id=action_id, verb=verb, run_id=run_id, user_id=user_id, exp=exp)
    cur.execute(
        """
        INSERT INTO brf_wa_nonces (nonce, hotel_slug, user_id, run_id, action_id, item_no, verb, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (nonce) DO NOTHING
        """,
        (n, hotel_slug, user_id, run_id, action_id, item_no, verb, exp),
    )
    return n


def load(cur: Any, nonce: str) -> Nonce | None:
    cur.execute("SELECT * FROM brf_wa_nonces WHERE nonce = %s", (nonce,))
    row = cur.fetchone()
    if row is None:
        return None
    return Nonce(
        nonce=row["nonce"], hotel_slug=row["hotel_slug"], user_id=row["user_id"], run_id=row["run_id"],
        action_id=row["action_id"], item_no=row["item_no"], verb=row["verb"], expires_at=row["expires_at"],
        used_at=row["used_at"],
    )


def verify(cur: Any, nonce: str, *, user_id: int | None, verb: str | None = None,
           action_id: int | None = None) -> Nonce:
    """The stored nonce, re-derived for the person who tapped. Raises
    NonceError: unknown (never minted), other_recipient (minted for someone
    else: a forwarded message), expired, mismatch (the tapped verb or action
    is not the one this nonce was minted for), replayed (a state verb that
    already ran through this nonce)."""
    row = load(cur, nonce or "")
    if row is None:
        raise NonceError("unknown")
    if user_id is None:
        raise NonceError("other_recipient")
    expected = compute(action_id=row.action_id, verb=row.verb, run_id=row.run_id, user_id=int(user_id),
                       exp=row.expires_at)
    if not hmac.compare_digest(expected.encode("ascii"), row.nonce.encode("ascii", "replace")):
        raise NonceError("other_recipient")
    if row.expires_at <= clock.now():
        raise NonceError("expired")
    if verb is not None and verb != row.verb:
        raise NonceError("mismatch")
    if action_id is not None and row.action_id is not None and int(action_id) != row.action_id:
        raise NonceError("mismatch")
    if row.used_at is not None and base_verb(row.verb) in STATE_VERBS:
        raise NonceError("replayed")
    return row


def mark_used(cur: Any, nonce: str) -> None:
    cur.execute("UPDATE brf_wa_nonces SET used_at = COALESCE(used_at, %s) WHERE nonce = %s", (clock.now(), nonce))


def purge_expired(cur: Any, *, older_than: timedelta = timedelta(days=7)) -> int:
    """Housekeeping (the template sync job calls it daily)."""
    cur.execute("DELETE FROM brf_wa_nonces WHERE expires_at < %s", (clock.now() - older_than,))
    return cur.rowcount or 0
