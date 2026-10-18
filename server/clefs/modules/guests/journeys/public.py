"""
Public journey routes (04 spec 3.10): the data behind /g/[token] (J1
preferences and own contact, the J5 transfer tick) and /g/f/[token] (J7
feedback). Rate-limited per IP through ratelimit.hit; the token's purpose is
enforced on every route (a feedback token cannot post preferences).

Consent tick boxes are rendered from the texts returned here (3.4), never
hardcoded in the client, and the version shown is posted back.
"""

from __future__ import annotations

import logging
from datetime import time, timedelta
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.core import clock
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.hotels import get_hotel
from clefs.modules.guests import contacts, identity, stays_port, timeutil
from clefs.modules.guests.errors import ConsentNotAllowed
from clefs.modules.guests.journeys import feedback
from clefs.modules.guests.journeys import tokens as jtokens

log = logging.getLogger("clefs.guests.journeys.public")

router = APIRouter(prefix="/public/journeys", tags=["guests-journeys-public"])

PER_IP = 60
WINDOW_S = 600
PREFS_PURPOSES = ("transfer_abroad", "whatsapp_updates", "marketing_whatsapp", "marketing_email")


def _limit(cur: Any, what: str) -> None:
    if not ratelimit.hit(cur, f"gst:journey:{what}:{request_info().ip or 'unknown'}", limit=PER_IP, window_s=WINDOW_S):
        raise too_many()


def _token(cur: Any, token: str, purposes: tuple[str, ...], *, lock: bool = False, need_valid: bool = True) -> dict:
    row = jtokens.lookup(cur, token, lock=lock)
    if row is None or row["purpose"] not in purposes:
        raise ClefsError(404, "This link is not valid", "link_invalid")
    st = jtokens.state(row)
    if need_valid and st != "valid":
        raise ClefsError(410, "This link has expired", "link_expired" if st != "used" else "link_used")
    return row


def _texts(cur: Any, hotel: Any, purposes: tuple[str, ...], language: str) -> dict:
    out = {}
    for p in purposes:
        t = identity.consent_text(cur, hotel.slug, p, language)
        if t:
            out[p] = {"version": t["version"], "language": t["language"], "body": t["body"].replace("{hotel}", hotel.name)}
    return out


def _shown_version(cur: Any, hotel_slug: str, purpose: str, language: str, posted: str | None) -> str | None:
    """The wording version to store as consent evidence. A posted version
    is kept only when it is one this page could have shown (the current one
    for the language, or the EN fallback); anything else is a forged or
    stale claim and is refused, so the page reloads the current wording
    instead of the store recording a text the guest never read."""
    current = identity.text_version(cur, hotel_slug, purpose, language)
    if posted is None:
        return current
    if posted in {current, identity.text_version(cur, hotel_slug, purpose, "en")} - {None}:
        return posted
    raise ConsentNotAllowed("The consent wording has changed; please reload the page", reason="text_version_mismatch")


def _is_ota(cur: Any, hotel_slug: str, reservation_id: int) -> bool:
    """An OTA stay gets no marketing boxes (Canon 19). Unknown counts as OTA."""
    stay = contacts.stay_for_reservation(cur, hotel_slug, reservation_id)
    if stay is None:
        return True
    if stays_port.is_ota(stay.get("source")):
        return True
    cur.execute(
        "SELECT 1 FROM gst_contact_points WHERE hotel_slug = %s AND reservation_id = %s AND provenance IN ('pms_ota', 'ota_alias') LIMIT 1",
        (hotel_slug, reservation_id),
    )
    return cur.fetchone() is not None


def page_config(cur: Any, row: dict, *, language: str | None = None) -> dict:
    """GET /public/journeys/{token}: everything a page needs to render."""
    hotel = get_hotel(cur, row["hotel_slug"])
    stay = contacts.stay_for_reservation(cur, hotel.slug, row["reservation_id"]) or {}
    lang = language if language in ("en", "ar") else ((stay.get("language") or "en")[:2] if (stay.get("language") or "en")[:2] in ("en", "ar") else "en")
    st = jtokens.state(row)
    out: dict[str, Any] = {
        "purpose": row["purpose"], "state": st, "language": lang,
        "hotel": {"slug": hotel.slug, "name": hotel.name, "city": hotel.city},
    }
    if st != "valid" and row["purpose"] != "feedback":
        # A dead link only says so. The stored preferences (free-text
        # requests, often dietary or health notes) and the stay dates live
        # until purge_after, long past the link's expiry: a forwarded or
        # leaked link must not keep reading them.
        return out
    if st == "valid":
        # An expired feedback link still shows the review line (the same
        # public links for every guest, no-gating rule), never the stay.
        out["stay"] = {"check_in": stay.get("check_in"), "check_out": stay.get("check_out")}
    if row["purpose"] == "prefs":
        ota = _is_ota(cur, hotel.slug, row["reservation_id"])
        purposes = ("transfer_abroad", "whatsapp_updates") if ota else PREFS_PURPOSES
        cur.execute("SELECT arrival_time, requests, occasion, language FROM gst_stay_prefs WHERE hotel_slug = %s "
                    "AND reservation_id = %s", (hotel.slug, row["reservation_id"]))
        prefs = cur.fetchone()
        out.update(marketing_boxes=not ota, consent_texts=_texts(cur, hotel, purposes, lang),
                   prefs=dict(prefs) if prefs else None)
    elif row["purpose"] == "consent":
        out.update(consent_texts=_texts(cur, hotel, ("transfer_abroad",), lang))
    else:
        cur.execute("SELECT 1 FROM gst_feedback WHERE hotel_slug = %s AND reservation_id = %s",
                    (hotel.slug, row["reservation_id"]))
        # The same links for every guest, above the survey (3.9).
        out.update(submitted=cur.fetchone() is not None, review_links=feedback.review_links(cur, hotel.slug).model_dump())
    return out


@router.get("/{token}")
def get_page(token: str, lang: str | None = None) -> dict:
    with db.db() as cur:
        _limit(cur, "get")
        row = _token(cur, token, jtokens.PURPOSES, need_valid=False)
        return page_config(cur, row, language=lang)


# ─── POST prefs (J1 page) ───


class ContactIn(BaseModel):
    kind: Literal["phone", "email"]
    value: str = Field(min_length=3, max_length=200)


class PrefsIn(BaseModel):
    arrival_time: str | None = Field(default=None, max_length=40)
    requests: str | None = Field(default=None, max_length=1000)
    occasion: str | None = Field(default=None, max_length=200)
    language: Literal["en", "ar"] | None = None
    contact: ContactIn | None = None
    consents: dict[str, bool] = Field(default_factory=dict)
    consent_text_versions: dict[str, str] = Field(default_factory=dict)


@router.post("/{token}/prefs")
def post_prefs(token: str, body: PrefsIn) -> dict:
    """Arrival details, and optionally the guest's own contact. On an OTA
    stay that contact becomes a guest_stay point (this stay's messages
    only); marketing consents there are refused (422 consent_not_allowed)."""
    with db.db() as cur:
        _limit(cur, "prefs")
        row = _token(cur, token, ("prefs",))
        hotel = get_hotel(cur, row["hotel_slug"])
        rid = row["reservation_id"]
        stay = contacts.stay_for_reservation(cur, hotel.slug, rid) or {}
        co = stays_port.as_date(stay.get("check_out"))
        purge = timeutil.at_local(hotel, co + timedelta(days=30), time(23, 59)) if co else clock.now() + timedelta(days=60)
        cur.execute(
            """
            INSERT INTO gst_stay_prefs (hotel_slug, reservation_id, arrival_time, requests, occasion, language,
                                        submitted_at, purge_after)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, reservation_id) DO UPDATE SET arrival_time = EXCLUDED.arrival_time,
                requests = EXCLUDED.requests, occasion = EXCLUDED.occasion, language = EXCLUDED.language,
                submitted_at = EXCLUDED.submitted_at, purge_after = EXCLUDED.purge_after
            """,
            (hotel.slug, rid, body.arrival_time, body.requests, body.occasion, body.language, clock.now(), purge),
        )
        point_id = None
        if body.contact is not None:
            lang = body.language or "en"
            granted = {p: bool(v) for p, v in body.consents.items() if p in PREFS_PURPOSES}
            versions = {p: _shown_version(cur, hotel.slug, p, lang, body.consent_text_versions.get(p) or None)
                        for p, g in granted.items() if g}
            point_id = contacts.record_own_contact(
                cur, hotel.slug, reservation_id=rid, kind=body.contact.kind, value=body.contact.value,
                capture_source="j1_form", consents=granted, consent_text_versions=versions,
                evidence={"language": lang, "token_purpose": "prefs"}, actor=None,
            )
        return {"ok": True, "contact_saved": point_id is not None}


# ─── POST consent (the J5 transfer tick) ───


class ConsentIn(BaseModel):
    transfer_abroad: bool
    text_version: str | None = None
    language: Literal["en", "ar"] = "en"


@router.post("/{token}/consent")
def post_consent(token: str, body: ConsentIn) -> dict:
    """Creates the guest_direct point from the number that said YES (3.4).
    Single use; without the tick nothing is stored."""
    with db.db() as cur:
        _limit(cur, "consent")
        row = _token(cur, token, ("consent",), lock=True)
        if not body.transfer_abroad:
            raise ConsentNotAllowed("The transfer consent is required to keep your number", reason="transfer_abroad_required")
        inbound_id = (row.get("ref") or {}).get("inbound_message_id")
        cur.execute("SELECT sender FROM inbound_messages WHERE id = %s AND hotel_slug = %s", (inbound_id, row["hotel_slug"]))
        msg = cur.fetchone()
        if msg is None or not msg["sender"]:
            raise ClefsError(404, "This link is not valid", "link_invalid")
        version = _shown_version(cur, row["hotel_slug"], "transfer_abroad", body.language, body.text_version or None)
        point_id = contacts.record_own_contact(
            cur, row["hotel_slug"], reservation_id=row["reservation_id"], kind="phone", value=msg["sender"],
            capture_source="j5_reply", consents={"transfer_abroad": True},
            consent_text_versions={"transfer_abroad": version},
            evidence={"inbound_message_id": inbound_id, "language": body.language}, actor=None,
        )
        jtokens.mark_used(cur, row["token_hash"])
        return {"ok": True, "point_created": bool(point_id)}


# ─── Feedback (J7) ───


@router.post("/{token}/feedback")
def post_feedback(token: str, body: feedback.FeedbackIn) -> dict:
    """Identical for every payload: {completion_key: "thanks", review_links}."""
    with db.db() as cur:
        _limit(cur, "feedback")
        row = _token(cur, token, ("feedback",))
        return feedback.submit(cur, row, body)


@router.get("/{token}/go/{platform}")
def go(token: str, platform: str) -> RedirectResponse:
    """302 to the hotel's review page; counts the click, nothing else."""
    with db.db() as cur:
        _limit(cur, "go")
        row = _token(cur, token, ("feedback",), need_valid=False)
        url = feedback.click(cur, row["hotel_slug"], platform)
        if url is None:
            raise ClefsError(404, "No review link for this platform", "review_link_missing")
    return RedirectResponse(url, status_code=302)
