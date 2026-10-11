"""
Guest identity (03 spec 2.7, Canon 10): hashed contacts, consent-gated
profiles, preferences with health data, direct codes and the returning-guest
badge.

Rules this file enforces, because each one is a legal line, not a nicety:
- A contact becomes a joinable key only through core.privacy.contact_hmac
  (per-hotel pepper). Raw phones and emails never land in a gst_ table
  except sealed (privacy.seal) on a point the guest gave us themselves.
- A profile exists ONLY while its subject has a granted profile_memory
  consent AND a granted transfer_abroad consent (consent is the only basis
  Clefs uses, and the data is stored in Google Cloud's India region, outside
  most guests' own country). No consent means no row, and a withdrawal
  deletes the row (stays unlink through ON DELETE SET NULL).
- Allergies are health data: they live in preferences.health and every
  serializer masks them with core.pii's HEALTH class (full for owner, gm and
  front desk; F&B only for department heads and devices; none otherwise).
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Literal

from clefs.audit import audit
from clefs.core import clock, consent, pii, privacy
from clefs.core.errors import ClefsError
from clefs.db import Json

log = logging.getLogger("clefs.guests.identity")

Kind = Literal["phone", "email"]
KINDS = ("phone", "email")

# ─── Consent purposes ───
# 04 spec 3.4: marketing is separate per channel. These retire 03's single
# `marketing` purpose; core already knows the other purposes used here.
MARKETING_PURPOSE = {"whatsapp": "marketing_whatsapp", "email": "marketing_email"}
for _p in MARKETING_PURPOSE.values():
    consent.register_consent_purpose(_p)

PROFILE_PURPOSES = ("profile_memory", "transfer_abroad")


# Platform wording this module pins: gst.v3 (migration 0679) for every
# purpose it captures. current_consent_text returns the newest platform text
# of a purpose, and other lanes seed their own: Trust's generic
# "clefs-2026-10" wording does not mention health data (a profile stores
# allergies), does not name the hotel (Meta's utility opt-in must name the
# business) and its transfer text names the UAE and Singapore, and the
# concierge's sheet speaks of AI use, not of the contact details kept here.
# gst.v3 says where the data is stored (Google Cloud's India region) and
# that the AI models may run in other countries, including the United
# States. gst.v1 (0675) and gst.v2 (0651) stay, retired, as the evidence
# older consents point at. A hotel's own wording still wins; that is the
# hotel's (and its counsel's) call.
CONSENT_TEXT_VERSION = "gst.v3"
PINNED_PURPOSES = ("transfer_abroad", "profile_memory", "whatsapp_updates", "marketing_whatsapp", "marketing_email")
PINNED_PLATFORM_VERSIONS = {p: CONSENT_TEXT_VERSION for p in PINNED_PURPOSES}
# The languages gst.v3 is written in. A capture in another language records
# the English wording (text_version falls back to EN).
CONSENT_LANGUAGES: tuple[str, ...] = ("en", "ar", "ja", "ko", "th")


def consent_language(language: str | None) -> str:
    """The wording language for a capture: "ja-JP" -> "ja", anything gst.v3
    is not written in -> "en"."""
    base = (language or "").strip().lower().replace("_", "-").split("-")[0]
    return base if base in CONSENT_LANGUAGES else "en"


def _pinned_exists(cur: Any, purpose: str, version: str, language: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM consent_texts
        WHERE hotel_slug IS NULL AND purpose = %s AND version = %s AND language = %s AND retired_at IS NULL
        """,
        (purpose, version, language),
    )
    return cur.fetchone() is not None


def text_version(cur: Any, hotel_slug: str, purpose: str, language: str = "en") -> str | None:
    """The version of the wording a guest is shown now (hotel's, else the
    platform's, pinned where this module owns it), falling back to EN when
    the language has none."""
    for lang in dict.fromkeys((language, "en")):
        row = consent.current_consent_text(cur, purpose, language=lang, hotel_slug=hotel_slug)
        if row is None:
            continue
        pinned = PINNED_PLATFORM_VERSIONS.get(purpose)
        if row.get("hotel_slug") is None and pinned and _pinned_exists(cur, purpose, pinned, lang):
            return pinned
        return row["version"]
    return None


def consent_text(cur: Any, hotel_slug: str, purpose: str, language: str = "en") -> dict | None:
    """{purpose, version, language, body} of the wording text_version picks,
    for any page that renders a tick box (bodies carry a "{hotel}" slot)."""
    version = text_version(cur, hotel_slug, purpose, language)
    if version is None:
        return None
    cur.execute(
        """
        SELECT purpose, version, language, body FROM consent_texts
        WHERE purpose = %s AND version = %s AND language IN (%s, 'en') AND (hotel_slug = %s OR hotel_slug IS NULL)
        ORDER BY (language = %s) DESC, (hotel_slug IS NULL) LIMIT 1
        """,
        (purpose, version, language, hotel_slug, language),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def granted(cur: Any, hotel_slug: str, identity: bytes, purpose: str, *, at: datetime | None = None) -> bool:
    return consent.has_consent(cur, hotel_slug, ref(identity), purpose, at=at)


def profile_allowed(cur: Any, hotel_slug: str, identity: bytes) -> bool:
    """Both profile consents stand for this identity right now."""
    return all(granted(cur, hotel_slug, identity, p) for p in PROFILE_PURPOSES)


# ─── Hashing ───


def hmac_for(cur: Any, hotel_slug: str, kind: str, value: str) -> bytes:
    """contact_hmac with the request cursor (national numbers read the hotel's
    country). Raises 400 invalid_phone / invalid_email on junk."""
    if kind not in KINDS:
        raise ClefsError(400, "kind must be phone or email", "invalid_contact_kind")
    return privacy.contact_hmac(hotel_slug, kind, value, cur=cur)  # type: ignore[arg-type]


def normalized(cur: Any, hotel_slug: str, kind: str, value: str) -> str:
    """E.164 phone or lowercased email, as contact_hmac saw it."""
    # Same "national input" test contact_hmac applies, so the sealed value and
    # the HMAC always describe the same number.
    national = kind == "phone" and not re.sub(r"[\s\-().]", "", value or "").startswith(("+", "00"))
    cc = privacy.hotel_calling_code(hotel_slug, cur=cur) if national else None
    return privacy.normalize_contact(kind, value, default_country_code=cc)  # type: ignore[arg-type]


def ref(identity: bytes | memoryview) -> str:
    """The consent subject_ref of an identity: hex of its contact_hmac."""
    return bytes(identity).hex()


def from_ref(contact_ref: str) -> bytes | None:
    try:
        raw = bytes.fromhex(contact_ref)
    except (TypeError, ValueError):
        return None
    return raw if len(raw) == 32 else None


def country_prefix(e164: str) -> str | None:
    """"+971" for a UAE number, "+1" for NANP. The longest known calling code
    wins, so the NANP marketing block (04 spec 3.4) reads a reliable prefix."""
    if not e164.startswith("+"):
        return None
    digits = e164[1:]
    codes = sorted({str(c).lstrip("+") for c in privacy.CALLING_CODES.values()} | {"1"}, key=len, reverse=True)
    for c in codes:
        if c and digits.startswith(c):
            return f"+{c}"
    return None


# ─── Preferences ───
# {tags: [...], notes, health: {allergies: [...], notes}}. Bounded so a
# console paste cannot turn a profile into a document store.

MAX_TAGS = 20
MAX_TAG_LEN = 40
MAX_NOTES_LEN = 500


def _clean_list(v: Any) -> list[str]:
    if not isinstance(v, (list, tuple)):
        return []
    out: list[str] = []
    for item in v:
        if isinstance(item, str) and item.strip():
            s = item.strip()[:MAX_TAG_LEN]
            if s.lower() not in {x.lower() for x in out}:
                out.append(s)
    return out[:MAX_TAGS]


def _clean_text(v: Any) -> str | None:
    return v.strip()[:MAX_NOTES_LEN] or None if isinstance(v, str) else None


def clean_preferences(p: Any) -> dict:
    p = p if isinstance(p, dict) else {}
    health = p.get("health") if isinstance(p.get("health"), dict) else {}
    out: dict[str, Any] = {"tags": _clean_list(p.get("tags")), "notes": _clean_text(p.get("notes"))}
    allergies = _clean_list(health.get("allergies"))
    h_notes = _clean_text(health.get("notes"))
    if allergies or h_notes:
        out["health"] = {"allergies": allergies, "notes": h_notes}
    return out


def merge_preferences(old: Any, new: Any) -> dict:
    """Tags and allergies accumulate across stays; notes are replaced when
    new ones are given. Nothing is dropped by a capture that omits a field."""
    a, b = clean_preferences(old), clean_preferences(new)
    ah, bh = a.get("health") or {}, b.get("health") or {}
    return clean_preferences(
        {
            "tags": a["tags"] + b["tags"],
            "notes": b["notes"] or a["notes"],
            "health": {
                "allergies": (ah.get("allergies") or []) + (bh.get("allergies") or []),
                "notes": bh.get("notes") or ah.get("notes"),
            },
        }
    )


# ─── Profiles ───

PROFILE_COLS = (
    "id, hotel_slug, phone_hmac, email_hmac, first_name_enc, language, preferences, stays_count, first_seen, "
    "last_seen, vip, marketing_consent_id, profile_consent_id, created_at, updated_at"
)


def get_profile(cur: Any, hotel_slug: str, profile_id: int) -> dict | None:
    cur.execute(f"SELECT {PROFILE_COLS} FROM gst_profiles WHERE hotel_slug = %s AND id = %s", (hotel_slug, profile_id))
    row = cur.fetchone()
    return dict(row) if row else None


def find_profile(cur: Any, hotel_slug: str, identities: Iterable[bytes | None]) -> dict | None:
    hs = [bytes(h) for h in identities if h]
    if not hs:
        return None
    cur.execute(
        f"""
        SELECT {PROFILE_COLS} FROM gst_profiles
        WHERE hotel_slug = %s AND (phone_hmac = ANY(%s) OR email_hmac = ANY(%s))
        ORDER BY id LIMIT 1
        """,
        (hotel_slug, hs, hs),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def identities_of(profile: dict) -> list[bytes]:
    return [bytes(h) for h in (profile.get("phone_hmac"), profile.get("email_hmac")) if h]


def _consent_id(cur: Any, hotel_slug: str, identities: list[bytes], purpose: str) -> int | None:
    for h in identities:
        row = consent.latest(cur, hotel_slug, ref(h), purpose)
        if row and row["granted"]:
            return int(row["id"])
    return None


def _hmac_free(cur: Any, hotel_slug: str, col: str, h: bytes, profile_id: int) -> bool:
    cur.execute(f"SELECT 1 FROM gst_profiles WHERE hotel_slug = %s AND {col} = %s AND id <> %s", (hotel_slug, h, profile_id))
    return cur.fetchone() is None


def upsert_profile(
    cur: Any,
    hotel_slug: str,
    *,
    phone_hmac: bytes | None = None,
    email_hmac: bytes | None = None,
    first_name: str | None = None,
    language: str | None = None,
    preferences: dict | None = None,
    vip: bool | None = None,
    actor: Any = None,
) -> dict | None:
    """Create or refresh the profile behind these identities, or None when
    no identity carries both profile consents (then nothing is written).

    An identity that already belongs to another profile is not moved: two
    profiles are merged by a person, never by a capture."""
    ids = [bytes(h) for h in (phone_hmac, email_hmac) if h]
    consented = [h for h in ids if profile_allowed(cur, hotel_slug, h)]
    if not consented:
        return None
    existing = find_profile(cur, hotel_slug, ids)
    first_enc = privacy.seal(first_name.strip()[:80]) if first_name and first_name.strip() else None
    profile_cid = _consent_id(cur, hotel_slug, consented, "profile_memory")
    if existing is None:
        cur.execute(
            f"""
            INSERT INTO gst_profiles (hotel_slug, phone_hmac, email_hmac, first_name_enc, language, preferences, vip,
                                      profile_consent_id, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING {PROFILE_COLS}
            """,
            (hotel_slug, phone_hmac, email_hmac, first_enc, language, Json(clean_preferences(preferences)),
             bool(vip), profile_cid, clock.now(), clock.now()),
        )
        row = dict(cur.fetchone())
        audit(cur, actor, "guests.profile.create", "gst_profile", row["id"], hotel_slug=hotel_slug,
              after={"has_phone": bool(phone_hmac), "has_email": bool(email_hmac)})
        return row
    pid = existing["id"]
    phone = existing["phone_hmac"] or (phone_hmac if phone_hmac and _hmac_free(cur, hotel_slug, "phone_hmac", phone_hmac, pid) else None)
    email = existing["email_hmac"] or (email_hmac if email_hmac and _hmac_free(cur, hotel_slug, "email_hmac", email_hmac, pid) else None)
    cur.execute(
        f"""
        UPDATE gst_profiles SET phone_hmac = %s, email_hmac = %s, first_name_enc = COALESCE(%s, first_name_enc),
            language = COALESCE(%s, language), preferences = %s, vip = COALESCE(%s, vip),
            profile_consent_id = COALESCE(%s, profile_consent_id), updated_at = %s
        WHERE id = %s RETURNING {PROFILE_COLS}
        """,
        (phone, email, first_enc, language, Json(merge_preferences(existing["preferences"], preferences)),
         vip, profile_cid, clock.now(), pid),
    )
    return dict(cur.fetchone())


def update_profile(cur: Any, hotel_slug: str, profile_id: int, changes: dict, *, actor: Any = None) -> dict:
    """A console edit: preferences are REPLACED (the person saw them all),
    unlike a capture, which merges."""
    row = get_profile(cur, hotel_slug, profile_id)
    if row is None:
        raise ClefsError(404, "Guest profile not found", "profile_not_found")
    sets: list[str] = []
    params: list[Any] = []
    if "preferences" in changes:
        sets.append("preferences = %s")
        params.append(Json(clean_preferences(changes["preferences"])))
    if "first_name" in changes:
        name = (changes["first_name"] or "").strip()
        sets.append("first_name_enc = %s")
        params.append(privacy.seal(name[:80]) if name else None)
    for col in ("language", "vip"):
        if col in changes and changes[col] is not None:
            sets.append(f"{col} = %s")
            params.append(changes[col])
    if not sets:
        return row
    cur.execute(
        f"UPDATE gst_profiles SET {', '.join(sets)}, updated_at = %s WHERE id = %s RETURNING {PROFILE_COLS}",
        (*params, clock.now(), profile_id),
    )
    after = dict(cur.fetchone())
    # Field names only: the audit log must not become a copy of health data.
    audit(cur, actor, "guests.profile.update", "gst_profile", profile_id, hotel_slug=hotel_slug,
          after={"fields": sorted(k for k in changes if k in ("preferences", "first_name", "language", "vip"))})
    return after


def forget_profile(cur: Any, hotel_slug: str, profile_id: int, *, source: str, actor: Any = None) -> bool:
    """The guest asked to be forgotten (or withdrew profile_memory): record
    the withdrawal on every identity, then delete the row. Stays unlink
    (FK ON DELETE SET NULL) and direct codes lose their owner."""
    row = get_profile(cur, hotel_slug, profile_id)
    if row is None:
        return False
    for h in identities_of(row):
        if granted(cur, hotel_slug, h, "profile_memory"):
            consent.withdraw(cur, hotel_slug, ref(h), "profile_memory", source=source, actor=actor)
    cur.execute("DELETE FROM gst_profiles WHERE id = %s", (profile_id,))
    audit(cur, actor, "guests.profile.forget", "gst_profile", profile_id, hotel_slug=hotel_slug, after={"source": source})
    return True


def forget_identity(cur: Any, hotel_slug: str, identity: bytes, *, source: str, actor: Any = None) -> int:
    """Delete every profile keyed by this identity (profile_memory withdrawn)."""
    cur.execute(
        "SELECT id FROM gst_profiles WHERE hotel_slug = %s AND (phone_hmac = %s OR email_hmac = %s)",
        (hotel_slug, identity, identity),
    )
    ids = [r["id"] for r in cur.fetchall()]
    return sum(1 for pid in ids if forget_profile(cur, hotel_slug, pid, source=source, actor=actor))


# A stay counts toward a profile unless it never happened.
COUNTED_STAY = "(s.status IS NULL OR s.status NOT IN ('CANCELLED', 'NO_SHOW'))"


def refresh_stats(cur: Any, profile_id: int) -> None:
    cur.execute(
        f"""
        UPDATE gst_profiles p SET stays_count = x.n, first_seen = x.f, last_seen = x.l, updated_at = %s
        FROM (SELECT COUNT(*) AS n, MIN(s.check_in) AS f, MAX(COALESCE(s.check_out, s.check_in)) AS l
              FROM gst_stays s WHERE s.guest_profile_id = %s AND {COUNTED_STAY}) x
        WHERE p.id = %s
        """,
        (clock.now(), profile_id, profile_id),
    )


def link_stay(cur: Any, hotel_slug: str, stay_id: int, profile_id: int) -> None:
    cur.execute(
        "UPDATE gst_stays SET guest_profile_id = %s, updated_at = %s WHERE hotel_slug = %s AND id = %s",
        (profile_id, clock.now(), hotel_slug, stay_id),
    )
    refresh_stats(cur, profile_id)


# ─── Serializers (always masked before leaving the server) ───

PROFILE_FIELD_CLASSES = {
    "first_name": pii.GUEST_NAME,
    "preferences.health": pii.HEALTH,
    "contacts.value": pii.GUEST_CONTACT,
}


def profile_contacts(cur: Any, hotel_slug: str, profile: dict) -> list[dict]:
    """The guest's own captured values (sealed on guest_direct points) for
    display; PMS-held values stay in core and are not shown here."""
    ids = identities_of(profile)
    if not ids:
        return []
    cur.execute(
        """
        SELECT DISTINCT ON (value_hmac) kind, value_enc FROM gst_contact_points
        WHERE hotel_slug = %s AND value_hmac = ANY(%s) AND provenance = 'guest_direct' AND value_enc IS NOT NULL
        ORDER BY value_hmac, id DESC
        """,
        (hotel_slug, ids),
    )
    out = []
    for r in cur.fetchall():
        try:
            out.append({"kind": r["kind"], "value": privacy.unseal(r["value_enc"])})
        except Exception:  # a rotated key must not take the console down
            log.warning("could not unseal a guest contact point")
    return out


def serialize_profile(cur: Any, hotel_slug: str, row: dict) -> dict:
    """Unmasked API shape. Callers MUST pass it through mask_profile."""
    try:
        first = privacy.unseal(row.get("first_name_enc"))
    except Exception:
        first = None
    prefs = clean_preferences(row.get("preferences"))
    prefs.setdefault("health", None)
    return {
        "id": row["id"],
        "first_name": first,
        "language": row.get("language"),
        "preferences": prefs,
        "stays_count": row.get("stays_count") or 0,
        "first_seen": row.get("first_seen"),
        "last_seen": row.get("last_seen"),
        "vip": bool(row.get("vip")),
        "returning": (row.get("stays_count") or 0) >= 2,
        "has_phone": bool(row.get("phone_hmac")),
        "has_email": bool(row.get("email_hmac")),
        "contacts": profile_contacts(cur, hotel_slug, row),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def mask_profile(obj: Any, ctx: Any) -> Any:
    return pii.mask_for_ctx(obj, ctx, field_classes=PROFILE_FIELD_CLASSES, department_field=None)

# ─── Direct codes ───
# 03's gst_direct_codes, amended by 04 3.2: a J8 code needs no profile and is
# unique per plan. No 0/O/1/I/L so a code read over the phone survives.

CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_COLS = "id, code, hotel_slug, guest_profile_id, discount_pct, valid_to, redeemed_at, redeemed_reservation_id, plan_id, offer_id, created_at"


def new_code(prefix: str | None = None) -> str:
    body = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
    p = re.sub(r"[^A-Z0-9]", "", (prefix or "").upper())[:8]
    return f"{p}-{body}" if p else body


def issue_direct_code(
    cur: Any,
    hotel_slug: str,
    *,
    valid_to: date | None,
    discount_pct: Decimal | float | None = None,
    guest_profile_id: int | None = None,
    plan_id: int | None = None,
    offer_id: int | None = None,
    prefix: str | None = None,
    actor: Any = None,
) -> dict:
    if discount_pct is not None and not (0 < Decimal(str(discount_pct)) <= 100):
        raise ClefsError(400, "discount_pct must be in (0, 100]", "invalid_discount")
    if plan_id is not None:
        cur.execute(f"SELECT {CODE_COLS} FROM gst_direct_codes WHERE plan_id = %s", (plan_id,))
        row = cur.fetchone()
        if row:
            return dict(row)  # one code per plan (04 spec 3.7): a retried send reuses it
    for _ in range(8):
        cur.execute(
            f"""
            INSERT INTO gst_direct_codes (code, hotel_slug, guest_profile_id, discount_pct, valid_to, plan_id, offer_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, code) DO NOTHING RETURNING {CODE_COLS}
            """,
            (new_code(prefix), hotel_slug, guest_profile_id, discount_pct, valid_to, plan_id, offer_id),
        )
        row = cur.fetchone()
        if row:
            audit(cur, actor, "guests.direct_code.issue", "gst_direct_code", row["id"], hotel_slug=hotel_slug,
                  after={"plan_id": plan_id, "profile": bool(guest_profile_id)})
            return dict(row)
    raise ClefsError(503, "Could not allocate a unique direct code", "code_exhausted")


def redeem_direct_code(cur: Any, hotel_slug: str, code: str, *, reservation_id: int, on: date) -> dict | None:
    """Mark a code used by a booking; None when unknown, used or expired
    (`on` is the booking's hotel-local date)."""
    cur.execute(
        f"""
        UPDATE gst_direct_codes SET redeemed_at = %s, redeemed_reservation_id = %s
        WHERE hotel_slug = %s AND code = %s AND redeemed_at IS NULL AND (valid_to IS NULL OR valid_to >= %s)
        RETURNING {CODE_COLS}
        """,
        (clock.now(), reservation_id, hotel_slug, (code or "").strip().upper(), on),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Returning badge ───
# "3rd stay · extra pillows · no nuts" (03 spec 2.7). Built from a stay
# linked to a consented profile with at least one earlier counted stay; the
# health part is masked per viewer BEFORE the label is composed, so a role
# that may not see allergies never gets them through the label either.

BADGE_FIELD_CLASSES = {"first_name": pii.GUEST_NAME, "health": pii.HEALTH}


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def stay_number(cur: Any, stay: dict) -> int:
    """Which counted stay of its profile this one is (1 = first)."""
    cur.execute(
        f"""
        SELECT COUNT(*) AS n FROM gst_stays s
        WHERE s.guest_profile_id = %s AND {COUNTED_STAY}
          AND (s.check_in < %s OR (s.check_in = %s AND s.id <= %s) OR s.check_in IS NULL)
        """,
        (stay["guest_profile_id"], stay.get("check_in"), stay.get("check_in"), stay["id"]),
    )
    return int(cur.fetchone()["n"])


def badge_for_stay(cur: Any, hotel_slug: str, stay: dict) -> dict | None:
    """Unmasked badge data, or None when the stay is not a returning one."""
    if not stay.get("guest_profile_id"):
        return None
    profile = get_profile(cur, hotel_slug, stay["guest_profile_id"])
    if profile is None:
        return None
    n = stay_number(cur, stay)
    if n < 2:
        return None
    prefs = clean_preferences(profile.get("preferences"))
    try:
        first = privacy.unseal(profile.get("first_name_enc"))
    except Exception:
        first = None
    return {
        "stay_id": stay["id"],
        "reservation_id": stay.get("reservation_id"),
        "room": stay.get("room"),
        "check_in": stay.get("check_in"),
        "check_out": stay.get("check_out"),
        "profile_id": profile["id"],
        "stay_number": n,
        "vip": bool(profile.get("vip")),
        "first_name": first,
        "tags": prefs["tags"][:3],
        "health": prefs.get("health"),
    }


def badge_label(b: dict) -> str:
    parts = [f"{ordinal(int(b['stay_number']))} stay"] + list(b.get("tags") or [])[:2]
    health = b.get("health") or {}
    parts += [f"no {a.lower()}" for a in (health.get("allergies") or [])[:2]]
    return " · ".join(parts)


def mask_badge(badge: dict, role: str | None, departments: Iterable[str] = ()) -> dict:
    out = pii.mask(badge, role, field_classes=BADGE_FIELD_CLASSES, departments=tuple(departments), department_field=None)
    out["label"] = badge_label(out)
    return out
