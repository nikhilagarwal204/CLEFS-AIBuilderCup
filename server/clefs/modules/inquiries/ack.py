"""
The fixed acknowledgement (04 spec 4.3): `inquiries.ack`, dial `auto`.

Every channel except staff-logged ones gets one ack on its first inbound. The
text is fixed (no AI): it thanks the writer, says a person will reply and
links the hotel's privacy notice, which is what makes the basis
`pre_contract_notice` (never downgrading an `express_web_form`). WhatsApp
free-form inside the 24 h window, else an email reply; with neither, the ack
fails and the inquiry simply waits for a person.

The ack does not stop the SLA clock: it is not a first response.
"""

from __future__ import annotations

import html
from typing import Any

from clefs import notify
from clefs.core import clock
from clefs.core.actions import ActionCtx, ActionType, ExecResult, register_action_type
from clefs.modules.inquiries import store

ACK_TYPE = "inquiries.ack"

ACK_TEXT = {
    "en": "Thank you for contacting {hotel}. A member of our team will reply shortly. How we handle your details: {privacy_url}",
    "ar": "شكراً لتواصلك مع {hotel}. سيرد عليك أحد أعضاء فريقنا قريباً. كيف نتعامل مع بياناتك: {privacy_url}",
}
ACK_SUBJECT = {"en": "We received your inquiry ({ref})", "ar": "تلقينا استفسارك ({ref})"}


def privacy_url(hotel: Any, settings: Any) -> str:
    """The hotel's own notice when set, else the Clefs-hosted one."""
    if getattr(settings, "privacy_url", None):
        return settings.privacy_url
    return f"https://www.clefs.world/privacy?hotel={hotel.slug}"


def ack_text(hotel: Any, settings: Any, language: str | None) -> tuple[str, str]:
    lang = "ar" if language == "ar" else "en"
    return lang, ACK_TEXT[lang].format(hotel=hotel.name, privacy_url=privacy_url(hotel, settings))


def _execute(ctx: ActionCtx) -> ExecResult:
    from clefs.modules.inquiries import sla

    cur, hotel = ctx.cur, ctx.hotel
    row = store.get_inquiry(cur, hotel.slug, int(ctx.params["inquiry_id"]), lock=True)
    if row.get("notice_sent_at") is not None:
        return ExecResult(ok=True, data={"skipped": "already_acked"})
    settings = sla.settings_for(cur, hotel.slug)
    lang, body = ack_text(hotel, settings, row.get("language"))
    clear = store.unsealed(row)
    sent = None
    channel = None
    key = f"inq.ack:{hotel.slug}:{row['id']}"
    window = row.get("wa_window_expires_at")
    if clear["phone"] and row["source"] == "whatsapp" and window and window > clock.now():
        sent = notify.send_whatsapp_text(
            clear["phone"], body, hotel_slug=hotel.slug, purpose="inquiries.ack", sender="hotel",
            idempotency_key=f"{key}:wa", action_id=ctx.action_id, cur=cur,
        )
        channel = "whatsapp"
    if (sent is None or not sent.ok) and clear["email"]:
        reply_to = notify.hotel_inbound_address(cur, hotel.slug, "inquiries", row["ref"])
        sent = notify.send_email(
            clear["email"], ACK_SUBJECT[lang].format(ref=row["ref"].upper()),
            f"<p>{html.escape(body)}</p>", text=body, reply_to=reply_to, hotel_slug=hotel.slug,
            purpose="inquiries.ack", category="utility", idempotency_key=f"{key}:email",
            action_id=ctx.action_id, cur=cur,
        )
        channel = "email"
    if sent is None or not sent.ok:
        return ExecResult(ok=False, error="no_reachable_channel", data={"channel": channel})
    changes: dict[str, Any] = {"notice_sent_at": clock.now()}
    if row["consent_basis"] != "express_web_form":
        changes["consent_basis"] = "pre_contract_notice"
    store.update_inquiry(cur, row["id"], changes)
    store.add_message(
        cur, row, direction="out", channel=channel, body=body, author_type="system",
        provider_message_id=f"ack:{sent.outbox_id}", outbound_message_id=sent.outbox_id, action_id=ctx.action_id,
    )
    return ExecResult(ok=True, data={"channel": channel, "outbox_id": sent.outbox_id},
                      consent_basis=f"inquiry:{row['consent_basis']}")


register_action_type(ActionType(
    key=ACK_TYPE,
    module="inquiries",
    label="Acknowledge a new inquiry",
    description="Fixed thank-you with the privacy notice; no AI, no prices.",
    executor=_execute,
    default_owner_role="front_desk",
    default_level="auto",
    max_level="auto",
    decide_perm="inquiries.respond",
))
