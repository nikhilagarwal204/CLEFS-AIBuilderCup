"""
Guests console routes (03 spec 4.2): profiles (masked), returning arrivals,
consent state, the GuestModal identity capture, the registration-card
capture (04 spec 3.10), opt-outs, direct codes and the rebooking pipeline.

Every path lives under /hotels/{slug}/guests/ (Canon 13) and needs a member
permission: none of these routes is open to legacy visitors, because every
one of them carries guest identity. Every guest-bearing response is passed
through core.pii masking for the caller's role before it leaves (03 5.2).
The legacy roster routes (/hotels/{slug}/guests, PUT/DELETE
/hotels/{slug}/guests/{room_number}) stay in legacy.py; the paths here have
two or more segments, or are POST/GET on a fixed word, so none shadows them.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm, require_perm_or_device
from clefs.core import consent, pii, privacy
from clefs.core.errors import ClefsError, forbidden
from clefs.core.time import local_date
from clefs.modules.guests import contacts, identity, linking, stays_port
from clefs.modules.guests import settings as gsettings

router = APIRouter(prefix="/hotels/{slug}/guests", tags=["guests"])

VIEW = require_perm("guests.view")
MANAGE = require_perm("guests.manage")

# ─── Bodies ───


class HealthIn(BaseModel):
    allergies: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=500)


class PreferencesIn(BaseModel):
    tags: list[str] = Field(default_factory=list, max_length=20)
    notes: str | None = Field(default=None, max_length=500)
    health: HealthIn | None = None


class ConsentsIn(BaseModel):
    transfer_abroad: bool = False
    profile_memory: bool = False
    whatsapp_updates: bool = False
    marketing_whatsapp: bool = False
    marketing_email: bool = False


class IdentityIn(BaseModel):
    """The GuestModal's contact block."""

    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    first_name: str | None = Field(default=None, max_length=80)
    consents: ConsentsIn
    consent_language: Literal["en", "ar", "ja", "ko", "th"] = "en"  # identity.CONSENT_LANGUAGES
    preferences: PreferencesIn | None = None
    check_in: date | None = None
    check_out: date | None = None


class ProfilePatch(BaseModel):
    first_name: str | None = Field(default=None, max_length=80)
    language: Literal["en", "ar"] | None = None
    vip: bool | None = None
    preferences: PreferencesIn | None = None


class ContactRef(BaseModel):
    kind: Literal["phone", "email"]
    value: str = Field(min_length=3, max_length=320)


class OptOutIn(ContactRef):
    scope: Literal["all", "marketing"] = "all"


class CodeIn(BaseModel):
    profile_id: int | None = None
    discount_pct: Decimal | None = Field(default=None, gt=0, le=100)
    valid_to: date
    prefix: str | None = Field(default=None, max_length=8)


# ─── Helpers ───


def _today(cur: Any, ctx: HotelCtx) -> date:
    return local_date(ctx.hotel)


def _profile_out(cur: Any, ctx: HotelCtx, row: dict) -> dict:
    return identity.mask_profile(identity.serialize_profile(cur, ctx.hotel.slug, row), ctx)


def _latest_consents(cur: Any, slug: str, purposes: tuple[str, ...]) -> dict[str, int]:
    cur.execute(
        """
        SELECT purpose, COUNT(*) AS n FROM (
            SELECT DISTINCT ON (subject_ref, purpose) purpose, granted FROM consents
            WHERE hotel_slug = %s AND purpose = ANY(%s)
            ORDER BY subject_ref, purpose, created_at DESC, id DESC
        ) x WHERE granted GROUP BY purpose
        """,
        (slug, list(purposes)),
    )
    got = {r["purpose"]: int(r["n"]) for r in cur.fetchall()}
    return {p: got.get(p, 0) for p in purposes}


# ─── Overview ───


@router.get("/overview")
def overview(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        cur.execute(
            """
            SELECT COUNT(*) AS profiles, COUNT(*) FILTER (WHERE stays_count >= 2) AS returning,
                   COUNT(*) FILTER (WHERE vip) AS vip FROM gst_profiles WHERE hotel_slug = %s
            """,
            (slug,),
        )
        p = dict(cur.fetchone())
        cur.execute(
            "SELECT COUNT(*) AS issued, COUNT(redeemed_at) AS redeemed FROM gst_direct_codes WHERE hotel_slug = %s",
            (slug,),
        )
        codes = dict(cur.fetchone())
        cur.execute("SELECT COUNT(DISTINCT value_hmac) AS n FROM gst_suppressions WHERE hotel_slug = %s", (slug,))
        suppressed = int(cur.fetchone()["n"])
        today = _today(cur, ctx)
        arrivals = [s for s in linking.returning_stays_on(cur, slug, today) if identity.stay_number(cur, s) >= 2]
        return {
            "profiles": int(p["profiles"]),
            "returning_profiles": int(p["returning"]),
            "vip_profiles": int(p["vip"]),
            "returning_arrivals_today": len(arrivals),
            "consents": _latest_consents(
                cur, slug, ("profile_memory", "marketing_whatsapp", "marketing_email", "whatsapp_updates", "transfer_abroad")
            ),
            "suppressed_contacts": suppressed,
            "direct_codes": {"issued": int(codes["issued"]), "redeemed": int(codes["redeemed"])},
            "returning_badge": gsettings.load(cur, slug).returning_badge,
            "day": today,
        }


# ─── Profiles ───


@router.get("/profiles")
def list_profiles(
    slug: str,
    ctx: HotelCtx = Depends(VIEW),
    contact: str | None = Query(default=None, max_length=320, description="exact phone or email; hashed, never stored"),
    vip: bool | None = None,
    returning: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Newest first. Names are sealed, so search is by exact contact only."""
    where, params = ["hotel_slug = %s"], [slug]
    with db.db(readonly=True) as cur:
        if contact:
            kind = "email" if "@" in contact else "phone"
            try:
                h = identity.hmac_for(cur, slug, kind, contact)
            except ClefsError:
                return {"items": [], "total": 0}
            where.append("(phone_hmac = %s OR email_hmac = %s)")
            params += [h, h]
        if vip is not None:
            where.append("vip = %s")
            params.append(vip)
        if returning is not None:
            where.append("stays_count >= 2" if returning else "stays_count < 2")
        sql_where = " AND ".join(where)
        cur.execute(f"SELECT COUNT(*) AS n FROM gst_profiles WHERE {sql_where}", params)
        total = int(cur.fetchone()["n"])
        cur.execute(
            f"SELECT {identity.PROFILE_COLS} FROM gst_profiles WHERE {sql_where} ORDER BY last_seen DESC NULLS LAST, id DESC "
            "LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return {"items": [_profile_out(cur, ctx, r) for r in rows], "total": total}


def _consent_view(cur: Any, slug: str, ids: list[bytes]) -> list[dict]:
    """Per identity: which purposes stand, and whether it is suppressed.
    The identity is shown as its kind and a short hash prefix, never raw."""
    out = []
    for h in ids:
        state = consent.consent_state(cur, slug, identity.ref(h))
        out.append(
            {
                "ref": identity.ref(h)[:12],
                "purposes": {p: {"granted": s["granted"], "at": s["at"], "text_version": s["text_version"], "source": s["source"]}
                             for p, s in state.items()},
                "suppressed": contacts.is_suppressed(cur, slug, [h], marketing=False),
                "marketing_suppressed": contacts.is_suppressed(cur, slug, [h], marketing=True),
            }
        )
    return out


@router.get("/profiles/{profile_id}")
def get_profile(slug: str, profile_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        row = identity.get_profile(cur, slug, profile_id)
        if row is None:
            raise ClefsError(404, "Guest profile not found", "profile_not_found")
        out = _profile_out(cur, ctx, row)
        cur.execute(
            """
            SELECT id, reservation_id, origin, room, check_in, check_out, source, status FROM gst_stays
            WHERE hotel_slug = %s AND guest_profile_id = %s ORDER BY check_in DESC NULLS LAST, id DESC LIMIT 50
            """,
            (slug, profile_id),
        )
        out["stays"] = [dict(r) for r in cur.fetchall()]
        out["consents"] = _consent_view(cur, slug, identity.identities_of(row))
        cur.execute(
            f"SELECT {identity.CODE_COLS} FROM gst_direct_codes WHERE hotel_slug = %s AND guest_profile_id = %s ORDER BY id DESC",
            (slug, profile_id),
        )
        out["direct_codes"] = [dict(r) for r in cur.fetchall()]
        return out


@router.patch("/profiles/{profile_id}")
def patch_profile(slug: str, profile_id: int, body: ProfilePatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "preferences" in changes and body.preferences is not None:
        changes["preferences"] = body.preferences.model_dump()
        if pii.policy_for(pii.HEALTH, pii.role_for_ctx(ctx), departments=ctx.departments) != pii.FULL:
            # A role that cannot SEE allergies must not overwrite them blind:
            # keep the stored health block.
            with db.db(readonly=True) as cur:
                row = identity.get_profile(cur, slug, profile_id)
            stored = identity.clean_preferences((row or {}).get("preferences")).get("health")
            changes["preferences"]["health"] = stored
    with db.db() as cur:
        row = identity.update_profile(cur, slug, profile_id, changes, actor=ctx)
        return _profile_out(cur, ctx, row)


@router.delete("/profiles/{profile_id}")
def forget_profile(slug: str, profile_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """The guest withdrew profile_memory: record it and delete the profile."""
    with db.db() as cur:
        if not identity.forget_profile(cur, slug, profile_id, source="console", actor=ctx):
            raise ClefsError(404, "Guest profile not found", "profile_not_found")
    return {"ok": True}


@router.post("/rooms/{room}/identity")
def capture_identity(slug: str, room: str, body: IdentityIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """The GuestModal contact block: hashed at once, never kept raw."""
    from clefs.core import rooms as rooms_mod

    if not rooms_mod.valid_room(room):
        raise ClefsError(400, "Invalid room", "invalid_room")
    with db.db() as cur:
        out = linking.capture_room_identity(
            cur, slug, room, phone=body.phone, email=body.email, consents=body.consents.model_dump(),
            consent_language=body.consent_language, first_name=body.first_name,
            preferences=body.preferences.model_dump() if body.preferences else None,
            check_in=body.check_in, check_out=body.check_out, actor=ctx,
        )
        prof = identity.get_profile(cur, slug, out["profile_id"]) if out["profile_id"] else None
        out["profile"] = _profile_out(cur, ctx, prof) if prof else None
        out["contact"] = pii.mask_for_ctx(out["contact"], ctx, field_classes={"phone": pii.GUEST_CONTACT, "email": pii.GUEST_CONTACT})
        return out


# ─── Returning arrivals ───


@router.get("/arrivals")
def arrivals(slug: str, ctx: HotelCtx = Depends(VIEW), day: date | None = None) -> dict:
    """Returning guests arriving on `day` (hotel-local, default today), as
    masked badges ("3rd stay · extra pillows · no nuts")."""
    from clefs.modules.guests import api

    with db.db(readonly=True) as cur:
        d = day or _today(cur, ctx)
        items = api.returning_arrivals(cur, slug, day=d, role=pii.role_for_ctx(ctx), departments=ctx.departments)
        return {"day": d, "items": items}


# ─── Consent state and opt-outs ───


@router.post("/consents/lookup")
def consent_lookup(slug: str, body: ContactRef, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Consent state of one phone/email (POST so the value never sits in a
    URL or an access log). Hashed on arrival; nothing is stored."""
    with db.db(readonly=True) as cur:
        h = identity.hmac_for(cur, slug, body.kind, body.value)
        points = [
            {"id": p["id"], "provenance": p["provenance"], "status": p["status"], "capture_source": p["capture_source"],
             "reservation_id": p["reservation_id"], "usable_until": p["usable_until"], "created_at": p["created_at"]}
            for p in contacts.points_for(cur, slug, [h], active_only=False)
        ]
        prof = identity.find_profile(cur, slug, [h])
        return {
            **_consent_view(cur, slug, [h])[0],
            "points": points,
            "profile_id": prof["id"] if prof else None,
        }


@router.post("/opt-out")
def opt_out(slug: str, body: OptOutIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """A guest asked the desk to stop messages (scope all or marketing)."""
    with db.db() as cur:
        h = identity.hmac_for(cur, slug, body.kind, body.value)
        new = contacts.suppress(cur, slug, h, scope=body.scope, reason="manual", source="console", actor=ctx)
    return {"ok": True, "created": new}


@router.post("/forget")
def forget(slug: str, body: ContactRef, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """"Forget me": withdraw every consent, delete the profile, purge the
    guest's own points and their evidence files. STOP suppressions stay."""
    with db.db() as cur:
        h = identity.hmac_for(cur, slug, body.kind, body.value)
        return {"ok": True, **contacts.forget_contact(cur, slug, h, source="console", actor=ctx)}


# ─── Direct codes ───


@router.get("/codes")
def list_codes(slug: str, ctx: HotelCtx = Depends(VIEW), limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    with db.db(readonly=True) as cur:
        cur.execute(
            f"SELECT {identity.CODE_COLS} FROM gst_direct_codes WHERE hotel_slug = %s ORDER BY id DESC LIMIT %s",
            (slug, limit),
        )
        return [dict(r) for r in cur.fetchall()]


@router.post("/codes")
def issue_code(slug: str, body: CodeIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Issue a direct booking code (nothing is sent: sending is J8's job,
    under marketing consent and the 09:00-18:00 window)."""
    with db.db() as cur:
        if body.profile_id is not None and identity.get_profile(cur, slug, body.profile_id) is None:
            raise ClefsError(404, "Guest profile not found", "profile_not_found")
        if body.valid_to < _today(cur, ctx):
            raise ClefsError(400, "valid_to is in the past", "invalid_valid_to")
        return identity.issue_direct_code(
            cur, slug, valid_to=body.valid_to, discount_pct=body.discount_pct, guest_profile_id=body.profile_id,
            prefix=body.prefix, actor=ctx,
        )


# ─── Rebooking pipeline ───


def _stay_identities(cur: Any, slug: str, stay: dict) -> list[bytes]:
    """The stay's own direct identities (never OTA-provided ones)."""
    ids = [bytes(h) for h in (stay.get("contact_hmacs") or [])]
    if stay.get("reservation_id") is not None:
        cur.execute(
            """
            SELECT DISTINCT value_hmac FROM gst_contact_points
            WHERE hotel_slug = %s AND reservation_id = %s AND provenance IN ('guest_direct', 'pms_direct')
              AND value_hmac IS NOT NULL
            """,
            (slug, stay["reservation_id"]),
        )
        ids += [bytes(r["value_hmac"]) for r in cur.fetchall()]
    if stay.get("guest_profile_id"):
        prof = identity.get_profile(cur, slug, stay["guest_profile_id"])
        ids += identity.identities_of(prof) if prof else []
    return list(dict.fromkeys(ids))


@router.get("/rebooking")
def rebooking(
    slug: str, ctx: HotelCtx = Depends(VIEW), days: int = Query(default=70, ge=7, le=400),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    """Stays that checked out in the last `days` days and where each stands
    for a rebooking offer (J8 at check-out + 30 and + 60 days): marketing
    consent per channel, suppression, J8 plans and codes. OTA stays with no
    direct identity show `eligible: false`: they are never marketed to."""
    with db.db(readonly=True) as cur:
        today = _today(cur, ctx)
        cur.execute(
            """
            SELECT * FROM gst_stays
            WHERE hotel_slug = %s AND check_out >= %s AND check_out < %s
              AND (status IS NULL OR status = 'CHECKED_OUT')
            ORDER BY check_out DESC, id DESC LIMIT %s
            """,
            (slug, today - timedelta(days=days), today, limit),
        )
        stays = [dict(r) for r in cur.fetchall()]
        items = []
        for s in stays:
            ids = _stay_identities(cur, slug, s)
            suppressed = contacts.is_suppressed(cur, slug, ids, marketing=True)
            channels = {
                ch: bool(ids) and not suppressed and any(identity.granted(cur, slug, h, identity.MARKETING_PURPOSE[ch]) for h in ids)
                for ch in ("whatsapp", "email")
            }
            plans: list[dict] = []
            if s.get("reservation_id") is not None:
                cur.execute(
                    "SELECT seq, status, due_at, skip_reason FROM gst_journey_plans WHERE hotel_slug = %s AND reservation_id = %s "
                    "AND step = 'j8' AND status <> 'cancelled' ORDER BY seq",
                    (slug, s["reservation_id"]),
                )
                plans = [dict(r) for r in cur.fetchall()]
            prof = identity.get_profile(cur, slug, s["guest_profile_id"]) if s.get("guest_profile_id") else None
            items.append(
                {
                    "stay_id": s["id"], "reservation_id": s.get("reservation_id"), "room": s.get("room"),
                    "check_out": s.get("check_out"), "days_since": (today - s["check_out"]).days,
                    "source": s.get("source"), "ota": stays_port.is_ota(s.get("source")),
                    "profile": _profile_out(cur, ctx, prof) if prof else None,
                    "marketing": channels, "suppressed": suppressed,
                    "eligible": any(channels.values()), "j8": plans,
                }
            )
        return {"day": today, "items": items}


# ─── Registration-card capture (04 spec 3.4 / 3.10) ───

FRONT_OFFICE = frozenset({"frontoffice", "frontdesk", "reception", "fo", "fd", "guestrelations"})


class EvidenceIn(BaseModel):
    kind: Literal["card_crop", "staff_attestation"]
    file_id: str | None = Field(default=None, max_length=64)
    signature_file_id: str | None = Field(default=None, max_length=64)


class CaptureIn(ContactRef):
    reservation_id: int | None = None
    room: str | None = Field(default=None, max_length=16)
    consents: ConsentsIn
    # The wording versions the card showed; omitted = the current platform
    # or hotel wording in `language` (what the printed card carries).
    consent_text_versions: dict[str, str] | None = None
    language: Literal["en", "ar", "ja", "ko", "th"] = "en"  # identity.CONSENT_LANGUAGES
    evidence: EvidenceIn
    first_name: str | None = Field(default=None, max_length=80)
    preferences: PreferencesIn | None = None


def _is_front_office(departments: Any) -> bool:
    return any("".join(ch for ch in str(d).lower() if ch.isalnum()) in FRONT_OFFICE for d in departments or ())


@router.post("/contacts", status_code=201)
def capture_contact(slug: str, body: CaptureIn, ctx: HotelCtx = Depends(require_perm_or_device("guests.contacts.capture"))) -> dict:
    """The guest's own phone/email from the registration card, with its
    consents and minimal evidence. A paired device must belong to Front
    Office: every paired device holds the `device` pseudo-role, and a
    housekeeping tablet must not capture guest contacts."""
    if getattr(ctx.principal, "kind", None) == "device" and not _is_front_office(ctx.principal.device_departments):
        raise forbidden("Only a Front Office device can capture guest contacts")
    consents = body.consents.model_dump()
    with db.db() as cur:
        versions = dict(body.consent_text_versions or {})
        for p, g in consents.items():
            if g and not versions.get(p):
                v = identity.text_version(cur, slug, p, body.language)
                if v:
                    versions[p] = v
        evidence = {**body.evidence.model_dump(exclude_none=True), "language": body.language}
        if body.first_name:
            evidence["first_name"] = body.first_name
        if body.preferences is not None:
            evidence["preferences"] = body.preferences.model_dump()
        point_id = contacts.record_own_contact(
            cur, slug, reservation_id=body.reservation_id, room=body.room, kind=body.kind, value=body.value,
            capture_source="registration_card", consents=consents, consent_text_versions=versions,
            evidence=evidence, actor=ctx,
        )
        h = identity.hmac_for(cur, slug, body.kind, body.value)
        prof = identity.find_profile(cur, slug, [h]) if consents.get("profile_memory") else None
        # Echo only a masked form, and nothing to a viewer who may not see
        # contacts at all (devices: the matrix says none).
        may_see = pii.policy_for(pii.GUEST_CONTACT, pii.role_for_ctx(ctx), departments=ctx.departments) != pii.NONE
        shown = (privacy.mask_email(body.value) if body.kind == "email" else privacy.mask_phone(body.value)) if may_see else None
        return {
            "point_id": point_id,
            "contact": {"kind": body.kind, "value": shown},
            "profile_id": prof["id"] if prof else None,
            "consents": {p: g for p, g in consents.items()},
        }
