"""Reservation contacts (04 spec D1 and 3.4): sealed, HMAC'd, never plaintext.

A PMS phone or email lands only here: `value_enc` is privacy.seal(value) and
`value_hmac` is privacy.contact_hmac (per hotel), the only joinable key. Every
row carries its provenance and lifetime:

| provenance  | what                                        | usable_until / purge_after           |
|-------------|---------------------------------------------|--------------------------------------|
| ota_alias   | a relay address (guest.booking.com, ...)    | Booking: check_out + 30 d; others: check_out |
| pms_ota     | a real contact passed on by an OTA          | check_out + 30 d                     |
| pms_direct  | a contact on a direct reservation           | check_out + PMS_DIRECT_RETENTION_DAYS |

At purge_after the value is nulled; OTA rows also lose the hmac, so no hashed
OTA identifier outlives the Booking.com 30-day window (Canon 19).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from clefs.audit import audit
from clefs.core import clock, privacy
from clefs.core.hotels import get_hotel
from clefs.core.stay_events import CONTACT_CHANGED
from clefs.core.stays import events
from clefs.settings import get_settings

# Channels whose reservations are OTA bookings (the contact came from the OTA).
OTA_CHANNELS = frozenset({"BOOKING", "AGODA", "EXPEDIA"})
# Direct channels (04 spec 3.4 pms_direct).
DIRECT_CHANNELS = frozenset({"DIRECT_WEB", "DIRECT_PHONE", "WALKIN", "CORPORATE"})
OTA_WINDOW_DAYS = 30  # GDT 2.2.6
# 03 spec 5 default retention for guest data: 24 months.
PMS_DIRECT_RETENTION_DAYS = 730
PROVENANCES = ("pms_direct", "pms_ota", "ota_alias")
OTA_PROVENANCES = ("pms_ota", "ota_alias")


# ─── Classification ───


def alias_domain_of(kind: str, value: str, channel_code: str | None) -> str | None:
    """The OTA relay domain of an email, or None. Configured domains
    (OTA_ALIAS_DOMAINS) plus the Agoda heuristic (UNVERIFIED, 04 spec A2):
    an Agoda reservation's email on any agoda domain."""
    if kind != "email" or "@" not in value:
        return None
    domain = value.rsplit("@", 1)[1].strip().lower()
    configured = [d.lower() for d in get_settings().OTA_ALIAS_DOMAINS]
    if any(domain == d or domain.endswith("." + d) for d in configured):
        return domain
    if (channel_code or "").upper() == "AGODA" and "agoda" in domain:
        return domain
    return None


def classify(kind: str, value: str, channel_code: str | None) -> tuple[str, str | None]:
    """(provenance, alias_domain) for a contact on a reservation of this channel."""
    alias = alias_domain_of(kind, value, channel_code)
    if alias:
        return "ota_alias", alias
    if (channel_code or "").upper() in OTA_CHANNELS:
        return "pms_ota", None
    return "pms_direct", None


def lifetime(
    provenance: str, *, check_out: date, alias_domain: str | None, tz: Any
) -> tuple[datetime, datetime]:
    """(usable_until, purge_after) as hotel-local midnight after the date."""

    def at(d: date) -> datetime:
        return datetime.combine(d, time(0), tzinfo=tz)

    if provenance == "ota_alias":
        booking = (alias_domain or "").endswith("booking.com")
        until = at(check_out + timedelta(days=OTA_WINDOW_DAYS if booking else 0))
        # The hmac and value go at check_out + 30 d even where use stops sooner.
        return until, at(check_out + timedelta(days=OTA_WINDOW_DAYS))
    if provenance == "pms_ota":
        end = at(check_out + timedelta(days=OTA_WINDOW_DAYS))
        return end, end
    end = at(check_out + timedelta(days=PMS_DIRECT_RETENTION_DAYS))
    return end, end


# ─── Writes ───


def _reservation(cur: Any, reservation_id: int) -> dict | None:
    cur.execute(
        "SELECT id, hotel_slug, channel_code, check_out, actual_check_out FROM reservations WHERE id = %s",
        (reservation_id,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _active_ids(cur: Any, reservation_id: int) -> list[int]:
    cur.execute(
        "SELECT id FROM reservation_contacts WHERE reservation_id = %s AND purged_at IS NULL ORDER BY id",
        (reservation_id,),
    )
    return [int(r["id"]) for r in cur.fetchall()]


def set_contacts(
    cur: Any,
    hotel_slug: str,
    reservation_id: int,
    contacts: list[dict],
    *,
    replace: bool = True,
    import_batch_id: int | None = None,
    occurred_at: datetime | None = None,
) -> dict:
    """Store a reservation's contacts: [{kind: phone|email, value, provenance?}].

    Values are sealed and HMAC'd; a value that does not parse as a phone or
    email is skipped (counted in `invalid`). With replace (the PMS row is the
    whole truth), an active contact missing from `contacts` is purged. A
    change in the active set emits one contact_changed event carrying ids
    only. Returns {ids, added, removed, invalid}."""
    res = _reservation(cur, reservation_id)
    if res is None or res["hotel_slug"] != hotel_slug:
        raise ValueError(f"reservation {reservation_id} is not a {hotel_slug} reservation")
    tz = get_hotel(cur, hotel_slug).tz
    check_out = res["actual_check_out"] or res["check_out"]
    before = _active_ids(cur, reservation_id)
    cur.execute(
        "SELECT id, kind, value_hmac FROM reservation_contacts WHERE reservation_id = %s AND purged_at IS NULL",
        (reservation_id,),
    )
    existing = {(r["kind"], bytes(r["value_hmac"])): int(r["id"]) for r in cur.fetchall() if r["value_hmac"]}
    keep: set[int] = set()
    added = invalid = 0
    calling_code = privacy.hotel_calling_code(hotel_slug, cur=cur)
    for c in contacts:
        kind, raw = c.get("kind"), (c.get("value") or "").strip()
        if kind not in ("phone", "email") or not raw:
            invalid += int(bool(raw))
            continue
        try:
            # The sealed value is the normalised one (E.164 / lower-case), so
            # a consumer never re-parses a national number.
            cc = calling_code if kind == "phone" else None
            value = privacy.normalize_contact(kind, raw, default_country_code=cc)
            digest = privacy.contact_hmac(hotel_slug, kind, raw, default_country_code=cc, cur=cur)
        except Exception:  # noqa: BLE001 - junk in a PMS export is skipped, not fatal
            invalid += 1
            continue
        hit = existing.get((kind, digest))
        if hit is not None:
            keep.add(hit)
            continue
        provenance, alias = classify(kind, value, res["channel_code"])
        if c.get("provenance") in PROVENANCES:
            provenance = c["provenance"]
        until, purge = lifetime(provenance, check_out=check_out, alias_domain=alias, tz=tz)
        cur.execute(
            """
            INSERT INTO reservation_contacts (hotel_slug, reservation_id, kind, value_enc, value_hmac, provenance,
                                              alias_domain, usable_until, purge_after, import_batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (reservation_id, kind, value_hmac) WHERE value_hmac IS NOT NULL DO NOTHING
            RETURNING id
            """,
            (hotel_slug, reservation_id, kind, privacy.seal(value), digest, provenance, alias, until, purge,
             import_batch_id),
        )
        row = cur.fetchone()
        if row:
            keep.add(int(row["id"]))
            existing[(kind, digest)] = int(row["id"])
            added += 1
    removed = 0
    if replace:
        gone = [i for i in before if i not in keep]
        if gone:
            cur.execute(
                """
                UPDATE reservation_contacts SET value_enc = NULL, value_hmac = NULL, purged_at = NOW()
                WHERE id = ANY(%s)
                """,
                (gone,),
            )
            removed = len(gone)
    after = _active_ids(cur, reservation_id)
    if after != before:
        events.emit(cur, hotel_slug, reservation_id, CONTACT_CHANGED,
                    {"reservation_contact_ids": (before, after)}, occurred_at=occurred_at)
    return {"ids": after, "added": added, "removed": removed, "invalid": invalid}


def refresh_lifetimes(cur: Any, reservation_id: int) -> int:
    """Recompute usable_until and purge_after of unpurged contacts after the
    check-out moved (a shortened or extended stay). Returns rows updated."""
    res = _reservation(cur, reservation_id)
    if res is None:
        return 0
    tz = get_hotel(cur, res["hotel_slug"]).tz
    check_out = res["actual_check_out"] or res["check_out"]
    cur.execute(
        "SELECT id, provenance, alias_domain FROM reservation_contacts WHERE reservation_id = %s AND purged_at IS NULL",
        (reservation_id,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        until, purge = lifetime(r["provenance"], check_out=check_out, alias_domain=r["alias_domain"], tz=tz)
        cur.execute(
            "UPDATE reservation_contacts SET usable_until = %s, purge_after = %s WHERE id = %s",
            (until, purge, r["id"]),
        )
    return len(rows)


# ─── Reads ───

_META_COLS = """id, hotel_slug, reservation_id, kind, value_hmac, provenance, alias_domain, usable_until,
                purge_after, purged_at, created_at, (value_enc IS NOT NULL) AS has_value"""


def contacts_for(cur: Any, hotel_slug: str, reservation_id: int, *, include_purged: bool = False) -> list[dict]:
    """Contact metadata of a reservation (never the value): id, kind,
    value_hmac (bytes), provenance, alias_domain, usable_until, purge_after,
    purged_at, has_value."""
    cur.execute(
        f"""
        SELECT {_META_COLS} FROM reservation_contacts
        WHERE hotel_slug = %s AND reservation_id = %s AND (%s OR purged_at IS NULL) ORDER BY id
        """,
        (hotel_slug, reservation_id, include_purged),
    )
    return [dict(r) for r in cur.fetchall()]


def reservations_for_contact(cur: Any, hotel_slug: str, value_hmac: bytes) -> list[int]:
    """Reservation ids holding this contact hmac (newest check-in first)."""
    cur.execute(
        """
        SELECT DISTINCT r.id, r.check_in FROM reservation_contacts c JOIN reservations r ON r.id = c.reservation_id
        WHERE c.hotel_slug = %s AND c.value_hmac = %s ORDER BY r.check_in DESC, r.id DESC
        """,
        (hotel_slug, value_hmac),
    )
    return [int(r["id"]) for r in cur.fetchall()]


def contact_value(
    cur: Any, reservation_contact_id: int, *, actor: Any = None, purpose: str | None = None
) -> str | None:
    """Unseal one contact (04 spec D1) and write an audit row. None when the
    row is unknown, purged, or past usable_until (use has ended even if the
    value is still held until purge_after)."""
    cur.execute(
        "SELECT id, hotel_slug, reservation_id, kind, provenance, value_enc, usable_until, purged_at "
        "FROM reservation_contacts WHERE id = %s",
        (reservation_contact_id,),
    )
    row = cur.fetchone()
    if row is None or row["purged_at"] is not None or row["value_enc"] is None:
        return None
    if row["usable_until"] is not None and clock.now() >= row["usable_until"]:
        return None
    value = privacy.unseal(row["value_enc"])
    audit(
        cur,
        actor,
        "stays.contact_unseal",
        "reservation_contact",
        row["id"],
        meta={"kind": row["kind"], "provenance": row["provenance"], "reservation_id": row["reservation_id"],
              "purpose": purpose},
        hotel_slug=row["hotel_slug"],
    )
    return value


# ─── Purge (job stays.contact_purge) ───


def purge_contacts(cur: Any, *, now: datetime | None = None, limit: int = 5000) -> dict[str, int]:
    """Null value_enc on every contact past purge_after; OTA-provenance rows
    (pms_ota, ota_alias) also lose value_hmac. Batched; returns counts."""
    at = now or clock.now()
    cur.execute(
        """
        WITH due AS (
            SELECT id FROM reservation_contacts
            WHERE purged_at IS NULL AND purge_after IS NOT NULL AND purge_after <= %s
            ORDER BY purge_after LIMIT %s FOR UPDATE SKIP LOCKED
        )
        UPDATE reservation_contacts c
        SET value_enc = NULL,
            value_hmac = CASE WHEN c.provenance IN ('pms_ota', 'ota_alias') THEN NULL ELSE c.value_hmac END,
            purged_at = %s
        FROM due WHERE c.id = due.id
        RETURNING c.provenance
        """,
        (at, limit, at),
    )
    rows = cur.fetchall()
    ota = sum(1 for r in rows if r["provenance"] in OTA_PROVENANCES)
    return {"purged": len(rows), "ota": ota, "direct": len(rows) - ota}
