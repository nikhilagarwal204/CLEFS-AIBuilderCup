"""
FastAPI dependencies for identity and access.

    @router.get("/hotels/{slug}/profit/findings")
    def findings(ctx: HotelCtx = Depends(require_perm("profit.view"))): ...

    @router.post("/hotels/{slug}/requests")
    def legacy(ctx: HotelCtx = Depends(require_legacy_or("service.work"))): ...

Who is calling (Principal), resolved once per request and cached on
request.state:
  1. Authorization: Bearer <token>: CRON_SECRET / WORKER_TOKEN -> service,
     otherwise a session token (scripts);
  2. clefs_session cookie -> user (web or magic session);
  3. clefs_device cookie -> paired device (+ its open PIN shifts);
  4. otherwise anonymous.

What they may do at one hotel (HotelCtx) = the permissions of their
membership role, of the device pseudo-role, or everything for a clefs admin
(audited). `require_legacy_or` also lets anonymous callers through when the
hotel still has legacy_open_access (the grandfathered Indian pilots).

Hotel-scoped deps read the `slug` path parameter; every hotel route must name
it `slug`.
"""

from __future__ import annotations

import hmac
import logging
import threading
import time as _time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from fastapi import Request

from clefs import db
from clefs.auth import devices as devices_mod
from clefs.auth import permissions as perms_mod
from clefs.auth import sessions
from clefs.core.errors import ClefsError
from clefs.core.hotels import Hotel, get_hotel
from clefs.settings import get_settings

log = logging.getLogger("clefs.auth")

PrincipalKind = Literal["user", "device", "service", "anonymous"]
STAFF_HEADER = "X-Clefs-Staff"


@dataclass(frozen=True)
class Principal:
    kind: PrincipalKind
    user_id: int | None = None
    device_id: int | None = None
    active_staff_ids: tuple[int, ...] = ()
    is_clefs_admin: bool = False
    session_id: int | None = None
    scope: dict = field(default_factory=dict)
    session_kind: str | None = None  # web | magic | api (users)
    label: str | None = None  # user name, device name or service name
    email: str | None = None
    via_cookie: bool = False
    device_hotel: str | None = None
    device_floors: tuple[str, ...] = ()
    device_departments: tuple[str, ...] = ()
    user: dict | None = None  # the /auth/me "user" object

    @property
    def is_authenticated(self) -> bool:
        return self.kind != "anonymous"


ANONYMOUS = Principal(kind="anonymous")


@dataclass(frozen=True)
class HotelCtx:
    principal: Principal
    hotel: Hotel
    role: str | None
    permissions: frozenset[str]
    departments: tuple[str, ...] = ()
    floors: tuple[str, ...] = ()
    via_legacy: bool = False
    membership_id: int | None = None
    admin_bypass: bool = False

    @property
    def slug(self) -> str:
        return self.hotel.slug

    def can(self, *perms: str) -> bool:
        return any(p in self.permissions for p in perms)


# ─── Principal resolution ───


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    return None


def _service_name(token: str) -> str | None:
    s = get_settings()
    for name, secret in (("cron", s.CRON_SECRET), ("worker", s.WORKER_TOKEN)):
        if secret and hmac.compare_digest(token.encode(), secret.encode()):
            return name
    return None


def _user_principal(info: sessions.SessionInfo, *, via_cookie: bool) -> Principal:
    return Principal(
        kind="user",
        user_id=info.user_id,
        is_clefs_admin=info.is_clefs_admin,
        session_id=info.session_id,
        scope=info.scope,
        session_kind=info.kind,
        label=info.name,
        email=info.email,
        via_cookie=via_cookie,
        user=info.user_json(),
    )


def device_principal(request: Request) -> Principal | None:
    """The paired device named by the clefs_device cookie, whatever else the
    request carries (a manager may be logged in on a floor tablet)."""
    cached = getattr(request.state, "clefs_device_principal", "unset")
    if cached != "unset":
        return cached
    info = devices_mod.lookup_device(request.cookies.get(sessions.DEVICE_COOKIE))
    principal = None
    if info is not None:
        with db.db() as cur:
            staff = devices_mod.active_staff_ids(cur, info.id)
            # A write by an on-shift person counts as activity (auto-logout's
            # idle rule); a GET does not, so a tablet polling its feed with
            # the header set never keeps an abandoned session alive.
            named = request.headers.get(STAFF_HEADER, "")
            if request.method not in ("GET", "HEAD", "OPTIONS") and named.isdigit() and int(named) in staff:
                devices_mod.touch_activity(cur, info.id, int(named))
        principal = Principal(
            kind="device",
            device_id=info.id,
            active_staff_ids=staff,
            label=info.name,
            via_cookie=True,
            device_hotel=info.hotel_slug,
            device_floors=info.floors,
            device_departments=info.departments,
        )
    request.state.clefs_device_principal = principal
    return principal


def current_principal(request: Request) -> Principal:
    """Never raises; anonymous when nothing valid is presented."""
    cached = getattr(request.state, "clefs_principal", None)
    if cached is not None:
        return cached
    principal = ANONYMOUS
    token = _bearer(request)
    if token:
        name = _service_name(token)
        if name:
            principal = Principal(kind="service", label=name)
        else:
            info = sessions.lookup_session(token)
            if info is not None:
                principal = _user_principal(info, via_cookie=False)
    if principal is ANONYMOUS:
        info = sessions.lookup_session(request.cookies.get(sessions.SESSION_COOKIE))
        if info is not None:
            principal = _user_principal(info, via_cookie=True)
    if principal is ANONYMOUS:
        principal = device_principal(request) or ANONYMOUS
    request.state.clefs_principal = principal
    return principal


# ─── Hotel context ───


def _membership(cur: Any, user_id: int, slug: str) -> dict | None:
    cur.execute(
        "SELECT id, role, departments, floors FROM hotel_memberships WHERE user_id = %s AND hotel_slug = %s AND is_active",
        (user_id, slug),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def resolve_ctx(principal: Principal, slug: str, *, cur: Any = None) -> HotelCtx:
    """Role and permissions of `principal` at hotel `slug` (404 if no hotel).
    Does not raise for "no access"; callers decide with ctx.can()."""
    if cur is None:
        with db.db() as c:
            return resolve_ctx(principal, slug, cur=c)
    hotel = get_hotel(cur, slug)
    role = None
    perms: frozenset[str] = frozenset()
    departments: tuple[str, ...] = ()
    floors: tuple[str, ...] = ()
    membership_id = None
    admin_bypass = False

    if principal.kind == "user" and principal.user_id is not None:
        m = _membership(cur, principal.user_id, slug)
        if m is not None:
            role = m["role"]
            perms = perms_mod.permissions_for_role(role)
            departments = tuple(m["departments"] or ())
            floors = tuple(m["floors"] or ())
            membership_id = m["id"]
        if principal.is_clefs_admin:
            all_perms = perms_mod.all_permissions()
            admin_bypass = perms != all_perms
            perms = all_perms
        if principal.session_kind == "magic":
            if principal.scope.get("hotel") != slug:
                # A magic link is scoped to ONE hotel. Elsewhere the session
                # has no relationship at all: keeping the role would still pass
                # every "any member" check (stream tickets, /context, no-perm
                # routes) at the user's other hotels.
                role, membership_id = None, None
                departments, floors = (), ()
                perms = frozenset()
            else:
                perms = perms_mod.restrict_for_magic(perms, principal.scope.get("purposes") or [])
            admin_bypass = False
    elif principal.kind == "device" and principal.device_hotel == slug:
        role = perms_mod.DEVICE_ROLE
        perms = perms_mod.device_permissions()
        departments = principal.device_departments
        floors = principal.device_floors

    return HotelCtx(
        principal=principal,
        hotel=hotel,
        role=role,
        permissions=perms,
        departments=departments,
        floors=floors,
        membership_id=membership_id,
        admin_bypass=admin_bypass,
    )


def legacy_ctx(ctx: HotelCtx) -> HotelCtx:
    """The same context, granted legacy open access (anonymous-equivalent)."""
    return HotelCtx(
        principal=ctx.principal,
        hotel=ctx.hotel,
        role=ctx.role,
        permissions=ctx.permissions | perms_mod.legacy_open_permissions(),
        departments=ctx.departments,
        floors=ctx.floors,
        via_legacy=True,
        membership_id=ctx.membership_id,
        admin_bypass=ctx.admin_bypass,
    )


# Admin bypasses are audited, deduplicated per (session, hotel, permission) per
# window, so an admin browsing a hotel does not write a row per request.
_ADMIN_AUDIT_WINDOW_S = 600.0
_admin_seen: dict[tuple, float] = {}
_admin_lock = threading.Lock()


def _audit_admin_bypass(ctx: HotelCtx, perms: tuple[str, ...], request: Request) -> None:
    key = (ctx.principal.session_id, ctx.hotel.slug, perms)
    now = _time.monotonic()
    with _admin_lock:
        if _admin_seen.get(key, 0) > now:
            return
        _admin_seen[key] = now + _ADMIN_AUDIT_WINDOW_S
    from clefs.audit import audit

    try:
        with db.db() as cur:
            audit(
                cur,
                ctx,
                "auth.admin_bypass",
                "route",
                request.url.path,
                meta={"method": request.method, "permissions": list(perms)},
            )
    except Exception:  # auditing must not take the request down
        log.exception("admin bypass audit failed")


def _slug_of(request: Request) -> str:
    slug = request.path_params.get("slug")
    if not slug:
        raise RuntimeError(f"route {request.url.path} uses a hotel dependency but has no {{slug}} path parameter")
    return slug


def _deny(principal: Principal) -> ClefsError:
    if not principal.is_authenticated:
        return ClefsError(401, "Sign in required", "unauthorized")
    return ClefsError(403, "You do not have access to this", "forbidden")


def has_admin_powers(principal: Principal) -> bool:
    """A Clefs admin acting as one. A magic-link session never is: it holds
    only the brief/actions permissions of its one hotel, admin or not."""
    return principal.is_clefs_admin and principal.session_kind != "magic"


def check(ctx: HotelCtx, perms: tuple[str, ...], request: Request | None = None) -> HotelCtx:
    """Raise 401/403 unless ctx holds any of `perms` (no perms = any access)."""
    if perms and not ctx.can(*perms):
        raise _deny(ctx.principal)
    if not perms and ctx.role is None and not has_admin_powers(ctx.principal):
        raise _deny(ctx.principal)
    if ctx.admin_bypass and request is not None:
        needed = tuple(p for p in perms if p not in perms_mod.permissions_for_role(ctx.role))
        if needed or not perms:
            _audit_admin_bypass(ctx, perms, request)
    return ctx


def any_access_ctx(request: Request, slug: str) -> HotelCtx:
    """Anyone with a relationship to the hotel: a member (any role), a Clefs
    admin, a paired device of the hotel, or, while the hotel keeps
    legacy_open_access, any visitor. 401/403 otherwise. Used by the context
    and ticket routes, which serve every kind of caller."""
    principal = current_principal(request)
    ctx = resolve_ctx(principal, slug)
    if ctx.role is not None or ctx.permissions:
        return ctx
    dev = device_principal(request)
    if dev is not None and dev.device_hotel == slug:
        return resolve_ctx(dev, slug)
    if ctx.hotel.legacy_open_access:
        return ctx
    if not principal.is_authenticated:
        raise ClefsError(401, "Sign in required", "unauthorized")
    raise ClefsError(403, "No access to this hotel", "forbidden")


# ─── Public dependency factories ───


def require_user(request: Request) -> Principal:
    principal = current_principal(request)
    if principal.kind != "user":
        raise ClefsError(401, "Sign in required", "unauthorized")
    return principal


def require_perm(*perms: str) -> Callable[..., HotelCtx]:
    """User (or clefs admin) holding ANY of perms at the {slug} hotel. Devices
    pass when the permission includes the "device" pseudo-role."""

    def dependency(request: Request) -> HotelCtx:
        return check(resolve_ctx(current_principal(request), _slug_of(request)), perms, request)

    return dependency


def require_legacy_or(*perms: str) -> Callable[..., HotelCtx]:
    """require_perm, except that anyone passes while the hotel still has
    legacy_open_access (the grandfathered pre-auth dashboards and phones)."""

    def dependency(request: Request) -> HotelCtx:
        ctx = resolve_ctx(current_principal(request), _slug_of(request))
        if ctx.hotel.legacy_open_access and not (perms and ctx.can(*perms)):
            return legacy_ctx(ctx)
        return check(ctx, perms, request)

    return dependency


def require_device(*, departments: set[str] | None = None) -> Callable[..., HotelCtx]:
    """A paired device (clefs_device cookie). If the route has {slug}, the
    device must belong to that hotel. `departments` narrows to devices scoped
    to one of them (a device with no departments serves all)."""
    wanted = {d.lower() for d in departments} if departments else None

    def dependency(request: Request) -> HotelCtx:
        principal = device_principal(request)
        if principal is None:
            raise ClefsError(401, "This device is not paired", "device_required")
        slug = request.path_params.get("slug") or principal.device_hotel
        if slug != principal.device_hotel:
            raise ClefsError(403, "This device belongs to another hotel", "forbidden")
        if wanted and principal.device_departments and not ({d.lower() for d in principal.device_departments} & wanted):
            raise ClefsError(403, "This device is not set up for that department", "forbidden")
        return resolve_ctx(principal, slug)

    return dependency


def require_perm_or_device(*perms: str) -> Callable[..., HotelCtx]:
    """A user with any of perms, or any paired device of the {slug} hotel."""

    def dependency(request: Request) -> HotelCtx:
        slug = _slug_of(request)
        principal = current_principal(request)
        ctx = resolve_ctx(principal, slug)
        if ctx.can(*perms):
            return check(ctx, perms, request)
        dev = device_principal(request)
        if dev is not None and dev.device_hotel == slug:
            return resolve_ctx(dev, slug)
        raise _deny(principal)

    return dependency


def require_service(name: Literal["cron", "worker"]) -> Callable[..., Principal]:
    """Bearer CRON_SECRET (Vercel Cron sends it automatically) or WORKER_TOKEN.
    The worker may also call cron routes."""

    def dependency(request: Request) -> Principal:
        token = _bearer(request)
        s = get_settings()
        secret = s.CRON_SECRET if name == "cron" else s.WORKER_TOKEN
        if not secret and not (name == "cron" and s.WORKER_TOKEN):
            raise ClefsError(503, f"{name} access is not configured", "not_configured")
        got = _service_name(token) if token else None
        if got == name or (name == "cron" and got == "worker"):
            return Principal(kind="service", label=got)
        raise ClefsError(401, "Service token required", "unauthorized")

    return dependency


def acting_staff(ctx: HotelCtx, request: Request) -> int | None:
    """Staff member named by X-Clefs-Staff on a shared device, validated
    against that device's open PIN shifts. None when the header is absent."""
    raw = request.headers.get(STAFF_HEADER)
    if not raw:
        return None
    try:
        staff_id = int(raw)
    except ValueError as exc:
        raise ClefsError(400, f"{STAFF_HEADER} must be a staff id", "bad_request") from exc
    principal = ctx.principal
    if principal.kind != "device":
        principal = device_principal(request) or principal
    if principal.kind != "device" or staff_id not in principal.active_staff_ids:
        raise ClefsError(403, "That staff member is not on shift on this device", "staff_not_on_shift")
    return staff_id
