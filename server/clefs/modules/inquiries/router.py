"""
Inquiry Guard console routes, all under /hotels/{slug}/inquiries (Canon 13).
The HTTP contract is in this module's README.md. Static paths are declared
before /{inquiry_id} so "settings", "widget" and "channels" never parse as
an id.

Contacts leave the server masked by role (store.to_api): revenue sees no
contact, front desk sees it in full (Trust matrix, 04 spec 7.1).
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core.time import local_date
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.inquiries import store
from clefs.modules.inquiries.models import SEGMENTS, SOURCES, STAGES

router = APIRouter(prefix="/hotels/{slug}/inquiries", tags=["inquiries"])

VIEW = require_perm("inquiries.view")
RESPOND = require_perm("inquiries.respond")
MANAGE = require_perm("inquiries.manage")


def _user_id(ctx: HotelCtx) -> int | None:
    return ctx.principal.user_id


# ─── List ───


@router.get("")
def list_inquiries(
    stage: str | None = Query(None, description="comma list"),
    segment: str | None = Query(None, description="comma list"),
    source: str | None = Query(None, description="comma list"),
    sla: Literal["breached", "due", "answered"] | None = None,
    check_in_from: date | None = None,
    check_in_to: date | None = None,
    include_test: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    ctx: HotelCtx = Depends(VIEW),
) -> list[dict]:
    where, params = ["hotel_slug = %s", "duplicate_of IS NULL"], [ctx.hotel.slug]
    for col, raw, allowed in (("stage", stage, STAGES), ("segment", segment, SEGMENTS), ("source", source, SOURCES)):
        if raw:
            vals = [v.strip() for v in raw.split(",") if v.strip()]
            bad = [v for v in vals if v not in allowed]
            if bad:
                raise ClefsError(400, f"unknown {col}: {', '.join(bad)}", f"invalid_{col}")
            where.append(f"{col} = ANY(%s)")
            params.append(vals)
    if sla == "breached":
        where.append("sla_breached AND first_response_at IS NULL")
    elif sla == "due":
        where.append("NOT sla_breached AND first_response_at IS NULL AND stage = 'new'")
    elif sla == "answered":
        where.append("first_response_at IS NOT NULL")
    if check_in_from:
        where.append("check_in >= %s")
        params.append(check_in_from)
    if check_in_to:
        where.append("check_in <= %s")
        params.append(check_in_to)
    if not include_test:
        where.append("NOT is_test")
    with db.db(readonly=True) as cur:
        cur.execute(
            f"SELECT * FROM inq_inquiries WHERE {' AND '.join(where)} ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s",
            (*params, limit, offset),
        )
        return [store.to_api(dict(r), ctx) for r in cur.fetchall()]


# ─── Log an inquiry (phone desk; 4.3) ───


class LogIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["phone_desk"] = "phone_desk"
    text: str = Field(min_length=1, max_length=5000)
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    company: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=10)
    check_in: date | None = None
    check_out: date | None = None
    rooms: int | None = Field(default=None, ge=1, le=200)
    adults: int | None = Field(default=None, ge=1, le=2000)
    children: int | None = Field(default=None, ge=0, le=2000)


@router.post("", status_code=201)
def log_inquiry(body: LogIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    from clefs.modules.inquiries.intake import Inbound, ingest

    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        res = ingest(cur, hotel, Inbound(
            source=body.source, text=body.text, name=body.name, phone=body.phone, email=body.email,
            company=body.company, language=body.language, check_in=body.check_in, check_out=body.check_out,
            rooms=body.rooms, adults=body.adults, children=body.children, strict_contact=True,
            consent_basis="staff_logged", author_type="staff", author_user_id=_user_id(ctx),
            provider_message_id=None,
        ), actor=ctx)
        audit(cur, ctx, "inquiries.log", "inquiry", res.inquiry["id"], after={"source": body.source, "outcome": res.outcome},
              hotel_slug=hotel.slug)
        return {**store.to_api(res.inquiry, ctx), "outcome": res.outcome}


# ─── Settings (4.11; also reachable through core module-settings) ───


@router.get("/settings")
def get_settings_route(ctx: HotelCtx = Depends(VIEW)) -> dict:
    from clefs.modules.inquiries.sla import settings_for

    with db.db(readonly=True) as cur:
        return settings_for(cur, ctx.hotel.slug).model_dump()


@router.put("/settings")
def put_settings(body: dict, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from pydantic import ValidationError

    from clefs.core.hotels import set_module_settings
    from clefs.modules.inquiries.settings import InquirySettings
    from clefs.modules.inquiries.sla import settings_for

    with db.db() as cur:
        merged = {**settings_for(cur, ctx.hotel.slug).model_dump(), **(body or {})}
        try:
            clean = InquirySettings.model_validate(merged)
        except ValidationError as exc:
            raise ClefsError(422, str(exc.errors()[0].get("msg")), "invalid_settings") from exc
        set_module_settings(cur, ctx.hotel.slug, "inquiries", clean.model_dump(), actor=ctx)
        return clean.model_dump()


# ─── Widget (4.3) ───


def _embed(slug: str, key: str) -> dict:
    script = f'<script src="https://www.clefs.world/w/inquiry.js" data-hotel="{slug}" data-key="{key}" async></script>'
    return {"widget_key": key, "embed_code": script, "iframe_path": f"/w/inquiry/{slug}?k={key}"}


@router.get("/widget")
def get_widget(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.modules.inquiries import channels

    with db.db() as cur:
        row = channels.ensure_widget(cur, ctx.hotel.slug, actor_user_id=_user_id(ctx))
        cfg = row.get("config") or {}
        return {**_embed(ctx.hotel.slug, cfg["widget_key"]), "allowed_origins": cfg.get("allowed_origins") or []}


class OriginsIn(BaseModel):
    allowed_origins: list[str] = Field(default_factory=list, max_length=20)


@router.put("/widget")
def put_widget(body: OriginsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.modules.inquiries import channels

    with db.db() as cur:
        row = channels.set_widget_origins(cur, ctx.hotel.slug, body.allowed_origins, actor_user_id=_user_id(ctx))
        audit(cur, ctx, "inquiries.widget_origins", "inq_channel_account", row["id"],
              after={"allowed_origins": row["config"].get("allowed_origins")}, hotel_slug=ctx.hotel.slug)
        cfg = row["config"]
        return {**_embed(ctx.hotel.slug, cfg["widget_key"]), "allowed_origins": cfg.get("allowed_origins") or []}


@router.post("/widget/rotate")
def rotate_widget(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.modules.inquiries import channels

    with db.db() as cur:
        row = channels.rotate_widget(cur, ctx.hotel.slug, actor_user_id=_user_id(ctx))
        audit(cur, ctx, "inquiries.widget_rotate", "inq_channel_account", row["id"], hotel_slug=ctx.hotel.slug)
        cfg = row["config"]
        return {**_embed(ctx.hotel.slug, cfg["widget_key"]), "allowed_origins": cfg.get("allowed_origins") or []}


# ─── Channels (4.3; intake kinds only, payments are their own part) ───


class ChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["meta_page", "google_leadform", "web_widget"]
    page_id: str | None = Field(default=None, max_length=40)
    page_access_token: str | None = Field(default=None, max_length=1000)
    page_name: str | None = Field(default=None, max_length=200)
    google_key: str | None = Field(default=None, max_length=200)
    allowed_origins: list[str] | None = Field(default=None, max_length=20)


@router.get("/channels")
def list_channels(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.modules.inquiries import channels
    from clefs.notify import hotel_inbound_address

    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        return {
            "accounts": channels.list_accounts(cur, ctx.hotel.slug),
            "whatsapp_connected": bool(hotel.whatsapp_phone_number_id),
            "email_address": hotel_inbound_address(cur, ctx.hotel.slug, "inquiries"),
        }


@router.post("/channels", status_code=201)
def connect_channel(body: ChannelIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    from clefs.modules.inquiries import channels

    uid = _user_id(ctx)
    with db.db() as cur:
        if body.kind == "meta_page":
            out = channels.to_api(channels.connect_meta_page(
                cur, ctx.hotel.slug, page_id=body.page_id or "", page_access_token=body.page_access_token or "",
                page_name=body.page_name, actor_user_id=uid))
        elif body.kind == "google_leadform":
            out = channels.connect_google_leadform(cur, ctx.hotel.slug, google_key=body.google_key, actor_user_id=uid)
        else:
            row = channels.ensure_widget(cur, ctx.hotel.slug, actor_user_id=uid)
            if body.allowed_origins is not None:
                row = channels.set_widget_origins(cur, ctx.hotel.slug, body.allowed_origins, actor_user_id=uid)
            out = channels.to_api(row)
        audit(cur, ctx, "inquiries.channel_connect", "inq_channel_account", out.get("id"),
              after={"kind": body.kind, "status": out.get("status")}, hotel_slug=ctx.hotel.slug)
        return out


@router.delete("/channels/{kind}", status_code=204)
def disconnect_channel(kind: Literal["meta_page", "google_leadform", "web_widget"], ctx: HotelCtx = Depends(MANAGE)) -> None:
    from clefs.modules.inquiries import channels

    with db.db() as cur:
        if not channels.disconnect(cur, ctx.hotel.slug, kind):
            raise ClefsError(404, "channel not connected", "not_connected")
        audit(cur, ctx, "inquiries.channel_disconnect", "inq_channel_account", kind, hotel_slug=ctx.hotel.slug)


# ─── One inquiry ───


@router.get("/{inquiry_id}")
def get_inquiry(inquiry_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        row = store.get_inquiry(cur, ctx.hotel.slug, inquiry_id)
        out = store.to_api(row, ctx, with_thread=store.messages(cur, inquiry_id))
        cur.execute(
            "SELECT from_stage, to_stage, at, actor_type, actor_id, reason FROM inq_stage_events "
            "WHERE inquiry_id = %s ORDER BY at, id",
            (inquiry_id,),
        )
        out["stage_events"] = [dict(r) for r in cur.fetchall()]
        from clefs.modules.inquiries import dedupe

        out["merge_suggestions"] = dedupe.suggestions(cur, row)
        return out


class PatchIn(BaseModel):
    """What a person may correct in the extraction panel. A segment set here
    is never overwritten by a later extraction (segment_by = staff)."""

    model_config = ConfigDict(extra="forbid")

    segment: Literal["fit", "corporate", "group", "long_stay", "event", "unknown"] | None = None
    is_rfp: bool | None = None
    check_in: date | None = None
    check_out: date | None = None
    rooms: int | None = Field(default=None, ge=1, le=200)
    adults: int | None = Field(default=None, ge=1, le=2000)
    children: int | None = Field(default=None, ge=0, le=2000)
    language: str | None = Field(default=None, max_length=10)
    company: str | None = Field(default=None, max_length=200)
    contact_name: str | None = Field(default=None, max_length=200)
    room_type_code: str | None = Field(default=None, max_length=40)
    dates_flexible: bool | None = None
    assignee_user_id: int | None = None


@router.patch("/{inquiry_id}")
def patch_inquiry(inquiry_id: int, body: PatchIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    from clefs.modules.inquiries import risk, sla

    changes = body.model_dump(exclude_unset=True)
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        ci, co = changes.get("check_in", row["check_in"]), changes.get("check_out", row["check_out"])
        if ci and co:
            if co <= ci:
                raise ClefsError(400, "check_out must be after check_in", "invalid_dates")
            changes["nights"] = (co - ci).days
        if "segment" in changes:
            changes["extraction"] = {**(row.get("extraction") or {}), "segment_by": "staff"}
        before = {k: row.get(k) for k in changes if k != "extraction"}
        row = store.update_inquiry(cur, inquiry_id, changes)
        row = store.update_inquiry(cur, inquiry_id, risk.compute(cur, hotel, row))
        if row["stage"] == "new":
            sla.arm(cur, hotel, row)  # a segment change moves the due time
        audit(cur, ctx, "inquiries.edit", "inquiry", inquiry_id, before=_jsonable(before),
              after=_jsonable({k: row.get(k) for k in before}), hotel_slug=hotel.slug)
        return store.to_api(row, ctx)


def _jsonable(d: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in d.items()}


# ─── Closing moves (4.2, 4.10) ───


class CloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=2000)
    remind_on: date | None = None


def _close(inquiry_id: int, ctx: HotelCtx, to_stage: str, body: CloseIn) -> dict:
    from clefs.modules.inquiries import service

    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        if to_stage == "future" and (body.remind_on is None or body.remind_on <= local_date(hotel)):
            raise ClefsError(400, "remind_on must be a future date", "remind_on_required")
        service.transition(cur, row, to_stage, actor=ctx, reason=body.reason or to_stage, lost_reason=body.reason,
                           lost_note=body.note, remind_on=body.remind_on, hotel=hotel)
        return store.to_api(row, ctx)


@router.post("/{inquiry_id}/lost")
def mark_lost(inquiry_id: int, body: CloseIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    return _close(inquiry_id, ctx, "lost", body)


@router.post("/{inquiry_id}/decline")
def decline(inquiry_id: int, body: CloseIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    return _close(inquiry_id, ctx, "turned_down", body)


@router.post("/{inquiry_id}/future")
def mark_future(inquiry_id: int, body: CloseIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    return _close(inquiry_id, ctx, "future", body.model_copy(update={"reason": body.reason or "DATES_CHANGED"}))


@router.post("/{inquiry_id}/spam")
def mark_spam(inquiry_id: int, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    return _close(inquiry_id, ctx, "spam", CloseIn(reason="SPAM_DUPLICATE"))


# ─── Staff reply (the console thread) ───
# Integrator (Wave 2): the console's ThreadPanel posts a person's reply here.
# WhatsApp free-form only inside the guest's 24 h window (outside it Meta
# needs an approved template: the quote and follow-up actions send those);
# email replies carry the thread's +inq-<ref> Reply-To so the answer threads
# back. A delivered reply is the first response (service.on_outbound stops
# the SLA clock and moves new -> replied).

class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=4000)
    channel: Literal["whatsapp", "email"]


@router.post("/{inquiry_id}/messages", status_code=201)
def post_message(inquiry_id: int, body: ReplyIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    """A staff-typed reply (4.11): sent at once through replies.send_reply,
    the same path the approved AI draft takes."""
    from clefs.modules.inquiries import replies

    text = body.body.strip()
    if not text:
        raise ClefsError(400, "Write a reply first", "empty_body")
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        msg = replies.send_reply(cur, hotel, row, channel=body.channel, text=text, author_type="staff",
                                 author_user_id=_user_id(ctx), by=f"staff:{_user_id(ctx)}", actor=ctx)
        # The person answered: an AI draft still waiting would be a second,
        # stale reply if someone approved it later.
        replies.cancel_open_drafts(cur, hotel.slug, inquiry_id, reason="replied_in_console")
        audit(cur, ctx, "inquiries.reply", "inquiry", inquiry_id, after={"channel": body.channel},
              hotel_slug=hotel.slug)
        return msg


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    channel: Literal["whatsapp", "email"] | None = None


@router.post("/{inquiry_id}/draft-reply", status_code=201)
def post_draft_reply(inquiry_id: int, body: DraftIn | None = None, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    """An AI draft (4.11): price-linted, stored as an `inquiries.respond`
    action the dial governs (default suggest, never auto). The console puts
    `body` in the composer; approving the action sends it as is."""
    from clefs.modules.inquiries import replies

    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        out = replies.draft_reply(cur, hotel, row, channel=(body.channel if body else None), actor=ctx)
        audit(cur, ctx, "inquiries.draft_reply", "inquiry", inquiry_id,
              after={"channel": out["channel"], "action_id": out["action"].get("id")}, hotel_slug=hotel.slug)
        return out


# ─── Merge (4.4 suggestions; a person decides) ───


class MergeIn(BaseModel):
    into_id: int


@router.post("/{inquiry_id}/merge")
def merge_inquiry(inquiry_id: int, body: MergeIn, ctx: HotelCtx = Depends(RESPOND)) -> dict:
    from clefs.modules.inquiries import dedupe

    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        row = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        into = store.get_inquiry(cur, hotel.slug, body.into_id, lock=True)
        target = dedupe.merge(cur, hotel, row, into, actor=ctx)
        audit(cur, ctx, "inquiries.merge", "inquiry", inquiry_id, after={"into": body.into_id}, hotel_slug=hotel.slug)
        return store.to_api(target, ctx)
