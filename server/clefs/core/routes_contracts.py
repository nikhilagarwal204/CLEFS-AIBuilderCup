"""
HTTP routes for the cross-module contracts (CONTRACTS.md Part 2): signals,
actions and the autonomy dial, metrics, files, the WhatsApp and Resend (inbound
email) webhooks, and the internal cron tick / status.

Mounted by clefs.app.create_app through CORE_ROUTER_HOOKS. None of the
contract modules (signals, actions, jobs, files, notify, ...) defines its own
`router`, so nothing here is mounted twice.

Access:
  signals / actions (read)     core.signals.view (magic-link sessions hold it)
  create an action             core.actions.decide, or the type's decide_perm
  decide / done                any member; core.actions.decide() enforces the
                               type's decide_perm (or "you are the assignee")
  autonomy                     core.autonomy.view / .manage; set_autonomy and
                               accept_suggestion add .raise_auto for auto
                               levels and looser limits
  metrics                      any member; each metric's own permission
  files                        upload: core.files.upload or an upload ticket;
                               download: core.files.view, the uploader, or a
                               signed download ticket
  webhooks                     provider signatures (Meta HMAC, Resend's Svix HMAC)
  /internal/*                  Bearer CRON_SECRET (or WORKER_TOKEN)
Legacy open access never reaches any of these.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse, Response
from pydantic import AwareDatetime, BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from clefs import ai, db, events, files, jobs
from clefs import notify as notify_pkg
from clefs.audit import audit
from clefs.auth import tokens
from clefs.auth.deps import HotelCtx, Principal, current_principal, require_perm, require_service, resolve_ctx
from clefs.core import actions as actions_mod
from clefs.core import metrics as metrics_mod
from clefs.core import signals as signals_mod
from clefs.core.errors import ClefsError
from clefs.notify import inbound
from clefs.settings import get_settings

router = APIRouter(tags=["contracts"])


def _csv(v: str | None) -> list[str] | None:
    items = [x.strip() for x in (v or "").split(",") if x.strip()]
    return items or None


# ─── Signals ───


@router.get("/hotels/{slug}/signals")
def list_signals(
    status: str | None = None,
    section: str | None = None,
    module: str | None = None,
    cadence: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm("core.signals.view")),
) -> list[dict]:
    statuses = _csv(status) or list(signals_mod.OPEN_STATUSES)
    bad = [s for s in statuses if s not in signals_mod.SIGNAL_STATUSES]
    if bad:
        raise ClefsError(400, f"unknown status {bad[0]!r}", "invalid_status")
    with db.db() as cur:
        return signals_mod.list_signals(
            cur, ctx.slug, status=statuses, modules=_csv(module), sections=_csv(section), cadence=cadence, limit=limit
        )


# ─── Actions ───


class CreateActionBody(BaseModel):
    type: str = Field(max_length=120)
    title: str = Field(min_length=1, max_length=300)
    params: dict = {}
    signal_id: int | None = None
    expected_value: Decimal | None = None
    currency: str | None = None
    owner_role: str | None = None
    due_at: datetime | None = None
    assignee_user_id: int | None = None
    dedupe_key: str | None = Field(default=None, max_length=300)
    # K1: when a still proposed/approved action lapses (a quote's validity).
    expires_at: AwareDatetime | None = None


class DecideBody(BaseModel):
    decision: str
    params_patch: dict | None = None
    assignee_user_id: int | None = None
    assignee_role: str | None = None
    note: str | None = Field(default=None, max_length=2000)
    snooze_until: datetime | None = None


class DoneBody(BaseModel):
    note: str | None = Field(default=None, max_length=2000)
    result: dict | None = None


@router.get("/hotels/{slug}/actions")
def list_actions(
    status: str | None = None,
    type: str | None = None,  # noqa: A002 - query parameter name
    overdue: bool = False,
    signal_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm("core.signals.view", "core.actions.decide")),
) -> list[dict]:
    with db.db() as cur:
        return actions_mod.list_actions(
            cur, ctx.slug, status=_csv(status), types=_csv(type), overdue=overdue, signal_id=signal_id, limit=limit
        )


@router.post("/hotels/{slug}/actions", status_code=201)
def create_action(body: CreateActionBody, ctx: HotelCtx = Depends(require_perm())) -> dict:
    t = actions_mod.get_action_type(body.type)
    if t is None:
        raise ClefsError(400, f"unknown action type {body.type!r}", "unknown_action_type")
    if not ctx.can(t.decide_perm, "core.actions.decide"):
        raise ClefsError(403, "You cannot create this action", "forbidden")
    with db.db() as cur:
        return actions_mod.create_action(
            cur,
            ctx.slug,
            type=body.type,
            title=body.title,
            params=body.params,
            signal_id=body.signal_id,
            expected_value=body.expected_value,
            currency=body.currency,
            owner_role=body.owner_role,
            due_at=body.due_at,
            assignee_user_id=body.assignee_user_id,
            dedupe_key=body.dedupe_key,
            actor=ctx,
            via="console",
            expires_at=body.expires_at,
        )


@router.get("/hotels/{slug}/actions/{action_id}")
def get_action(action_id: int, ctx: HotelCtx = Depends(require_perm("core.signals.view", "core.actions.decide"))) -> dict:
    with db.db() as cur:
        return actions_mod.get_action(cur, ctx.slug, action_id)


@router.post("/hotels/{slug}/actions/{action_id}/decide")
def decide_action(action_id: int, body: DecideBody, ctx: HotelCtx = Depends(require_perm())) -> dict:
    with db.db() as cur:
        return actions_mod.decide(
            cur,
            action_id,
            body.decision,
            actor=ctx,
            via="console",
            params_patch=body.params_patch,
            assignee_user_id=body.assignee_user_id,
            assignee_role=body.assignee_role,
            note=body.note,
            snooze_until=body.snooze_until,
            hotel_slug=ctx.slug,
        )


@router.post("/hotels/{slug}/actions/{action_id}/done")
def action_done(action_id: int, body: DoneBody | None = None, ctx: HotelCtx = Depends(require_perm())) -> dict:
    body = body or DoneBody()
    with db.db() as cur:
        return actions_mod.mark_done(cur, action_id, actor=ctx, result=body.result, note=body.note, hotel_slug=ctx.slug)


# ─── Autonomy dial ───


class AutonomyBody(BaseModel):
    level: str
    limits: dict | None = None
    reason: str | None = Field(default=None, max_length=500)


@router.get("/hotels/{slug}/autonomy")
def list_autonomy(ctx: HotelCtx = Depends(require_perm("core.autonomy.view"))) -> list[dict]:
    with db.db() as cur:
        return actions_mod.list_autonomy(cur, ctx.slug)


@router.get("/hotels/{slug}/autonomy/{action_type}")
def get_autonomy(action_type: str, ctx: HotelCtx = Depends(require_perm("core.autonomy.view"))) -> dict:
    with db.db() as cur:
        return actions_mod.get_autonomy(cur, ctx.slug, action_type)


@router.get("/hotels/{slug}/autonomy/{action_type}/events")
def autonomy_events(
    action_type: str,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: HotelCtx = Depends(require_perm("core.autonomy.view")),
) -> list[dict]:
    with db.db() as cur:
        return actions_mod.list_autonomy_events(cur, ctx.slug, action_type, limit=limit)


@router.put("/hotels/{slug}/autonomy/{action_type}")
def put_autonomy(action_type: str, body: AutonomyBody, ctx: HotelCtx = Depends(require_perm("core.autonomy.manage"))) -> dict:
    if body.level not in actions_mod.LEVELS:
        raise ClefsError(422, f"level must be one of {', '.join(actions_mod.LEVELS)}", "invalid_level")
    with db.db() as cur:
        return actions_mod.set_autonomy(
            cur, ctx.slug, action_type, level=body.level, limits=body.limits, actor=ctx, reason=body.reason
        )


@router.post("/hotels/{slug}/autonomy/suggestions/{event_id}/accept")
def accept_suggestion(event_id: int, ctx: HotelCtx = Depends(require_perm("core.autonomy.manage"))) -> dict:
    # manage gets through the door; accept_suggestion then checks
    # accept_perm_for(to_level): raise_auto only above approve (04 spec 1.5).
    with db.db() as cur:
        return actions_mod.accept_suggestion(cur, ctx.slug, event_id, actor=ctx)


@router.post("/hotels/{slug}/autonomy/suggestions/{event_id}/dismiss")
def dismiss_suggestion(event_id: int, ctx: HotelCtx = Depends(require_perm("core.autonomy.manage"))) -> dict:
    with db.db() as cur:
        return actions_mod.dismiss_suggestion(cur, ctx.slug, event_id, actor=ctx)


# ─── Metrics ───


@router.get("/hotels/{slug}/metrics")
def list_metrics(ctx: HotelCtx = Depends(require_perm())) -> list[dict]:
    return metrics_mod.list_metrics(ctx)


@router.post("/hotels/{slug}/metrics/query")
def query_metric(q: metrics_mod.MetricQuery, ctx: HotelCtx = Depends(require_perm())) -> dict:
    return metrics_mod.run_metric(ctx, q)


@router.get("/hotels/{slug}/metrics/{metric}/values/{dimension}")
def metric_values(metric: str, dimension: str, ctx: HotelCtx = Depends(require_perm())) -> list[str]:
    return metrics_mod.dimension_values(ctx, metric, dimension)


# ─── Files ───
# Order matters for uploads: authenticate and size-check BEFORE the multipart
# body is parsed. Declaring File()/Form() parameters would make FastAPI spool
# the whole body (Starlette puts no cap on file parts) for any anonymous
# caller before the handler ran a single check.

# Multipart framing (boundaries, part headers, the purpose field) on top of the file.
UPLOAD_OVERHEAD_BYTES = 64 * 1024


def _too_large(max_bytes: int) -> ClefsError:
    return ClefsError(413, f"Files can be at most {max_bytes // (1024 * 1024)} MB", "file_too_large")


async def _read_capped(upload: UploadFile, max_bytes: int) -> bytes:
    chunks, size = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > max_bytes:
            raise _too_large(max_bytes)
        chunks.append(chunk)
    return b"".join(chunks)


def _capped_request(request: Request, limit: int) -> Request:
    """The same request, with a receive() that stops once the body passes
    `limit` bytes (a chunked upload has no Content-Length to check)."""
    received = 0

    async def receive() -> dict:
        nonlocal received
        message = await request.receive()
        if message.get("type") == "http.request":
            received += len(message.get("body", b""))
            if received > limit:
                raise _too_large(limit - UPLOAD_OVERHEAD_BYTES)
        return message

    return Request(request.scope, receive)


def _upload_principal(request: Request, slug: str, t: str | None) -> tuple[Principal, str | None]:
    """Who is uploading, checked before the body is read: a direct-upload
    ticket (minted by POST /files/upload-ticket for exactly this hotel and a
    purpose, returned for the later match) or a member with core.files.upload
    through the cookie."""
    if t:
        claims = tokens.verify_ticket("upload", t)
        if claims is None:
            raise ClefsError(401, "Upload ticket expired or invalid", "invalid_ticket")
        if claims.get("h") != slug:
            raise ClefsError(403, "This ticket is for another hotel or purpose", "ticket_mismatch")
        kind = claims.get("k") if claims.get("k") in ("user", "device") else "user"
        principal = Principal(kind=kind, user_id=claims.get("u"), device_id=claims.get("d"), label="upload ticket")
        return principal, claims.get("purpose")
    ctx = resolve_ctx(current_principal(request), slug)
    if not ctx.can("core.files.upload"):
        if not ctx.principal.is_authenticated:
            raise ClefsError(401, "Sign in to upload files", "unauthorized")
        raise ClefsError(403, "You cannot upload files here", "forbidden")
    return ctx.principal, None


def _uploader(request: Request, slug: str, purpose: str, t: str | None) -> Principal:
    """Kept for callers that already have the purpose: authenticate, then
    require the ticket's purpose to match."""
    principal, ticket_purpose = _upload_principal(request, slug, t)
    if t and ticket_purpose != purpose:
        raise ClefsError(403, "This ticket is for another hotel or purpose", "ticket_mismatch")
    return principal


def _store_upload(slug: str, data: bytes, filename: str | None, content_type: str | None, purpose: str, actor: Principal) -> dict:
    with db.db() as cur:
        ref = files.put_file(
            cur, slug, data, filename=filename or "upload", content_type=content_type, purpose=purpose, source="upload", actor=actor
        )
        audit(cur, actor, "core.files.upload", "file", ref.id, meta={"purpose": purpose, "size_bytes": ref.size_bytes}, hotel_slug=slug)
        return ref.model_dump()


@router.post("/hotels/{slug}/files")
async def upload_file(slug: str, request: Request, t: str | None = Query(default=None)) -> dict:
    """Multipart `file` + `purpose`. Auth, then Content-Length, then a
    byte-capped parse of one file and a handful of fields."""
    actor, ticket_purpose = await run_in_threadpool(_upload_principal, request, slug, t)
    max_bytes = get_settings().FILE_MAX_BYTES
    limit = max_bytes + UPLOAD_OVERHEAD_BYTES
    length = request.headers.get("content-length")
    if length is not None:
        try:
            if int(length) > limit:
                raise _too_large(max_bytes)
        except ValueError as exc:
            raise ClefsError(400, "Bad Content-Length", "bad_request") from exc
    if not (request.headers.get("content-type") or "").lower().startswith("multipart/form-data"):
        raise ClefsError(400, "Send multipart/form-data with a file and a purpose", "bad_request")
    form = await _capped_request(request, limit).form(max_files=1, max_fields=5)
    try:
        file = form.get("file")
        purpose = form.get("purpose")
        if not isinstance(file, UploadFile):
            raise ClefsError(422, "A file is required", "file_required")
        if not isinstance(purpose, str) or not files.PURPOSE_RE.match(purpose):
            raise ClefsError(400, "purpose must be a short lowercase key", "invalid_purpose")
        if t and ticket_purpose != purpose:
            raise ClefsError(403, "This ticket is for another hotel or purpose", "ticket_mismatch")
        data = await _read_capped(file, max_bytes)
        return await run_in_threadpool(_store_upload, slug, data, file.filename, file.content_type, purpose, actor)
    finally:
        await form.close()


def _disposition(filename: str) -> str:
    ascii_name = filename.encode("ascii", "ignore").decode() or "file"
    ascii_name = ascii_name.replace('"', "").replace("\\", "")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


@router.get("/hotels/{slug}/files/{file_id}")
def download_file(slug: str, file_id: str, request: Request, t: str | None = None) -> Response:
    with db.db() as cur:
        if t:
            if not files.verify_download(slug, file_id, t):
                raise ClefsError(401, "Download link expired or invalid", "invalid_ticket")
        else:
            principal = current_principal(request)
            ctx = resolve_ctx(principal, slug, cur=cur)
            row = files.file_row(cur, slug, file_id)
            mine = (principal.kind == "user" and principal.user_id and principal.user_id == row["uploaded_by"]) or (
                principal.kind == "device" and principal.device_id and principal.device_id == row["uploaded_by_device"]
            )
            if not (ctx.can("core.files.view") or mine):
                if not principal.is_authenticated:
                    raise ClefsError(401, "Sign in required", "unauthorized")
                raise ClefsError(403, "You cannot open this file", "forbidden")
        ref, data = files.get_file(cur, slug, file_id)
    return Response(
        content=data,
        media_type=ref.content_type,
        headers={
            "Content-Disposition": _disposition(ref.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ─── Webhooks ───


@router.get("/webhooks/whatsapp")
def whatsapp_verify(request: Request) -> PlainTextResponse:
    q = request.query_params
    if inbound.verify_whatsapp_challenge(q.get("hub.mode"), q.get("hub.verify_token")):
        return PlainTextResponse(q.get("hub.challenge") or "")
    raise ClefsError(403, "Verification failed", "forbidden")


# Meta batches at most a few hundred KB of JSON and Resend posts metadata
# only; anything bigger is refused before it is buffered in memory (the
# signature check needs the whole body).
WEBHOOK_MAX_BYTES = 1024 * 1024


async def _read_body_capped(request: Request, limit: int) -> bytes:
    length = request.headers.get("content-length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise ClefsError(413, "Body too large", "body_too_large")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise ClefsError(413, "Body too large", "body_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(request: Request) -> dict:
    raw = await _read_body_capped(request, WEBHOOK_MAX_BYTES)
    if not inbound.verify_whatsapp_signature(raw, request.headers.get("x-hub-signature-256")):
        raise ClefsError(401, "Invalid signature", "invalid_signature")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise ClefsError(400, "Body is not JSON", "bad_request") from exc
    counts = await run_in_threadpool(notify_pkg.process_whatsapp, payload)
    return {"ok": True, "processed": counts}


@router.post("/webhooks/email/resend")
async def resend_webhook(request: Request) -> dict:
    if not get_settings().INBOUND_EMAIL_ENABLED:
        raise ClefsError(503, "Inbound email is switched off", "not_configured")
    raw = await _read_body_capped(request, WEBHOOK_MAX_BYTES)
    h = request.headers
    if not inbound.verify_resend_signature(raw, h.get("svix-id"), h.get("svix-timestamp"), h.get("svix-signature")):
        raise ClefsError(401, "Invalid signature", "invalid_signature")
    try:
        event = json.loads(raw)
    except ValueError as exc:
        raise ClefsError(400, "Body is not JSON", "bad_request") from exc
    return await run_in_threadpool(notify_pkg.process_resend, event)


# ─── Internal ───


@router.api_route("/internal/cron/tick", methods=["GET", "POST"])
def cron_tick(
    request: Request,
    budget_s: float = Query(default=25.0, ge=1, le=55),
    _: Principal = Depends(require_service("cron")),
) -> dict:
    """Vercel Cron (GET, Bearer CRON_SECRET) or an external pinger (POST)."""
    jobs.load_all(getattr(request.app.state, "clefs_modules", None))
    report = jobs.tick(budget_s=budget_s, worker_id="cron")
    jobs.record_heartbeat("cron", kind="cron", is_scheduler=True, ticked=True, info={"last": report.as_dict()})
    return report.as_dict()


@router.get("/internal/status")
def internal_status(_: Principal = Depends(require_service("cron"))) -> dict:
    s = get_settings()
    with db.db() as cur:
        out: dict[str, Any] = jobs.status_report(cur)
    out.update(
        events_backend=s.EVENTS_BACKEND,
        events_listener=events.listener_ready.is_set(),
        notify_mode=s.NOTIFY_MODE,
        ai_enabled=ai.enabled(),
    )
    return out
