"""
Public send API (01 spec E6). Every function validates the payload, writes an
outbox row (joining `cur` when given) and returns a NotifyResult at once;
delivery happens in core.notify.deliver (inline in capture/log modes).

    send_whatsapp_template("+971501234567", "owner_brief_v1", "en", ["3", "4,120"],
                           hotel_slug=slug, purpose="brief.daily", buttons=[...], cur=cur)
    send_whatsapp_interactive(to, "Approve?", buttons=[(sign_ref("act:7:approve"), "Approve")], ...)
    send_email("gm@hotel.ae", "Your brief", html, hotel_slug=slug, purpose="brief.daily")
    notify(Recipient(phone=..., email=...), Message(purpose=..., whatsapp_template=..., email_subject=...))

`ok` means accepted for delivery (queued or already sent). It is False when a
channel is not configured, the WhatsApp window is closed for a session
message, or the recipient cannot be reached; notify() then tries the next
channel. Failures that happen later, at the provider, surface through the
outbox status and on_outbound_status hooks, not through this return value.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Literal

from pydantic import BaseModel

from clefs import db
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.phone import normalize_phone
from clefs.notify import outbox, validate
from clefs.notify.transport import get_transport
from clefs.settings import get_settings

log = logging.getLogger("clefs.notify")

Sender = Literal["platform", "hotel"]
OK_STATES = frozenset({"queued", "sending", "sent", "delivered", "read"})


class NotifyResult(BaseModel):
    ok: bool
    channel: str
    outbox_id: int | None
    status: str
    error: str | None = None
    deduped: bool = False


@contextmanager
def _cursor(cur: Any) -> Iterator[Any]:
    if cur is not None:
        yield cur
    else:
        with db.db() as c:
            yield c


def _result(row: dict, deduped: bool) -> NotifyResult:
    return NotifyResult(
        ok=row["status"] in OK_STATES,
        channel=row["channel"],
        outbox_id=row["id"],
        status=row["status"],
        error=row.get("error"),
        deduped=deduped,
    )


def _phone(to: str) -> str:
    p = normalize_phone(to)
    if p is None:
        raise validate.PayloadError("a phone number is required")
    return p


def platform_sender_id() -> str | None:
    return get_settings().WA_PLATFORM_PHONE_NUMBER_ID or ("platform" if get_settings().NOTIFY_MODE != "live" else None)


def _sender_id(cur: Any, sender: Sender, hotel_slug: str | None) -> str | None:
    if sender == "platform":
        return platform_sender_id()
    if not hotel_slug:
        raise ValueError("sender='hotel' needs hotel_slug")
    from clefs.core.hotels import get_hotel

    return get_hotel(cur, hotel_slug).whatsapp_phone_number_id


def _queue(
    cur: Any,
    *,
    channel: str,
    kind: str,
    recipient: str,
    payload: dict,
    purpose: str,
    hotel_slug: str | None,
    category: str | None,
    sender: str = "platform",
    sender_id: str | None = None,
    idempotency_key: str | None,
    template_name: str | None = None,
    template_lang: str | None = None,
    meta: dict | None = None,
    action_id: int | None = None,
    sms_provider: str | None = None,
) -> NotifyResult:
    if not purpose or "." not in purpose:
        raise ValueError("purpose must look like '<module>.<what>'")
    configured = get_transport().configured(channel, sender_id=sender_id, provider=sms_provider)
    with _cursor(cur) as c:
        row, deduped = outbox.create(
            c,
            channel=channel,
            kind=kind,
            recipient=recipient,
            payload=payload,
            purpose=purpose,
            hotel_slug=hotel_slug,
            category=category,
            sender=sender,
            sender_id=sender_id,
            idempotency_key=idempotency_key,
            template_name=template_name,
            template_lang=template_lang,
            meta={**(meta or {}), **({"sms_provider": sms_provider} if sms_provider else {})},
            action_id=action_id,
            configured=configured,
        )
        if not deduped and row["status"] == "queued" and outbox.inline_delivery():
            row = outbox.deliver(c, row["id"], raise_not_due=False)
    if cur is None and not deduped and row["status"] == "queued" and not outbox.inline_delivery():
        # Own transaction, committed: try once now so a message does not wait
        # for the next worker tick. The queued task covers any failure.
        try:
            with db.db() as c:
                row = outbox.deliver(c, row["id"], raise_not_due=False)
        except Exception:
            log.exception("inline delivery of outbox %s failed; the queue will retry", row["id"])
    return _result(row, deduped)


# ─── WhatsApp ───


def send_whatsapp_template(
    to: str,
    template: str,
    lang: str,
    params: list[Any] | None = None,
    *,
    hotel_slug: str | None,
    purpose: str,
    sender: Sender = "platform",
    buttons: list[dict] | None = None,
    header: str | dict | None = None,
    category: Literal["utility", "marketing", "authentication"] = "utility",
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    """A pre-approved template: the only thing allowed outside the 24 h window."""
    recipient = _phone(to)
    payload = validate.template_payload(recipient, template, lang, params, header=header, buttons=buttons)
    with _cursor(cur) as c:
        sender_id = _sender_id(c, sender, hotel_slug)
        return _queue(
            c, channel="whatsapp", kind="template", recipient=recipient, payload=payload, purpose=purpose,
            hotel_slug=hotel_slug, category=category, sender=sender, sender_id=sender_id,
            idempotency_key=idempotency_key, template_name=template, template_lang=lang, action_id=action_id,
        )


def _session(
    kind: str,
    recipient: str,
    payload: dict,
    *,
    hotel_slug: str | None,
    purpose: str,
    sender: Sender,
    fallback_template: dict | None,
    idempotency_key: str | None,
    action_id: int | None,
    cur: Any,
) -> NotifyResult:
    """Session messages need an open window. Outside it the row is recorded as
    outside_window (ok=False) and the fallback template, if any, goes instead."""
    with _cursor(cur) as c:
        sender_id = _sender_id(c, sender, hotel_slug)
        meta = {"fallback_template": fallback_template} if fallback_template else None
        if sender_id and not outbox.window_open(c, sender_id, recipient):
            row, deduped = outbox.create(
                c, channel="whatsapp", kind=kind, recipient=recipient, payload=payload, purpose=purpose,
                hotel_slug=hotel_slug, category="service", sender=sender, sender_id=sender_id,
                idempotency_key=idempotency_key, meta=meta, action_id=action_id, configured=False,
            )
            if not deduped:
                row = outbox._update(c, row["id"], {"status": "outside_window", "error": "the 24 h customer service window is closed"})
                outbox._fallback_template(c, row, "outside_window")
            return _result(row, deduped)
        return _queue(
            c, channel="whatsapp", kind=kind, recipient=recipient, payload=payload, purpose=purpose,
            hotel_slug=hotel_slug, category="service", sender=sender, sender_id=sender_id,
            idempotency_key=idempotency_key, meta=meta, action_id=action_id,
        )


def send_whatsapp_text(
    to: str,
    body: str,
    *,
    hotel_slug: str | None,
    purpose: str,
    sender: Sender = "platform",
    preview_url: bool = False,
    fallback_template: dict | None = None,
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    """Free text; status 'outside_window' (ok=False) when the window is closed.
    fallback_template = {"name", "lang", "params", "buttons"?, "header"?, "category"?}."""
    recipient = _phone(to)
    payload = validate.text_payload(recipient, body, preview_url=preview_url)
    return _session(
        "text", recipient, payload, hotel_slug=hotel_slug, purpose=purpose, sender=sender,
        fallback_template=fallback_template, idempotency_key=idempotency_key, action_id=action_id, cur=cur,
    )


def send_whatsapp_interactive(
    to: str,
    body: str,
    *,
    buttons: list[tuple[str, str]] | None = None,
    sections: list[dict] | None = None,
    button_text: str = "Choose",
    header: str | None = None,
    footer: str | None = None,
    hotel_slug: str | None,
    purpose: str,
    sender: Sender = "platform",
    fallback_template: dict | None = None,
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    """Reply buttons (max 3) or a list (max 10 rows). Button and row ids should
    be signed with clefs.auth.tokens.sign_ref so taps can be trusted."""
    recipient = _phone(to)
    if bool(buttons) == bool(sections):
        raise validate.PayloadError("pass either buttons or sections")
    if buttons:
        payload = validate.buttons_payload(recipient, body, buttons, header=header, footer=footer)
    else:
        payload = validate.list_payload(recipient, body, sections or [], button_text=button_text, header=header, footer=footer)
    return _session(
        "interactive", recipient, payload, hotel_slug=hotel_slug, purpose=purpose, sender=sender,
        fallback_template=fallback_template, idempotency_key=idempotency_key, action_id=action_id, cur=cur,
    )


def send_whatsapp_document(
    to: str,
    file_id: str,
    filename: str,
    caption: str | None = None,
    *,
    hotel_slug: str,
    purpose: str,
    sender: Sender = "platform",
    fallback_template: dict | None = None,
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    recipient = _phone(to)
    payload = validate.document_payload(recipient, file_id=file_id, filename=filename, caption=caption)
    return _session(
        "document", recipient, payload, hotel_slug=hotel_slug, purpose=purpose, sender=sender,
        fallback_template=fallback_template, idempotency_key=idempotency_key, action_id=action_id, cur=cur,
    )


# ─── Email ───


def send_email(
    to: str,
    subject: str,
    html: str,
    *,
    text: str | None = None,
    attachments: list[str] | tuple[str, ...] = (),
    reply_to: str | None = None,
    headers: dict[str, str] | None = None,
    hotel_slug: str | None = None,
    purpose: str,
    category: Literal["utility", "marketing"] = "utility",
    from_: str | None = None,
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    """Resend. The outbox row holds our email payload (Postmark-style field
    names: From, To, HtmlBody, Headers, ...), converted for Resend's API by
    the transport. `attachments` are file ids from clefs.files (loaded at send
    time); `headers` carries e.g. List-Unsubscribe (marketing, 04 spec F4) or
    In-Reply-To / References (threading). Header names are checked and
    address headers refused (validate.email_headers): recipients and the
    sender go through to / reply_to / from_ only."""
    address = (to or "").strip()
    if "@" not in address or len(address) > 320:
        raise validate.PayloadError("not an email address")
    if not subject or len(subject) > 998:
        raise validate.PayloadError("subject is required (at most 998 characters)")
    header_list = validate.email_headers(headers)
    s = get_settings()
    payload: dict[str, Any] = {
        "From": from_ or s.EMAIL_FROM or "Clefs <no-reply@clefs.world>",
        "To": address,
        "Subject": subject,
        "HtmlBody": html,
        "MessageStream": "broadcast" if category == "marketing" else "outbound",
        "Tag": purpose[:1000],
    }
    if text:
        payload["TextBody"] = text
    if reply_to:
        payload["ReplyTo"] = reply_to
    if header_list:
        payload["Headers"] = header_list
    if attachments:
        payload["AttachmentFileIds"] = [str(a) for a in attachments]
    return _queue(
        cur, channel="email", kind="email", recipient=address.lower(), payload=payload, purpose=purpose,
        hotel_slug=hotel_slug, category=category, sender="platform", sender_id=payload["From"],
        idempotency_key=idempotency_key, action_id=action_id,
    )


# ─── SMS ───


def sms_provider_for(phone: str) -> str:
    """msg91 for Indian numbers (the legacy pilots, DLT-registered template),
    twilio everywhere else (MSG91 cannot deliver in the UAE)."""
    return "msg91" if phone.startswith("+91") else "twilio"


def send_sms(
    to: str,
    text: str,
    *,
    hotel_slug: str | None = None,
    purpose: str,
    provider: str | None = None,
    template_vars: dict | None = None,
    idempotency_key: str | None = None,
    action_id: int | None = None,
    cur: Any = None,
) -> NotifyResult:
    """UAE rule: no URLs in SMS to +971 numbers (sender-ID filtering blocks them)."""
    recipient = _phone(to)
    if not text or len(text) > 1600:
        raise validate.PayloadError("SMS text is required (at most 1600 characters)")
    if recipient.startswith("+971") and ("http://" in text or "https://" in text or "www." in text):
        raise validate.PayloadError("SMS to UAE numbers cannot contain links")
    chosen = provider or sms_provider_for(recipient)
    s = get_settings()
    if chosen == "msg91":
        from clefs.legacy import msg91

        v = template_vars or {}
        payload = msg91.build_payload(
            recipient, v.get("room", "-"), v.get("task", purpose), v.get("desc", text), v.get("priority", "medium")
        )
    else:
        payload = {"To": recipient, "From": s.TWILIO_FROM or "", "Body": text}
    return _queue(
        cur, channel="sms", kind="sms", recipient=recipient, payload=payload, purpose=purpose, hotel_slug=hotel_slug,
        category="utility", idempotency_key=idempotency_key, action_id=action_id, sms_provider=chosen,
    )


# ─── Fallback chain ───


class Recipient(BaseModel):
    phone: str | None = None
    email: str | None = None
    name: str | None = None
    user_id: int | None = None
    locale: str = "en"


class Message(BaseModel):
    """One notification in every channel's form; notify() picks the first
    channel that accepts it."""

    purpose: str
    hotel_slug: str | None = None
    category: Literal["utility", "marketing", "authentication"] = "utility"
    sender: Sender = "platform"
    whatsapp_text: str | None = None  # used while the window is open
    whatsapp_template: dict | None = None  # {"name", "lang", "params", "buttons"?, "header"?}
    sms_text: str | None = None
    email_subject: str | None = None
    email_html: str | None = None
    email_text: str | None = None
    idempotency_key: str | None = None
    action_id: int | None = None


def notify(
    recipient: Recipient, msg: Message, *, channels: tuple[str, ...] = ("whatsapp", "sms", "email"), cur: Any = None
) -> NotifyResult:
    """Try channels in order until one accepts the message."""
    last = NotifyResult(ok=False, channel="none", outbox_id=None, status="no_channel", error="no reachable channel")
    for ch in channels:
        key = f"{msg.idempotency_key}:{ch}" if msg.idempotency_key else None
        try:
            if ch == "whatsapp" and recipient.phone and (msg.whatsapp_text or msg.whatsapp_template):
                res = None
                with _cursor(cur) as c:
                    sender_id = _sender_id(c, msg.sender, msg.hotel_slug)
                    open_ = bool(sender_id) and outbox.window_open(c, sender_id, _phone(recipient.phone))
                if msg.whatsapp_text and (open_ or not msg.whatsapp_template):
                    res = send_whatsapp_text(
                        recipient.phone, msg.whatsapp_text, hotel_slug=msg.hotel_slug, purpose=msg.purpose,
                        sender=msg.sender, idempotency_key=key, action_id=msg.action_id, cur=cur,
                    )
                if (res is None or not res.ok) and msg.whatsapp_template:
                    t = msg.whatsapp_template
                    res = send_whatsapp_template(
                        recipient.phone, t["name"], t.get("lang", recipient.locale), t.get("params") or [],
                        hotel_slug=msg.hotel_slug, purpose=msg.purpose, sender=msg.sender, buttons=t.get("buttons"),
                        header=t.get("header"), category=msg.category,
                        idempotency_key=f"{key}:template" if key else None, action_id=msg.action_id, cur=cur,
                    )
            elif ch == "sms" and recipient.phone and msg.sms_text:
                res = send_sms(
                    recipient.phone, msg.sms_text, hotel_slug=msg.hotel_slug, purpose=msg.purpose,
                    idempotency_key=key, action_id=msg.action_id, cur=cur,
                )
            elif ch == "email" and recipient.email and msg.email_subject and (msg.email_html or msg.email_text):
                res = send_email(
                    recipient.email, msg.email_subject, msg.email_html or f"<pre>{msg.email_text}</pre>",
                    text=msg.email_text, hotel_slug=msg.hotel_slug, purpose=msg.purpose, category=msg.category
                    if msg.category != "authentication" else "utility", idempotency_key=key, action_id=msg.action_id, cur=cur,
                )
            else:
                continue
        except (validate.PayloadError, ClefsError) as exc:
            last = NotifyResult(ok=False, channel=ch, outbox_id=None, status="invalid", error=str(exc))
            continue
        if res is not None:
            if res.ok:
                return res
            last = res
    return last


# ─── Magic-link delivery (registered with clefs.auth.magic) ───

MAGIC_TEMPLATE = "clefs_magic_link"


def magic_sender(user: dict, hotel_slug: str, url: str, purpose: str) -> bool:
    """WhatsApp text inside the window, else email, else the approved
    authentication template. Links are credentials: never logged here."""
    phone = user.get("phone_e164")
    text = f"Your Clefs link: {url}\nIt works for 24 hours. If you did not ask for it, ignore this message."
    msg = Message(
        purpose=f"core.magic_link.{purpose}"[:60],
        hotel_slug=hotel_slug,
        category="authentication",
        whatsapp_text=text,
        email_subject="Your Clefs sign-in link",
        email_html=f'<p>Tap to open Clefs:</p><p><a href="{url}">{url}</a></p><p>The link works for 24 hours.</p>',
        email_text=text,
    )
    order: list[str] = []
    with db.db() as cur:
        sender_id = platform_sender_id()
        window = bool(phone and sender_id and outbox.window_open(cur, sender_id, phone, at=clock.now()))
    if window:
        order.append("whatsapp")
    if user.get("email"):
        order.append("email")
    if phone and not window:
        order.append("whatsapp")
        template = get_settings().WA_MAGIC_LINK_TEMPLATE or MAGIC_TEMPLATE
        msg.whatsapp_template = {"name": template, "lang": "en", "params": [url]}
        msg.whatsapp_text = None
    if not order:
        return False
    res = notify(Recipient(phone=phone, email=user.get("email")), msg, channels=tuple(dict.fromkeys(order)))
    return res.ok
