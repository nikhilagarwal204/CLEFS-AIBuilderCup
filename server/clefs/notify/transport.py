"""
Transports: the only code that sends through Meta, Resend, MSG91 and Twilio
(received mail is read back from Resend by clefs.notify.inbound).

NOTIFY_MODE picks one:
  live     real HTTP calls (httpx, 10 s timeout)
  log      logs a one-line summary and pretends it was sent (local dev)
  capture  keeps every payload in memory for tests (captured()), and can be
           told to fail the next send with a provider error code, so the
           outbox's backoff and fallback paths are testable without a network

A transport raises ProviderError(code) for a provider-side refusal; the
outbox decides between retry, backoff, fallback and failure from the code.
"""

from __future__ import annotations

import base64
import itertools
import logging
import threading
from collections import deque
from typing import Any, Protocol

from clefs.settings import get_settings

log = logging.getLogger("clefs.notify")

GRAPH_URL = "https://graph.facebook.com"
RESEND_URL = "https://api.resend.com/emails"
MSG91_FLOW_URL = "https://control.msg91.com/api/v5/flow"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
HTTP_TIMEOUT_S = 10.0


class ProviderError(Exception):
    def __init__(self, code: str | int | None, message: str = "", *, permanent: bool = False):
        super().__init__(f"{code}: {message}" if code is not None else message)
        self.code = None if code is None else str(code)
        self.message = message
        self.permanent = permanent


class Transport(Protocol):
    name: str

    def configured(self, channel: str, *, sender_id: str | None = None, provider: str | None = None) -> bool: ...

    def whatsapp(self, sender_id: str, payload: dict) -> str: ...

    def email(self, payload: dict) -> str: ...

    def sms(self, provider: str, payload: dict) -> str: ...


# ─── Live ───


class LiveTransport:
    name = "live"

    def configured(self, channel: str, *, sender_id: str | None = None, provider: str | None = None) -> bool:
        s = get_settings()
        if channel == "whatsapp":
            return bool(s.WA_ACCESS_TOKEN and sender_id)
        if channel == "email":
            return bool(s.RESEND_API_KEY and s.EMAIL_FROM)
        if channel == "sms":
            if provider == "twilio":
                return bool(s.TWILIO_ACCOUNT_SID and s.TWILIO_AUTH_TOKEN and s.TWILIO_FROM)
            return bool(s.SMS_ENABLED and s.SMS_AUTH_KEY and s.SMS_TEMPLATE_ID)
        return False

    def whatsapp(self, sender_id: str, payload: dict) -> str:
        import httpx

        s = get_settings()
        try:
            r = httpx.post(
                f"{GRAPH_URL}/{s.WA_API_VERSION}/{sender_id}/messages",
                json=payload,
                headers={"Authorization": f"Bearer {s.WA_ACCESS_TOKEN}"},
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("network", str(exc)) from exc
        body = _json(r)
        if r.status_code >= 400 or "error" in body:
            err = body.get("error") or {}
            raise ProviderError(err.get("code") or r.status_code, err.get("message") or r.text[:300])
        try:
            return body["messages"][0]["id"]
        except (KeyError, IndexError) as exc:
            raise ProviderError("bad_response", r.text[:300]) from exc

    def email(self, payload: dict) -> str:
        s = get_settings()
        # The demo hotel lives in production and its users are @example.com
        # (RFC 2606). Mail to reserved domains can only bounce, and bounces cost
        # the sending account its standing, so those recipients are dropped
        # here; a message with nobody left is reported sent without a call.
        payload = _without_reserved(payload)
        if not payload.get("To"):
            log.info("email suppressed: every recipient is on a reserved domain")
            return f"suppressed-reserved-{next(_SUPPRESSED)}"
        if s.EMAIL_REPLY_TO and not payload.get("ReplyTo"):
            payload["ReplyTo"] = s.EMAIL_REPLY_TO
        return _resend_email(payload, s.RESEND_API_KEY or "")

    def sms(self, provider: str, payload: dict) -> str:
        import httpx

        s = get_settings()
        try:
            if provider == "twilio":
                r = httpx.post(
                    TWILIO_URL.format(sid=s.TWILIO_ACCOUNT_SID),
                    data=payload,
                    auth=(s.TWILIO_ACCOUNT_SID or "", s.TWILIO_AUTH_TOKEN or ""),
                    timeout=HTTP_TIMEOUT_S,
                )
                body = _json(r)
                if r.status_code >= 400:
                    raise ProviderError(body.get("code") or r.status_code, body.get("message") or r.text[:300])
                return str(body.get("sid") or "")
            r = httpx.post(
                MSG91_FLOW_URL,
                json=payload,
                headers={"authkey": s.SMS_AUTH_KEY or "", "Content-Type": "application/json", "Accept": "application/json"},
                timeout=HTTP_TIMEOUT_S,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("network", str(exc)) from exc
        body = _json(r)
        if r.status_code >= 400 or body.get("type") == "error":
            raise ProviderError(r.status_code, str(body.get("message") or r.text[:300]))
        return str(body.get("request_id") or body.get("message") or "msg91")


# ─── Resend ───


def _resend_email(payload: dict, api_key: str) -> str:
    """Send our email payload (Postmark-style field names, built by
    send_email and the outbox) through Resend. MessageStream has no Resend
    equivalent and is dropped; Resend allows only [A-Za-z0-9_-] in a tag."""
    import re
    from email.utils import getaddresses

    import httpx

    def addrs(field: str) -> list[str]:
        return [a for _n, a in getaddresses([payload[field]]) if a] if payload.get(field) else []

    body: dict[str, Any] = {"from": payload["From"], "to": addrs("To"), "subject": payload.get("Subject") or "",
                            "html": payload.get("HtmlBody") or ""}
    if payload.get("TextBody"):
        body["text"] = payload["TextBody"]
    for src, dst in (("Cc", "cc"), ("Bcc", "bcc"), ("ReplyTo", "reply_to")):
        if addrs(src):
            body[dst] = addrs(src)
    if payload.get("Headers"):
        body["headers"] = {h["Name"]: h["Value"] for h in payload["Headers"]}
    if payload.get("Attachments"):
        body["attachments"] = [{"filename": a["Name"], "content": a["Content"]} for a in payload["Attachments"]]
    if payload.get("Tag"):
        body["tags"] = [{"name": "purpose", "value": re.sub(r"[^A-Za-z0-9_-]", "_", payload["Tag"])[:256]}]
    try:
        r = httpx.post(RESEND_URL, json=body, headers={"Authorization": f"Bearer {api_key}"}, timeout=HTTP_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise ProviderError("network", str(exc)) from exc
    out = _json(r)
    if r.status_code >= 400:
        # A 4xx other than a rate limit is the request itself (a bad address,
        # an unverified sending domain): retrying never helps.
        raise ProviderError(out.get("name") or r.status_code, out.get("message") or r.text[:300],
                            permanent=400 <= r.status_code < 500 and r.status_code != 429)
    return str(out.get("id") or "")


# ─── Reserved recipients ───

# RFC 2606 and RFC 6761 names that never take mail.
RESERVED_DOMAINS = frozenset({"example.com", "example.net", "example.org", "localhost"})
RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost",
                     ".example.com", ".example.net", ".example.org")
_SUPPRESSED = itertools.count(1)


def is_reserved_address(addr: str) -> bool:
    domain = addr.rpartition("@")[2].strip().lower().rstrip(".")
    return domain in RESERVED_DOMAINS or domain.endswith(RESERVED_SUFFIXES)


def _without_reserved(payload: dict) -> dict:
    """The payload with reserved addresses removed from To, Cc and Bcc."""
    from email.utils import formataddr, getaddresses

    out = dict(payload)
    for field in ("To", "Cc", "Bcc"):
        if not payload.get(field):
            continue
        kept = [(n, a) for n, a in getaddresses([payload[field]]) if a and not is_reserved_address(a)]
        if kept:
            out[field] = ", ".join(formataddr(p) for p in kept)
        else:
            out.pop(field)
    return out


def _json(r: Any) -> dict:
    try:
        out = r.json()
        return out if isinstance(out, dict) else {}
    except ValueError:
        return {}


# ─── Log ───


class LogTransport:
    name = "log"
    _seq = itertools.count(1)

    def configured(self, channel: str, *, sender_id: str | None = None, provider: str | None = None) -> bool:
        return True

    def _id(self, prefix: str) -> str:
        return f"{prefix}.log.{next(self._seq)}"

    def whatsapp(self, sender_id: str, payload: dict) -> str:
        log.info("[notify:log] whatsapp %s -> %s (%s)", sender_id, payload.get("to"), payload.get("type"))
        return self._id("wamid")

    def email(self, payload: dict) -> str:
        log.info("[notify:log] email -> %s: %s", payload.get("To"), payload.get("Subject"))
        return self._id("email")

    def sms(self, provider: str, payload: dict) -> str:
        log.info("[notify:log] sms via %s", provider)
        return self._id("sms")


# ─── Capture ───


class CaptureTransport:
    """In-memory transport for tests. `sent` holds {"channel", "sender_id",
    "provider", "payload", "provider_id"} in send order."""

    name = "capture"

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.unconfigured: set[str] = set()
        self._failures: deque[tuple[str | None, str, str]] = deque()
        self._lock = threading.Lock()
        self._seq = itertools.count(1)

    def reset(self) -> None:
        with self._lock:
            self.sent.clear()
            self.unconfigured.clear()
            self._failures.clear()

    def fail_next(self, code: str | int, message: str = "", *, channel: str | None = None, times: int = 1) -> None:
        """The next `times` sends (on `channel`, or any) raise ProviderError(code)."""
        with self._lock:
            for _ in range(times):
                self._failures.append((channel, str(code), message))

    def configured(self, channel: str, *, sender_id: str | None = None, provider: str | None = None) -> bool:
        if channel == "whatsapp" and not sender_id:
            return False
        return channel not in self.unconfigured and f"{channel}:{provider}" not in self.unconfigured

    def _record(self, channel: str, prefix: str, payload: dict, **extra: Any) -> str:
        with self._lock:
            for i, (ch, code, msg) in enumerate(self._failures):
                if ch is None or ch == channel:
                    del self._failures[i]
                    raise ProviderError(code, msg or f"captured failure {code}")
            pid = f"{prefix}.capture.{next(self._seq)}"
            self.sent.append({"channel": channel, "payload": payload, "provider_id": pid, **extra})
            return pid

    def whatsapp(self, sender_id: str, payload: dict) -> str:
        return self._record("whatsapp", "wamid", payload, sender_id=sender_id)

    def email(self, payload: dict) -> str:
        return self._record("email", "email", payload)

    def sms(self, provider: str, payload: dict) -> str:
        return self._record("sms", "sms", payload, provider=provider)


CAPTURE = CaptureTransport()
_override: Any = None


def set_transport(t: Any) -> None:
    global _override
    _override = t


def get_transport() -> Transport:
    if _override is not None:
        return _override
    mode = get_settings().NOTIFY_MODE
    if mode == "capture":
        return CAPTURE
    if mode == "log":
        return LogTransport()
    return LiveTransport()


def captured(channel: str | None = None) -> list[dict]:
    """Tests: what the capture transport sent (optionally one channel)."""
    return [m for m in CAPTURE.sent if channel is None or m["channel"] == channel]


def clear_captured() -> None:
    CAPTURE.reset()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
