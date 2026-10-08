"""
Contact eligibility and channel resolution (04 spec 3.4).

Order: the first eligible channel wins, WhatsApp before email. Template
approval is part of eligibility, so a WhatsApp-eligible guest with no
approved WhatsApp template falls through to email. With none:
- `no_eligible_contact` when no channel has a usable contact,
- `template_missing` when a contact exists but no channel has an approved
  template (the dispatcher then lets the dial gate record it as observed),
- `alias_expired` when the only contacts are past usable_until (an Expedia
  alias after check-out, a Booking alias after check-out + 30 d).

Canon 19 is enforced HERE, per provenance, whatever the consent table says:
ota_alias, pms_ota and guest_stay points never receive marketing, and a
pms_ota phone never receives a business-initiated WhatsApp unless the guest
opened a window by writing first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from clefs import notify
from clefs.modules.brief import api as brief_api
from clefs.modules.guests import contacts, identity
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import steps, templates

CHANNEL_ORDER = ("whatsapp", "email")
KIND = {"whatsapp": "phone", "email": "email"}
WA_UTILITY_PROVENANCES = frozenset({"pms_direct", "guest_direct", "guest_stay"})
MARKETING_PROVENANCES = frozenset({"guest_direct", "pms_direct"})
OTA_PROVIDED = frozenset({"pms_ota", "ota_alias"})


@dataclass
class Choice:
    channel: str
    point: dict
    template: dict
    category: str  # UTILITY | MARKETING (Meta's, when synced)
    consent_id: int | None = None  # the marketing consent a send relies on


@dataclass
class Resolution:
    choice: Choice | None
    reason: str | None
    channels: dict[str, bool] = field(default_factory=dict)  # eligible channel -> approved template (the gate)
    language: str = "en"


# ─── WhatsApp readiness (3.5) ───


def whatsapp_configured(hotel: Any) -> bool:
    """A hotel without its own WhatsApp number sends email only."""
    return bool(getattr(hotel, "whatsapp_phone_number_id", None))


def billing_ready(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings) -> bool:
    """The WABA has a Meta payment method (from 2026-09-30 unpaid accounts
    reportedly stop delivering service messages; partly UNVERIFIED). Read
    through Brief's WhatsApp integration when it offers a status, else the
    owner's attestation in settings. Fails closed."""
    fn = getattr(brief_api, "wa_billing_status", None)  # FOUNDATION-REQUEST: Brief lane, see README
    if fn is not None:
        try:
            status = fn(cur, hotel.slug)
        except Exception:
            status = None
        # Brief answers ready=None when no WABA is connected or it was never
        # checked ("unknown"): only a real True/False overrides the owner's
        # attestation below (integrator fix: brief.api.wa_billing_status).
        if isinstance(status, dict) and isinstance(status.get("ready"), bool):
            return status["ready"]
    return cfg.wa_billing_attested_at is not None


def whatsapp_usable(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings) -> bool:
    return whatsapp_configured(hotel) and billing_ready(cur, hotel, cfg)


# ─── Points of a stay ───


def stay_points(cur: Any, hotel_slug: str, stay: dict) -> list[dict]:
    """Active points of the reservation, plus the guest's own guest_direct
    points that belong to the same person: captured without a reservation
    (registration card by room) but hashed onto this stay or its profile."""
    idents: list[bytes] = [bytes(h) for h in (stay.get("contact_hmacs") or []) if h]
    if stay.get("guest_profile_id"):
        prof = identity.get_profile(cur, hotel_slug, stay["guest_profile_id"])
        if prof:
            idents += identity.identities_of(prof)
    cur.execute(
        f"""
        SELECT {contacts.POINT_COLS} FROM gst_contact_points
        WHERE hotel_slug = %s AND status = 'active' AND value_hmac IS NOT NULL
          AND (reservation_id = %s OR (provenance = 'guest_direct' AND value_hmac = ANY(%s)))
        ORDER BY (provenance = 'guest_direct') DESC, last_used_at DESC, id DESC
        """,
        (hotel_slug, stay["reservation_id"], idents),
    )
    return [dict(r) for r in cur.fetchall()]


def consent_id(cur: Any, hotel_slug: str, h: bytes, purpose: str) -> int | None:
    """The latest consent row for the identity and purpose when it is a
    grant (the consents table is append-only; the latest row is the state)."""
    cur.execute(
        "SELECT id, granted FROM consents WHERE hotel_slug = %s AND subject_ref = %s AND purpose = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (hotel_slug, bytes(h).hex(), purpose),
    )
    row = cur.fetchone()
    return int(row["id"]) if row and row["granted"] else None


# ─── Eligibility of one point ───


def _usable(p: dict, at: datetime) -> bool:
    return p.get("usable_until") is None or p["usable_until"] > at


def point_eligible(cur: Any, hotel_slug: str, p: dict, *, channel: str, category: str, step: str,
                   cfg: gsettings.GuestsSettings, at: datetime) -> tuple[bool, str | None, int | None]:
    """(eligible, reason when not, marketing consent id) for one point."""
    if p["kind"] != KIND[channel]:
        return False, "kind", None
    h = bytes(p["value_hmac"])
    marketing = category == "MARKETING"
    if contacts.is_suppressed(cur, hotel_slug, [h], marketing=marketing):
        return False, "suppressed", None
    if not _usable(p, at):
        return False, "alias_expired", None
    prov = p["provenance"]
    if marketing:
        if prov not in MARKETING_PROVENANCES:
            return False, "provenance", None  # Canon 19: never ota_alias, pms_ota or guest_stay
        cid = consent_id(cur, hotel_slug, h, identity.MARKETING_PURPOSE[channel])
        if cid is None:
            return False, "no_consent", None
        if channel == "whatsapp" and p.get("country_prefix") in (None, "+1"):
            return False, "nanp_blocked", None  # +1 is a superset of Meta's US ban; unknown fails closed
        return True, None, cid
    if channel == "whatsapp":
        window = notify.wa_window_open_until(cur, hotel_slug, sender_hmac=h, at=at)
        if prov in WA_UTILITY_PROVENANCES:
            if contacts.identity_granted(cur, hotel_slug, h, "whatsapp_updates") or window:
                return True, None, None
            return False, "no_optin", None
        if prov == "pms_ota" and window:
            return True, None, None  # the guest wrote first
        return False, "provenance", None
    if step == "j7" and prov in OTA_PROVIDED and not cfg.j7_for_ota_provided_contacts:
        return False, "ota_contact_off", None
    return True, None, None


# ─── Resolution ───


def stay_language(stay: dict, hotel: Any) -> str:
    lang = (stay.get("language") or "").lower()[:2]
    if lang in ("en", "ar"):
        return lang
    return "ar" if (getattr(hotel, "locale", "") or "").startswith("ar") else "en"


def resolve(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, step: str, *, at: datetime,
            only: tuple[str, ...] | None = None, sync: bool = True) -> Resolution:
    """Pick the channel, point and template for one step of one stay."""
    lang = stay_language(stay, hotel)
    points = stay_points(cur, hotel.slug, stay)
    channels: dict[str, bool] = {}
    reasons: set[str] = set()
    wa_ok = whatsapp_usable(cur, hotel, cfg)
    for ch in only or CHANNEL_ORDER:
        if ch == "whatsapp" and not wa_ok:
            continue
        tpl = templates.approved_template(cur, hotel.slug, step, ch, lang, sync=sync)
        category = templates.effective_category(tpl) if tpl else steps.EXPECTED_CATEGORY[step]
        eligible: list[tuple[dict, int | None]] = []
        for p in points:
            ok, why, cid = point_eligible(cur, hotel.slug, p, channel=ch, category=category, step=step, cfg=cfg, at=at)
            if ok:
                eligible.append((p, cid))
            elif why and why != "kind":
                reasons.add(why)
        if not eligible:
            continue
        channels[ch] = tpl is not None
        if tpl is not None:
            p, cid = eligible[0]
            return Resolution(Choice(ch, p, tpl, category, cid), None, channels, lang)
    if channels:
        return Resolution(None, "template_missing", channels, lang)
    for r in ("alias_expired", "ota_contact_off"):
        if r in reasons:
            return Resolution(None, r, channels, lang)
    return Resolution(None, "no_eligible_contact", channels, lang)
