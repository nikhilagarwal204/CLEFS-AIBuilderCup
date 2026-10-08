"""
STOP and unsubscribe (03 spec 2.7: "STOP/unsubscribe withdraws consent";
04 spec F4 slot guests.optout_consent = 5, the first guest handler).

- WhatsApp: a message to the hotel's number that IS a stop word (the whole
  message, EN or AR, not a substring: "don't stop the music" is not an
  opt-out), from someone the hotel holds or has messaged (known_to_hotel),
  suppresses the sender for everything; "stop promotions" (the
  marketing templates' quick reply) suppresses marketing only. The guest
  gets one short confirmation inside the window their message opened.
- Email: every marketing email carries /public/guests/unsubscribe/<token>.
  GET only describes (mail scanners prefetch links); POST unsubscribes
  (RFC 8058 one-click posts there too). The token is a signed ref holding
  the identity hmac, never the address.

YES (journey consent) is not handled here: it needs the journey message it
answers (04 spec 3.8), so it belongs to the journey replies handler.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Any

from fastapi import APIRouter

from clefs import db, notify, ratelimit
from clefs.auth import tokens
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import find_hotel
from clefs.modules.guests import contacts, identity

log = logging.getLogger("clefs.guests.optout")

STOP_ALL = frozenset(
    {"stop", "stopall", "stop all", "unsubscribe", "optout", "opt out", "end", "quit", "cancel all",
     "توقف", "إيقاف", "ايقاف", "الغاء الاشتراك", "إلغاء الاشتراك", "وقف"}
)
STOP_MARKETING = frozenset({"stop promotions", "stop offers", "إيقاف العروض", "ايقاف العروض"})
ACK = {
    "en": "You will not receive further messages from {hotel} on WhatsApp. Message us any time if you need help.",
    "ar": "لن تصلك رسائل أخرى من {hotel} عبر واتساب. راسلنا في أي وقت إذا احتجت إلى مساعدة.",
}
ACK_MARKETING = {
    "en": "You will not receive further offers from {hotel}. Messages about your stay continue.",
    "ar": "لن تصلك عروض أخرى من {hotel}. ستستمر الرسائل المتعلقة بإقامتك.",
}


def normalise(text: str | None) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower().strip()
    t = re.sub(r"[ً-ْـ]", "", t)  # Arabic diacritics and tatweel
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def stop_scope(text: str | None) -> str | None:
    """'all', 'marketing' or None for a message that is not an opt-out."""
    t = normalise(text)
    if t in STOP_ALL or t.replace(" ", "") in STOP_ALL:
        return "all"
    if t in STOP_MARKETING:
        return "marketing"
    return None


def _is_arabic(text: str | None) -> bool:
    return bool(re.search(r"[؀-ۿ]", text or ""))


def known_to_hotel(cur: Any, hotel_slug: str, h: bytes, sender: str) -> bool:
    """Someone this hotel holds (a point, a consent, a stay, a PMS contact,
    an earlier suppression) or has messaged on WhatsApp. Only then does a
    STOP mean "stop messaging me": a first-ever "stop" from a stranger has
    nothing to stop, and passing it on lets the concierge answer a person
    who may just be saying "stop" about something else."""
    cur.execute(
        """
        SELECT (EXISTS (SELECT 1 FROM gst_contact_points WHERE hotel_slug = %(s)s AND value_hmac = %(h)s)
             OR EXISTS (SELECT 1 FROM gst_suppressions WHERE hotel_slug = %(s)s AND value_hmac = %(h)s)
             OR EXISTS (SELECT 1 FROM consents WHERE hotel_slug = %(s)s AND subject_ref = %(ref)s)
             OR EXISTS (SELECT 1 FROM gst_stays WHERE hotel_slug = %(s)s AND %(h)s = ANY(contact_hmacs))
             OR EXISTS (SELECT 1 FROM reservation_contacts WHERE hotel_slug = %(s)s AND value_hmac = %(h)s)
             OR EXISTS (SELECT 1 FROM outbound_messages WHERE hotel_slug = %(s)s AND channel = 'whatsapp'
                        AND recipient = %(to)s)) AS known
        """,
        {"s": hotel_slug, "h": bytes(h), "ref": bytes(h).hex(), "to": sender},
    )
    return bool(cur.fetchone()["known"])


def _sender_hmac(ctx: notify.InboundCtx) -> bytes | None:
    if ctx.sender_hmac:
        return bytes(ctx.sender_hmac)
    try:
        return identity.hmac_for(ctx.cur, ctx.hotel_slug, "phone", ctx.sender)
    except ClefsError:
        return None


def _stop_match(ctx: notify.InboundCtx) -> bool:
    if not ctx.hotel_slug or stop_scope(ctx.text or ctx.button_title) is None:
        return False
    h = _sender_hmac(ctx)
    return h is not None and known_to_hotel(ctx.cur, ctx.hotel_slug, h, ctx.sender)


@notify.on_whatsapp("guest", priority=notify.GUEST_PRIORITY["guests.optout_consent"], match=_stop_match)
def whatsapp_stop(ctx: notify.InboundCtx) -> dict:
    text = ctx.text or ctx.button_title
    scope = stop_scope(text)
    if scope is None or not ctx.hotel_slug:
        return {"handled": False}
    h = _sender_hmac(ctx)
    if h is None:
        return {"handled": False}
    contacts.suppress(ctx.cur, ctx.hotel_slug, h, scope=scope, reason="stop", source="whatsapp_stop")
    hotel = find_hotel(ctx.cur, ctx.hotel_slug)
    lang = "ar" if _is_arabic(text) else "en"
    body = (ACK if scope == "all" else ACK_MARKETING)[lang].format(hotel=hotel.name if hotel else "the hotel")
    try:
        ctx.reply_text(body, purpose="guests.optout_ack", idempotency_key=f"gst-optout-ack:{ctx.inbound_id}")
    except Exception:  # the opt-out is recorded; a failed courtesy reply must not undo it
        log.exception("opt-out acknowledgement failed at %s", ctx.hotel_slug)
    return {"handled": True, "scope": scope}


# ─── Email unsubscribe ───

UNSUB_PREFIX = "gu"
UNSUB_PER_IP = 30
UNSUB_WINDOW_S = 600
router = APIRouter(prefix="/public/guests", tags=["guests-public"])


def unsubscribe_token(hotel_slug: str, *, identity_hmac: bytes, channel: str) -> str:
    return tokens.sign_ref(f"{UNSUB_PREFIX}:{hotel_slug}:{channel}:{bytes(identity_hmac).hex()}")


def unsubscribe_path(hotel_slug: str, *, identity_hmac: bytes, channel: str) -> str:
    return f"/public/guests/unsubscribe/{unsubscribe_token(hotel_slug, identity_hmac=identity_hmac, channel=channel)}"


def _parse(token: str) -> tuple[str, str, bytes]:
    ref = tokens.verify_ref(token or "")
    parts = (ref or "").split(":")
    if len(parts) != 4 or parts[0] != UNSUB_PREFIX or parts[2] not in ("email", "whatsapp"):
        raise ClefsError(404, "This unsubscribe link is not valid", "link_invalid")
    h = identity.from_ref(parts[3])
    if h is None:
        raise ClefsError(404, "This unsubscribe link is not valid", "link_invalid")
    return parts[1], parts[2], h


def _limit(cur: Any) -> None:
    if not ratelimit.hit(cur, f"gst:unsub:{request_info().ip or 'unknown'}", limit=UNSUB_PER_IP, window_s=UNSUB_WINDOW_S):
        raise too_many()


@router.get("/unsubscribe/{token}")
def unsubscribe_status(token: str) -> dict:
    slug, channel, h = _parse(token)
    with db.db() as cur:
        _limit(cur)
        hotel = find_hotel(cur, slug)
        if hotel is None:
            raise ClefsError(404, "This unsubscribe link is not valid", "link_invalid")
        done = contacts.is_suppressed(cur, slug, [h], marketing=True)
        return {"hotel": {"slug": slug, "name": hotel.name}, "channel": channel,
                "status": "unsubscribed" if done else "subscribed"}


@router.post("/unsubscribe/{token}")
def unsubscribe(token: str) -> dict:
    """One-click (RFC 8058 posts a form body, which is ignored)."""
    slug, channel, h = _parse(token)
    with db.db() as cur:
        _limit(cur)
        if find_hotel(cur, slug) is None:
            raise ClefsError(404, "This unsubscribe link is not valid", "link_invalid")
        contacts.suppress(cur, slug, h, scope="marketing", reason="stop", source=f"{channel}_unsubscribe")
    return {"ok": True, "status": "unsubscribed", "channel": channel}
