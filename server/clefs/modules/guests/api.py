"""
Guests public API (frozen signatures; other modules import only this file).

Canon 10: guest identity (hashed phone/email, consent-gated profiles, stay
links, direct codes) belongs to this module. Canon 19: OTA alias addresses and
OTA-sourced contacts never enter a marketing audience.

04 spec 7.2 functions are built (identity part of lane F2): the bodies live
in identity.py, contacts.py, marketing.py and linking.py; the signatures
below are frozen (FROZEN_API) and never change. Functions added after the
freeze (returning_badge, returning_badges, returning_arrivals, forget_contact,
unsubscribe_path, room_for_sender) are additive.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Iterable, Literal

from clefs.core import clock
from clefs.core.hotels import get_hotel
from clefs.core.time import local_date
from clefs.modules.guests import contacts, identity, marketing, sender_room, stays_port
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.errors import ConsentNotAllowed

log = logging.getLogger("clefs.guests.api")

Phase = Literal["pre_stay", "in_stay", "post_stay"]

__all__ = [
    "ConsentNotAllowed", "GUEST_FIELDS", "Phase", "arrival_prefs", "can_send_marketing", "forget_contact",
    "guest_context_for_room", "is_known_guest_sender", "journey_timeline", "marketing_audience", "opt_out",
    "record_external_send", "record_own_contact", "returning_arrivals", "returning_badge", "returning_badges",
    "room_for_sender",
    "unsubscribe_path",
]

# Fields of a legacy guest_rooms record the guest page may see for its own
# room. Ages, internal notes, contacts and consents stay server-side.
GUEST_FIELDS = ("roomNumber", "stayType", "checkIn", "checkOut", "loyaltyTier", "occasions")
GUEST_PERSON_FIELDS = ("name", "type")
PREFERENCE_FIELDS = ("tags", "language")


def guest_context_for_room(cur: Any, hotel_slug: str, room: str) -> dict | None:
    """Minimal, allowlisted context for the guest in `room`, or None.

    Consent-aware: it never widens beyond this allowlist, and the one
    profile-derived key (`returningGuest: {stayNumber}`, for a "welcome
    back") appears only when the current stay is linked to a profile, which
    exists only under profile_memory consent. Preferences and health data
    from the profile never reach the guest page: whoever holds the room QR
    is not necessarily the guest."""
    cur.execute(
        "SELECT data FROM guest_rooms WHERE hotel_slug = %s AND room_number = %s",
        (hotel_slug, room),
    )
    row = cur.fetchone()
    if row is None or not isinstance(row["data"], dict):
        return None
    data = row["data"]
    out: dict[str, Any] = {k: data.get(k) for k in GUEST_FIELDS if k in data}
    out["roomNumber"] = room
    guests = data.get("guests")
    if isinstance(guests, list):
        out["guests"] = [
            {k: g.get(k) for k in GUEST_PERSON_FIELDS if k in g} for g in guests if isinstance(g, dict)
        ]
    prefs = data.get("preferences")
    if isinstance(prefs, dict):
        out["preferences"] = {k: prefs.get(k) for k in PREFERENCE_FIELDS if k in prefs}
    stay = contacts.current_stay_in_room(cur, hotel_slug, room, on=local_date(get_hotel(cur, hotel_slug)))
    if stay is not None and stay.get("guest_profile_id"):
        n = identity.stay_number(cur, stay)
        if n >= 2:
            out["returningGuest"] = {"stayNumber": n}
    return out


def marketing_audience(
    cur: Any,
    hotel_slug: str,
    *,
    purpose: str = "marketing",
    channel: str = "whatsapp",
    segment: dict | None = None,
) -> list[dict]:
    """Consented direct contacts for a campaign: [{contact_ref, channel, locale}].

    `purpose='marketing'` maps to the consent purpose `marketing_<channel>`.
    `contact_ref` is the hex contact_hmac; Demand passes it back (as bytes)
    in `identity_hmacs`. guest_stay, pms_ota and ota_alias points, suppressed
    identities and +1 WhatsApp numbers are never returned (04 spec 3.4)."""
    return marketing.marketing_audience(cur, hotel_slug, purpose=purpose, channel=channel, segment=segment)


# ─── Contacts and consent (04 spec 3.4) ───


def record_own_contact(
    cur: Any,
    hotel_slug: str,
    *,
    reservation_id: int | None = None,
    room: str | None = None,
    kind: str,
    value: str,
    capture_source: str,
    consents: dict[str, bool],
    consent_text_versions: dict[str, str],
    evidence: dict,
    actor: Any,
) -> int:
    """Store a contact the guest gave the hotel themselves (registration
    card, concierge, a J1 form, a J5 reply, a converted inquiry, the
    console) with its consents; returns the contact point id.

    Raises ConsentNotAllowed (422 consent_not_allowed) unless
    consents["transfer_abroad"] is true with a text version (every
    capture_source), when a granted consent has no text version, and for
    marketing or profile consents from j1_form on an OTA reservation (that
    contact becomes a guest_stay point for this stay's messages only).
    Registration-card evidence must be a card crop (<= 300 KB, no MRZ) or a
    staff attestation with the guest's signature (422 evidence_*).
    `evidence` may carry `language` (en|ar, the wording shown) and, for the
    profile only, `first_name` and `preferences` (never stored as evidence)."""
    return contacts.record_own_contact(
        cur, hotel_slug, reservation_id=reservation_id, room=room, kind=kind, value=value,
        capture_source=capture_source, consents=consents, consent_text_versions=consent_text_versions,
        evidence=evidence, actor=actor,
    )


def is_known_guest_sender(cur: Any, hotel_slug: str, *, phone_hmac: bytes | None = None, email_hmac: bytes | None = None) -> bool:
    """True when the hmac is on a reservation that is IN_HOUSE, or CONFIRMED
    with check-in within 1 day (04 spec F4: WhatsApp routing between Inquiry
    Guard and the concierge). Looks at core reservation contacts, at the
    guest's own points on a stay, and at roster (GuestModal) stays."""
    hs = [bytes(h) for h in (phone_hmac, email_hmac) if h]
    if not hs:
        return False
    today = local_date(get_hotel(cur, hotel_slug))
    if stays_port.backend().known_sender(cur, hotel_slug, hs, today=today):
        return True
    tomorrow = date.fromordinal(today.toordinal() + 1)
    cur.execute(
        """
        SELECT 1 FROM gst_stays s
        WHERE s.hotel_slug = %s
          AND (s.contact_hmacs && %s::bytea[]
               OR s.reservation_id IN (SELECT p.reservation_id FROM gst_contact_points p
                                       WHERE p.hotel_slug = %s AND p.value_hmac = ANY(%s) AND p.status <> 'purged'
                                         AND p.reservation_id IS NOT NULL))
          AND (s.status = 'IN_HOUSE'
               OR ((s.status IS NULL OR s.status = 'CONFIRMED') AND s.check_in <= %s AND (s.check_out IS NULL OR s.check_out >= %s)))
        LIMIT 1
        """,
        (hotel_slug, hs, hotel_slug, hs, tomorrow, today),
    )
    return cur.fetchone() is not None


def room_for_sender(
    cur: Any, hotel_slug: str, *, sender_hmac: bytes | None = None, phone_hmac: bytes | None = None
) -> str | None:
    """The room a WhatsApp sender is staying in right now, or None, so a
    concierge chat can be treated as coming from that room (verified).

    `sender_hmac` is the per-hotel contact HMAC of the sender's number
    (notify InboundCtx.sender_hmac); `phone_hmac` is an alias for callers
    written against is_known_guest_sender's name (the concierge). Matches
    core reservation contacts, the guest's own points and roster stays.
    Only an IN_HOUSE stay (or a roster stay covering today) counts, never a
    CONFIRMED arrival; a stay overtaken by a later in-house stay in the same
    room is ignored; two or more rooms for one number return None. Never
    raises for an unknown hotel's empty data; reads only HMACs."""
    return sender_room.room_for_sender(cur, hotel_slug, sender_hmac or phone_hmac)


def can_send_marketing(
    cur: Any, hotel_slug: str, *, identity_hmacs: list[bytes], channel: str, at: datetime, phase: Phase
) -> tuple[bool, str]:
    """(allowed, reason) for one marketing message to the person behind
    `identity_hmacs`: consent, suppressions, Canon 19 provenance, the +1
    block, the 09:00-18:00 hotel-time window (narrowed by settings) and the
    caps (1/day, 1 pre-stay and 1 in-stay per reservation, 2 post-stay per
    30 days). Reasons are listed in marketing.py."""
    return marketing.can_send_marketing(cur, hotel_slug, identity_hmacs=identity_hmacs, channel=channel, at=at, phase=phase)


def record_external_send(
    cur: Any, hotel_slug: str, *, identity_hmacs: list[bytes], channel: str, source: str, sent_at: datetime, ref: str
) -> None:
    """Count a marketing send made outside the journeys (a Demand campaign
    export, counted at export time; an upsell offer) toward the guest's
    caps. `source` "demand*" counts as demand_export (post-stay), "upsell*"
    as upsell (in-stay). The same (source, ref) is counted once."""
    marketing.record_send(cur, hotel_slug, identity_hmacs=identity_hmacs, channel=channel, source=source,
                          sent_at=sent_at, ref=ref)
    return None


def opt_out(cur: Any, hotel_slug: str, *, kind: str, value: str, source: str, scope: str = "all") -> None:
    """A guest said STOP (kind phone|email, the raw value): suppress the
    identity (scope all|marketing) and withdraw its messaging consents.
    Every future point hashing the same is blocked too. Idempotent."""
    contacts.opt_out(cur, hotel_slug, kind=kind, value=value, source=source, scope=scope)
    return None


# ─── Journeys (04 spec 3.10) ───


def journey_timeline(cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]:
    """[{step, seq, status, channel, at, action_id, skip_reason}] for one
    stay, in due order. [] until the journeys planner has planned it."""
    cur.execute(
        """
        SELECT step, seq, status, channel, due_at, last_attempt_at, action_id, skip_reason FROM gst_journey_plans
        WHERE hotel_slug = %s AND reservation_id = %s AND status <> 'cancelled'
        ORDER BY due_at, step, seq
        """,
        (hotel_slug, reservation_id),
    )
    return [
        {
            "step": r["step"], "seq": r["seq"], "status": r["status"], "channel": r["channel"],
            "at": r["last_attempt_at"] or r["due_at"], "action_id": r["action_id"], "skip_reason": r["skip_reason"],
        }
        for r in cur.fetchall()
    ]


def arrival_prefs(cur: Any, hotel_slug: str, *, day: date) -> list[dict]:
    """Preferences guests arriving on `day` gave through J2 / the /g page
    (arrival time, requests, occasion, language) for the handover sheet."""
    cur.execute(
        """
        SELECT p.reservation_id, s.room, p.arrival_time, p.requests, p.occasion, p.language, p.submitted_at
        FROM gst_stay_prefs p
        JOIN gst_stays s ON s.hotel_slug = p.hotel_slug AND s.reservation_id = p.reservation_id
        WHERE p.hotel_slug = %s AND s.check_in = %s AND p.purge_after > %s
          AND (s.status IS NULL OR s.status IN ('CONFIRMED', 'IN_HOUSE'))
        ORDER BY s.room NULLS LAST, p.reservation_id
        """,
        (hotel_slug, day, clock.now()),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── Returning guests (03 spec 2.7; additive, for service and devices) ───


def returning_badge(
    cur: Any, hotel_slug: str, *, room: str, role: str | None, departments: Iterable[str] = (), at: datetime | None = None
) -> dict | None:
    """The returning-guest badge of the stay in `room` now, masked for the
    viewer: {stay_id, reservation_id, room, check_in, check_out, profile_id,
    stay_number, vip, first_name, tags, health: {allergies, notes} | None,
    label: "3rd stay · extra pillows · no nuts"}. None when the room's guest
    is not a consented returning guest, or the hotel turned badges off.
    `role` is the viewer's matrix role (core.pii.role_for_ctx; "device" for
    a paired device) and `departments` theirs: allergies show only for roles
    the PII matrix allows (F&B departments for heads and devices)."""
    if not gsettings.load(cur, hotel_slug).returning_badge:
        return None
    hotel = get_hotel(cur, hotel_slug)
    stay = contacts.current_stay_in_room(cur, hotel_slug, room, on=local_date(hotel, at))
    b = identity.badge_for_stay(cur, hotel_slug, stay) if stay else None
    return identity.mask_badge(b, role, departments) if b else None


def returning_badges(
    cur: Any, hotel_slug: str, *, rooms: Iterable[str], role: str | None, departments: Iterable[str] = (),
    at: datetime | None = None,
) -> dict[str, dict]:
    """returning_badge for a batch of rooms: {room: badge} (masked the same
    way), holding only the rooms whose guest is a consented returning guest.
    For list serializers (the service queue, the device feed): one settings
    read and one stay query for the whole page instead of N. {} when the
    hotel turned badges off or no room qualifies."""
    if not gsettings.load(cur, hotel_slug).returning_badge:
        return {}
    hotel = get_hotel(cur, hotel_slug)
    stays = contacts.current_stays_in_rooms(cur, hotel_slug, rooms, on=local_date(hotel, at))
    depts = tuple(departments)
    out: dict[str, dict] = {}
    for room, stay in stays.items():
        # Only a stay linked to a profile (profile_memory consent) can badge.
        b = identity.badge_for_stay(cur, hotel_slug, stay) if stay.get("guest_profile_id") else None
        if b is not None:
            out[room] = identity.mask_badge(b, role, depts)
    return out


def returning_arrivals(cur: Any, hotel_slug: str, *, day: date, role: str | None, departments: Iterable[str] = ()) -> list[dict]:
    """Badges (masked as in returning_badge) of returning guests arriving on
    hotel-local `day`, for the queue, the device and the handover sheet."""
    if not gsettings.load(cur, hotel_slug).returning_badge:
        return []
    from clefs.modules.guests import linking

    out = []
    for stay in linking.returning_stays_on(cur, hotel_slug, day):
        b = identity.badge_for_stay(cur, hotel_slug, stay)
        if b is not None:
            out.append(identity.mask_badge(b, role, departments))
    return out


def forget_contact(cur: Any, hotel_slug: str, *, kind: str, value: str, source: str, actor: Any = None) -> dict:
    """A DSR erase or "forget me" for one phone/email: every consent this
    module records is withdrawn, the profile deleted, own-capture points
    purged and their evidence files deleted. The STOP suppression stays."""
    h = identity.hmac_for(cur, hotel_slug, kind, value)
    return contacts.forget_contact(cur, hotel_slug, h, source=source, actor=actor)


def unsubscribe_path(hotel_slug: str, *, identity_hmac: bytes, channel: str) -> str:
    """"/public/guests/unsubscribe/<signed token>": the one-click unsubscribe
    link every marketing email (J8, Demand) must carry. The token holds the
    hmac, never the address."""
    from clefs.modules.guests import optout

    return optout.unsubscribe_path(hotel_slug, identity_hmac=identity_hmac, channel=channel)


# ─── Demo seed door ───
# WHY: the UAE demo seed (clefs.seed.demo_uae) is foundation code, which may
# never import a module's internals (tests/foundation/test_module_contracts).
# It loads this api by name to save review links through the settings model
# (so the URL validators run) and to mint the /g and /g/f tokens the journey
# executor would have minted on send (the seed writes sent plans directly).
# Nothing else calls this.


def demo_seed_door() -> Any:
    """load_settings(cur, hotel_slug), ReviewLinksIn (the settings model's
    review links), and mint_journey_token(cur, hotel_slug, reservation_id,
    purpose, *, expires_at, ...) from journeys/tokens.py."""
    from types import SimpleNamespace

    from clefs.modules.guests.journeys import tokens

    return SimpleNamespace(load_settings=gsettings.load, ReviewLinksIn=gsettings.ReviewLinksIn,
                           mint_journey_token=tokens.mint)
