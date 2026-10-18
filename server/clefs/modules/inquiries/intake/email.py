"""
Email intake (04 spec 4.3): mail to <alias>+inq[-<ref>]@in.clefs.world.

- A `+inq-<ref>` address, or an In-Reply-To / References header naming a
  mail already on an inquiry, joins that thread.
- Quoted history and signatures are stripped by our own cut (strip_reply;
  Resend sends no pre-stripped reply, so it runs on the full text).
- OTA notification senders and `Auto-Submitted` mail are ignored (left
  unhandled): they are system mail, not guests.
"""

from __future__ import annotations

import re
from typing import Any

from clefs import notify
from clefs.core.hotels import get_hotel, module_enabled
from clefs.modules.inquiries import store
from clefs.modules.inquiries.intake.core import Inbound, ingest
from clefs.modules.inquiries.models import OTA_SENDER_DOMAINS
from clefs.settings import get_settings

_QUOTE_HEADER_RE = re.compile(
    r"^(On .{0,200}wrote:|-----\s*Original Message\s*-----|From:\s.+|Sent from my \w+|في .{0,200}كتب:)\s*$",
    re.I | re.M,
)
_SIG_RE = re.compile(r"^(--|__)\s*$", re.M)


def strip_reply(text: str) -> str:
    """Drop quoted history (">" lines and everything after a quote header)
    and the signature block."""
    t = text or ""
    m = _QUOTE_HEADER_RE.search(t)
    if m:
        t = t[: m.start()]
    m = _SIG_RE.search(t)
    if m:
        t = t[: m.start()]
    lines = [ln for ln in t.splitlines() if not ln.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def ignored_reason(mail: notify.InboundEmail) -> str | None:
    sender = (mail.sender or "").lower()
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else ""
    # A guest's OTA relay alias (guest.booking.com, m.expediapartnercentral.com)
    # is a person writing, often about an existing booking (rerouted by
    # extraction); only the OTAs' own system senders are dropped.
    aliases = {d.lower() for d in (get_settings().OTA_ALIAS_DOMAINS or [])}
    if domain not in aliases and any(domain == d or domain.endswith("." + d) for d in OTA_SENDER_DOMAINS):
        return "ota_sender"
    auto = next((v for k, v in mail.headers.items() if k.lower() == "auto-submitted"), "")
    if auto and auto.strip().lower() != "no":
        return "auto_submitted"
    if not sender:
        return "no_sender"
    return None


def _thread(cur: Any, mail: notify.InboundEmail) -> dict | None:
    if mail.ref:
        row = store.by_ref(cur, mail.hotel_slug, mail.ref)
        if row is not None:
            return row
    ids = []
    for k, v in mail.headers.items():
        if k.lower() in ("in-reply-to", "references"):
            ids += re.findall(r"<([^<>\s]+)>", v or "")
    if not ids:
        return None
    cur.execute(
        """
        SELECT i.* FROM inq_messages m JOIN inq_inquiries i ON i.id = m.inquiry_id
        WHERE m.hotel_slug = %s AND m.channel = 'email' AND m.provider_message_id = ANY(%s)
        ORDER BY m.created_at DESC LIMIT 1 FOR UPDATE OF i
        """,
        (mail.hotel_slug, ids),
    )
    row = cur.fetchone()
    return dict(row) if row else None


@notify.on_inbound_email("inquiries")
def email_intake(mail: notify.InboundEmail) -> dict[str, Any]:
    cur = mail.cur
    if not module_enabled(cur, mail.hotel_slug, "inquiries"):
        return {"handled": False, "reason": "module_off"}
    reason = ignored_reason(mail)
    if reason:
        return {"handled": False, "reason": reason}
    hotel = get_hotel(cur, mail.hotel_slug)
    body = strip_reply(mail.stripped_reply or mail.text or "")
    subject = (mail.subject or "").strip()
    text = f"{subject}\n\n{body}".strip() if subject else body
    res = ingest(cur, hotel, Inbound(
        source="email",
        text=text or "[empty email]",
        provider_message_id=mail.message_id,
        name=mail.sender_name,
        email=mail.sender,
        channel_ref=mail.to,
        attachments=[{"file_id": f.id, "filename": f.filename} for f in mail.attachments],
    ), thread=_thread(cur, mail))
    return {"handled": True, "inquiry_id": res.inquiry["id"] if res.inquiry else None, "outcome": res.outcome}
