"""
Who may receive marketing, when, and how often (03 spec 2.7 rebooking offer
rules, 04 spec 3.4 eligibility and 3.5 guards). Journeys J3/J8, Demand
campaign exports and upsell all ask can_send_marketing first and count the
send with record_external_send (or the journey dispatcher's own row), so the
caps hold across every sender.

The rules, in the order they are checked (the first failure is the reason):
  unknown_channel, no_identity  bad input
  suppressed                    a STOP / unsubscribe / manual suppression
  no_consent                    no granted marketing_<channel> consent
  no_eligible_contact           only OTA-provided or guest_stay points (Canon 19)
  nanp_blocked                  WhatsApp marketing to +1 (or an unknown prefix)
  outside_window                outside [09:00, 18:00) hotel time, or the
                                narrower window set in guests settings
  cap_daily                     already one marketing message today (hotel day)
  cap_pre_stay / cap_in_stay    one per reservation in that phase
  cap_post_stay                 two per identity per rolling 30 days

The 03 spec's "one rebooking offer per 30 days" is kept by J8's own spacing
(check-out + 30 d and + 60 d); the shared post-stay cap is 04's two.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from clefs.core import consent
from clefs.core.hotels import get_hotel
from clefs.modules.guests import contacts, identity, timeutil
from clefs.modules.guests import settings as gsettings

CHANNEL_KIND = {"whatsapp": "phone", "email": "email"}
MARKETING_PROVENANCES = ("guest_direct", "pms_direct")
POST_STAY_CAP = 2
POST_STAY_WINDOW = timedelta(days=30)
SEND_SOURCES = ("journey_j3", "journey_j8", "demand_export", "upsell", "other")


def _source(source: str) -> str:
    s = (source or "").strip().lower()
    if s in SEND_SOURCES:
        return s
    if s.startswith("demand"):
        return "demand_export"
    if s.startswith("upsell"):
        return "upsell"
    return "other"


def _eligible_points(cur: Any, hotel_slug: str, hs: list[bytes], channel: str, at: datetime) -> list[dict]:
    kind = CHANNEL_KIND[channel]
    return [
        p for p in contacts.points_for(cur, hotel_slug, hs)
        if p["kind"] == kind and p["provenance"] in MARKETING_PROVENANCES
        and (p["usable_until"] is None or p["usable_until"] > at)
    ]


def _marketing_consent(cur: Any, hotel_slug: str, hs: list[bytes], channel: str, at: datetime) -> list[bytes]:
    purpose = identity.MARKETING_PURPOSE[channel]
    return [h for h in hs if consent.has_consent(cur, hotel_slug, h.hex(), purpose, at=at)]


def _count_sends(cur: Any, hotel_slug: str, hs: list[bytes], *, since: datetime, until: datetime,
                 phase: str | None = None, reservation_ids: list[int] | None = None) -> int:
    sql = "SELECT COUNT(*) AS n FROM gst_marketing_sends WHERE hotel_slug = %s AND identity_hmac = ANY(%s) AND sent_at >= %s AND sent_at < %s"
    params: list[Any] = [hotel_slug, hs, since, until]
    if phase:
        sql += " AND phase = %s"
        params.append(phase)
    if reservation_ids is not None:
        sql += " AND reservation_id = ANY(%s)"
        params.append(reservation_ids)
    cur.execute(sql, params)
    return int(cur.fetchone()["n"])


def _stay_reservations(cur: Any, hotel_slug: str, hs: list[bytes], day: Any, phase: str) -> list[int]:
    """Reservations of this identity that are upcoming (pre_stay) or in
    house (in_stay) on hotel-local `day`."""
    cond = "s.check_in > %s" if phase == "pre_stay" else "s.check_in <= %s AND (s.check_out IS NULL OR s.check_out >= %s)"
    params: list[Any] = [hotel_slug, hs, day] + ([day] if phase == "in_stay" else [])
    cur.execute(
        f"""
        SELECT DISTINCT s.reservation_id FROM gst_contact_points p
        JOIN gst_stays s ON s.hotel_slug = p.hotel_slug AND s.reservation_id = p.reservation_id
        WHERE p.hotel_slug = %s AND p.value_hmac = ANY(%s) AND s.reservation_id IS NOT NULL
          AND (s.status IS NULL OR s.status IN ('CONFIRMED', 'IN_HOUSE')) AND {cond}
        """,
        params,
    )
    return [int(r["reservation_id"]) for r in cur.fetchall()]


# ─── can_send_marketing ───


def can_send_marketing(
    cur: Any, hotel_slug: str, *, identity_hmacs: list[bytes], channel: str, at: datetime, phase: str
) -> tuple[bool, str]:
    if channel not in CHANNEL_KIND:
        return False, "unknown_channel"
    hs = list(dict.fromkeys(bytes(h) for h in (identity_hmacs or []) if h))
    if not hs:
        return False, "no_identity"
    if phase not in ("pre_stay", "in_stay", "post_stay"):
        return False, "unknown_phase"
    if contacts.is_suppressed(cur, hotel_slug, hs, marketing=True):
        return False, "suppressed"
    consented = _marketing_consent(cur, hotel_slug, hs, channel, at)
    if not consented:
        return False, "no_consent"
    # Consent and an eligible point must belong to the same identity: a
    # marketing tick on a Booking.com phone (pms_ota) is not a route to a
    # direct contact, whatever the consent table says (Canon 19).
    points = _eligible_points(cur, hotel_slug, consented, channel, at)
    if not points:
        return False, "no_eligible_contact"
    if channel == "whatsapp" and all(p.get("country_prefix") in (None, "+1") for p in points):
        # NANP is a conservative superset of Meta's US marketing ban; an
        # unknown prefix is treated the same way (fail closed).
        return False, "nanp_blocked"
    hotel = get_hotel(cur, hotel_slug)
    cfg = gsettings.load(cur, hotel_slug)
    t0, t1 = cfg.marketing_window.t0, cfg.marketing_window.t1
    if not timeutil.in_window(hotel, at, max(t0, gsettings.MARKETING_EARLIEST), min(t1, gsettings.MARKETING_LATEST)):
        return False, "outside_window"
    d0, d1 = timeutil.day_bounds(hotel, at)
    if _count_sends(cur, hotel_slug, hs, since=d0, until=d1) >= 1:
        return False, "cap_daily"
    day = timeutil.local(hotel, at).date()
    if phase in ("pre_stay", "in_stay"):
        res_ids = _stay_reservations(cur, hotel_slug, hs, day, phase)
        if res_ids:
            n = _count_sends(cur, hotel_slug, hs, since=at - timedelta(days=400), until=at + timedelta(seconds=1),
                             phase=phase, reservation_ids=res_ids)
        else:
            # No reservation on file for this identity: fall back to one per
            # rolling 30 days in that phase, never "unlimited".
            n = _count_sends(cur, hotel_slug, hs, since=at - POST_STAY_WINDOW, until=at + timedelta(seconds=1), phase=phase)
        if n >= 1:
            return False, f"cap_{phase}"
    else:
        n = _count_sends(cur, hotel_slug, hs, since=at - POST_STAY_WINDOW, until=at + timedelta(seconds=1), phase="post_stay")
        if n >= POST_STAY_CAP:
            return False, "cap_post_stay"
    return True, "ok"


# ─── record_external_send ───


def record_send(
    cur: Any, hotel_slug: str, *, identity_hmacs: list[bytes], channel: str, source: str, sent_at: datetime,
    ref: str | None, phase: str | None = None, reservation_id: int | None = None,
) -> int:
    """Count one marketing message per identity toward the caps. A retry with
    the same (source, ref) is not counted twice."""
    src = _source(source)
    ph = phase or ("in_stay" if src == "upsell" else "post_stay")
    n = 0
    for h in dict.fromkeys(bytes(x) for x in (identity_hmacs or []) if x):
        if ref:
            cur.execute(
                "SELECT 1 FROM gst_marketing_sends WHERE hotel_slug = %s AND identity_hmac = %s AND source = %s AND ref = %s",
                (hotel_slug, h, src, ref),
            )
            if cur.fetchone():
                continue
        cur.execute(
            """
            INSERT INTO gst_marketing_sends (hotel_slug, identity_hmac, channel, source, phase, reservation_id, ref, sent_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (hotel_slug, h, channel, src, ph, reservation_id, ref, sent_at),
        )
        n += 1
    return n


# ─── marketing_audience ───

AUDIENCE_LIMIT = 5000


def _countries_of(row: dict) -> set[str]:
    """ISO2 countries a contact may belong to: the reservation's guest_country
    when it is an ISO2 code, else every country sharing the phone's calling
    code (+1 is US and Canada; the prefix alone cannot tell them apart)."""
    gc = (row.get("guest_country") or "").strip().upper()
    if len(gc) == 2 and gc.isalpha():
        return {gc}
    prefix = (row.get("country_prefix") or "").lstrip("+")
    if not prefix:
        return set()
    from clefs.core import privacy

    return {iso for iso, code in privacy.CALLING_CODES.items() if str(code).lstrip("+") == prefix}


def marketing_audience(
    cur: Any, hotel_slug: str, *, purpose: str = "marketing", channel: str = "whatsapp", segment: dict | None = None
) -> list[dict]:
    """Consented direct contacts: [{contact_ref, channel, locale}].

    Only guest_direct points, and pms_direct points whose identity recorded
    the channel's marketing consent separately. guest_stay, pms_ota and
    ota_alias points are never returned (Canon 19), nor suppressed ones, nor
    +1 numbers for WhatsApp. `segment` narrows: {"language": "ar",
    "returning": true, "min_stays": 2, "last_stay_before": "2026-08-01",
    "last_stay_after": "2026-01-01", "countries": ["IN", "GB"]} (profile-based
    keys need a profile). `countries` (ISO2, Demand's event source markets)
    matches the reservation's guest_country, else the phone's calling code;
    a contact whose country is unknown does not match a country segment."""
    if channel not in CHANNEL_KIND:
        return []
    consent_purpose = identity.MARKETING_PURPOSE[channel] if purpose == "marketing" else purpose
    seg = segment or {}
    from clefs.core import clock

    now = clock.now()
    cur.execute(
        """
        SELECT DISTINCT ON (p.value_hmac) p.value_hmac, p.country_prefix, s.language AS stay_language,
               g.language AS profile_language, g.stays_count, g.last_seen, r.guest_country
        FROM gst_contact_points p
        LEFT JOIN gst_stays s ON s.hotel_slug = p.hotel_slug AND s.reservation_id = p.reservation_id
        LEFT JOIN reservations r ON r.hotel_slug = p.hotel_slug AND r.id = p.reservation_id
        LEFT JOIN gst_profiles g ON g.hotel_slug = p.hotel_slug AND (g.phone_hmac = p.value_hmac OR g.email_hmac = p.value_hmac)
        WHERE p.hotel_slug = %s AND p.kind = %s AND p.status = 'active' AND p.value_hmac IS NOT NULL
          AND p.provenance = ANY(%s) AND (p.usable_until IS NULL OR p.usable_until > %s)
          AND NOT EXISTS (SELECT 1 FROM gst_suppressions x WHERE x.hotel_slug = p.hotel_slug
                          AND x.value_hmac = p.value_hmac AND x.scope IN ('all', 'marketing'))
        ORDER BY p.value_hmac, p.last_used_at DESC
        LIMIT %s
        """,
        (hotel_slug, CHANNEL_KIND[channel], list(MARKETING_PROVENANCES), now, AUDIENCE_LIMIT),
    )
    rows = [dict(r) for r in cur.fetchall()]  # before any other query reuses the cursor
    hotel = get_hotel(cur, hotel_slug)
    default_locale = (hotel.locale or "en").split("-")[0]
    out: list[dict] = []
    for r in rows:
        h = bytes(r["value_hmac"])
        if channel == "whatsapp" and r["country_prefix"] in (None, "+1"):
            continue
        locale = r["profile_language"] or r["stay_language"] or default_locale
        if seg.get("language") and locale != seg["language"]:
            continue
        stays = int(r["stays_count"] or 0)
        if seg.get("returning") and stays < 2:
            continue
        if seg.get("min_stays") and stays < int(seg["min_stays"]):
            continue
        last = r["last_seen"]
        if seg.get("last_stay_before") and (last is None or str(last) >= str(seg["last_stay_before"])):
            continue
        if seg.get("last_stay_after") and (last is None or str(last) < str(seg["last_stay_after"])):
            continue
        if seg.get("countries") and not (_countries_of(r) & {str(c).upper() for c in seg["countries"]}):
            continue
        if not consent.has_consent(cur, hotel_slug, h.hex(), consent_purpose, at=now):
            continue
        out.append({"contact_ref": h.hex(), "channel": channel, "locale": locale})
    return out
