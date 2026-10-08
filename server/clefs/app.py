"""
create_app(): the one place the API is assembled.

    from clefs.app import create_app; app = create_app()           # main.py
    create_app(modules=["demand"])                                 # a module's tests

Order of work:
  1. settings + permission registry reset;
  2. load each enabled module's MODULE manifest (registry order), registering
     its permissions and settings model;
  3. middleware: request context (request id, client ip, user agent) + CSRF
     origin check, then CORS (outermost);
  4. error handlers: ClefsError -> {"detail", "code"}; HTTPException keeps
     FastAPI's {"detail"} (the legacy dashboards read exactly that);
  5. routers: core (auth, hotels/settings/team/rooms, stream), the contracts
     hooks below if those modules exist, then every module in registry order;
  6. lifespan: migrate if MIGRATE_ON_STARTUP, start the events listener.

FROZEN after the foundation wave; module agents leave FOUNDATION-REQUEST notes.
"""

from __future__ import annotations

import importlib
import importlib.util
import ipaddress
import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers

from clefs import db, events, logsafe, registry
from clefs.auth import permissions
from clefs.auth.sessions import DEVICE_COOKIE, SESSION_COOKIE
from clefs.core.context import RequestInfo, reset_request_info, set_request_info
from clefs.core.errors import ClefsError
from clefs.settings import Settings, get_settings

log = logging.getLogger("clefs")

# Foundation modules built by the contracts lane (and later lanes of core).
# Each one that exists and defines `router` is mounted, in this order, after
# the core routers. Adding a router there needs no edit to this file.
CORE_ROUTER_HOOKS: tuple[str, ...] = (
    "clefs.core.routes_contracts",  # signals, actions, autonomy, metrics, files, webhooks, internal
    "clefs.core.signals",
    "clefs.core.actions",
    "clefs.core.metrics",
    "clefs.core.consent",
    "clefs.core.privacy",
    "clefs.files",
    "clefs.jobs",
    "clefs.notify.routes",
    "clefs.core.stays",
    "clefs.core.ingest",
    "clefs.core.tax",
    "clefs.core.oauth",  # 04 spec F6: /hotels/{slug}/oauth/*, /public/oauth/{provider}/callback
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
REQUEST_ID_HEADER = "x-request-id"


# ─── Middleware ───


def _client_ip(scope: dict, headers: Headers, s: Settings) -> str | None:
    """The caller's address for rate limits and audit rows.

    X-Forwarded-For is client-controlled unless a proxy we trust rewrote it.
    Vercel overwrites it (and X-Real-IP) with the real caller, so it is read
    only when TRUST_FORWARDED_FOR is on (the Vercel default). Everywhere else
    uvicorn's --proxy-headers has already resolved scope["client"] from the
    headers of trusted peers only (--forwarded-allow-ips), so reading the raw
    header here would let any client pick a fresh IP per request.

    Even a trusted header must parse as an IP address: rate-limit keys and
    audit rows take this value verbatim, and a proxy misconfiguration that
    passes a client's free text through must not mint unlimited keys."""
    if s.TRUST_FORWARDED_FOR:
        for raw in (headers.get("x-real-ip"), (headers.get("x-forwarded-for") or "").split(",")[0]):
            ip = _parse_ip(raw)
            if ip:
                return ip
    client = scope.get("client")
    return client[0] if client else None


def _parse_ip(raw: str | None) -> str | None:
    """Canonical text of an IPv4/IPv6 address (a bracketed or port-suffixed
    form is accepted), else None."""
    value = (raw or "").strip()
    if not value or len(value) > 64:
        return None
    if value.startswith("[") and "]" in value:  # "[2001:db8::1]:443"
        value = value[1 : value.index("]")]
    elif value.count(":") == 1:  # "203.0.113.9:443"
        value = value.split(":", 1)[0]
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def origin_allowed(headers: Headers, s: Settings) -> bool:
    """CSRF: a cookie-authenticated mutation must come from our own web origin.
    Browsers always send Origin on cross-site POST/PATCH/DELETE and
    Sec-Fetch-Site on modern engines, so a missing pair means a non-browser
    client (curl, a script), which cannot be the victim of CSRF."""
    origin = headers.get("origin")
    if origin and origin != "null":
        if origin in s.WEB_ORIGINS or origin == s.PUBLIC_API_ORIGIN:
            return True
        if s.CORS_ALLOW_ORIGIN_REGEX and re.fullmatch(s.CORS_ALLOW_ORIGIN_REGEX, origin):
            return True
        return False
    if origin == "null":
        return False
    site = headers.get("sec-fetch-site")
    return site in (None, "same-origin", "same-site", "none")


def _has_auth_cookie(headers: Headers) -> bool:
    raw = headers.get("cookie") or ""
    return f"{SESSION_COOKIE}=" in raw or f"{DEVICE_COOKIE}=" in raw


class RequestContextMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so SSE streams pass straight through."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        rid = (headers.get(REQUEST_ID_HEADER) or "")[:64] or uuid.uuid4().hex[:16]
        settings = get_settings()
        token = set_request_info(
            RequestInfo(request_id=rid, ip=_client_ip(scope, headers, settings), user_agent=headers.get("user-agent"))
        )
        try:
            if scope["method"] not in SAFE_METHODS and _has_auth_cookie(headers) and not origin_allowed(headers, settings):
                resp = JSONResponse(
                    {"detail": "Cross-site request blocked", "code": "csrf_failed"},
                    status_code=403,
                    headers={REQUEST_ID_HEADER: rid},
                )
                await resp(scope, receive, send)
                return

            async def send_with_id(message: dict) -> None:
                if message["type"] == "http.response.start":
                    message.setdefault("headers", [])
                    message["headers"] = list(message["headers"]) + [(REQUEST_ID_HEADER.encode(), rid.encode())]
                await send(message)

            await self.app(scope, receive, send_with_id)
        finally:
            reset_request_info(token)


# ─── Error handlers ───


async def _clefs_error(request: Request, exc: ClefsError) -> JSONResponse:
    return JSONResponse(exc.to_dict(), status_code=exc.status, headers=exc.headers)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse({"detail": "Internal server error", "code": "internal_error"}, status_code=500)


# ─── Assembly ───


def _optional_module(name: str) -> Any | None:
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        return None
    if spec is None:
        return None
    return importlib.import_module(name)


def create_app(modules: list[str] | None = None) -> FastAPI:
    settings = get_settings()
    # basicConfig plus the query-secret guards (httpx logs full request URLs,
    # and SerpApi / Google API keys ride in the query string): clefs.logsafe.
    logsafe.configure(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
    warnings = settings.validate_for_startup() + settings.production_gaps()

    from clefs.core import routes as core_routes

    permissions.reset()
    core_routes.SETTINGS_MODELS.clear()
    enabled = registry.enabled_modules(modules)
    manifests = [registry.load_module(name) for name in enabled]
    for m in manifests:
        permissions.register_permissions(m.permissions, module=m.name)
        if m.settings_model is not None:
            core_routes.SETTINGS_MODELS[m.name] = m.settings_model
    core_routes.LOADED_MODULES[:] = enabled

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        for w in warnings:
            log.warning(w)
        if settings.MIGRATE_ON_STARTUP:
            from clefs import migrate

            await run_in_threadpool(migrate.up, enabled)
        db.init_pool()
        events.start_listener()
        for m in manifests:
            if m.on_startup is not None:
                m.on_startup()
        try:
            yield
        finally:
            events.stop_listener()
            db.close_pool()

    app = FastAPI(title="Clefs API", version="2.0", lifespan=lifespan)
    app.state.clefs_modules = enabled

    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ALLOWED_ORIGINS,
        allow_origin_regex=settings.CORS_ALLOW_ORIGIN_REGEX,
        # Cookies never cross origins: the browser reaches the API through the
        # same-origin Next rewrite, and direct calls (SSE, uploads) use tickets.
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_exception_handler(ClefsError, _clefs_error)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled)

    @app.get("/", include_in_schema=False)
    def read_root() -> str:
        return "Welcome to Clefs Backend APIs"

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict:
        return {"ok": True, "modules": enabled}

    from clefs.auth.routes import router as auth_router

    app.include_router(auth_router)
    app.include_router(core_routes.router)
    app.include_router(events.router)
    for hook in CORE_ROUTER_HOOKS:
        mod = _optional_module(hook)
        router = getattr(mod, "router", None) if mod is not None else None
        if router is not None:
            app.include_router(router)
    for m in manifests:
        for r in m.routers:
            app.include_router(r)
    return app
