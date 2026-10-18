"""
WhatsApp intake (04 spec 4.3; F4 guest priority 20).

Runs after opt-out (5) and journey replies (10), before the concierge (30):
a message to the hotel's number is an inquiry when it carries a
click-to-WhatsApp `referral`, or when the sender is not a known in-house
guest (guests.api.is_known_guest_sender). Known guests stay with the
concierge, whose tool may call inquiries.api.create_inquiry.

The CTWA referral (source_url, source_id, source_type, headline, body,
media_type, ctwa_clid) is kept on the inquiry. The Conversions API is not
built in this lite version; ctwa_clid is stored for it (7.7).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import notify
from clefs.core import clock
from clefs.core.hotels import get_hotel, module_enabled
from clefs.modules.guests import api as guests_api
from clefs.modules.inquiries.intake.core import Inbound, ingest

REFERRAL_FIELDS = ("source_url", "source_id", "source_type", "headline", "body", "media_type", "ctwa_clid")
WA_WINDOW = timedelta(hours=24)


def _match(ctx: notify.InboundCtx) -> bool:
    """Read-only (match() must not write)."""
    if not ctx.hotel_slug or not module_enabled(ctx.cur, ctx.hotel_slug, "inquiries"):
        return False
    if ctx.referral:
        return True
    if ctx.sender_hmac is None:
        return True
    return not known_guest(ctx.cur, ctx.hotel_slug, ctx.sender_hmac)


def known_guest(cur: Any, hotel_slug: str, sender_hmac: bytes) -> bool:
    """guests.api.is_known_guest_sender, degrading to "not known" when the
    Guests module is not loaded in this process (its tables may not exist)
    or its lookup fails. The savepoint keeps a failed lookup from aborting
    the webhook's transaction; nothing is written."""
    from clefs.registry import enabled_modules

    if "guests" not in enabled_modules():
        return False
    cur.execute("SAVEPOINT inq_known_guest")
    try:
        known = bool(guests_api.is_known_guest_sender(cur, hotel_slug, phone_hmac=sender_hmac))
    except Exception:  # noqa: BLE001 - an inquiry the concierge could have taken beats a lost message
        cur.execute("ROLLBACK TO SAVEPOINT inq_known_guest")
        known = False
    cur.execute("RELEASE SAVEPOINT inq_known_guest")
    return known


def referral_fields(referral: dict | None) -> dict | None:
    if not referral:
        return None
    return {k: referral.get(k) for k in REFERRAL_FIELDS if referral.get(k) is not None} or None


@notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["inquiries.whatsapp_intake"], match=_match)
def whatsapp_intake(ctx: notify.InboundCtx) -> dict[str, Any]:
    hotel = get_hotel(ctx.cur, ctx.hotel_slug)
    at = ctx.timestamp or clock.now()
    ref = referral_fields(ctx.referral)
    text = ctx.text if ctx.text else f"[{ctx.kind}]"
    res = ingest(ctx.cur, hotel, Inbound(
        source="whatsapp",
        text=text,
        provider_message_id=ctx.provider_id,
        name=ctx.sender_name,
        phone=ctx.sender,
        channel_ref=ctx.recipient_id,
        ad_referral=ref,
        ctwa_clid=(ref or {}).get("ctwa_clid"),
        wa_window_expires_at=ctx.window_open_until or at + WA_WINDOW,
    ))
    return {"handled": True, "inquiry_id": res.inquiry["id"] if res.inquiry else None, "outcome": res.outcome}
