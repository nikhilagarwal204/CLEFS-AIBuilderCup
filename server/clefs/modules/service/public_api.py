"""
Public guest tracker (03 spec 1.7): the link a guest gets after filing a
request. The 22-character token is the only credential (128 random bits,
stored hashed), so there is no hotel in the path. Staff appear by first
name only (05 spec: "Maria is on her way"); never a surname. This is the
superset of router_public.py (same paths and shapes, plus the WhatsApp
opt-in and the recovery hooks).

- GET  /public/r/{token}: api.guest_view + {whatsapp_available, subscribed}
  + the 05 spec's item_label, quantity, staff_first_name, eta_minutes,
  eta_at, accepted_at, proof_status, needs_guest_confirmation, closed_at and
  messages (staff replies and approved gestures in the guest's language).
- POST /public/r/{token}/feedback {fixed, comment?} (05 spec) and its older
  twin POST /public/r/{token}/confirm {resolved, comment?}: yes confirms
  and closes; no reopens the request, routes it again and flags recovery (a
  case with a suggested gesture for the duty manager). After checkout a
  "no" flags a post-checkout case and does not reopen (no one can fix a
  room the guest has left).
- POST /public/r/{token}/whatsapp {phone, consent: true, language?}: records
  whatsapp_updates consent against the hashed phone (never the raw number:
  core.privacy.contact_ref), stores the sealed phone, and sends the
  request_status_v1 utility template now; later state changes go out from
  whatsapp.push_tracker_updates.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.core import consent, privacy
from clefs.core.context import request_info
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.phone import normalize_phone
from clefs.modules.service import api, intake, state

router = APIRouter(prefix="/public/r", tags=["service-public"])

# Generous: the guest page polls each open job's card every 10 s, so one
# guest with a few open jobs reads often; tokens are long and random, so this
# only needs to stop token scanning.
IP_LIMIT = 600
IP_WINDOW_S = 600
# Opt-ins send a message to a number the caller typed: tight, per token and IP.
OPTIN_TOKEN_LIMIT = 3
OPTIN_IP_LIMIT = 10
OPTIN_WINDOW_S = 3600
CONSENT_PURPOSE = "whatsapp_updates"

# ─── Loading ───


def _load(cur: Any, token: str) -> tuple[str, dict]:
    if not ratelimit.hit(cur, f"svc:tracker:ip:{request_info().ip or 'unknown'}", limit=IP_LIMIT, window_s=IP_WINDOW_S):
        raise ClefsError(429, "Too many requests, try again shortly", "rate_limited")
    found = intake.token_lookup(cur, token)
    row = state.load(cur, found[0], found[1], lock=False) if found else None
    if row is None:
        raise ClefsError(404, "This link has expired", "not_found")
    return found[0], row


def _whatsapp_available(cur: Any, hotel_slug: str) -> bool:
    """The opt-in is offered only when the hotel can send from its own
    number (utility updates come from the hotel, not the platform)."""
    return bool(get_hotel(cur, hotel_slug).whatsapp_phone_number_id)


def view(cur: Any, slug: str, row: dict) -> dict:
    from clefs.modules.service import dispatch  # dispatch imports api, which this module also serves

    out = dispatch.tracker_view(cur, slug, row)
    cur.execute(
        "SELECT 1 FROM svc_tracker_subscriptions WHERE request_id = %s AND opted_out_at IS NULL LIMIT 1", (row["id"],)
    )
    out["subscribed"] = cur.fetchone() is not None
    out["whatsapp_available"] = _whatsapp_available(cur, slug)
    return out


@router.get("/{token}")
def tracker(token: str) -> dict:
    with db.db() as cur:
        slug, row = _load(cur, token)
        return view(cur, slug, row)


# ─── Confirm ───


class ConfirmIn(BaseModel):
    resolved: bool
    comment: str | None = Field(None, max_length=1000)


def _checked_out(cur: Any, slug: str, row: dict) -> bool:
    """True when the guest's stay in that room ended before today (hotel
    time). Unknown stays count as in-house: the request reopens."""
    if not row.get("room"):
        return False
    try:
        from clefs.modules.guests import api as guests_api

        cur.execute("SAVEPOINT svc_tracker_guest")
        g = guests_api.guest_context_for_room(cur, slug, row["room"]) or {}
        cur.execute("RELEASE SAVEPOINT svc_tracker_guest")
    except Exception:  # noqa: BLE001 - guest context is optional here
        cur.execute("ROLLBACK TO SAVEPOINT svc_tracker_guest")
        return False
    out = str(g.get("checkOut") or "")[:10]
    if not out:
        return False
    try:
        from clefs.core import time as htime

        return date.fromisoformat(out) < htime.local_date(get_hotel(cur, slug))
    except ValueError:
        return False


def _answer(token: str, fixed: bool, comment: str | None) -> dict:
    from clefs.modules.service import dispatch

    with db.db() as cur:
        slug, row = _load(cur, token)
        if not api.guest_view(row)["can_confirm"]:
            raise ClefsError(409, "This request can no longer be confirmed", "cannot_confirm")
        checked_out = (not fixed) and _checked_out(cur, slug, row)
        after = dispatch.feedback(cur, slug, row, fixed=fixed, comment=comment, checked_out=checked_out)
        return view(cur, slug, after)


@router.post("/{token}/confirm")
def confirm(token: str, body: ConfirmIn) -> dict:
    """"Was it sorted?" Yes confirms. No reopens the request, routes it again
    (the ack clock restarts from now) and flags recovery; after checkout it
    only flags recovery."""
    return _answer(token, body.resolved, body.comment)


class FeedbackIn(BaseModel):
    fixed: bool
    comment: str | None = Field(None, max_length=1000)


@router.post("/{token}/feedback")
def feedback(token: str, body: FeedbackIn) -> dict:
    """The guest card's "Is everything OK?" (05 spec): fixed=true confirms
    and closes the job; fixed=false reopens it and flags recovery. Returns
    the tracker view."""
    return _answer(token, body.fixed, body.comment)


# ─── WhatsApp opt-in ───


class WhatsAppIn(BaseModel):
    phone: str = Field(..., min_length=6, max_length=32)
    # the tracker's checkbox; nothing is stored or sent without it (PDPL: consent is the basis)
    consent: bool
    language: Literal["en", "ar"] = "en"


def _e164(cur: Any, slug: str, raw: str) -> str:
    """E.164 from what the guest typed; a national number ("050 123 4567")
    takes the hotel's calling code."""
    try:
        return normalize_phone(raw)
    except ClefsError:
        code = privacy.hotel_calling_code(slug, cur=cur)
        digits = "".join(ch for ch in raw if ch.isdigit())
        if not code or not digits or raw.strip().startswith(("+", "00")):
            raise
        return normalize_phone(f"+{code}{digits.lstrip('0')}")


@router.post("/{token}/whatsapp")
def whatsapp_optin(token: str, body: WhatsAppIn) -> dict:
    """Status updates on WhatsApp for this request. Records consent
    (whatsapp_updates, the wording's version when the hotel published one),
    keeps the number sealed, and sends the current status now."""
    from clefs.modules.service import whatsapp

    if not body.consent:
        raise ClefsError(400, "Tick the box to agree to WhatsApp updates", "consent_required")
    with db.db() as cur:
        slug, row = _load(cur, token)
        ip = request_info().ip or "unknown"
        if not (ratelimit.hit(cur, f"svc:tracker:optin:{token[:22]}", limit=OPTIN_TOKEN_LIMIT, window_s=OPTIN_WINDOW_S)
                and ratelimit.hit(cur, f"svc:tracker:optin-ip:{ip}", limit=OPTIN_IP_LIMIT, window_s=OPTIN_WINDOW_S)):
            raise ClefsError(429, "Too many requests, try again later", "rate_limited")
        if not _whatsapp_available(cur, slug):
            raise ClefsError(409, "This hotel does not send WhatsApp updates yet", "whatsapp_unavailable")
        phone = _e164(cur, slug, body.phone)
        phone_hmac = privacy.contact_hmac(slug, "phone", phone, cur=cur)
        text = consent.current_consent_text(cur, CONSENT_PURPOSE, language=body.language, hotel_slug=slug)
        c = consent.record_consent(
            cur, slug, phone_hmac.hex(), CONSENT_PURPOSE, True, source="service.tracker",
            evidence={"request_id": row["id"], "surface": "tracker"}, text_version=(text or {}).get("version"),
            language=body.language,
        )
        cur.execute(
            """INSERT INTO svc_tracker_subscriptions (hotel_slug, request_id, phone_hmac, phone_enc, consent_id, language)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (request_id, phone_hmac) DO UPDATE SET opted_out_at = NULL, consent_id = EXCLUDED.consent_id,
                   language = EXCLUDED.language, phone_enc = EXCLUDED.phone_enc
               RETURNING *""",
            (slug, row["id"], phone_hmac, privacy.seal(phone), c.get("id"), body.language),
        )
        sub = dict(cur.fetchone())
        res = whatsapp.send_status(cur, get_hotel(cur, slug), sub, row)
        out = view(cur, slug, row)
        out["whatsapp"] = {"subscribed": True, "sent": bool(getattr(res, "ok", False)),
                           "phone": privacy.mask_phone(phone)}
        return out
