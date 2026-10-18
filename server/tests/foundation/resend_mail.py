"""
Inbound email through the Resend webhook, for route tests.

post() sends a correctly signed email.received event to /webhooks/email/resend
and, while the request is handled, swaps the read-back from Resend's API
(notify.inbound.fetch_resend_email) for the test's own message, given as our
email payload (what from_resend produces). The signature check, the event
filter and process_email all run for real.

    env(INBOUND_EMAIL_ENABLED="true", RESEND_WEBHOOK_SECRET=resend_mail.SECRET)
    r = resend_mail.post(client, {"MessageID": "m-1", "ToFull": [...], ...})
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from clefs.core import clock
from clefs.notify import inbound

PATH = "/webhooks/email/resend"
SECRET = "whsec_" + base64.b64encode(b"clefs-resend-webhook-test-secret").decode()
EMAIL_ID = "4ef9a417-02e9-4d39-ad75-9611e0fcc33c"


def sign(body: bytes, *, svix_id: str = "msg_test", ts: int | None = None, secret: str = SECRET) -> dict[str, str]:
    """The Svix headers Resend sends with `body` (timestamp: the app's clock)."""
    ts = int(clock.now().timestamp()) if ts is None else ts
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{svix_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    return {"svix-id": svix_id, "svix-timestamp": str(ts), "svix-signature": "v1," + base64.b64encode(mac).decode()}


def event(kind: str = "email.received", email_id: str = EMAIL_ID) -> dict:
    """A webhook body as Resend sends it: metadata only."""
    return {
        "type": kind,
        "created_at": "2026-10-07T08:00:00.000Z",
        "data": {"email_id": email_id, "created_at": "2026-10-07T08:00:00.000Z", "from": "guest@example.com",
                 "to": ["testdubai@in.clefs.world"], "bcc": [], "cc": [], "received_for": [],
                 "message_id": "<m-1@example.com>", "subject": "Hello", "attachments": []},
    }


def post(client: Any, message: dict | None, *, body: dict | None = None, headers: dict[str, str] | None = None) -> Any:
    """POST a signed event (default: email.received). Resend's read-back
    answers `message` (None: Resend has no such email)."""
    raw = json.dumps(event() if body is None else body).encode()
    real = inbound.fetch_resend_email
    inbound.fetch_resend_email = lambda email_id: message
    try:
        return client.post(PATH, content=raw,
                           headers={"content-type": "application/json", **(sign(raw) if headers is None else headers)})
    finally:
        inbound.fetch_resend_email = real
