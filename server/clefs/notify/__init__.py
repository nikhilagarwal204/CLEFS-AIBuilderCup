"""
Notifications: WhatsApp (template, text, interactive, document), email
(Resend), SMS (MSG91 for the Indian pilots, Twilio for the UAE), the outbox
that delivers them, and the inbound WhatsApp / email dispatch.

    from clefs import notify
    notify.send_whatsapp_template(to, "owner_brief_v1", "en", params, hotel_slug=slug, purpose="brief.daily", cur=cur)
    notify.notify(notify.Recipient(phone=..., email=...), notify.Message(purpose="brief.daily", ...))

    @notify.on_whatsapp("staff", button_prefix="brief:")
    def tap(ctx: notify.InboundCtx) -> bool: ...

    @notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["inquiries.whatsapp_intake"])
    def intake(ctx): ...

    notify.wa_window_open_until(cur, slug, sender_hmac=h)       # datetime | None
    notify.inbound_address("demo", "inquiries", 42)            # demo+inq-42@in.clefs.world
    notify.attempt_key("gst:17", 2)                            # gst:17:a2 (a deliberate re-send)
    notify.cancel_queued(cur, idempotency_key="gst:17:a1")     # retract a send not gone out yet

NOTIFY_MODE=live|log|capture. Tests run in capture mode: notify.captured()
lists what would have been sent, notify.CAPTURE.fail_next(131056) scripts a
provider error.

Importing this package registers the magic-link sender with clefs.auth.magic,
so POST /auth/magic/request delivers by WhatsApp or email.
"""

from __future__ import annotations

from clefs.auth import magic as _magic
from clefs.notify import outbox
from clefs.notify.inbound import (
    GUEST_PRIORITY,
    InboundCtx,
    InboundEmail,
    hotel_inbound_address,
    inbound_address,
    inbound_domain,
    on_inbound_email,
    on_outbound_status,
    on_whatsapp,
    parse_address,
    process_email,
    process_resend,
    process_whatsapp,
    purpose_for_token,
    unregister_handler,
)
from clefs.notify.outbox import attempt_key, cancel_queued, wa_window_open_until
from clefs.notify.send import (
    Message,
    NotifyResult,
    Recipient,
    magic_sender,
    notify,
    platform_sender_id,
    send_email,
    send_sms,
    send_whatsapp_document,
    send_whatsapp_interactive,
    send_whatsapp_template,
    send_whatsapp_text,
)
from clefs.notify.transport import CAPTURE, ProviderError, captured, clear_captured, set_transport
from clefs.notify.validate import PayloadError, validate_template_definition

deliver = outbox.deliver
window_open = outbox.window_open

_magic.register_sender(magic_sender)

__all__ = [
    "CAPTURE",
    "GUEST_PRIORITY",
    "InboundCtx",
    "InboundEmail",
    "Message",
    "NotifyResult",
    "PayloadError",
    "ProviderError",
    "Recipient",
    "attempt_key",
    "cancel_queued",
    "captured",
    "clear_captured",
    "deliver",
    "hotel_inbound_address",
    "inbound_address",
    "inbound_domain",
    "magic_sender",
    "notify",
    "on_inbound_email",
    "on_outbound_status",
    "on_whatsapp",
    "outbox",
    "parse_address",
    "platform_sender_id",
    "process_email",
    "process_resend",
    "process_whatsapp",
    "purpose_for_token",
    "send_email",
    "send_sms",
    "send_whatsapp_document",
    "send_whatsapp_interactive",
    "send_whatsapp_template",
    "send_whatsapp_text",
    "set_transport",
    "unregister_handler",
    "validate_template_definition",
    "wa_window_open_until",
    "window_open",
]
