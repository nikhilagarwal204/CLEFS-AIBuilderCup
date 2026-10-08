"""
HTTP routes of the quote-to-cash side (04 spec 4.6-4.11). The contract is
in the module README ("Quotes, payments and verification").

- console: /hotels/{slug}/inquiries/... (quotes, payment links, accounts,
  templates, match reviews, pipeline, outcome);
- public: /public/inquiries/quotes/{token}[/accept] (the /q/[token] page);
- webhooks: /webhooks/inquiries/payments/{provider}/{account_key}.

module.py lists the three routers in MODULE.routers, the console one BEFORE
the intake router, so /pipeline and /match-reviews never parse as an inquiry
id (integrator: this replaced the earlier mount() onto the intake routers).
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core.errors import ClefsError
from clefs.modules.inquiries import quotes, store, templates, verify
from clefs.modules.inquiries.payments import service as pay

console = APIRouter(prefix="/hotels/{slug}/inquiries", tags=["inquiries"])
public = APIRouter(tags=["inquiries-public"])
webhooks = APIRouter(tags=["inquiries-webhooks"])

VIEW = require_perm("inquiries.view")
QUOTE = require_perm("inquiries.quote")
APPROVE = require_perm("inquiries.approve")
MANAGE = require_perm("inquiries.manage")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RoomIn(Strict):
    room_type_code: str | None = Field(None, max_length=40)
    rooms: int = Field(ge=1, le=500)


class EditIn(Strict):
    stay_date: str
    room_type_code: str | None = None
    rate_minor: int = Field(gt=0)


class QuoteIn(Strict):
    rooms: list[RoomIn] | None = Field(None, max_length=10)
    edits: list[EditIn] = Field(default_factory=list, max_length=800)
    reason: str | None = Field(None, max_length=500)
    language: Literal["en", "ar"] | None = None


class QuoteEditIn(Strict):
    edits: list[EditIn] = Field(default_factory=list, max_length=800)
    reason: str | None = Field(None, max_length=500)
    language: Literal["en", "ar"] | None = None


class SendIn(Strict):
    request_approval: bool = False  # True: never self-approve, send to the approver


class LinkIn(Strict):
    quote_version: int = Field(ge=1)
    purpose: Literal["deposit", "full", "balance"] = "deposit"
    amount_minor: int | None = Field(None, gt=0)
    milestone: int | None = Field(None, ge=1)
    provider: Literal["ngenius", "telr", "stripe", "tap", "manual"] | None = None
    url: str | None = Field(None, max_length=2000)
    request_approval: bool = False


class MarkPaidIn(Strict):
    evidence_file_id: str = Field(min_length=1, max_length=64)


class AccountIn(Strict):
    provider: Literal["ngenius", "telr", "stripe", "tap"]
    config: dict = Field(default_factory=dict)
    secret: dict = Field(default_factory=dict)


def _hotel(cur, ctx: HotelCtx):
    from clefs.core.hotels import get_hotel

    return get_hotel(cur, ctx.hotel.slug)


def _rooms(body_rooms: list[RoomIn] | None) -> list[quotes.RoomRequest] | None:
    return [quotes.RoomRequest(r.room_type_code, r.rooms) for r in body_rooms] if body_rooms else None


# ─── Static console paths (mounted before /{inquiry_id}) ───


@console.get("/pipeline")
def get_pipeline(ctx: HotelCtx = Depends(VIEW)) -> dict:
    from clefs.modules.inquiries import signals

    with db.db(readonly=True) as cur:
        return signals.pipeline(cur, _hotel(cur, ctx))


def _mask_review(row: dict, ctx: HotelCtx) -> dict:
    """Leak and verify reviews name a PMS guest: hidden for roles without
    guests.view (Canon 19)."""
    out = {k: v for k, v in row.items()}
    if not ctx.can("guests.view"):
        out["res_guest_name"] = None
        ev = dict(out.get("evidence") or {})
        out["evidence"] = {k: v for k, v in ev.items() if k not in ("identity", "provenance")}
    return out


@console.get("/match-reviews")
def get_reviews(status: Literal["open", "confirmed", "rejected", "superseded"] = "open",
                kind: Literal["verify", "leak"] | None = None, ctx: HotelCtx = Depends(APPROVE)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [_mask_review(r, ctx) for r in verify.list_reviews(cur, ctx.hotel.slug, status=status, kind=kind)]


@console.post("/match-reviews/{review_id}/confirm")
def confirm_review(review_id: int, ctx: HotelCtx = Depends(APPROVE)) -> dict:
    with db.db() as cur:
        return verify.decide_review(cur, _hotel(cur, ctx), review_id, confirm=True, actor=ctx)


@console.post("/match-reviews/{review_id}/reject")
def reject_review(review_id: int, ctx: HotelCtx = Depends(APPROVE)) -> dict:
    with db.db() as cur:
        return verify.decide_review(cur, _hotel(cur, ctx), review_id, confirm=False, actor=ctx)


@console.get("/payment-accounts")
def get_accounts(ctx: HotelCtx = Depends(MANAGE)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return pay.list_accounts(cur, ctx.hotel.slug)


@console.post("/payment-accounts", status_code=201)
def post_account(body: AccountIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return pay.connect(cur, ctx.hotel.slug, provider=body.provider, config=body.config, secret=body.secret, actor=ctx)


@console.delete("/payment-accounts/{provider}", status_code=204)
def delete_account(provider: Literal["ngenius", "telr", "stripe", "tap"], ctx: HotelCtx = Depends(MANAGE)) -> None:
    with db.db() as cur:
        if not pay.disconnect(cur, ctx.hotel.slug, provider, actor=ctx):
            raise ClefsError(404, "not connected", "not_connected")


@console.get("/templates")
def get_templates(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db() as cur:
        return templates.list_templates(cur, ctx.hotel.slug)


@console.post("/templates/{template_id}/approve")
def approve_template(template_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return templates.approve(cur, ctx.hotel.slug, template_id, actor=ctx)


@console.get("/quote-settings")
def get_quote_settings(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return quotes.quote_settings(cur, ctx.hotel.slug).model_dump(mode="json")


@console.put("/quote-settings")
def put_quote_settings(body: dict, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        return quotes.save_quote_settings(cur, ctx.hotel.slug, body, actor=ctx).model_dump(mode="json")


@console.post("/payment-links/{link_id}/cancel")
def cancel_link(link_id: int, ctx: HotelCtx = Depends(QUOTE)) -> dict:
    with db.db() as cur:
        return pay.link_to_api(pay.cancel(cur, _hotel(cur, ctx), link_id, actor=ctx))


@console.post("/payment-links/{link_id}/mark-paid")
def mark_paid(link_id: int, body: MarkPaidIn, ctx: HotelCtx = Depends(APPROVE)) -> dict:
    with db.db() as cur:
        return pay.link_to_api(pay.mark_paid_manual(cur, _hotel(cur, ctx), link_id,
                                                    evidence_file_id=body.evidence_file_id, actor=ctx))


# ─── Per inquiry ───


@console.get("/{inquiry_id}/quotes")
def get_quotes(inquiry_id: int, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        store.get_inquiry(cur, ctx.hotel.slug, inquiry_id)
        return [quotes.to_api(q) for q in quotes.list_quotes(cur, inquiry_id)]


@console.post("/{inquiry_id}/quotes", status_code=201)
def post_quote(inquiry_id: int, body: QuoteIn, ctx: HotelCtx = Depends(QUOTE)) -> dict:
    with db.db() as cur:
        hotel = _hotel(cur, ctx)
        inquiry = store.get_inquiry(cur, hotel.slug, inquiry_id, lock=True)
        q = quotes.build(cur, hotel, inquiry, rooms=_rooms(body.rooms), edits=[e.model_dump() for e in body.edits],
                         reason=body.reason, can_approve=ctx.can("inquiries.approve"), language=body.language, actor=ctx)
        return quotes.to_api(q)


@console.get("/{inquiry_id}/quotes/{version}")
def get_quote(inquiry_id: int, version: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return quotes.to_api(quotes.get_quote(cur, ctx.hotel.slug, inquiry_id, version))


@console.put("/{inquiry_id}/quotes/{version}")
def put_quote(inquiry_id: int, version: int, body: QuoteEditIn, ctx: HotelCtx = Depends(QUOTE)) -> dict:
    with db.db() as cur:
        hotel = _hotel(cur, ctx)
        q = quotes.get_quote(cur, hotel.slug, inquiry_id, version, lock=True)
        return quotes.to_api(quotes.revise(cur, hotel, q, edits=[e.model_dump() for e in body.edits], reason=body.reason,
                                           can_approve=ctx.can("inquiries.approve"), language=body.language))


@console.post("/{inquiry_id}/quotes/{version}/send")
def send_quote(inquiry_id: int, version: int, body: SendIn | None = None, ctx: HotelCtx = Depends(QUOTE)) -> dict:
    """Send (a holder of inquiries.approve approves at once) or request
    approval. The dial still decides when nobody approves by hand."""
    body = body or SendIn()
    with db.db() as cur:
        hotel = _hotel(cur, ctx)
        q = quotes.get_quote(cur, hotel.slug, inquiry_id, version, lock=True)
        act = quotes.request_send(cur, hotel, q, actor=ctx, approve_now=ctx.can("inquiries.approve")
                                  and not body.request_approval)
        return {"quote": quotes.to_api(quotes.quote_by_id(cur, q["id"])), "action": act}


@console.get("/{inquiry_id}/payment-links")
def get_links(inquiry_id: int, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        store.get_inquiry(cur, ctx.hotel.slug, inquiry_id)
        return [pay.link_to_api(r) for r in pay.list_links(cur, inquiry_id)]


@console.post("/{inquiry_id}/payment-links", status_code=201)
def post_link(inquiry_id: int, body: LinkIn, ctx: HotelCtx = Depends(QUOTE)) -> dict:
    with db.db() as cur:
        hotel = _hotel(cur, ctx)
        q = quotes.get_quote(cur, hotel.slug, inquiry_id, body.quote_version, lock=True)
        act = pay.request_link(cur, hotel, q, purpose=body.purpose, amount_minor=body.amount_minor, actor=ctx,
                               milestone=body.milestone, provider=body.provider, url=body.url,
                               approve_now=ctx.can("inquiries.approve") and not body.request_approval)
        return {"action": act}


@console.get("/{inquiry_id}/outcome")
def get_outcome(inquiry_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The verified or leaked outcome (null fields until there is one). Leak
    details are internal: hidden without guests.view (Canon 19)."""
    from clefs.core import money

    with db.db(readonly=True) as cur:
        store.get_inquiry(cur, ctx.hotel.slug, inquiry_id)
        out = verify.get_outcome(cur, inquiry_id)
        if out is None:
            return {"inquiry_id": inquiry_id, "outcome": None}
        ccy = out["currency"]
        for k in ("verified_revenue_minor", "commission_avoided_minor", "payment_fee_minor", "outcome_net_minor",
                  "leaked_commission_minor"):
            out[k.replace("_minor", "_value")] = money.money_str(out.get(k), ccy)
        if out["outcome"] == "leaked_to_ota" and not ctx.can("guests.view"):
            for k in ("leaked_reservation_id", "match_evidence", "profit_guard_ref"):
                out[k] = None
        return out


# ─── Public quote page (/q/[token]) ───


def _public_limit(cur, token: str, what: str) -> None:
    from clefs import ratelimit
    from clefs.core.context import request_info
    from clefs.core.errors import too_many
    from clefs.modules.inquiries.router_public import _ip_hash

    if not ratelimit.hit(cur, f"inq:quote:{what}:{_ip_hash(request_info().ip)}", limit=60, window_s=600):
        raise too_many("too many requests")


@public.get("/public/inquiries/quotes/{token}")
def public_quote(token: str, lang: Literal["en", "ar"] | None = None) -> JSONResponse:
    with db.db() as cur:
        _public_limit(cur, token, "view")
    with db.db(readonly=True) as cur:
        data = quotes.public_view(cur, token, language=lang)
        data["pay_url"] = pay.open_link_url(cur, token)
    return JSONResponse(data, headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


@public.post("/public/inquiries/quotes/{token}/accept")
def public_accept(token: str) -> JSONResponse:
    with db.db() as cur:
        _public_limit(cur, token, "accept")
    with db.db() as cur:
        out = quotes.accept(cur, token)
    return JSONResponse(out, headers={"X-Robots-Tag": "noindex", "Cache-Control": "no-store"})


# ─── Payment webhooks ───


@webhooks.post("/webhooks/inquiries/payments/{provider}/{account_key}")
async def payment_webhook(provider: str, account_key: str, request: Request) -> JSONResponse:
    raw = await request.body()
    if len(raw) > 256 * 1024:
        raise ClefsError(413, "payload too large", "payload_too_large")
    headers = {k: v for k, v in request.headers.items()}
    with db.db() as cur:
        status, body = pay.ingest_webhook(cur, provider, account_key, headers, raw)
    return JSONResponse(body, status_code=status)
