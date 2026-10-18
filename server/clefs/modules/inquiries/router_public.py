"""
Inquiry Guard public routes (no login; Canon 13 `/public/`), 04 spec 4.3.

- GET  /public/inquiries/{slug}/widget?k=&lang=  the embeddable form's
  config: hotel name, the consent lines (exact wording + version), allowed
  origins for the iframe's CSP, and a signed render stamp.
- POST /public/inquiries/{slug}                  the form post: 202 with no
  inquiry detail.

Bot handling: a filled honeypot or a form sent under 3 s after render gets
the same 202 and stores nothing (a bot learns nothing). Real limits are
5 per hour per IP hash and 200 per day per hotel (429).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from clefs import db, ratelimit
from clefs.auth import tokens
from clefs.core import clock
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, not_found, too_many
from clefs.core.hotels import find_hotel, module_enabled
from clefs.modules.inquiries import channels, consents, sla
from clefs.modules.inquiries.intake import Inbound, ingest
from clefs.settings import get_settings

log = logging.getLogger("clefs.inquiries.public")
router = APIRouter(tags=["inquiries-public"])
RENDER_PREFIX = "inqform"
# A render stamp older than this is refused like a bot: a real visitor fills
# the form within a sitting, while a harvested stamp replayed for days would
# otherwise pass the minimum-fill gate forever.
RENDER_MAX_AGE_MS = 6 * 3600 * 1000


class WebFormIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=40)
    check_in: date | None = None
    check_out: date | None = None
    rooms: int | None = Field(default=None, ge=1, le=200)
    adults: int | None = Field(default=None, ge=1, le=2000)
    children: int | None = Field(default=None, ge=0, le=2000)
    message: str | None = Field(default=None, max_length=5000)
    language: str | None = Field(default=None, max_length=10)
    consent_contact: bool = False
    consent_transfer_abroad: bool = False
    whatsapp_optin: bool = False
    consent_marketing_whatsapp: bool = False
    consent_marketing_email: bool = False
    utm: dict[str, str] | None = None
    gclid: str | None = Field(default=None, max_length=300)
    page_url: str | None = Field(default=None, max_length=2000)
    widget_key: str | None = Field(default=None, max_length=100)
    hp: str | None = Field(default=None, max_length=500)  # honeypot: humans leave it empty
    # The signed stamp from GET .../widget. Anything else (a bare number, a
    # forged or foreign stamp) is treated as a bot: silent 202, nothing stored.
    rendered_at: str | int | None = None


def _hotel(cur: Any, slug: str):
    h = find_hotel(cur, slug)
    if h is None or not module_enabled(cur, slug, "inquiries"):
        raise not_found("hotel not found")
    return h


def _ip_hash(ip: str | None) -> str:
    key = (get_settings().SECRET_KEY or "clefs").encode()
    return hmac.new(key, f"inq:{ip or 'unknown'}".encode(), hashlib.sha256).hexdigest()[:24]


def render_stamp(slug: str) -> str:
    return tokens.sign_ref(f"{RENDER_PREFIX}:{slug}:{clock.now_ms()}")


def _rendered_ms(slug: str, value: str | int | None) -> int | None:
    """The render time from the signed stamp only, or None (a bot).

    WHY no numeric fallback: an unsigned "client epoch ms" let a bot post
    rendered_at=0 and pass the minimum-fill gate every time, so the stamp
    protected nothing and each post stored an inquiry and sent the hotel's
    auto-ack to an arbitrary address. A stamp older than RENDER_MAX_AGE_MS,
    or one dated in the future, is refused too."""
    if not isinstance(value, str) or not value or value.isdigit():
        return None
    ref = tokens.verify_ref(value)
    parts = (ref or "").split(":")
    if len(parts) != 3 or parts[0] != RENDER_PREFIX or parts[1] != slug or not parts[2].isdigit():
        return None
    ms = int(parts[2])
    age = clock.now_ms() - ms
    if age < 0 or age > RENDER_MAX_AGE_MS:
        return None
    return ms


def _accepted() -> JSONResponse:
    return JSONResponse({"ok": True}, status_code=202)


# ─── Widget config ───


@router.get("/public/inquiries/{slug}/widget")
def widget_config(slug: str, k: str | None = None, lang: str | None = None) -> dict:
    with db.db() as cur:
        hotel = _hotel(cur, slug)
        acct = channels.widget_matches(cur, slug, k)
        if acct is None:
            raise not_found("widget not found")
        settings = sla.settings_for(cur, slug)
        from clefs.modules.inquiries.ack import privacy_url

        return {
            "hotel": {"slug": hotel.slug, "name": hotel.name, "languages": hotel.guest_languages or ["en"]},
            "language": consents.lang_of(lang),
            "consents": consents.texts(hotel, lang),
            "allowed_origins": (acct.get("config") or {}).get("allowed_origins") or [],
            "privacy_url": privacy_url(hotel, settings),
            "rendered_at": render_stamp(slug),
            "min_fill_s": settings.web_form_min_fill_s,
        }


# ─── Form post ───


@router.post("/public/inquiries/{slug}", status_code=202)
def submit_form(slug: str, body: WebFormIn) -> JSONResponse:
    ip = request_info().ip
    # The limits commit in their own transaction: a post refused below (a
    # missing consent, a bad key) still spends its slot.
    with db.db() as cur:
        _hotel(cur, slug)
        settings = sla.settings_for(cur, slug)
        ip_ok = ratelimit.hit(cur, f"inq:form:ip:{_ip_hash(ip)}", limit=settings.web_form_per_ip_hour, window_s=3600)
        hotel_ok = ip_ok and ratelimit.hit(
            cur, f"inq:form:hotel:{slug}", limit=settings.web_form_per_hotel_day, window_s=86400
        )
    if not ip_ok:
        raise too_many()
    if not hotel_ok:
        raise too_many("This hotel is receiving many requests; please try again later")
    with db.db() as cur:
        hotel = _hotel(cur, slug)
        if (body.hp or "").strip():
            return _accepted()
        rendered = _rendered_ms(slug, body.rendered_at)
        if rendered is None or clock.now_ms() - rendered < settings.web_form_min_fill_s * 1000:
            return _accepted()
        if channels.widget_matches(cur, slug, body.widget_key) is None:
            raise ClefsError(403, "this form is not connected to the hotel", "invalid_widget_key")
        # Both required consents are enforced separately (4.3, 4.12).
        if not body.consent_contact:
            raise ClefsError(422, "consent to be contacted is required", "consent_contact_required")
        if not body.consent_transfer_abroad:
            raise ClefsError(422, "consent to processing outside the UAE is required", "consent_transfer_abroad_required")
        if not (body.email or body.phone):
            raise ClefsError(422, "an email or a phone number is required", "contact_required")
        _store(cur, hotel, body, ip_hash=_ip_hash(ip))
    return _accepted()


def _store(cur: Any, hotel: Any, body: WebFormIn, *, ip_hash: str) -> None:
    from clefs.modules.inquiries import store

    # Normalise first (400 invalid_phone / invalid_email) so no consent row
    # is written for a contact that will not be stored.
    store.contact_columns(cur, hotel.slug, phone=body.phone, email=body.email, strict=True)
    granted = {k: bool(getattr(body, k)) for k in consents.LINES}
    evidence = {"ip_hash": ip_hash, "page_url": (body.page_url or "")[:500], "at": clock.now().isoformat()}
    ids, versions = consents.record(cur, hotel, granted=granted, phone=body.phone, email=body.email,
                                    language=body.language, evidence=evidence)
    utm = {k[:40]: str(v)[:200] for k, v in (body.utm or {}).items() if k.startswith("utm_")} or None
    res = ingest(cur, hotel, Inbound(
        source="web_form", text=(body.message or "").strip() or "[web form without a message]",
        name=body.name, phone=body.phone, email=body.email, language=body.language, strict_contact=True,
        check_in=body.check_in, check_out=body.check_out, rooms=body.rooms, adults=body.adults, children=body.children,
        gclid=body.gclid, utm=utm, page_url=body.page_url, consent_basis="express_web_form", consent_ids=ids,
        channel_ref="web_widget",
    ))
    _marketing_points(cur, hotel, body, versions, res.inquiry)


def _marketing_points(cur: Any, hotel: Any, body: WebFormIn, versions: dict[str, str], inquiry: dict | None) -> None:
    """A marketing opt-in is only reachable through Guests (Canon 10:
    audiences come from guests.api.marketing_audience), so each opted-in
    channel becomes a guest contact point with its consents. Guests not
    loaded, or refusing, never loses the inquiry."""
    from clefs.registry import enabled_modules

    if "guests" not in enabled_modules():
        return
    from clefs.modules.guests import api as guests_api

    for key, kind, value in (("consent_marketing_whatsapp", "phone", body.phone),
                             ("consent_marketing_email", "email", body.email)):
        if not (getattr(body, key) and value):
            continue
        cur.execute("SAVEPOINT inq_marketing_point")
        try:
            guests_api.record_own_contact(
                cur, hotel.slug, kind=kind, value=value, capture_source="inquiry",
                consents={"transfer_abroad": True, "marketing": True},
                consent_text_versions={"transfer_abroad": versions["consent_transfer_abroad"], "marketing": versions[key]},
                evidence={"language": consents.lang_of(body.language), "inquiry_ref": (inquiry or {}).get("ref")},
                actor=None,
            )
            cur.execute("RELEASE SAVEPOINT inq_marketing_point")
        except Exception:  # noqa: BLE001 - the consent rows above stand; Guests can be re-synced
            cur.execute("ROLLBACK TO SAVEPOINT inq_marketing_point")
            cur.execute("RELEASE SAVEPOINT inq_marketing_point")
            log.warning("guests.record_own_contact refused a web-form marketing opt-in", exc_info=True)
