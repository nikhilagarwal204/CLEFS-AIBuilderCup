"""
Auth routes (the SHARED HTTP CONTRACT with the client; see CONTRACTS.md).

The browser calls these through the Next rewrite /api/clefs/*, so the cookies
set here land on the web origin. Errors are {"detail": str, "code": str}.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.audit import audit
from clefs.auth import devices, magic, passwords, sessions, tokens
from clefs.auth import permissions as perms_mod
from clefs.auth.deps import (
    ANONYMOUS,
    HotelCtx,
    Principal,
    any_access_ctx,
    current_principal,
    has_admin_powers,
    require_device,
    require_perm,
    require_user,
    resolve_ctx,
)
from clefs.core.context import request_info
from clefs.core.errors import ClefsError, too_many
from clefs.core.phone import normalize_phone
from clefs.settings import get_settings

router = APIRouter(tags=["auth"])
log = logging.getLogger("clefs.auth")

STREAM_TICKET_TTL_S = 60
UPLOAD_TICKET_TTL_S = 60
PURPOSE_RE = re.compile(r"^[a-z0-9_.:-]{1,64}$")


# ─── Bodies ───


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=512)


class StreamTicketBody(BaseModel):
    hotel_slug: str


class UploadTicketBody(BaseModel):
    hotel_slug: str
    purpose: str


class MagicExchangeBody(BaseModel):
    token: str = Field(max_length=256)


class MagicRequestBody(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=32)
    purpose: str = "login"
    hotel_slug: str | None = None
    next_path: str | None = Field(default=None, max_length=512)


class PairingCodeBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: str
    floors: list[str] = []
    departments: list[str] = []


class PairBody(BaseModel):
    code: str = Field(max_length=32)


class ShiftStartBody(BaseModel):
    staff_member_id: int
    pin: str = Field(max_length=12)


class ShiftEndBody(BaseModel):
    session_id: int
    handover_note: str | None = Field(default=None, max_length=4000)


class StaffPinBody(BaseModel):
    pin: str | None = None


# ─── Helpers ───


def memberships_for(cur: Any, user_id: int) -> list[dict]:
    cur.execute(
        """
        SELECT m.hotel_slug, h.name AS hotel_name, m.role
        FROM hotel_memberships m JOIN hotels h ON h.slug = m.hotel_slug
        WHERE m.user_id = %s AND m.is_active
        ORDER BY h.name
        """,
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def invites_for(cur: Any, user_id: int) -> list[dict]:
    """Pending invitations to hotels (core/routes.py creates them when a
    manager adds an email that already has an account)."""
    cur.execute(
        """
        SELECT m.id AS membership_id, m.hotel_slug, h.name AS hotel_name, m.role, m.invited_at,
               inviter.name AS invited_by_name
        FROM hotel_memberships m
        JOIN hotels h ON h.slug = m.hotel_slug
        LEFT JOIN users inviter ON inviter.id = m.invited_by
        WHERE m.user_id = %s AND m.invited_at IS NOT NULL AND m.accepted_at IS NULL
        ORDER BY m.invited_at DESC
        """,
        (user_id,),
    )
    return [dict(r) for r in cur.fetchall()]


def _ip() -> str:
    return request_info().ip or "unknown"


# ─── Login / logout / me ───


@router.post("/auth/login")
def login(body: LoginBody, response: Response) -> dict:
    email = body.email.strip().lower()
    ip = _ip()
    with db.db() as cur:
        # Per-email is the brute-force guard; the per-IP cap is loose because
        # every browser call arrives through the Next rewrite (one source IP)
        # and hotel staff share a NAT address.
        allowed = ratelimit.hit(cur, f"login:ip:{ip}", limit=200, window_s=900) and ratelimit.hit(
            cur, f"login:email:{email}", limit=10, window_s=900
        )
        if allowed:
            cur.execute(
                "SELECT id, name, email, password_hash, is_active, is_clefs_admin, phone_e164, locale FROM users WHERE email = %s",
                (email,),
            )
            user = cur.fetchone()
    if not allowed:
        raise too_many("Too many sign-in attempts. Wait a few minutes and try again.")

    # Password check runs outside any DB transaction (scrypt is ~50 ms).
    # Every failing path costs one scrypt, so response time never tells an
    # unknown or deactivated account from a wrong password.
    ok = False
    if user is None or not user["password_hash"] or not user["is_active"]:
        passwords.burn_time(body.password)
    else:
        ok = passwords.verify_password(body.password, user["password_hash"])
    if not ok:
        with db.db() as cur:
            audit(cur, None, "auth.login_failed", "user", user["id"] if user else None, meta={"email": email})
        raise ClefsError(401, "Email or password is incorrect", "invalid_credentials")

    info = request_info()
    with db.db() as cur:
        token, session_id, expires_at = sessions.create_session(
            cur, user["id"], kind="web", ip=info.ip, user_agent=info.user_agent
        )
        new_hash = passwords.hash_password(body.password) if passwords.needs_rehash(user["password_hash"]) else None
        cur.execute(
            "UPDATE users SET last_login_at = NOW(), password_hash = COALESCE(%s, password_hash) WHERE id = %s",
            (new_hash, user["id"]),
        )
        memberships = memberships_for(cur, user["id"])
        invites = invites_for(cur, user["id"])
        # A successful sign-in clears the per-email counter, so an owner who
        # signs in on several devices is never locked out by their own logins.
        ratelimit.reset(cur, f"login:email:{email}")
        audit(
            cur,
            Principal(kind="user", user_id=user["id"], label=user["name"]),
            "auth.login",
            "auth_session",
            session_id,
        )
    sessions.set_session_cookie(response, token, expires_at)
    return {
        "user": {
            "id": user["id"],
            "name": user["name"],
            "email": user["email"],
            "is_clefs_admin": user["is_clefs_admin"],
            "phone_e164": user["phone_e164"],
            "locale": user["locale"],
        },
        "memberships": memberships,
        "invites": invites,
    }


@router.post("/auth/logout", status_code=204)
def logout(request: Request) -> Response:
    principal = current_principal(request)
    if principal.kind == "user" and principal.session_id is not None:
        with db.db() as cur:
            sessions.revoke_session(cur, principal.session_id)
            audit(cur, principal, "auth.logout", "auth_session", principal.session_id)
    resp = Response(status_code=204)
    sessions.clear_session_cookie(resp)
    return resp


@router.get("/auth/me")
def me(request: Request, hotel: str | None = Query(default=None)) -> dict:
    principal = current_principal(request)
    is_user = principal.kind == "user"
    out: dict[str, Any] = {
        "user": principal.user if is_user else None,
        "memberships": [],
        "hotel_slug": hotel,
        "role": None,
        "permissions": [],
        "legacy_mode": False,
        "invites": [],
    }
    with db.db() as cur:
        if is_user:
            out["memberships"] = memberships_for(cur, principal.user_id)
            out["invites"] = invites_for(cur, principal.user_id)
        if hotel:
            # /auth/me describes a person; a device cookie alone is not one.
            ctx = resolve_ctx(principal if is_user else ANONYMOUS, hotel, cur=cur)
            if is_user and (ctx.role is not None or has_admin_powers(principal) or ctx.permissions):
                out["role"] = ctx.role
                out["permissions"] = sorted(ctx.permissions)
            elif ctx.hotel.legacy_open_access:
                out["permissions"] = sorted(perms_mod.legacy_open_permissions())
                out["legacy_mode"] = True
            elif not is_user:
                raise ClefsError(401, "Sign in required", "unauthorized")
        elif not is_user:
            raise ClefsError(401, "Sign in required", "unauthorized")
    return out


# ─── Invitations ───
# Only the invited person answers an invitation, from their own session (any
# kind: a magic link proves control of the same inbox or phone a password
# reset would). Accepting turns it into an active membership.


def _own_invite(cur: Any, principal: Principal, membership_id: int) -> dict:
    cur.execute(
        "SELECT id, hotel_slug, role FROM hotel_memberships WHERE id = %s AND user_id = %s "
        "AND invited_at IS NOT NULL AND accepted_at IS NULL FOR UPDATE",
        (membership_id, principal.user_id),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Invitation not found", "invite_not_found")
    return dict(row)


@router.post("/auth/invites/{membership_id}/accept")
def accept_invite(membership_id: int, principal: Principal = Depends(require_user)) -> dict:
    with db.db() as cur:
        inv = _own_invite(cur, principal, membership_id)
        cur.execute(
            "UPDATE hotel_memberships SET is_active = TRUE, accepted_at = NOW(), updated_at = NOW() WHERE id = %s",
            (membership_id,),
        )
        audit(cur, principal, "core.team.invite_accept", "hotel_membership", membership_id,
              after={"role": inv["role"]}, hotel_slug=inv["hotel_slug"])
        return {
            "hotel_slug": inv["hotel_slug"],
            "memberships": memberships_for(cur, principal.user_id),
            "invites": invites_for(cur, principal.user_id),
        }


@router.post("/auth/invites/{membership_id}/decline", status_code=204)
def decline_invite(membership_id: int, principal: Principal = Depends(require_user)) -> Response:
    with db.db() as cur:
        inv = _own_invite(cur, principal, membership_id)
        cur.execute("DELETE FROM hotel_memberships WHERE id = %s", (membership_id,))
        audit(cur, principal, "core.team.invite_decline", "hotel_membership", membership_id,
              before={"role": inv["role"]}, hotel_slug=inv["hotel_slug"])
    return Response(status_code=204)


# ─── Tickets ───


@router.post("/auth/stream-ticket")
def stream_ticket(body: StreamTicketBody, request: Request) -> dict:
    ctx = any_access_ctx(request, body.hotel_slug)
    p = ctx.principal
    claims = {
        "k": p.kind if p.kind in ("user", "device") else "anonymous",
        "u": p.user_id,
        "d": p.device_id,
        "r": ctx.role,
        "dep": list(ctx.departments),
        "fl": list(ctx.floors),
        "h": ctx.hotel.slug,
    }
    t = tokens.mint_ticket("stream", claims, ttl_s=STREAM_TICKET_TTL_S)
    return {"url": f"/hotels/{ctx.hotel.slug}/stream?t={t}", "expires_in": STREAM_TICKET_TTL_S}


@router.post("/files/upload-ticket")
def upload_ticket(body: UploadTicketBody, request: Request) -> dict:
    if not PURPOSE_RE.match(body.purpose):
        raise ClefsError(400, "purpose must be a short lowercase key", "invalid_purpose")
    ctx = any_access_ctx(request, body.hotel_slug)
    if not ctx.can("core.files.upload"):
        # Legacy open access and magic-link sessions never extend to uploads
        # (files hold OTA statements, P&Ls and guest PII).
        if not ctx.principal.is_authenticated:
            raise ClefsError(401, "Sign in to upload files", "unauthorized")
        raise ClefsError(403, "You cannot upload files here", "forbidden")
    p = ctx.principal
    claims = {"k": p.kind, "u": p.user_id, "d": p.device_id, "h": ctx.hotel.slug, "purpose": body.purpose}
    t = tokens.mint_ticket("upload", claims, ttl_s=UPLOAD_TICKET_TTL_S)
    return {"url": f"/hotels/{ctx.hotel.slug}/files?t={t}", "expires_in": UPLOAD_TICKET_TTL_S}


# ─── Magic links ───


def _send_magic_link(email: str | None, phone: str | None, purpose: str, hotel_slug: str | None, next_path: str | None) -> None:
    """Look the account up, mint and deliver the link. Runs as a background
    task, after the 202 has gone out, so neither the answer nor its timing
    depends on whether the account exists (delivery is a DB write plus a
    WhatsApp or Resend call, easily measured)."""
    try:
        with db.db() as cur:
            found = magic.request_link(
                cur, email=email, phone=phone, purpose=purpose, hotel_slug=hotel_slug, next_path=next_path
            )
        if found is not None:
            user, slug, token = found
            magic.deliver(user, slug, f"{get_settings().web_base_url}{magic.link_path(token)}", purpose)
    except Exception:  # nobody is waiting on this request any more
        log.exception("magic link request failed")


@router.post("/auth/magic/request", status_code=202)
def magic_request(body: MagicRequestBody, background: BackgroundTasks) -> dict:
    """Send a one-tap link by WhatsApp or email. Always the same answer, in
    the same time, so it cannot be used to discover which emails or phones
    have accounts."""
    if body.purpose not in magic.PURPOSES:
        raise ClefsError(400, f"purpose must be one of {', '.join(magic.PURPOSES)}", "invalid_purpose")
    phone = normalize_phone(body.phone) if body.phone else None
    ident = (body.email or phone or "").strip().lower()
    with db.db() as cur:
        allowed = ratelimit.hit(cur, f"magicreq:ip:{_ip()}", limit=60, window_s=600) and (
            not ident or ratelimit.hit(cur, f"magicreq:id:{ident}", limit=5, window_s=3600)
        )
    if not allowed:
        raise too_many()
    if ident:
        background.add_task(_send_magic_link, body.email, phone, body.purpose, body.hotel_slug, body.next_path)
    return {"ok": True}


@router.post("/auth/magic/exchange")
def magic_exchange(body: MagicExchangeBody, response: Response) -> dict:
    info = request_info()
    with db.db() as cur:
        if not ratelimit.hit(cur, f"magic:ip:{_ip()}", limit=120, window_s=600):
            raise too_many()
    with db.db() as cur:
        session_token, expires_at, out = magic.exchange(cur, body.token, ip=info.ip, user_agent=info.user_agent)
    sessions.set_session_cookie(response, session_token, expires_at)
    return out


# ─── Devices ───


@router.post("/hotels/{slug}/devices/pairing-codes")
def create_pairing_code(
    body: PairingCodeBody, ctx: HotelCtx = Depends(require_perm("core.devices.manage", "service.manage"))
) -> dict:
    with db.db() as cur:
        return devices.create_pairing_code(
            cur, ctx.hotel.slug, name=body.name, kind=body.kind, floors=body.floors, departments=body.departments, actor=ctx
        )


@router.get("/hotels/{slug}/devices")
def list_devices(ctx: HotelCtx = Depends(require_perm("core.devices.manage", "service.manage"))) -> list[dict]:
    with db.db() as cur:
        return devices.list_devices(cur, ctx.hotel.slug)


@router.delete("/hotels/{slug}/devices/{device_id}", status_code=204)
def revoke_device(device_id: int, ctx: HotelCtx = Depends(require_perm("core.devices.manage", "service.manage"))) -> Response:
    with db.db() as cur:
        devices.revoke_device(cur, ctx.hotel.slug, device_id, actor=ctx)
    return Response(status_code=204)


@router.post("/auth/devices/pair")
def pair_device(body: PairBody, response: Response) -> dict:
    with db.db() as cur:
        if not ratelimit.hit(cur, f"pair:ip:{_ip()}", limit=30, window_s=600):
            raise too_many()
    with db.db() as cur:
        token, device = devices.pair(cur, body.code)
    sessions.set_device_cookie(response, token)
    return {"device": device.json(), "hotel_slug": device.hotel_slug}


@router.post("/auth/devices/shift/start")
def shift_start(body: ShiftStartBody, ctx: HotelCtx = Depends(require_device())) -> dict:
    device = _device_info(ctx)
    with db.db() as cur:
        outcome = devices.start_shift(cur, device, body.staff_member_id, body.pin)
    if not outcome.ok:
        assert outcome.error is not None
        raise outcome.error  # raised after commit so the failure counter sticks
    return outcome.data or {}


@router.post("/auth/devices/shift/end")
def shift_end(body: ShiftEndBody, ctx: HotelCtx = Depends(require_device())) -> dict:
    with db.db() as cur:
        return devices.end_shift(cur, _device_info(ctx), body.session_id, body.handover_note)


@router.get("/auth/devices/roster")
def device_roster(ctx: HotelCtx = Depends(require_device())) -> list[dict]:
    with db.db() as cur:
        return devices.roster(cur, _device_info(ctx))


@router.put("/hotels/{slug}/staff/{staff_id}/pin")
def set_staff_pin(
    staff_id: int, body: StaffPinBody, ctx: HotelCtx = Depends(require_perm("core.devices.manage", "service.manage"))
) -> dict:
    with db.db() as cur:
        devices.set_staff_pin(cur, ctx.hotel.slug, staff_id, body.pin, actor=ctx)
    return {"ok": True, "has_pin": body.pin is not None}


def _device_info(ctx: HotelCtx) -> devices.DeviceInfo:
    p = ctx.principal
    return devices.DeviceInfo(
        id=p.device_id or 0,
        hotel_slug=ctx.hotel.slug,
        name=p.label or "",
        kind="",
        floors=p.device_floors,
        departments=p.device_departments,
    )

