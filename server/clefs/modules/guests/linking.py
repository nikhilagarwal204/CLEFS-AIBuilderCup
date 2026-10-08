"""
Stay linking (03 spec 2.7): the hourly `guests.link_stays` job and the
GuestModal capture path.

Two sources feed gst_stays:
- PMS reservations (core/stays, read through stays_port): each is
  snapshotted, its contacts mirrored as points, and linked to a consented
  profile when one of its OWN direct identities (pms_direct or a
  guest_direct point of that reservation) matches. OTA-provided identifiers
  (pms_ota, ota_alias, guest_stay) never link a profile (Canon 19; 04 3.4).
- The legacy guest_rooms roster: the GuestModal phone/email is hashed,
  stored as a guest_direct point with its consents, linked to a roster stay,
  and then SCRUBBED from guest_rooms (only a masked copy stays), so the raw
  value sits in the roster JSON for at most one job interval.

A VIP returning guest arriving today raises `guests.returning_guest_arrival`.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock, privacy, signals
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.guests import contacts, identity, stays_port
from clefs.modules.guests.errors import ConsentNotAllowed

log = logging.getLogger("clefs.guests.linking")

MAX_PER_RUN = 2000  # reservations per hotel per run; the next run continues

# ─── PMS snapshots ───

_SNAPSHOT_SQL = """
INSERT INTO gst_stays (hotel_slug, reservation_id, origin, room, check_in, check_out, source, status, segment, booked_at,
                       checked_in_at, checked_out_at, language, stay_chain_id, booking_ref, rooms_count, promo_code, utm,
                       created_at, updated_at)
VALUES (%(h)s, %(id)s, 'pms', %(room)s, %(ci)s, %(co)s, %(src)s, %(status)s, %(segment)s, %(booked)s, %(cin)s, %(cout)s,
        %(lang)s, %(chain)s, %(ref)s, %(rooms)s, %(promo)s, %(utm)s, %(now)s, %(now)s)
ON CONFLICT (hotel_slug, reservation_id) WHERE reservation_id IS NOT NULL DO UPDATE SET
    room = COALESCE(EXCLUDED.room, gst_stays.room), check_in = EXCLUDED.check_in, check_out = EXCLUDED.check_out,
    source = EXCLUDED.source, status = EXCLUDED.status, segment = EXCLUDED.segment, booked_at = EXCLUDED.booked_at,
    checked_in_at = EXCLUDED.checked_in_at, checked_out_at = EXCLUDED.checked_out_at, language = EXCLUDED.language,
    stay_chain_id = EXCLUDED.stay_chain_id, booking_ref = EXCLUDED.booking_ref, rooms_count = EXCLUDED.rooms_count,
    promo_code = EXCLUDED.promo_code, utm = EXCLUDED.utm, updated_at = EXCLUDED.updated_at
RETURNING *
"""


def snapshot_reservation(cur: Any, hotel_slug: str, reservation_id: int) -> dict | None:
    """Refresh the gst_stays row of one core reservation (None if unknown)."""
    r = stays_port.backend().reservation(cur, hotel_slug, reservation_id)
    if r is None:
        return None
    cur.execute(
        _SNAPSHOT_SQL,
        {
            "h": hotel_slug, "id": reservation_id, "room": r.get("room"), "ci": stays_port.as_date(r.get("check_in")),
            "co": stays_port.as_date(r.get("check_out")), "src": r.get("channel_code"), "status": r.get("status"),
            "segment": r.get("segment"), "booked": stays_port.as_datetime(r.get("booked_at")),
            "cin": stays_port.as_datetime(r.get("checked_in_at")), "cout": stays_port.as_datetime(r.get("checked_out_at")),
            "lang": r.get("guest_language"), "chain": r.get("stay_chain_id"), "ref": r.get("booking_ref"),
            "rooms": r.get("rooms_count"), "promo": r.get("promo_code"), "utm": Json(r.get("utm") or {}),
            "now": clock.now(),
        },
    )
    return dict(cur.fetchone())


def _own_identities(cur: Any, hotel_slug: str, reservation_id: int, mirrored: list[dict]) -> list[bytes]:
    """Identities that may link this stay to a profile: pms_direct contacts
    and the guest's own guest_direct points on this reservation."""
    out = [bytes(c["value_hmac"]) for c in mirrored if c.get("provenance") == "pms_direct"]
    cur.execute(
        """
        SELECT value_hmac FROM gst_contact_points
        WHERE hotel_slug = %s AND reservation_id = %s AND provenance = 'guest_direct' AND status <> 'purged'
          AND value_hmac IS NOT NULL
        """,
        (hotel_slug, reservation_id),
    )
    out += [bytes(r["value_hmac"]) for r in cur.fetchall()]
    return list(dict.fromkeys(out))


def link_reservation(cur: Any, hotel_slug: str, reservation_id: int) -> dict | None:
    """Snapshot, mirror contacts and link one reservation. Returns the stay."""
    stay = snapshot_reservation(cur, hotel_slug, reservation_id)
    if stay is None:
        return None
    mirrored = contacts.mirror_reservation_contacts(cur, hotel_slug, reservation_id)
    if stay.get("guest_profile_id") is None:
        prof = identity.find_profile(cur, hotel_slug, _own_identities(cur, hotel_slug, reservation_id, mirrored))
        if prof is not None and any(identity.profile_allowed(cur, hotel_slug, h) for h in identity.identities_of(prof)):
            identity.link_stay(cur, hotel_slug, stay["id"], prof["id"])
            stay["guest_profile_id"] = prof["id"]
    else:
        identity.refresh_stats(cur, stay["guest_profile_id"])
    cur.execute("UPDATE gst_stays SET link_checked_at = %s WHERE id = %s", (clock.now(), stay["id"]))
    return stay


def link_pms_stays(cur: Any, hotel_slug: str, *, today: date) -> int:
    ids = stays_port.backend().reservations_since(cur, hotel_slug, check_out_from=today - timedelta(days=1))
    n = 0
    for rid in ids[:MAX_PER_RUN]:
        stay = link_reservation(cur, hotel_slug, rid)
        if stay is not None:
            n += 1
    return n


# ─── Roster (GuestModal) capture ───

ROSTER_RAW_KEYS = ("phone", "email")


def roster_stay(cur: Any, hotel_slug: str, room: str, *, check_in: date, check_out: date | None,
                identities: list[bytes], language: str | None = None) -> dict:
    """The roster stay of `room` starting `check_in`, created or refreshed."""
    cur.execute(
        """
        INSERT INTO gst_stays (hotel_slug, origin, room, check_in, check_out, source, language, contact_hmacs,
                               created_at, updated_at)
        VALUES (%s, 'roster', %s, %s, %s, 'ROSTER', %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, room, check_in) WHERE origin = 'roster' DO UPDATE SET
            check_out = COALESCE(EXCLUDED.check_out, gst_stays.check_out),
            language = COALESCE(EXCLUDED.language, gst_stays.language),
            contact_hmacs = ARRAY(SELECT DISTINCT unnest(gst_stays.contact_hmacs || EXCLUDED.contact_hmacs)),
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        (hotel_slug, room, check_in, check_out, language, [bytes(h) for h in identities], clock.now(), clock.now()),
    )
    return dict(cur.fetchone())


def capture_room_identity(
    cur: Any,
    hotel_slug: str,
    room: str,
    *,
    phone: str | None = None,
    email: str | None = None,
    consents: dict[str, bool],
    consent_language: str = "en",
    first_name: str | None = None,
    preferences: dict | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    actor: Any = None,
) -> dict:
    """The GuestModal path: hash the guest's own phone/email, record the
    consents (current wording in `consent_language`), store guest_direct
    points, link the roster stay and (with profile_memory) the profile.
    Raises ConsentNotAllowed without transfer_abroad, before hashing."""
    consents = {k: bool(v) for k, v in (consents or {}).items() if k in contacts.OWN_PURPOSES}
    if not consents.get("transfer_abroad"):
        raise ConsentNotAllowed(
            "Storing a contact needs the guest's transfer-abroad consent", reason="transfer_abroad_required"
        )
    values = [(k, v.strip()) for k, v in (("phone", phone), ("email", email)) if isinstance(v, str) and v.strip()]
    if not values:
        raise ClefsError(400, "Give a phone number or an email address", "contact_required")
    lang = consent_language if consent_language in ("en", "ar") else "en"
    versions = {p: identity.text_version(cur, hotel_slug, p, lang) for p, g in consents.items() if g}
    hotel = get_hotel(cur, hotel_slug)
    ci = check_in or local_date(hotel)
    ids = {k: identity.hmac_for(cur, hotel_slug, k, v) for k, v in values}  # 400 on junk, before anything is written
    hs = list(ids.values())
    stay = roster_stay(cur, hotel_slug, room, check_in=ci, check_out=check_out, identities=hs, language=lang)
    point_ids = []
    for k, v in values:
        point_ids.append(
            contacts.record_own_contact(
                cur, hotel_slug, reservation_id=None, room=room, kind=k, value=v, capture_source="console",
                consents=consents, consent_text_versions={p: ver for p, ver in versions.items() if ver},
                evidence={"kind": "staff_entry", "room": room, "language": lang, "first_name": first_name,
                          "preferences": preferences},
                actor=actor, profile_identities=ids,
            )
        )
    # record_own_contact links the stay covering today; a roster stay for a
    # later date is linked here once a profile exists.
    prof = identity.find_profile(cur, hotel_slug, hs) if consents.get("profile_memory") else None
    if prof is not None:
        identity.link_stay(cur, hotel_slug, stay["id"], prof["id"])
    return {
        "stay_id": stay["id"],
        "point_ids": point_ids,
        "profile_id": prof["id"] if prof else None,
        "contact": {
            "phone": privacy.mask_phone(dict(values).get("phone")),
            "email": privacy.mask_email(dict(values).get("email")),
        },
    }


def _as_date(v: Any) -> date | None:
    d = stays_port.as_date(v) if v else None
    return d if isinstance(d, date) else None


def scrub_roster(cur: Any, hotel_slug: str, *, actor: Any = None) -> int:
    """Hash and remove raw GuestModal contacts from guest_rooms. Whatever
    happens (stored, refused for lack of consent, unparseable), the raw
    value leaves the roster JSON; only a masked copy and a status remain."""
    cur.execute(
        "SELECT room_number, data FROM guest_rooms WHERE hotel_slug = %s AND (data ? 'phone' OR data ? 'email') FOR UPDATE",
        (hotel_slug,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        data = dict(r["data"] or {})
        phone, email = data.pop("phone", None), data.pop("email", None)
        status = "linked"
        try:
            cur.execute("SAVEPOINT gst_roster_capture")
            out = capture_room_identity(
                cur, hotel_slug, r["room_number"], phone=phone if isinstance(phone, str) else None,
                email=email if isinstance(email, str) else None,
                consents=data.get("consents") if isinstance(data.get("consents"), dict) else {},
                consent_language=data.get("consentLanguage") or "en",
                first_name=None, preferences=None,
                check_in=_as_date(data.get("checkIn")), check_out=_as_date(data.get("checkOut")), actor=actor,
            )
            cur.execute("RELEASE SAVEPOINT gst_roster_capture")
            if out["profile_id"] is None:
                status = "stored"
        except ClefsError as exc:
            cur.execute("ROLLBACK TO SAVEPOINT gst_roster_capture")
            cur.execute("RELEASE SAVEPOINT gst_roster_capture")
            status = f"not_stored:{getattr(exc, 'reason', None) or exc.code}"
        data["contact"] = {
            "phone": privacy.mask_phone(phone) if isinstance(phone, str) and phone.strip() else None,
            "email": privacy.mask_email(email) if isinstance(email, str) and email.strip() else None,
            "status": status,
        }
        cur.execute(
            "UPDATE guest_rooms SET data = %s, updated_at = NOW() WHERE hotel_slug = %s AND room_number = %s",
            (Json(data), hotel_slug, r["room_number"]),
        )
    return len(rows)


# ─── Returning arrivals ───

signals.register_signal_kind(
    "guests.returning_guest_arrival",
    label="VIP returning guest arriving",
    default_owner_role="front_desk",
    section="service_risk",
    cadence="daily",
    icon="user",
)


def returning_stays_on(cur: Any, hotel_slug: str, day: date) -> list[dict]:
    """Stays arriving on `day` (or in house then) linked to a profile."""
    cur.execute(
        """
        SELECT * FROM gst_stays
        WHERE hotel_slug = %s AND guest_profile_id IS NOT NULL AND check_in = %s
          AND (status IS NULL OR status IN ('CONFIRMED', 'IN_HOUSE'))
        ORDER BY room NULLS LAST, id
        """,
        (hotel_slug, day),
    )
    return [dict(r) for r in cur.fetchall()]


def raise_vip_arrivals(cur: Any, hotel_slug: str, day: date) -> int:
    """One signal per VIP returning stay. No name or health data in it:
    signals are read by roles (revenue, finance) that may not see either."""
    n = 0
    for stay in returning_stays_on(cur, hotel_slug, day):
        b = identity.badge_for_stay(cur, hotel_slug, stay)
        if b is None or not b["vip"]:
            continue
        room = stay.get("room") or "unassigned room"
        signals.upsert_signal(
            cur, hotel_slug,
            signals.SignalIn(
                module="guests", kind="guests.returning_guest_arrival",
                dedupe_key=f"guests.returning_guest_arrival:{stay['id']}",
                title=f"VIP returning guest arriving today ({room}, {identity.ordinal(b['stay_number'])} stay)",
                summary="Check the guest's saved preferences on the Guests page before arrival.",
                severity=2, confidence=1.0, owner_role="front_desk",
                subject_type="gst_stay", subject_id=str(stay["id"]),
            ),
        )
        n += 1
    return n


# ─── Job ───


@jobs.scheduled("guests.link_stays", jobs.every(hours=1))
def link_stays_job(ctx: jobs.JobCtx) -> dict:
    cur, slug = ctx.cur, ctx.hotel_slug
    today = local_date(ctx.hotel)
    roster = scrub_roster(cur, slug)
    linked = link_pms_stays(cur, slug, today=today)
    vip = raise_vip_arrivals(cur, slug, today)
    return {"roster_scrubbed": roster, "pms_linked": linked, "vip_signals": vip}
