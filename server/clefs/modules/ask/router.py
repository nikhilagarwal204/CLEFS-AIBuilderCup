"""
Ask Clefs console routes. Every path here lives under /hotels/{slug}/ask
(Canon 13). Ask is for signed-in people: each question is rate-limited and
logged against a user, so devices and legacy visitors never pass.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth import permissions as perms_mod
from clefs.auth.deps import HotelCtx, current_principal, require_perm, resolve_ctx
from clefs.core.errors import ClefsError
from clefs.modules.ask import catalog, engine, store, tools
from clefs.modules.ask.constants import WA_ROLES

router = APIRouter(prefix="/hotels/{slug}/ask", tags=["ask"])


# ─── Bodies ───


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


class FeedbackIn(BaseModel):
    value: int = Field(description="1 = helpful, -1 = wrong or unhelpful")
    note: str | None = Field(default=None, max_length=1000)


class ViewIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    query: dict | None = None  # query_metric arguments; or take them from log_id
    log_id: int | None = None
    result_index: int = Field(default=0, ge=0, le=2)
    pinned: bool = False


class ViewPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None


class WhatsAppIn(BaseModel):
    opted_in: bool


def _user_id(ctx: HotelCtx) -> int:
    uid = ctx.principal.user_id
    if uid is None:
        raise ClefsError(403, "Ask Clefs needs a signed-in user", "forbidden")
    return int(uid)


# ─── Ask ───


@router.post("")
def ask_question(body: AskIn, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    _user_id(ctx)
    with db.db() as cur:
        return engine.ask(cur, ctx, body.question, channel="WEB")


@router.get("/metrics")
def list_metrics(ctx: HotelCtx = Depends(require_perm("ask.view"))) -> list[dict]:
    """The catalog this viewer may ask about (role-filtered, no guest fields)."""
    return catalog.entries(ctx)


@router.post("/{log_id:int}/feedback")
def feedback(log_id: int, body: FeedbackIn, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    if body.value not in (-1, 1):
        raise ClefsError(400, "value must be 1 or -1", "invalid_feedback")
    with db.db() as cur:
        row = store.set_feedback(cur, ctx.slug, log_id, user_id=_user_id(ctx), value=body.value, note=body.note)
        return store.log_to_api(row)


# ─── History ───


@router.get("/logs")
def list_logs(
    limit: int = Query(20, ge=1, le=100),
    all_users: bool = Query(False, alias="all"),
    feedback_value: int | None = Query(None, alias="feedback"),
    ctx: HotelCtx = Depends(require_perm("ask.view")),
) -> list[dict]:
    """The caller's recent questions; ?all=true (ask.manage) shows the team's,
    e.g. ?all=true&feedback=-1 to review answers marked wrong."""
    if all_users and not ctx.can("ask.manage"):
        raise ClefsError(403, "Only owners and GMs can review the team's questions", "forbidden")
    with db.db() as cur:
        rows = store.list_logs(cur, ctx.slug, user_id=_user_id(ctx), limit=limit, all_users=all_users,
                               feedback=feedback_value)
    return [store.log_to_api(r) for r in rows]


def _log_access(request: Request, slug: str, log: dict) -> HotelCtx:
    """ask.view for your own answers (ask.manage for anyone's), or a magic
    session minted by the WhatsApp reply for exactly this answer."""
    ctx = resolve_ctx(current_principal(request), slug)
    p = ctx.principal
    if p.kind != "user" or p.user_id is None:
        raise ClefsError(401, "Sign in required", "unauthorized")
    own = log["user_id"] == p.user_id
    if ctx.can("ask.manage") or (own and ctx.can("ask.view")):
        return ctx
    # FOUNDATION-REQUEST: a magic-session purpose "ask" that holds ask.view,
    # so this narrow per-answer scope check can go.
    scoped = [int(x) for x in (p.scope or {}).get("ask_log_ids") or [] if str(x).isdigit()]
    if (p.session_kind == "magic" and own and log["id"] in scoped
            and "ask.view" in perms_mod.permissions_for_role(ctx.role)):
        return ctx
    raise ClefsError(403, "You cannot see this answer", "forbidden")


@router.get("/logs/{log_id:int}")
def get_log(log_id: int, slug: str, request: Request) -> dict:
    ctx = resolve_ctx(current_principal(request), slug)
    if ctx.principal.kind != "user":
        raise ClefsError(401, "Sign in required", "unauthorized")
    if ctx.role is None and not ctx.can("ask.view"):
        raise ClefsError(403, "You cannot see this answer", "forbidden")  # before any lookup: ids stay private
    with db.db() as cur:
        row = store.get_log(cur, slug, log_id)
    if row is None:
        raise ClefsError(404, "Answer not found", "ask_log_not_found")
    _log_access(request, slug, row)
    return store.log_to_api(row)


# ─── Saved views (re-run without the LLM) ───


@router.get("/views")
def list_views(ctx: HotelCtx = Depends(require_perm("ask.view"))) -> list[dict]:
    with db.db() as cur:
        return [store.view_to_api(r) for r in store.list_views(cur, ctx.slug, _user_id(ctx))]


@router.post("/views", status_code=201)
def create_view(body: ViewIn, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    """Save {title, query} or {title, log_id[, result_index]}: the stored
    query is the validated query_metric arguments, never SQL."""
    uid = _user_id(ctx)
    with db.db() as cur:
        query, question = body.query, None
        if body.log_id is not None:
            log = store.get_log(cur, ctx.slug, body.log_id)
            if log is None or log["user_id"] != uid:
                raise ClefsError(404, "Answer not found", "ask_log_not_found")
            results = (log["answer"] or {}).get("results") or []
            if body.result_index >= len(results):
                raise ClefsError(400, "That answer has no query to save", "invalid_view_query")
            query, question = results[body.result_index]["query"], log["question"]
        if not query:
            raise ClefsError(400, "Give a query or a log_id", "invalid_view_query")
        # Validate now (400 on a bad query) so a saved view always re-runs.
        spec = _validate(ctx, query)
        row = store.create_view(cur, ctx.slug, uid, title=body.title.strip(), query=spec.args, question=question,
                                source_log_id=body.log_id, pinned=body.pinned)
        audit(cur, ctx, "ask.view.create", "ask_saved_view", row["id"], after={"title": row["title"],
                                                                                "metric": spec.metric})
        return store.view_to_api(row)


def _validate(ctx: HotelCtx, query: dict) -> Any:
    try:
        return tools.validate_query(ctx, query, {e["key"]: e for e in catalog.entries(ctx)})
    except tools.ToolArgError as exc:
        raise ClefsError(400, str(exc), "invalid_view_query") from exc


@router.patch("/views/{view_id:int}")
def patch_view(view_id: int, body: ViewPatch, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    with db.db() as cur:
        row = store.update_view(cur, ctx.slug, _user_id(ctx), view_id,
                                title=body.title.strip() if body.title else None, pinned=body.pinned)
        return store.view_to_api(row)


@router.delete("/views/{view_id:int}", status_code=204)
def delete_view(view_id: int, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> None:
    with db.db() as cur:
        store.delete_view(cur, ctx.slug, _user_id(ctx), view_id)
        audit(cur, ctx, "ask.view.delete", "ask_saved_view", view_id)


@router.post("/views/{view_id:int}/run")
def run_view(view_id: int, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    uid = _user_id(ctx)
    with db.db() as cur:
        view = store.get_view(cur, ctx.slug, uid, view_id)
        out = engine.run_query(cur, ctx, view["query"], title=view["title"], saved_view_id=view_id)
        store.touch_view(cur, view_id)
        return {**out, "view": store.view_to_api(store.get_view(cur, ctx.slug, uid, view_id))}


# ─── WhatsApp Q&A opt-in ───


def _wa_view(cur: Any, ctx: HotelCtx, uid: int) -> dict:
    cur.execute("SELECT phone_e164 FROM users WHERE id = %s", (uid,))
    row = cur.fetchone()
    opt = store.get_optin(cur, ctx.slug, uid)
    return {
        "eligible": ctx.role in WA_ROLES,
        "role": ctx.role,
        "has_phone": bool(row and row["phone_e164"]),
        "opted_in": bool(opt and opt["opted_in"]),
        "updated_at": opt["updated_at"].isoformat() if opt else None,
        "last_asked_at": opt["last_asked_at"].isoformat() if opt and opt["last_asked_at"] else None,
    }


@router.get("/whatsapp")
def get_whatsapp(ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    with db.db() as cur:
        return _wa_view(cur, ctx, _user_id(ctx))


@router.put("/whatsapp")
def put_whatsapp(body: WhatsAppIn, ctx: HotelCtx = Depends(require_perm("ask.view"))) -> dict:
    """Opt yourself in or out of answering questions by WhatsApp. Owners,
    GMs and finance only; the answer goes to the phone on your account."""
    uid = _user_id(ctx)
    if ctx.principal.session_kind == "magic":
        raise ClefsError(403, "Sign in fully to change this", "forbidden")
    if body.opted_in and ctx.role not in WA_ROLES:
        raise ClefsError(403, "WhatsApp questions are for owners, GMs and finance", "ask_whatsapp_role")
    with db.db() as cur:
        view = _wa_view(cur, ctx, uid)
        if body.opted_in and not view["has_phone"]:
            raise ClefsError(409, "Add a WhatsApp number to your profile first", "ask_whatsapp_no_phone")
        store.set_optin(cur, ctx.slug, uid, opted_in=body.opted_in, method="console")
        audit(cur, ctx, "ask.whatsapp.opt_in" if body.opted_in else "ask.whatsapp.opt_out", "user", uid)
        return _wa_view(cur, ctx, uid)
