"""
Phone call control through the hotel's telephony bridge: accept, reject,
refer, hangup (03 spec 3.2-3.3), and webhook verification.

Google Gemini Live has no SIP endpoint, so the PBX trunk terminates on a
SIP-to-WebSocket media bridge (the hotel's carrier, e.g. a Twilio Media
Streams style bridge). The bridge posts call.incoming to Clefs, Clefs
answers with accept/reject, and once accepted the call's audio is streamed
over a WebSocket that the sideband worker bridges to a Gemini Live session.

Every call-control op goes through one swappable transport so the tests and
the harness record the exact payloads instead of reaching the bridge. The
default transport POSTs JSON to {VOICE_BRIDGE_URL}/calls/{id}/{op} with the
bearer VOICE_BRIDGE_TOKEN; the harness points VOICE_BRIDGE_URL at a stub.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Callable
from urllib.parse import quote

from clefs.modules.voice import constants as C

log = logging.getLogger("clefs.voice.realtime")

Transport = Callable[[str, str, dict], None]
_transport: Transport | None = None


class RealtimeOpError(Exception):
    """An accept/reject/refer/hangup that the bridge refused or that timed out."""


# ─── Webhook verification ───


class InvalidSignature(Exception):
    pass


def signature(body: bytes, secret: str, *, webhook_id: str, timestamp: str) -> str:
    """The X-Clefs-Signature value: sha256=<hex HMAC-SHA256(secret,
    "<webhook id>.<timestamp>.<raw body>")>. Signing the id and the time
    keeps a captured delivery from being replayed under a fresh id."""
    signed = f"{webhook_id}.{timestamp}.".encode() + body
    return "sha256=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()


def unwrap(body: bytes, headers: dict[str, str], secret: str) -> dict:
    """Verify X-Clefs-Signature (HMAC-SHA256 over webhook id, timestamp and
    raw body with VOICE_WEBHOOK_SECRET, constant-time compare; a timestamp
    outside SIGNATURE_TOLERANCE_S is a replay) and return the event as a
    plain dict. Raises InvalidSignature."""
    sent = (headers.get(C.SIGNATURE_HEADER) or "").strip()
    if not sent or not secret:
        raise InvalidSignature("missing signature")
    if not sent.startswith("sha256="):
        sent = "sha256=" + sent
    ts = (headers.get(C.TIMESTAMP_HEADER) or "").strip()
    try:
        skew = abs(time.time() - int(ts))
    except ValueError:
        raise InvalidSignature("missing or malformed timestamp") from None
    if skew > C.SIGNATURE_TOLERANCE_S:
        raise InvalidSignature("timestamp outside the tolerance window")
    expected = signature(body, secret, webhook_id=(headers.get(C.WEBHOOK_ID_HEADER) or "").strip(), timestamp=ts)
    if not hmac.compare_digest(sent.lower(), expected):
        raise InvalidSignature("signature mismatch")
    try:
        event = json.loads(body or b"{}")
    except ValueError:
        raise InvalidSignature("body is not JSON") from None
    if not isinstance(event, dict):
        raise InvalidSignature("body is not an object")
    return event


# ─── Call control ───


def bridge_url() -> str | None:
    return (os.environ.get("VOICE_BRIDGE_URL") or "").rstrip("/") or None


def _sdk_transport(op: str, call_id: str, body: dict) -> None:
    import httpx

    base = bridge_url()
    if not base:
        raise RealtimeOpError("VOICE_BRIDGE_URL is not set")
    timeout = C.ACCEPT_TIMEOUT_S if op in ("accept", "reject") else 10.0
    if op not in ("accept", "reject", "refer", "hangup"):
        raise RealtimeOpError(f"unknown op {op}")
    token = os.environ.get("VOICE_BRIDGE_TOKEN") or ""
    try:
        r = httpx.post(f"{base}/calls/{quote(call_id, safe='')}/{op}", json=body, timeout=timeout,
                       headers={"Authorization": f"Bearer {token}"} if token else {})
    except Exception as exc:
        raise RealtimeOpError(f"{op} failed: {type(exc).__name__}: {str(exc)[:200]}") from None
    if r.status_code >= 300:
        raise RealtimeOpError(f"{op} failed: HTTP {r.status_code}: {r.text[:200]}")


def set_transport(fn: Transport | None) -> None:
    """Tests and the harness: fn(op, call_id, body) replaces the HTTP transport."""
    global _transport
    _transport = fn


def _send(op: str, call_id: str, body: dict) -> None:
    (_transport or _sdk_transport)(op, call_id, body)


def accept(call_id: str, body: dict) -> None:
    _send("accept", call_id, body)


def reject(call_id: str, status_code: int = C.REJECT_CODE) -> None:
    _send("reject", call_id, {"status_code": int(status_code)})


def refer(call_id: str, target_uri: str) -> None:
    _send("refer", call_id, {"target_uri": target_uri})


def hangup(call_id: str) -> None:
    _send("hangup", call_id, {})


# ─── Test recorder ───


class Recorder:
    """A transport that records payloads; fail_next(op) makes the next such
    op raise RealtimeOpError (a refused REFER, an accept timeout)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self._fail: dict[str, int] = {}

    def __call__(self, op: str, call_id: str, body: dict) -> None:
        if self._fail.get(op):
            self._fail[op] -= 1
            raise RealtimeOpError(f"{op} failed (recorder)")
        self.calls.append((op, call_id, body))

    def fail_next(self, op: str, times: int = 1) -> None:
        self._fail[op] = self._fail.get(op, 0) + times

    def ops(self, op: str | None = None) -> list[tuple[str, str, dict]]:
        return [c for c in self.calls if op is None or c[0] == op]
