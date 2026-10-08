"""
Trust public routes (Canon 13: guest-facing paths live under /public/).
The privacy request form (03 spec 5.3, client page /privacy/request?hotel=)
posts here and lands in the console DSR queue.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.core import privacy
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, not_found, too_many
from clefs.core.hotels import find_hotel
from clefs.modules.trust import dsr

router = APIRouter(prefix="/public/privacy", tags=["trust"])
log = logging.getLogger("clefs.trust")

# ─── Privacy request form ───

PER_IP_PER_HOUR = 5
# Past this many submissions in 24 h a hotel's requests are still filed, but
# flagged suspected_flood (security-privacy-8): a 429 here would let a few
# IPs lock genuine data subjects out of a legally mandated channel. The
# counter is a threshold, not a quota.
PER_HOTEL_PER_DAY = 50
# Acknowledgement emails to one address per 24 h. With the per-IP limit this
# stops the form being used to mail a chosen address from the hotel's
# sending identity; flagged requests get no acknowledgement at all.
ACKS_PER_ADDRESS_PER_DAY = 2


class PublicRequestBody(BaseModel):
    hotel: str = Field(max_length=80)
    type: str = Field(max_length=20)
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    room: str | None = Field(default=None, max_length=20)
    reservation_ref: str | None = Field(default=None, max_length=80)
    stay_date: str | None = Field(default=None, max_length=10)
    details: str | None = Field(default=None, max_length=4000)
    language: str = Field(default="en", max_length=8)
    website: str | None = Field(default=None, max_length=200)  # honeypot: people leave it empty


def _open_hotel(cur, slug: str):
    hotel = find_hotel(cur, (slug or "").strip().lower())
    if hotel is None or hotel.status == "archived":
        raise not_found("Hotel not found", "hotel_not_found")
    return hotel


@router.get("/form")
def form_context(hotel: str) -> dict:
    """What the /privacy/request?hotel= page shows: the hotel's name and the
    request types (labels are the client's, keyed by type)."""
    with db.db(readonly=True) as cur:
        h = _open_hotel(cur, hotel)
    return {"hotel": {"slug": h.slug, "name": h.name, "guest_languages": h.guest_languages},
            "types": list(dsr.TYPES), "due_days": dsr.DUE_DAYS}


@router.post("/requests", status_code=202)
def submit_request(body: PublicRequestBody) -> dict:
    """File a privacy request into the hotel's console queue. The answer is
    the same whether or not the person is known to the hotel, so the form
    cannot be used to learn who stayed where."""
    ok = {"ok": True, "due_days": dsr.DUE_DAYS}
    if body.website:
        return ok  # a bot filled the hidden field: accept silently, file nothing
    ip = request_info().ip or "unknown"
    with db.db() as cur:
        hotel = _open_hotel(cur, body.hotel)
        if not ratelimit.hit(cur, f"trust:dsr:ip:{ip}", limit=PER_IP_PER_HOUR, window_s=3600):
            raise too_many()
        flood = not ratelimit.hit(cur, f"trust:dsr:hotel:{hotel.slug}", limit=PER_HOTEL_PER_DAY, window_s=86400)
        ids = dsr.Identifiers(name=body.name, email=body.email, phone=body.phone, room=body.room,
                              reservation_ref=body.reservation_ref, stay_date=body.stay_date)
        row = dsr.create_request(cur, hotel.slug, type=body.type, identifiers=ids, details=body.details,
                                 channel="public_form", language=body.language, suspected_flood=flood)
        if flood:
            log.warning("privacy form for %s over %s/day: request %s filed as suspected flood",
                        hotel.slug, PER_HOTEL_PER_DAY, row["id"])
        elif body.email and _ack_allowed(cur, hotel.slug, body.email):
            _acknowledge(cur, hotel, row, body.email)
    # Same answer whether flagged or not: a flooder learns nothing, and a
    # genuine requester is told the request is filed, which it is.
    return ok


def _ack_allowed(cur, hotel_slug: str, email: str) -> bool:
    # Keyed by the address's HMAC, never the address: rate_limits is not a
    # personal-data table and must not become one.
    try:
        h = privacy.contact_hmac(hotel_slug, "email", email, cur=cur).hex()[:32]
    except ClefsError:
        return False  # not a deliverable address: nothing to acknowledge to
    return ratelimit.hit(cur, f"trust:dsr:ack:{hotel_slug}:{h}", limit=ACKS_PER_ADDRESS_PER_DAY, window_s=86400)


def _acknowledge(cur, hotel, row: dict, email: str) -> None:
    # A receipt with no personal data in it: it proves the request was filed
    # and when it is due. A delivery problem never fails the request.
    try:
        from clefs import notify

        due = f"{row['due_at']:%d %B %Y}"
        text = (
            f"{hotel.name} has received your privacy request ({row['type']}). "
            f"The hotel will check your identity and reply by {due}. You do not need to do anything now."
        )
        notify.send_email(
            email, f"Your privacy request to {hotel.name}", f"<p>{text}</p>", text=text,
            hotel_slug=hotel.slug, purpose="trust.dsr_ack", category="utility",
            idempotency_key=f"trust.dsr_ack:{row['id']}", cur=cur,
        )
    except Exception:  # noqa: BLE001
        log.warning("privacy request %s: acknowledgement email not queued", row["id"], exc_info=True)
