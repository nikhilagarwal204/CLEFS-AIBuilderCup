"""
Core hotel routes: public hotel card, console context, settings, module
settings, team and the room register.

GET /hotels/{slug} is the legacy public endpoint (guest pages and the concierge
prompt read it). It keeps its exact pre-refactor keys but no longer SELECT *s,
so settings added since (tax profile, WhatsApp ids, flags) never leak.
"""

from __future__ import annotations

import re
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2 import errors as pg_errors
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, field_validator

from clefs import db
from clefs.audit import audit
from clefs.auth import passwords, sessions
from clefs.auth import permissions as perms_mod
from clefs.auth.deps import HotelCtx, any_access_ctx, has_admin_powers, require_perm
from clefs.core import hotels as hotels_mod
from clefs.core import rooms as rooms_mod
from clefs.core.phone import normalize_phone
from clefs.core.errors import ClefsError
from clefs.registry import INSTALLED_MODULES
from clefs.settings import get_settings as get_app_settings

router = APIRouter(tags=["core"])

EMIRATES = ("dubai", "abu_dhabi", "sharjah", "ajman", "rak", "fujairah", "uaq")
STATUSES = ("active", "pilot", "demo", "archived")
LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")

# Filled by app.create_app from each loaded Module.settings_model.
SETTINGS_MODELS: dict[str, type[BaseModel]] = {}
LOADED_MODULES: list[str] = []


# ─── Public hotel card (legacy) ───


@router.get("/hotels/{slug}")
def get_hotel_public(slug: str) -> dict:
    with db.db() as cur:
        hotel = hotels_mod.find_hotel(cur, slug)
    if hotel is None:
        raise HTTPException(status_code=404, detail=f"Hotel '{slug}' not found")
    return hotel.project(hotels_mod.LEGACY_PUBLIC_FIELDS)


# ─── Console context ───


@router.get("/hotels/{slug}/context")
def hotel_context(slug: str, request: Request) -> dict:
    ctx = any_access_ctx(request, slug)
    permissions = ctx.permissions
    if ctx.role is None and not permissions and ctx.hotel.legacy_open_access:
        permissions = perms_mod.legacy_open_permissions()
    with db.db() as cur:
        modules_enabled = hotels_mod.enabled_modules_for(cur, slug, LOADED_MODULES)
    kind = ctx.principal.kind if ctx.principal.kind in ("user", "device") else "anonymous"
    return {
        "hotel": ctx.hotel.project(hotels_mod.CONTEXT_FIELDS),
        "modules_enabled": modules_enabled,
        "role": ctx.role,
        "permissions": sorted(permissions),
        "legacy_open_access": ctx.hotel.legacy_open_access,
        "principal_kind": kind,
    }


# ─── Settings ───


class HotelSettingsBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    city: str | None = None
    country: str | None = None
    emirate: str | None = None
    timezone: str | None = None
    currency: str | None = None
    locale: str | None = None
    guest_languages: list[str] | None = None
    weekend_days: list[int] | None = None
    brief_time_local: time | None = None
    business_day_cutoff: time | None = None
    star_rating: int | None = Field(default=None, ge=1, le=7)
    rooms_count: int | None = Field(default=None, ge=0, le=100000)
    category: str | None = None
    website: str | None = None
    assistant_name: str | None = Field(default=None, max_length=40)
    status: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    address: str | None = None
    tax_profile: dict[str, Any] | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_display_number: str | None = None
    front_desk_phone: str | None = None
    inbound_email_alias: str | None = None
    knowledge_base: str | None = None
    legacy_open_access: bool | None = None

    @field_validator("country")
    @classmethod
    def _country(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[A-Za-z]{2}", v):
            raise ValueError("country must be an ISO 3166 alpha-2 code")
        return v.upper() if v else v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str | None) -> str | None:
        if v is not None and not re.fullmatch(r"[A-Za-z]{3}", v):
            raise ValueError("currency must be an ISO 4217 code")
        return v.upper() if v else v

    @field_validator("emirate")
    @classmethod
    def _emirate(cls, v: str | None) -> str | None:
        if v is not None and v not in EMIRATES:
            raise ValueError(f"emirate must be one of {', '.join(EMIRATES)}")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                ZoneInfo(v)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError("unknown IANA timezone") from exc
        return v

    @field_validator("weekend_days")
    @classmethod
    def _weekend(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and (any(d < 1 or d > 7 for d in v) or len(set(v)) != len(v)):
            raise ValueError("weekend_days are distinct ISO weekdays 1-7")
        return sorted(v) if v is not None else v

    @field_validator("guest_languages")
    @classmethod
    def _langs(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and (not v or any(not LANG_RE.match(x) for x in v)):
            raise ValueError("guest_languages must be language codes like en, ar, zh-Hans")
        return v

    @field_validator("status")
    @classmethod
    def _status(cls, v: str | None) -> str | None:
        if v is not None and v not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        return v


def _settings_view(hotel: hotels_mod.Hotel) -> dict:
    return hotel.project(hotels_mod.SETTINGS_FIELDS)


@router.get("/hotels/{slug}/settings")
def get_settings_view(ctx: HotelCtx = Depends(require_perm("core.settings.view"))) -> dict:
    return _settings_view(ctx.hotel)


@router.patch("/hotels/{slug}/settings")
def patch_settings(body: HotelSettingsBody, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "legacy_open_access" in changes:
        # Anyone who manages the hotel may lock it down; only Clefs may reopen it.
        if changes["legacy_open_access"] and not has_admin_powers(ctx.principal):
            raise ClefsError(403, "Only Clefs can re-enable open access", "forbidden")
    for nullable_forbidden in ("name", "timezone", "currency", "country", "locale", "guest_languages", "weekend_days", "status"):
        if nullable_forbidden in changes and changes[nullable_forbidden] is None:
            raise ClefsError(400, f"{nullable_forbidden} cannot be empty", "invalid_field")
    if "tax_profile" in changes and changes["tax_profile"] is None:
        changes["tax_profile"] = {}
    if "whatsapp_phone_number_id" in changes:
        # Outbound sends use the one platform WA token with this id as the
        # sender, and inbound Meta webhooks route by it. A manager who typed
        # another hotel's (or the platform's) id could send as that number
        # and read its guests' messages, so only Clefs connects a number
        # (after checking it belongs to the hotel's WABA). Clearing your own
        # is always allowed; resending the current value is a no-op.
        new_id = (changes["whatsapp_phone_number_id"] or "").strip() or None
        changes["whatsapp_phone_number_id"] = new_id
        if new_id is not None and new_id != ctx.hotel.whatsapp_phone_number_id:
            if not has_admin_powers(ctx.principal):
                raise ClefsError(403, "Only Clefs can connect a WhatsApp number", "forbidden")
            if new_id == get_app_settings().WA_PLATFORM_PHONE_NUMBER_ID:
                raise ClefsError(409, "That is the Clefs platform number", "whatsapp_number_taken")
    with db.db() as cur:
        try:
            before, after = hotels_mod.update_hotel(cur, ctx.hotel.slug, changes)
        except pg_errors.UniqueViolation as exc:
            # Partial unique indexes on whatsapp_phone_number_id (0016) and
            # inbound_email_alias (0003): another hotel holds the value.
            taken = "whatsapp_number_taken" if "whatsapp" in str(exc) else "alias_taken"
            raise ClefsError(409, "Another hotel already uses this value", taken) from exc
        b, a = _settings_view(before), _settings_view(after)
        diff_keys = [k for k in a if a[k] != b[k]]
        if diff_keys:
            audit(
                cur,
                ctx,
                "core.settings.update",
                "hotel",
                ctx.hotel.slug,
                before={k: b[k] for k in diff_keys},
                after={k: a[k] for k in diff_keys},
            )
    return a


# ─── Module settings ───


class ModuleSettingsBody(BaseModel):
    enabled: bool | None = None
    settings: dict[str, Any] = {}


def _module_view(cur: Any, slug: str, module: str) -> dict:
    row = hotels_mod.module_settings_row(cur, slug, module)
    model = SETTINGS_MODELS.get(module)
    raw = (row or {}).get("settings") or {}
    if model is not None:
        settings = hotels_mod.get_module_settings(cur, slug, module, model).model_dump(mode="json")
        schema = model.model_json_schema()
    else:
        settings, schema = raw, None
    return {
        "module": module,
        "enabled": True if row is None else bool(row["enabled"]),
        "settings": settings,
        "schema": schema,
        "updated_at": (row or {}).get("updated_at"),
    }


def _check_module(module: str) -> None:
    if module not in INSTALLED_MODULES:
        raise ClefsError(404, f"Unknown module '{module}'", "module_not_found")


@router.get("/hotels/{slug}/module-settings/{module}")
def get_module_settings(module: str, ctx: HotelCtx = Depends(require_perm("core.settings.view"))) -> dict:
    _check_module(module)
    with db.db() as cur:
        return _module_view(cur, ctx.hotel.slug, module)


@router.put("/hotels/{slug}/module-settings/{module}")
def put_module_settings(
    module: str, body: ModuleSettingsBody, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))
) -> dict:
    _check_module(module)
    model = SETTINGS_MODELS.get(module)
    data: BaseModel | dict[str, Any] = body.settings
    if model is not None:
        try:
            data = model.model_validate(body.settings)
        except ValidationError as exc:
            # Same 422 shape as any FastAPI body error, located under
            # body.settings, so the settings form can point at the bad field.
            errors = exc.errors(include_url=False, include_context=False, include_input=False)
            raise RequestValidationError([{**e, "loc": ("body", "settings", *e["loc"])} for e in errors]) from exc
    with db.db() as cur:
        hotels_mod.set_module_settings(cur, ctx.hotel.slug, module, data, actor=ctx, enabled=body.enabled)
        return _module_view(cur, ctx.hotel.slug, module)


# ─── Team ───
# `users` is global: one identity (email, WhatsApp number) per person across
# every hotel. A hotel's managers own the MEMBERSHIP (role, scope, access) but
# never the identity:
#   - adding an email that already has an account creates an invitation the
#     person accepts from their own session (POST /auth/invites/{id}/accept);
#     the answer carries only what the caller typed, never the account's
#     stored name, phone or last sign-in, and an existing phone is never set;
#   - name and phone edits are refused (409 profile_shared) for anyone whose
#     account also belongs to another hotel. Otherwise a GM at one hotel could
#     rebind the WhatsApp number that receives another hotel's magic links
#     and approval buttons.
# Clefs admins (onboarding) and the person themselves may edit an identity.


class TeamMemberBody(BaseModel):
    email: str = Field(max_length=320)
    name: str = Field(min_length=1, max_length=200)
    phone: str | None = None
    role: str
    departments: list[str] = []
    floors: list[str] = []
    password: str | None = Field(default=None, max_length=512)


class TeamPatchBody(BaseModel):
    role: str | None = None
    departments: list[str] | None = None
    floors: list[str] | None = None
    is_active: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    phone: str | None = None


# A pending invitation: shown with what the inviter typed, never the account's own data.
_PENDING_SQL = "(m.invited_at IS NOT NULL AND m.accepted_at IS NULL)"


def _team_rows(cur: Any, slug: str, membership_id: int | None = None) -> list[dict]:
    cur.execute(
        f"""
        SELECT m.id,
               CASE WHEN {_PENDING_SQL} THEN NULL ELSE m.user_id END AS user_id,
               CASE WHEN {_PENDING_SQL} THEN COALESCE(m.invite_name, '') ELSE u.name END AS name,
               u.email,
               CASE WHEN {_PENDING_SQL} THEN NULL ELSE u.phone_e164 END AS phone,
               m.role, m.departments, m.floors, m.is_active,
               CASE WHEN {_PENDING_SQL} THEN NULL ELSE u.last_login_at END AS last_login_at,
               CASE WHEN {_PENDING_SQL} THEN 'invited' WHEN m.is_active THEN 'active' ELSE 'inactive' END AS status,
               m.invited_at
        FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND (%s::BIGINT IS NULL OR m.id = %s)
        ORDER BY m.is_active DESC, {_PENDING_SQL}, array_position(%s::TEXT[], m.role), name
        """,
        (slug, membership_id, membership_id, list(perms_mod.ROLES)),
    )
    return [dict(r) for r in cur.fetchall()]


def _member(cur: Any, slug: str, membership_id: int) -> dict | None:
    """The membership and its account, for decisions (never returned as-is)."""
    cur.execute(
        f"""
        SELECT m.id, m.user_id, m.role, m.is_active, {_PENDING_SQL} AS pending,
               u.name, u.phone_e164, u.is_clefs_admin
        FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND m.id = %s
        FOR UPDATE OF m
        """,
        (slug, membership_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _identity_editable(cur: Any, ctx: HotelCtx, member: dict, slug: str) -> bool:
    """May this hotel's manager change the account's name and phone? Only for
    an account that belongs to this hotel alone (no membership or invitation
    anywhere else) and is not a Clefs admin, unless the caller is a Clefs
    admin or the person themselves."""
    if has_admin_powers(ctx.principal) or member["user_id"] == ctx.principal.user_id:
        return True
    if member["is_clefs_admin"]:
        return False
    cur.execute("SELECT 1 FROM hotel_memberships WHERE user_id = %s AND hotel_slug <> %s LIMIT 1", (member["user_id"], slug))
    return cur.fetchone() is None


def _check_role(role: str) -> None:
    if role not in perms_mod.ROLES:
        raise ClefsError(400, f"role must be one of {', '.join(perms_mod.ROLES)}", "invalid_role")


def _may_manage_owners(ctx: HotelCtx) -> bool:
    return ctx.role == "owner" or has_admin_powers(ctx.principal)


def _active_owner_count(cur: Any, slug: str) -> int:
    cur.execute("SELECT COUNT(*) AS n FROM hotel_memberships WHERE hotel_slug = %s AND role = 'owner' AND is_active", (slug,))
    return int(cur.fetchone()["n"])


@router.get("/hotels/{slug}/team")
def list_team(ctx: HotelCtx = Depends(require_perm("core.team.manage"))) -> list[dict]:
    with db.db() as cur:
        return _team_rows(cur, ctx.hotel.slug)


@router.post("/hotels/{slug}/team", status_code=201)
def add_team_member(body: TeamMemberBody, ctx: HotelCtx = Depends(require_perm("core.team.manage"))) -> dict:
    """A new email creates the account and its membership (status active).
    An email that already has an account gets an invitation (status invited)
    unless the caller is a Clefs admin, or the person was a member here
    before (then the old membership is simply reactivated). Only a Clefs
    admin may send `password`: a manager's is refused (403
    password_not_allowed) and the new person signs in by magic link."""
    _check_role(body.role)
    if body.role == "owner" and not _may_manage_owners(ctx):
        raise ClefsError(403, "Only an owner can add another owner", "forbidden")
    email = body.email.strip().lower()
    if "@" not in email:
        raise ClefsError(400, "email looks invalid", "invalid_email")
    phone = normalize_phone(body.phone)
    pw_hash = None
    if body.password and not has_admin_powers(ctx.principal):
        # Accounts are global. A manager who chose the password of a new
        # account could sign in as that person once another hotel invites the
        # same address (up to owner there): a cross-tenant pre-hijack. The
        # person signs in by magic link and sets their own password instead;
        # only a Clefs admin, trusted on every hotel already, may seed one.
        raise ClefsError(
            403, "Only the person can set their password: they sign in with a magic link", "password_not_allowed"
        )
    if body.password:
        try:
            passwords.validate_password(body.password)
        except ValueError as exc:
            raise ClefsError(400, str(exc), "weak_password") from exc
        pw_hash = passwords.hash_password(body.password)
    slug = ctx.hotel.slug
    with db.db() as cur:
        cur.execute("SELECT id FROM users WHERE email = %s FOR UPDATE", (email,))
        user = cur.fetchone()
        if user is None:
            if phone:
                cur.execute("SELECT 1 FROM users WHERE phone_e164 = %s", (phone,))
                if cur.fetchone():
                    raise ClefsError(409, "Another user already has this phone number", "phone_taken")
            cur.execute(
                "INSERT INTO users (email, name, phone_e164, password_hash) VALUES (%s, %s, %s, %s) RETURNING id",
                (email, body.name.strip(), phone, pw_hash),
            )
            user_id = cur.fetchone()["id"]
            cur.execute(
                "INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (user_id, slug, body.role, body.departments, body.floors),
            )
            membership_id = cur.fetchone()["id"]
            row = _team_rows(cur, slug, membership_id)[0]
            audit(cur, ctx, "core.team.add", "hotel_membership", membership_id, after=row)
            return row

        # An existing account: its name, phone and password are never touched here.
        user_id = user["id"]
        cur.execute(
            f"SELECT m.id, m.is_active, {_PENDING_SQL} AS pending FROM hotel_memberships m "
            "WHERE m.user_id = %s AND m.hotel_slug = %s FOR UPDATE",
            (user_id, slug),
        )
        existing = cur.fetchone()
        if existing and existing["is_active"]:
            raise ClefsError(409, "This person is already on the team", "already_member")
        if has_admin_powers(ctx.principal) or (existing is not None and not existing["pending"]):
            if existing:
                cur.execute(
                    "UPDATE hotel_memberships SET role = %s, departments = %s, floors = %s, is_active = TRUE, updated_at = NOW() "
                    "WHERE id = %s RETURNING id",
                    (body.role, body.departments, body.floors, existing["id"]),
                )
            else:
                cur.execute(
                    "INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (user_id, slug, body.role, body.departments, body.floors),
                )
            membership_id = cur.fetchone()["id"]
            row = _team_rows(cur, slug, membership_id)[0]
            audit(cur, ctx, "core.team.add", "hotel_membership", membership_id, after=row)
            return row

        inviter = ctx.principal.user_id if ctx.principal.kind == "user" else None
        if existing:  # a pending invitation: refresh it
            cur.execute(
                "UPDATE hotel_memberships SET role = %s, departments = %s, floors = %s, invite_name = %s, "
                "invited_at = NOW(), invited_by = %s, updated_at = NOW() WHERE id = %s RETURNING id, invited_at",
                (body.role, body.departments, body.floors, body.name.strip(), inviter, existing["id"]),
            )
        else:
            cur.execute(
                "INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors, is_active, "
                "invited_at, invited_by, invite_name) VALUES (%s, %s, %s, %s, %s, FALSE, NOW(), %s, %s) "
                "RETURNING id, invited_at",
                (user_id, slug, body.role, body.departments, body.floors, inviter, body.name.strip()),
            )
        inv = cur.fetchone()
        row = {
            "id": inv["id"],
            "user_id": None,
            "name": body.name.strip(),
            "email": email,
            "phone": None,
            "role": body.role,
            "departments": list(body.departments),
            "floors": list(body.floors),
            "is_active": False,
            "last_login_at": None,
            "status": "invited",
            "invited_at": inv["invited_at"],
        }
        audit(
            cur, ctx, "core.team.invite", "hotel_membership", inv["id"],
            after={k: row[k] for k in ("email", "role", "departments", "floors")},
        )
    return row


@router.patch("/hotels/{slug}/team/{membership_id}")
def patch_team_member(
    membership_id: int, body: TeamPatchBody, ctx: HotelCtx = Depends(require_perm("core.team.manage"))
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if "role" in changes:
        _check_role(changes["role"])
    slug = ctx.hotel.slug
    with db.db() as cur:
        member = _member(cur, slug, membership_id)
        if member is None:
            raise ClefsError(404, "Team member not found", "member_not_found")
        before = _team_rows(cur, slug, membership_id)[0]
        touches_owner = member["role"] == "owner" or changes.get("role") == "owner"
        if touches_owner and not _may_manage_owners(ctx):
            raise ClefsError(403, "Only an owner can change an owner", "forbidden")
        losing_owner = member["role"] == "owner" and member["is_active"] and (
            changes.get("role", "owner") != "owner" or changes.get("is_active") is False
        )
        if losing_owner and _active_owner_count(cur, slug) <= 1:
            raise ClefsError(409, "A hotel must keep at least one active owner", "last_owner")
        new_phone = normalize_phone(changes["phone"]) if "phone" in changes else None

        if member["pending"]:
            # Access starts only when they accept; their phone is theirs to set.
            if changes.get("is_active"):
                raise ClefsError(409, "They have not accepted the invitation yet", "invite_pending")
            if new_phone:
                raise ClefsError(409, "They add their own phone number once they accept the invitation", "invite_pending")
            m_sets = {k: changes[k] for k in ("role", "departments", "floors") if changes.get(k) is not None}
            if changes.get("name"):
                m_sets["invite_name"] = changes["name"].strip()
        else:
            m_sets = {k: changes[k] for k in ("role", "departments", "floors", "is_active") if changes.get(k) is not None}
            u_sets: dict[str, Any] = {}
            if changes.get("name") and changes["name"].strip() != member["name"]:
                u_sets["name"] = changes["name"].strip()
            if "phone" in changes and new_phone != member["phone_e164"]:
                u_sets["phone_e164"] = new_phone
            if u_sets and not _identity_editable(cur, ctx, member, slug):
                raise ClefsError(
                    409,
                    "This person also works with another hotel on Clefs, so only they can change their name and phone",
                    "profile_shared",
                )
            if u_sets.get("phone_e164"):
                cur.execute("SELECT 1 FROM users WHERE phone_e164 = %s AND id <> %s", (u_sets["phone_e164"], member["user_id"]))
                if cur.fetchone():
                    raise ClefsError(409, "Another user already has this phone number", "phone_taken")
            if u_sets:
                cur.execute(
                    f"UPDATE users SET {', '.join(f'{k} = %s' for k in u_sets)} WHERE id = %s",
                    list(u_sets.values()) + [member["user_id"]],
                )
                sessions.evict_user(member["user_id"])
        if m_sets:
            cur.execute(
                f"UPDATE hotel_memberships SET {', '.join(f'{k} = %s' for k in m_sets)}, updated_at = NOW() WHERE id = %s",
                list(m_sets.values()) + [membership_id],
            )
        after = _team_rows(cur, slug, membership_id)[0]
        audit(cur, ctx, "core.team.update", "hotel_membership", membership_id, before=before, after=after)
    return after


@router.delete("/hotels/{slug}/team/{membership_id}", status_code=204)
def remove_team_member(membership_id: int, ctx: HotelCtx = Depends(require_perm("core.team.manage"))) -> Response:
    """Removes a membership, or withdraws a pending invitation."""
    slug = ctx.hotel.slug
    with db.db() as cur:
        rows = _team_rows(cur, slug, membership_id)
        if not rows:
            raise ClefsError(404, "Team member not found", "member_not_found")
        before = rows[0]
        if before["role"] == "owner":
            if not _may_manage_owners(ctx):
                raise ClefsError(403, "Only an owner can remove an owner", "forbidden")
            if before["is_active"] and _active_owner_count(cur, slug) <= 1:
                raise ClefsError(409, "A hotel must keep at least one active owner", "last_owner")
        cur.execute("DELETE FROM hotel_memberships WHERE id = %s", (membership_id,))
        audit(cur, ctx, "core.team.remove", "hotel_membership", membership_id, before=before)
    return Response(status_code=204)


# ─── Rooms ───


class RoomBody(BaseModel):
    room_number: str
    floor: str | None = None
    wing: str | None = None
    room_type_code: str | None = None
    bedrooms: int | None = Field(default=None, ge=1, le=20)
    extension: str | None = None
    service_state: str | None = None
    ooo_reason: str | None = None
    is_active: bool | None = None


class RoomsBody(BaseModel):
    rooms: list[RoomBody]


@router.get("/hotels/{slug}/rooms")
def list_rooms(ctx: HotelCtx = Depends(require_perm("core.settings.view", "service.view"))) -> list[dict]:
    with db.db() as cur:
        return rooms_mod.list_rooms(cur, ctx.hotel.slug, active_only=False)


@router.put("/hotels/{slug}/rooms")
def put_rooms(body: RoomsBody, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> dict:
    items = [r.model_dump(exclude_none=True) for r in body.rooms]
    for r in items:
        if r.get("service_state") not in (None, "in_service", "ooo", "oos"):
            raise ClefsError(400, "service_state must be in_service, ooo or oos", "invalid_field")
    with db.db() as cur:
        try:
            n = rooms_mod.upsert_rooms(cur, ctx.hotel.slug, items)
        except ValueError as exc:
            raise ClefsError(400, str(exc), "invalid_room") from exc
        except pg_errors.ForeignKeyViolation as exc:
            # upsert_rooms creates unknown room type codes, so this is a race
            # or a bad column value; never a 500.
            raise ClefsError(400, "Unknown room type code", "invalid_room_type") from exc
        audit(cur, ctx, "core.rooms.upsert", "hotel_rooms", ctx.hotel.slug, meta={"count": n})
    return {"ok": True, "count": n}


class RoomTypeBody(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str | None = Field(None, min_length=1, max_length=120)
    bedrooms: int | None = Field(None, ge=0, le=20)
    max_occupancy: int | None = Field(None, ge=1, le=50)
    room_count: int | None = Field(None, ge=0, le=10000)
    is_active: bool | None = None


class RoomTypesBody(BaseModel):
    room_types: list[RoomTypeBody] = Field(max_length=500)


@router.get("/hotels/{slug}/room-types")
def list_room_types(ctx: HotelCtx = Depends(require_perm("core.settings.view", "service.view"))) -> list[dict]:
    with db.db() as cur:
        return rooms_mod.list_room_types(cur, ctx.hotel.slug)


@router.put("/hotels/{slug}/room-types")
def put_room_types(body: RoomTypesBody, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> list[dict]:
    """Partial upsert by code (omitted fields keep their value); never deletes."""
    items = [t.model_dump(exclude_none=True) for t in body.room_types]
    with db.db() as cur:
        try:
            n = rooms_mod.upsert_room_types(cur, ctx.hotel.slug, items)
        except ValueError as exc:
            raise ClefsError(400, str(exc), "invalid_room_type") from exc
        audit(cur, ctx, "core.room_types.upsert", "room_types", ctx.hotel.slug, meta={"count": n})
        return rooms_mod.list_room_types(cur, ctx.hotel.slug)


@router.get("/hotels/{slug}/rooms/qr")
def room_qr_links(ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> list[dict]:
    with db.db() as cur:
        rooms = rooms_mod.list_rooms(cur, ctx.hotel.slug)
    return [
        {
            "room_number": r["room_number"],
            "floor": r["floor"],
            "qr_key_version": r["qr_key_version"],
            "stay_path": rooms_mod.stay_path(ctx.hotel.slug, r["room_number"], r["qr_key_version"]),
        }
        for r in rooms
    ]


@router.post("/hotels/{slug}/rooms/{room_number}/rotate-qr")
def rotate_room_qr(room_number: str, ctx: HotelCtx = Depends(require_perm("core.settings.manage"))) -> dict:
    with db.db() as cur:
        try:
            version = rooms_mod.rotate_qr(cur, ctx.hotel.slug, room_number)
        except LookupError as exc:
            raise ClefsError(404, "Room not found", "room_not_found") from exc
        audit(cur, ctx, "core.rooms.rotate_qr", "hotel_room", room_number, after={"qr_key_version": version})
    return {
        "room_number": room_number,
        "qr_key_version": version,
        "stay_path": rooms_mod.stay_path(ctx.hotel.slug, room_number, version),
    }
