"""
Menus, orders and upsell console routes. Every path here lives under /hotels/{slug}/menu/
(Canon 13); the guest menu page routes are in router_public.py under /public/menu/.

Permissions (module.py): menu.view reads, menu.manage edits the menu,
menu.orders moves orders along. Every write is audited as menu.<entity>.<verb>.
The HTTP contract is documented in README.md.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from clefs import db, files
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import time as ctime
from clefs.core.errors import bad_request
from clefs.modules.upsell import api, importer, menu, orders
from clefs.modules.upsell import settings as settings_mod

router = APIRouter(prefix="/hotels/{slug}/menu", tags=["upsell"])

VIEW = require_perm("menu.view")
MANAGE = require_perm("menu.manage")
ORDERS = require_perm("menu.orders")
VIEW_OR_ORDERS = require_perm("menu.view", "menu.orders")


# ─── Outlets ───


@router.get("/outlets")
def list_outlets(ctx: HotelCtx = Depends(VIEW_OR_ORDERS)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [menu.outlet_out(o) for o in menu.list_outlets(cur, ctx.slug)]


@router.post("/outlets", status_code=201)
def create_outlet(body: menu.OutletIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = menu.create_outlet(cur, ctx.slug, body)
        audit(cur, ctx, "menu.outlet.create", "ups_outlet", row["id"], after=menu.outlet_out(row))
        return menu.outlet_out(row)


@router.patch("/outlets/{outlet_id}")
def update_outlet(outlet_id: int, body: menu.OutletPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, after = menu.update_outlet(cur, ctx.slug, outlet_id, body)
        audit(cur, ctx, "menu.outlet.update", "ups_outlet", outlet_id,
              before=menu.outlet_out(before), after=menu.outlet_out(after))
        return menu.outlet_out(after)


@router.delete("/outlets/{outlet_id}", status_code=204)
def delete_outlet(outlet_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        before = menu.delete_outlet(cur, ctx.slug, outlet_id)
        audit(cur, ctx, "menu.outlet.delete", "ups_outlet", outlet_id, before=menu.outlet_out(before))
    return Response(status_code=204)


# ─── Items ───


@router.get("/items")
def list_items(outlet_id: int | None = None, ctx: HotelCtx = Depends(VIEW_OR_ORDERS)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [menu.item_out(i) for i in menu.list_items(cur, ctx.slug, outlet_id=outlet_id)]


@router.post("/items", status_code=201)
def create_item(body: menu.ItemIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = menu.create_item(cur, ctx.slug, ctx.hotel.currency, body)
        audit(cur, ctx, "menu.item.create", "ups_menu_item", row["id"], after=menu.item_out(row))
        return menu.item_out(row)


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: menu.ItemPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, after = menu.update_item(cur, ctx.slug, ctx.hotel.currency, item_id, body)
        audit(cur, ctx, "menu.item.update", "ups_menu_item", item_id,
              before=menu.item_out(before), after=menu.item_out(after))
        return menu.item_out(after)


@router.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        before = menu.delete_item(cur, ctx.slug, item_id)
        audit(cur, ctx, "menu.item.delete", "ups_menu_item", item_id, before=menu.item_out(before))
    return Response(status_code=204)


# ─── Pairings ───


def _pair_out(p: dict) -> dict:
    return {k: p[k] for k in ("id", "item_id", "suggest_item_id", "kind", "reason_en", "reason_ar", "active", "sort")}


@router.get("/pairs")
def list_pairs(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [_pair_out(p) for p in menu.list_pairs(cur, ctx.slug)]


@router.post("/pairs", status_code=201)
def upsert_pair(body: menu.PairIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = menu.upsert_pair(cur, ctx.slug, body)
        audit(cur, ctx, "menu.pair.upsert", "ups_upsell_pair", row["id"], after=_pair_out(row))
        return _pair_out(row)


@router.delete("/pairs/{pair_id}", status_code=204)
def delete_pair(pair_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        before = menu.delete_pair(cur, ctx.slug, pair_id)
        audit(cur, ctx, "menu.pair.delete", "ups_upsell_pair", pair_id, before=_pair_out(before))
    return Response(status_code=204)


# ─── CSV import ───


class ImportBody(BaseModel):
    # Either the CSV text itself or a file uploaded through POST /hotels/{slug}/files.
    csv: str | None = Field(None, max_length=importer.MAX_BYTES)
    file_id: str | None = None
    default_outlet: str | None = Field(None, max_length=40)
    create_outlets: bool = True
    dry_run: bool = False
    strict: bool = False


@router.post("/import")
def import_menu(body: ImportBody, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    if not body.csv and not body.file_id:
        raise bad_request("Send csv text or a file_id", "import_source_required")
    with db.db() as cur:
        filename = None
        if body.file_id:
            ref, data = files.get_file(cur, ctx.slug, body.file_id)
            filename = ref.filename
        else:
            data = body.csv or ""
        # actor + filename name the person and file on the ingest.commit audit
        # row and import_batches.uploaded_by (integrator fix).
        report = importer.import_csv(cur, ctx.slug, ctx.hotel.currency, data, default_outlet=body.default_outlet,
                                     create_outlets=body.create_outlets, dry_run=body.dry_run, strict=body.strict,
                                     actor=ctx, filename=filename)
        if report["applied"]:
            audit(cur, ctx, "menu.import.apply", "ups_menu_item", None,
                  meta={k: report[k] for k in ("rows", "created", "updated", "outlets_created")}
                  | {"errors": len(report["errors"]), "file_id": body.file_id})
        return report


# ─── Guest preview ───


@router.get("/preview")
def preview(lang: str = "en", outlet: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The guest menu page payload, as a guest in this hotel sees it now."""
    with db.db(readonly=True) as cur:
        return api.guest_menu(cur, ctx.slug, outlet=outlet, language=lang)


# ─── Orders ───


class QuoteBody(BaseModel):
    room: str = Field(max_length=32)
    lines: list[dict] = Field(max_length=orders.MAX_LINES * 2)
    language: str = Field("en", max_length=12)


class ConfirmBody(BaseModel):
    quote_id: str = Field(max_length=64)
    room: str = Field(max_length=32)
    notes: str | None = Field(None, max_length=500)
    payment: str = "room_charge"


class StatusBody(BaseModel):
    status: str
    reason: str | None = Field(None, max_length=300)


class PaidBody(BaseModel):
    payment: str | None = None


def _console_session(ctx: HotelCtx) -> str:
    """Staff order entry (a phone order to the desk) binds its quote to the
    staff member, so another console user cannot confirm it by accident."""
    p = ctx.principal
    return f"console:{p.user_id or 'd' + str(p.device_id)}"


@router.post("/quote")
def staff_quote(body: QuoteBody, ctx: HotelCtx = Depends(ORDERS)) -> dict:
    with db.db() as cur:
        return api.quote_for_session(cur, ctx.slug, room=body.room, lines=body.lines,
                                     session_ref=_console_session(ctx), language=body.language, source="console")


@router.post("/orders", status_code=201)
def staff_order(body: ConfirmBody, ctx: HotelCtx = Depends(ORDERS)) -> dict:
    with db.db() as cur:
        room = api.check_room(cur, ctx.slug, body.room)
        s = settings_mod.load(cur, ctx.slug)
        o = orders.confirm_quote(cur, ctx.hotel, s, quote_id=body.quote_id, room=room, source="console",
                                 session_ref=_console_session(ctx), notes=body.notes, payment=body.payment)
        if not o.get("deduped"):
            audit(cur, ctx, "menu.order.create", "ups_order", o["order_id"],
                  after={"room": room, "total": o["total"], "currency": o["currency"]})
        return o


@router.get("/orders")
def list_orders(
    status: str | None = None,
    room: str | None = None,
    days: int = Query(7, ge=1, le=366),
    limit: int = Query(100, ge=1, le=500),
    ctx: HotelCtx = Depends(VIEW_OR_ORDERS),
) -> list[dict]:
    """Newest first; `status` takes one value or a comma list (placed,accepted)."""
    since_day = ctime.local_date(ctx.hotel) - timedelta(days=days - 1)
    since = ctime.local_day_bounds(ctx.hotel, since_day)[0]
    wanted = [s.strip() for s in status.split(",") if s.strip()] if status else None
    with db.db(readonly=True) as cur:
        return orders.list_orders(cur, ctx.slug, status=wanted, room=room, since=since, limit=limit)


@router.get("/orders/{order_id}")
def get_order(order_id: int, ctx: HotelCtx = Depends(VIEW_OR_ORDERS)) -> dict:
    with db.db(readonly=True) as cur:
        return orders.get_order(cur, ctx.slug, order_id)


@router.post("/orders/{order_id}/status")
def set_order_status(order_id: int, body: StatusBody, ctx: HotelCtx = Depends(ORDERS)) -> dict:
    with db.db() as cur:
        before, after = orders.set_status(cur, ctx.hotel, order_id, body.status, reason=body.reason)
        if before["status"] != after["status"]:
            audit(cur, ctx, f"menu.order.{body.status}", "ups_order", order_id,
                  before={"status": before["status"]}, after={"status": after["status"], "reason": body.reason})
        return after


@router.post("/orders/{order_id}/paid")
def mark_paid(order_id: int, body: PaidBody | None = None, ctx: HotelCtx = Depends(ORDERS)) -> dict:
    with db.db() as cur:
        before, after = orders.mark_paid(cur, ctx.hotel, order_id, payment=(body.payment if body else None))
        audit(cur, ctx, "menu.order.paid", "ups_order", order_id,
              before={"paid_at": before["paid_at"], "payment": before["payment"]},
              after={"paid_at": after["paid_at"], "payment": after["payment"]})
        return after


# ─── Funnel ───


@router.get("/funnel")
def funnel(start: date | None = None, end: date | None = None, ctx: HotelCtx = Depends(VIEW)) -> dict[str, Any]:
    """Upsell funnel for local dates [start, end] (default: the last 30 days)."""
    end = end or ctime.local_date(ctx.hotel)
    start = start or end - timedelta(days=29)
    if start > end or (end - start).days > 366:
        raise bad_request("Pick a range of at most a year, start before end", "invalid_range")
    with db.db(readonly=True) as cur:
        return orders.funnel_summary(cur, ctx.hotel, start, end)
