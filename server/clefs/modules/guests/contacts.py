"""
Contact points, suppressions and own-contact capture (04 spec 3.4, Canon 19).

A contact point is one phone or email of one stay (or of the guest, for
guest_direct points with no reservation), with its provenance and lifetime.
This file writes three kinds:
- guest_direct / guest_stay: the guest's own contact, captured with consent
  through record_own_contact (sealed value + HMAC);
- pms_direct / pms_ota / ota_alias: mirrors of core reservation_contacts
  rows (HMAC and rules only; the sealed value stays in core).

Canon 19 lines kept here:
- a J1-form contact on an OTA reservation becomes guest_stay (utility for
  that stay, check_out + 30 d), never guest_direct, and it may not carry a
  marketing or profile consent;
- transfer_abroad consent with a text version is required for every capture
  (PDPL Arts. 22-23), before anything else is looked at.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any, Iterable

from clefs.audit import audit
from clefs.core import clock, consent, privacy
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.guests import evidence as evidence_mod
from clefs.modules.guests import identity, stays_port, timeutil
from clefs.modules.guests.errors import ConsentNotAllowed

log = logging.getLogger("clefs.guests.contacts")

OWN_CAPTURE_SOURCES = ("j1_form", "concierge", "registration_card", "j5_reply", "inquiry", "console")
OWN_PURPOSES = ("transfer_abroad", "whatsapp_updates", "marketing_whatsapp", "marketing_email", "profile_memory")
# Purposes that make a message to the point wanted again (a fresh grant with
# proof of control lifts an earlier STOP suppression; a withdrawal is
# recorded as given).
MESSAGING_PURPOSES = ("whatsapp_updates", "marketing_whatsapp", "marketing_email")
MARKETING_PURPOSES = ("marketing_whatsapp", "marketing_email")
# Capture sources that may lift an earlier STOP on their own evidence: a
# registration card (card crop or staff attestation with the guest's
# signature, checked by evidence.check) and a signed-in staff entry (the
# actor is audited). Every other source lifts a STOP only when its evidence
# names an inbound message sent BY the captured value (_proves_control):
# typing a number into the J1 page or the concierge proves nothing about who
# controls it, so a link holder could otherwise undo a third party's STOP.
STOP_LIFT_SOURCES = ("registration_card", "console")
GUEST_STAY_DAYS = 30  # guest_stay lifetime after check-out (GDT 2.2.6 mirror)
OTA_PROVENANCES = ("pms_ota", "ota_alias", "guest_stay")
PMS_PROVENANCES = ("pms_direct", "pms_ota", "ota_alias")


# ─── Suppressions ───


def is_suppressed(cur: Any, hotel_slug: str, identities: list[bytes], *, marketing: bool) -> bool:
    """A STOP blocks every future point that hashes the same. Scope 'all'
    blocks everything; 'marketing' only blocks marketing."""
    hs = [bytes(h) for h in identities if h]
    if not hs:
        return False
    scopes = ["all", "marketing"] if marketing else ["all"]
    cur.execute(
        "SELECT 1 FROM gst_suppressions WHERE hotel_slug = %s AND value_hmac = ANY(%s) AND scope = ANY(%s) LIMIT 1",
        (hotel_slug, hs, scopes),
    )
    return cur.fetchone() is not None


def suppress(
    cur: Any, hotel_slug: str, identity: bytes, *, scope: str = "all", reason: str = "stop", source: str, actor: Any = None
) -> bool:
    """Record a suppression and withdraw the consents it implies. Returns
    True when it was new. Idempotent: a second STOP changes nothing."""
    if scope not in ("all", "marketing"):
        raise ClefsError(400, "scope must be all or marketing", "invalid_scope")
    identity = bytes(identity)
    cur.execute(
        """
        INSERT INTO gst_suppressions (hotel_slug, value_hmac, scope, reason, source, created_at)
        VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1
        """,
        (hotel_slug, identity, scope, reason, (source or "")[:60], clock.now()),
    )
    new = cur.fetchone() is not None
    purposes = MESSAGING_PURPOSES if scope == "all" else MARKETING_PURPOSES
    for p in purposes:
        if identity_granted(cur, hotel_slug, identity, p):
            consent.withdraw(cur, hotel_slug, identity.hex(), p, source=f"optout:{source}"[:60], actor=actor)
    if scope == "all":
        cur.execute(
            "UPDATE gst_contact_points SET status = 'suppressed' WHERE hotel_slug = %s AND value_hmac = %s AND status = 'active'",
            (hotel_slug, identity),
        )
    if new:
        audit(cur, actor, "guests.contact.suppress", "gst_suppression", identity.hex()[:16], hotel_slug=hotel_slug,
              after={"scope": scope, "reason": reason, "source": source})
    return new


def lift_stop(cur: Any, hotel_slug: str, identity: bytes) -> None:
    """A fresh, evidenced opt-in after a STOP. Only 'stop' suppressions go:
    wrong-number, complaint, DNCR and bounce entries stay."""
    cur.execute(
        "DELETE FROM gst_suppressions WHERE hotel_slug = %s AND value_hmac = %s AND reason = 'stop'",
        (hotel_slug, bytes(identity)),
    )
    cur.execute(
        "UPDATE gst_contact_points SET status = 'active' WHERE hotel_slug = %s AND value_hmac = %s AND status = 'suppressed'",
        (hotel_slug, bytes(identity)),
    )


def suppression_scopes(cur: Any, hotel_slug: str, identity: bytes) -> set[str]:
    """The scopes ('all', 'marketing') standing against one identity."""
    cur.execute("SELECT DISTINCT scope FROM gst_suppressions WHERE hotel_slug = %s AND value_hmac = %s",
                (hotel_slug, bytes(identity)))
    return {r["scope"] for r in cur.fetchall()}


def _proves_control(cur: Any, hotel_slug: str, capture_source: str, evidence: dict, identity: bytes) -> bool:
    """Whether this capture shows the guest controls the value, which is
    what it takes to lift a STOP. An inbound_message_id counts only when
    that message came FROM the captured value (sender_hmac is the same HMAC
    as the point's), so a copied message id cannot vouch for another number."""
    if capture_source in STOP_LIFT_SOURCES:
        return True
    mid = evidence.get("inbound_message_id")
    if mid is None or not str(mid).isdigit():
        return False
    cur.execute("SELECT 1 FROM inbound_messages WHERE id = %s AND hotel_slug = %s AND sender_hmac = %s",
                (int(mid), hotel_slug, bytes(identity)))
    return cur.fetchone() is not None


def identity_granted(cur: Any, hotel_slug: str, identity: bytes, purpose: str) -> bool:
    return consent.has_consent(cur, hotel_slug, bytes(identity).hex(), purpose)


# ─── Points ───

POINT_COLS = (
    "id, hotel_slug, reservation_id, kind, provenance, reservation_contact_id, value_hmac, country_prefix, "
    "alias_domain, capture_source, evidence, usable_until, last_used_at, status, created_at, purged_at"
)


def upsert_point(
    cur: Any,
    hotel_slug: str,
    *,
    reservation_id: int | None,
    kind: str,
    provenance: str,
    value_hmac: bytes,
    capture_source: str,
    value_enc: bytes | None = None,
    country_prefix: str | None = None,
    alias_domain: str | None = None,
    reservation_contact_id: int | None = None,
    evidence: dict | None = None,
    usable_until: datetime | None = None,
    reactivate: bool = False,
) -> int:
    """Insert or refresh one point (deduped per stay, kind, hmac and
    provenance). A suppressed point stays suppressed unless `reactivate`
    (a fresh messaging opt-in); a purged one never matches (its hmac is NULL)."""
    cur.execute(
        """
        INSERT INTO gst_contact_points (hotel_slug, reservation_id, kind, provenance, reservation_contact_id, value_enc,
                                        value_hmac, country_prefix, alias_domain, capture_source, evidence, usable_until,
                                        last_used_at, status, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
        ON CONFLICT (hotel_slug, (COALESCE(reservation_id, 0)), kind, value_hmac, provenance) WHERE value_hmac IS NOT NULL
        DO UPDATE SET
            value_enc = COALESCE(EXCLUDED.value_enc, gst_contact_points.value_enc),
            reservation_contact_id = COALESCE(EXCLUDED.reservation_contact_id, gst_contact_points.reservation_contact_id),
            country_prefix = COALESCE(EXCLUDED.country_prefix, gst_contact_points.country_prefix),
            alias_domain = COALESCE(EXCLUDED.alias_domain, gst_contact_points.alias_domain),
            capture_source = CASE WHEN EXCLUDED.capture_source = 'pms' THEN gst_contact_points.capture_source
                                  ELSE EXCLUDED.capture_source END,
            evidence = gst_contact_points.evidence || EXCLUDED.evidence,
            usable_until = EXCLUDED.usable_until,
            last_used_at = GREATEST(gst_contact_points.last_used_at, EXCLUDED.last_used_at),
            status = CASE WHEN %s THEN 'active' ELSE gst_contact_points.status END
        RETURNING id
        """,
        (
            hotel_slug, reservation_id, kind, provenance, reservation_contact_id, value_enc, bytes(value_hmac),
            country_prefix, alias_domain, capture_source, Json(evidence or {}), usable_until, clock.now(), clock.now(),
            bool(reactivate),
        ),
    )
    return int(cur.fetchone()["id"])


def mirror_reservation_contacts(cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]:
    """Copy core reservation_contacts of one reservation into points (HMAC,
    provenance, lifetime). Purged core rows are skipped: their identifier
    must not outlive the OTA window here either."""
    backend = stays_port.backend()
    out: list[dict] = []
    for c in backend.contacts(cur, hotel_slug, reservation_id):
        if not c.get("value_hmac") or c.get("purged_at") or c.get("provenance") not in PMS_PROVENANCES:
            continue
        prefix = None
        if c["kind"] == "phone" and c.get("id") is not None:
            raw = backend.contact_value(cur, c["id"])
            prefix = identity.country_prefix(raw) if raw else None
        pid = upsert_point(
            cur, hotel_slug, reservation_id=reservation_id, kind=c["kind"], provenance=c["provenance"],
            value_hmac=c["value_hmac"], capture_source="pms", country_prefix=prefix,
            alias_domain=c.get("alias_domain"), reservation_contact_id=c.get("id"),
            usable_until=stays_port.as_datetime(c.get("usable_until")),
        )
        out.append({**c, "point_id": pid})
    return out


def points_for(cur: Any, hotel_slug: str, identities: list[bytes], *, active_only: bool = True) -> list[dict]:
    hs = [bytes(h) for h in identities if h]
    if not hs:
        return []
    cur.execute(
        f"SELECT {POINT_COLS} FROM gst_contact_points WHERE hotel_slug = %s AND value_hmac = ANY(%s)"
        + (" AND status = 'active'" if active_only else "") + " ORDER BY id",
        (hotel_slug, hs),
    )
    return [dict(r) for r in cur.fetchall()]


def current_stay_in_room(cur: Any, hotel_slug: str, room: str, *, on: Any) -> dict | None:
    """The live stay in `room` on local date `on`: IN_HOUSE first, then a
    stay whose dates cover the day (roster rows carry no status)."""
    cur.execute(
        """
        SELECT * FROM gst_stays
        WHERE hotel_slug = %s AND room = %s
          AND (status IS NULL OR status IN ('CONFIRMED', 'IN_HOUSE'))
          AND (status = 'IN_HOUSE' OR ((check_in IS NULL OR check_in <= %s) AND (check_out IS NULL OR check_out >= %s)))
        ORDER BY (status = 'IN_HOUSE') DESC NULLS LAST, check_in DESC NULLS LAST, id DESC LIMIT 1
        """,
        (hotel_slug, room, on, on),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def current_stays_in_rooms(cur: Any, hotel_slug: str, rooms: Iterable[str], *, on: Any) -> dict[str, dict]:
    """current_stay_in_room for many rooms in one query: {room: stay} for
    the rooms with a live stay on `on`. WHY: the service queue and the
    device feed badge every card, and one lookup per card would be N
    round trips per poll. Same filter and same tie-break per room."""
    wanted = sorted({str(r) for r in rooms if r})
    if not wanted:
        return {}
    cur.execute(
        """
        SELECT DISTINCT ON (room) * FROM gst_stays
        WHERE hotel_slug = %s AND room = ANY(%s)
          AND (status IS NULL OR status IN ('CONFIRMED', 'IN_HOUSE'))
          AND (status = 'IN_HOUSE' OR ((check_in IS NULL OR check_in <= %s) AND (check_out IS NULL OR check_out >= %s)))
        ORDER BY room, (status = 'IN_HOUSE') DESC NULLS LAST, check_in DESC NULLS LAST, id DESC
        """,
        (hotel_slug, wanted, on, on),
    )
    return {r["room"]: dict(r) for r in cur.fetchall()}


def stay_for_reservation(cur: Any, hotel_slug: str, reservation_id: int) -> dict | None:
    cur.execute("SELECT * FROM gst_stays WHERE hotel_slug = %s AND reservation_id = %s", (hotel_slug, reservation_id))
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Own-contact capture ───


def _require_transfer(consents: dict, versions: dict) -> None:
    if not consents.get("transfer_abroad") or not versions.get("transfer_abroad"):
        raise ConsentNotAllowed(
            "Storing a contact needs the guest's transfer-abroad consent and its text version",
            reason="transfer_abroad_required",
        )


def _is_ota_stay(cur: Any, hotel_slug: str, reservation_id: int | None, capture_source: str) -> bool:
    """True when the reservation came through an OTA, judged by its channel
    or by any OTA-provenance contact on it. An unknown reservation on the
    J1 form counts as OTA: the conservative reading keeps it out of
    marketing."""
    if reservation_id is None:
        return False
    res = stays_port.backend().reservation(cur, hotel_slug, reservation_id)
    snap = stay_for_reservation(cur, hotel_slug, reservation_id)
    if res is None and snap is None:
        return capture_source == "j1_form"
    if stays_port.is_ota((res or {}).get("channel_code") or (snap or {}).get("source")):
        return True
    return any(c.get("provenance") in ("pms_ota", "ota_alias") for c in stays_port.backend().contacts(cur, hotel_slug, reservation_id))


def _guest_stay_until(cur: Any, hotel_slug: str, reservation_id: int | None) -> datetime:
    from clefs.core.hotels import get_hotel

    res = stays_port.backend().reservation(cur, hotel_slug, reservation_id) if reservation_id else None
    snap = stay_for_reservation(cur, hotel_slug, reservation_id) if reservation_id else None
    out = stays_port.as_date((res or {}).get("check_out") or (snap or {}).get("check_out"))
    if out is None:
        return clock.now() + timedelta(days=GUEST_STAY_DAYS)
    return timeutil.at_local(get_hotel(cur, hotel_slug), out + timedelta(days=GUEST_STAY_DAYS), time(23, 59))


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
    profile_identities: dict[str, bytes] | None = None,
) -> int:
    """api.record_own_contact. `profile_identities` (internal) names the
    other identities captured together ({"email": hmac}), so one person's
    phone and email land on ONE profile instead of two."""
    consents = {k: bool(v) for k, v in (consents or {}).items()}
    versions = dict(consent_text_versions or {})
    _require_transfer(consents, versions)  # first, whatever else is wrong (04 spec 3.4)
    if capture_source not in OWN_CAPTURE_SOURCES:
        raise ClefsError(400, f"capture_source must be one of {list(OWN_CAPTURE_SOURCES)}", "invalid_capture_source")
    unknown = sorted(set(consents) - set(OWN_PURPOSES))
    if unknown:
        raise ClefsError(400, f"unknown consent purposes {unknown}", "invalid_consent_purpose")
    missing = sorted(p for p, g in consents.items() if g and not versions.get(p))
    if missing:
        raise ConsentNotAllowed(f"Consents {missing} need the text version the guest was shown", reason="text_version_required")
    ota_j1 = capture_source == "j1_form" and _is_ota_stay(cur, hotel_slug, reservation_id, capture_source)
    if ota_j1 and any(consents.get(p) for p in (*MARKETING_PURPOSES, "profile_memory")):
        # Canon 19: the J1 page is not a route to a direct contact.
        raise ConsentNotAllowed(
            "A contact given on the stay page of an OTA booking is for this stay's messages only",
            reason="ota_j1_form",
        )
    # first_name / preferences ride along for the profile only: they are
    # taken out BEFORE the evidence is stored on the point or the consent.
    raw_ev = dict(evidence or {})
    profile_fields = {k: raw_ev.pop(k) for k in ("first_name", "preferences") if k in raw_ev}
    ev = evidence_mod.check(cur, hotel_slug, capture_source, raw_ev)
    # The language of the wording the guest read (gst.v3 is written in
    # identity.CONSENT_LANGUAGES); a profile's language stays one the
    # journeys write in (EN/AR templates).
    wording = identity.consent_language(ev.get("language"))
    language = wording if wording in ("en", "ar") else "en"
    h = identity.hmac_for(cur, hotel_slug, kind, value)
    norm = identity.normalized(cur, hotel_slug, kind, value)
    provenance = "guest_stay" if ota_j1 else "guest_direct"
    # ─── STOP handling ───
    # A fresh messaging grant lifts an earlier STOP only on proof that the
    # guest controls the value. Without it the suppression stands, and the
    # grants it covers are not recorded at all: a stored grant would be
    # consent evidence nobody gave, waiting for the next lift to act on it.
    blocked = suppression_scopes(cur, hotel_slug, h)
    proven = _proves_control(cur, hotel_slug, capture_source, ev, h)
    held: list[str] = []
    if any(consents.get(p) for p in MESSAGING_PURPOSES):
        if proven:
            lift_stop(cur, hotel_slug, h)
            blocked = suppression_scopes(cur, hotel_slug, h)  # wrong-number, DNCR and bounce stay
        else:
            held = sorted(p for p in MESSAGING_PURPOSES if consents.get(p)
                          and ("all" in blocked or ("marketing" in blocked and p in MARKETING_PURPOSES)))
            for p in held:
                consents.pop(p)
    fresh_optin = any(consents.get(p) for p in MESSAGING_PURPOSES) and "all" not in blocked
    point_id = upsert_point(
        cur, hotel_slug, reservation_id=reservation_id,
        kind=kind, provenance=provenance, value_hmac=h, capture_source=capture_source,
        value_enc=privacy.seal(norm), country_prefix=identity.country_prefix(norm) if kind == "phone" else None,
        evidence={"capture": {**ev, "at": clock.now().isoformat()}},
        usable_until=_guest_stay_until(cur, hotel_slug, reservation_id) if provenance == "guest_stay" else None,
        reactivate=fresh_optin,
    )
    if "all" in blocked:
        # suppress() marks every point of the identity; a point first made
        # after the STOP (another stay) must not start out active either.
        cur.execute("UPDATE gst_contact_points SET status = 'suppressed' WHERE id = %s AND status = 'active'", (point_id,))
    for purpose, g in consents.items():
        consent.record_consent(
            cur, hotel_slug, h.hex(), purpose, g, source=capture_source, subject_type="contact",
            evidence={"point_id": point_id, **{k: v for k, v in ev.items() if k in ("kind", "file_id", "signature_file_id", "inbound_message_id")}},
            text_version=versions.get(purpose), language=wording, actor=actor,
        )
    if provenance == "guest_direct" and consents.get("profile_memory"):
        _profile_from_capture(cur, hotel_slug, {**(profile_identities or {}), kind: h}, reservation_id=reservation_id,
                              room=room, fields={**profile_fields, "language": language}, actor=actor)
    audit(cur, actor, "guests.contact.capture", "gst_contact_point", point_id, hotel_slug=hotel_slug,
          after={"kind": kind, "provenance": provenance, "capture_source": capture_source,
                 "consents": {p: g for p, g in consents.items()}, "held_by_suppression": held})
    return point_id


def _profile_from_capture(cur: Any, hotel_slug: str, ids: dict[str, bytes], *, reservation_id: int | None,
                          room: str | None, fields: dict, actor: Any) -> None:
    from clefs.core.hotels import get_hotel
    from clefs.core.time import local_date

    prof = identity.upsert_profile(
        cur, hotel_slug, phone_hmac=ids.get("phone"), email_hmac=ids.get("email"),
        first_name=fields.get("first_name") if isinstance(fields.get("first_name"), str) else None,
        language=fields.get("language"), preferences=fields.get("preferences"), actor=actor,
    )
    if prof is None:
        return
    stay = stay_for_reservation(cur, hotel_slug, reservation_id) if reservation_id else None
    if stay is None and room:
        stay = current_stay_in_room(cur, hotel_slug, room, on=local_date(get_hotel(cur, hotel_slug)))
    if stay is not None:
        identity.link_stay(cur, hotel_slug, stay["id"], prof["id"])


# ─── Forgetting ───


def opt_out(cur: Any, hotel_slug: str, *, kind: str, value: str, source: str, scope: str = "all", actor: Any = None) -> bool:
    """A guest said STOP (or unsubscribed). A value that does not parse is
    logged and dropped: there is nothing it could be matched against."""
    try:
        h = identity.hmac_for(cur, hotel_slug, kind, value)
    except ClefsError:
        log.info("opt-out with an unparseable %s at %s ignored", kind, hotel_slug)
        return False
    return suppress(cur, hotel_slug, h, scope=scope, reason="stop", source=source, actor=actor)


def forget_contact(cur: Any, hotel_slug: str, identity_hmac: bytes, *, source: str, actor: Any = None) -> dict:
    """Withdraw every consent this module records for the identity, delete
    its profile, purge its own-capture points and delete their evidence
    files (04 spec 3.4: evidence goes when the consent does). The STOP
    suppression row is kept on purpose: it is what stops a later import from
    messaging the same number again."""
    h = bytes(identity_hmac)
    for p in OWN_PURPOSES:
        if identity_granted(cur, hotel_slug, h, p):
            consent.withdraw(cur, hotel_slug, h.hex(), p, source=source[:60], actor=actor)
    profiles = identity.forget_identity(cur, hotel_slug, h, source=source, actor=actor)
    cur.execute(
        """
        SELECT id, evidence FROM gst_contact_points
        WHERE hotel_slug = %s AND value_hmac = %s AND provenance IN ('guest_direct', 'guest_stay')
        """,
        (hotel_slug, h),
    )
    rows = [dict(r) for r in cur.fetchall()]
    deleted = 0
    for r in rows:
        deleted += evidence_mod.delete_files(cur, hotel_slug, (r["evidence"] or {}).get("capture"))
    if rows:
        cur.execute(
            """
            UPDATE gst_contact_points SET status = 'purged', value_enc = NULL, value_hmac = NULL, purged_at = %s,
                evidence = '{}'::jsonb
            WHERE id = ANY(%s)
            """,
            (clock.now(), [r["id"] for r in rows]),
        )
    cur.execute("UPDATE gst_stays SET contact_hmacs = array_remove(contact_hmacs, %s) WHERE hotel_slug = %s AND %s = ANY(contact_hmacs)",
                (h, hotel_slug, h))
    audit(cur, actor, "guests.contact.forget", "gst_contact", h.hex()[:16], hotel_slug=hotel_slug,
          after={"points": len(rows), "profiles": profiles, "evidence_files": deleted, "source": source})
    return {"points_purged": len(rows), "profiles_deleted": profiles, "evidence_files_deleted": deleted}
