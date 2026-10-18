"""
Amenities console routes. Every path here lives under /hotels/{slug}/amenities/
(Canon 13). The HTTP contract is documented in README.md.

Permissions (module.py): amenities.view reads, amenities.manage edits the
catalog, locations, par and request lines, amenities.issue logs issuance and
stock counts (staff and paired floor devices). Every write is audited as
amenities.<entity>.<verb>.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, acting_staff, require_perm
from clefs.core import ingest
from clefs.core import time as ctime
from clefs.core.errors import bad_request, not_found
from clefs.core.hotels import get_hotel
from clefs.core.money import to_minor
from clefs.modules.amenities import (
    benchmarks,
    catalog,
    detectors,
    issuance,
    par,
    rates,
    reports,
    requests,
    stock,
    variance,
)

router = APIRouter(prefix="/hotels/{slug}/amenities", tags=["amenities"])

VIEW = require_perm("amenities.view")
MANAGE = require_perm("amenities.manage")
ISSUE = require_perm("amenities.issue", "amenities.manage")
VIEW_OR_ISSUE = require_perm("amenities.view", "amenities.issue")
MAX_RANGE_DAYS = 400


# ─── Helpers ───


def _uid(ctx: HotelCtx) -> int | None:
    return getattr(ctx.principal, "user_id", None)


def _range(hotel: Any, start: date | None, end: date | None, default_days: int = 30) -> tuple[date, date]:
    """[start, end] with end defaulting to yesterday (hotel-local)."""
    end = end or ctime.local_date(hotel) - timedelta(days=1)
    start = start or end - timedelta(days=default_days - 1)
    if start > end:
        raise bad_request("from must be on or before to", "invalid_range")
    if (end - start).days >= MAX_RANGE_DAYS:
        raise bad_request(f"At most {MAX_RANGE_DAYS} days per request", "range_too_long")
    return start, end


FROM = Query(None, alias="from")
TO = Query(None, alias="to")


# ─── Items ───


@router.get("/items")
def list_items(ctx: HotelCtx = Depends(VIEW_OR_ISSUE), active_only: bool = False, language: str = "en",
               category: str | None = None) -> list[dict]:
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        # A UAE hotel's first visit seeds the default catalog (flagged for
        # review); amn_seed_runs keeps it to once, so deletions stick.
        if hotel.country == "AE":
            cur.execute("SELECT 1 FROM amn_seed_runs WHERE hotel_slug = %s", (ctx.slug,))
            if not cur.fetchone():
                res = catalog.seed_defaults(cur, ctx.slug, hotel.currency)
                audit(cur, ctx, "amenities.catalog.seed", "amn_items", ctx.slug, after=res)
        lang = "ar" if language == "ar" else "en"
        return [catalog.item_out(r, language=lang)
                for r in catalog.list_items(cur, ctx.slug, active_only=active_only, category=category)]


@router.post("/items", status_code=201)
def create_item(body: catalog.ItemIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        row = catalog.create_item(cur, ctx.slug, hotel.currency, body)
        audit(cur, ctx, "amenities.item.create", "amn_item", row["id"], after=catalog.item_out(row))
        return catalog.item_out(row)


@router.patch("/items/{item_id}")
def update_item(item_id: int, body: catalog.ItemPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, after = catalog.update_item(cur, ctx.slug, item_id, body)
        audit(cur, ctx, "amenities.item.update", "amn_item", item_id, before=catalog.item_out(before),
              after=catalog.item_out(after))
        return catalog.item_out(after)


@router.delete("/items/{item_id}")
def delete_item(item_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Deleted when unused, else deactivated (the ledger keeps its item)."""
    with db.db() as cur:
        before, deleted = catalog.delete_item(cur, ctx.slug, item_id)
        audit(cur, ctx, "amenities.item.delete" if deleted else "amenities.item.deactivate", "amn_item", item_id,
              before=catalog.item_out(before))
        return {"id": item_id, "deleted": deleted, "deactivated": not deleted}


class SeedIn(BaseModel):
    force: bool = False


@router.post("/items/seed-defaults")
def seed_defaults(body: SeedIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        res = catalog.seed_defaults(cur, ctx.slug, hotel.currency, force=bool(body and body.force))
        audit(cur, ctx, "amenities.catalog.seed", "amn_items", ctx.slug, after=res)
        return res


@router.get("/items/{item_id}/costs")
def list_costs(item_id: int, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [catalog.cost_out(r) for r in catalog.list_costs(cur, ctx.slug, item_id)]


@router.post("/items/{item_id}/costs", status_code=201)
def add_cost(item_id: int, body: catalog.CostIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        item = catalog.require_item(cur, ctx.slug, item_id)
        ccy = item["currency"].strip()
        row = catalog.add_cost(cur, ctx.slug, item_id, effective_from=body.effective_from,
                               unit_cost_minor=to_minor(body.unit_cost, ccy), excise_minor=to_minor(body.excise, ccy),
                               source=body.source, invoice_ref=body.invoice_ref, user_id=_uid(ctx))
        audit(cur, ctx, "amenities.cost.add", "amn_item", item_id, after=catalog.cost_out(row))
        hotel = get_hotel(cur, ctx.slug)
        issuance.schedule_rollup(cur, hotel, [body.effective_from])
        return catalog.cost_out(row)


@router.get("/items/{item_id}/trend")
def item_trend(item_id: int, ctx: HotelCtx = Depends(VIEW), start: date | None = FROM,
               end: date | None = TO) -> dict:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end, 56)
        item = catalog.require_item(cur, ctx.slug, item_id)
        return reports.item_trend(cur, hotel, item, s, e)


# ─── Locations and par ───


@router.get("/locations")
def list_locations(ctx: HotelCtx = Depends(VIEW_OR_ISSUE), active_only: bool = False) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [stock.location_out(r) for r in stock.list_locations(cur, ctx.slug, active_only=active_only)]


@router.post("/locations", status_code=201)
def create_location(body: stock.LocationIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = stock.create_location(cur, ctx.slug, body)
        audit(cur, ctx, "amenities.location.create", "amn_location", row["id"], after=stock.location_out(row))
        return stock.location_out(row)


@router.patch("/locations/{location_id}")
def update_location(location_id: int, body: stock.LocationPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, after = stock.update_location(cur, ctx.slug, location_id, body)
        audit(cur, ctx, "amenities.location.update", "amn_location", location_id,
              before=stock.location_out(before), after=stock.location_out(after))
        return stock.location_out(after)


@router.delete("/locations/{location_id}")
def delete_location(location_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before, deleted = stock.delete_location(cur, ctx.slug, location_id)
        audit(cur, ctx, "amenities.location.delete" if deleted else "amenities.location.deactivate", "amn_location",
              location_id, before=stock.location_out(before))
        return {"id": location_id, "deleted": deleted, "deactivated": not deleted}


def _par_with_on_hand(cur: Any, slug: str, rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        o = stock.par_out(r)
        oh = stock.on_hand(cur, slug, r["location_id"], r["item_id"])
        o["on_hand"] = catalog.qty_str(oh["qty"]) if oh else None
        o["last_counted_at"] = oh["counted_at"].isoformat() if oh else None
        out.append(o)
    return out


@router.get("/par")
def list_par(ctx: HotelCtx = Depends(VIEW_OR_ISSUE), location_id: int | None = None) -> list[dict]:
    with db.db(readonly=True) as cur:
        return _par_with_on_hand(cur, ctx.slug, stock.list_par(cur, ctx.slug, location_id=location_id))


@router.put("/par")
def upsert_par(body: stock.ParIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        stock.upsert_par(cur, ctx.slug, body)
        row = next(r for r in stock.list_par(cur, ctx.slug, location_id=body.location_id)
                   if r["item_id"] == body.item_id)
        out = stock.par_out(row)
        audit(cur, ctx, "amenities.par.upsert", "amn_par_level", f"{body.location_id}:{body.item_id}", after=out)
        return out


@router.delete("/par/{location_id}/{item_id}", status_code=204)
def delete_par(location_id: int, item_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    with db.db() as cur:
        if stock.delete_par(cur, ctx.slug, location_id, item_id):
            audit(cur, ctx, "amenities.par.delete", "amn_par_level", f"{location_id}:{item_id}")
    return Response(status_code=204)


@router.post("/par/recompute")
def recompute_par(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        res = par.recompute(cur, hotel, ctime.local_date(hotel))
        audit(cur, ctx, "amenities.par.recompute", "amn_par_level", ctx.slug, after=res)
        return {**res, "par": _par_with_on_hand(cur, ctx.slug, stock.list_par(cur, ctx.slug))}


# ─── Issuance ───


@router.get("/issuance")
def list_issuance(ctx: HotelCtx = Depends(VIEW_OR_ISSUE), start: date | None = FROM, end: date | None = TO,
                  item_id: int | None = None, staff: str | None = None, include_voided: bool = False,
                  limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end or ctime.local_date(hotel), 7)
        return [issuance.issuance_out(r) for r in issuance.list_lines(
            cur, ctx.slug, start=s, end=e, item_id=item_id, staff=staff, include_voided=include_voided, limit=limit)]


@router.post("/issuance", status_code=201)
def create_issuance(body: issuance.IssueIn | issuance.IssueLine, request: Request,
                    ctx: HotelCtx = Depends(ISSUE)) -> dict:
    """One line or {lines: [...]}. On a shared device the on-shift person in
    X-Clefs-Staff is the issuer when a line names nobody."""
    bulk = body if isinstance(body, issuance.IssueIn) else issuance.IssueIn(lines=[body])
    staff_id = acting_staff(ctx, request)
    if staff_id:
        for ln in bulk.lines:
            if not ln.staff_member_id and not ln.staff_name:
                ln.staff_member_id = staff_id
    with db.db() as cur:
        rows = issuance.create_lines(cur, ctx.slug, bulk, source="MANUAL", user_id=_uid(ctx))
        audit(cur, ctx, "amenities.issuance.create", "amn_issuance", rows[0]["id"],
              meta={"lines": len(rows), "ids": [r["id"] for r in rows][:50]})
        return {"lines": [issuance.issuance_out(r) for r in rows]}


@router.post("/issuance/{line_id}/void")
def void_issuance(line_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        row = issuance.void_line(cur, ctx.slug, line_id)
        row["item_key"] = row["item_name"] = None
        audit(cur, ctx, "amenities.issuance.void", "amn_issuance", line_id, after={"voided_at": str(row["voided_at"])})
        issuance.schedule_rollup(cur, get_hotel(cur, ctx.slug), [row["business_date"]])
        return issuance.issuance_out(row)


# CSV/XLSX import rides clefs.core.ingest: upload the file (POST
# /files/upload-ticket, purpose "import"), open a batch here, then commit it
# here. Mapping edits use the ingest routes. These wrappers exist because a
# housekeeping head holds amenities.manage but not core.ingest.manage.
IMPORT_KINDS = ("ISSUANCE", "SUPPLIER_INVOICE")
_BATCH_KEYS = ("id", "kind", "status", "filename", "file_format", "row_count", "headers", "mapping",
               "mapping_source", "gate_status", "printed_total_minor", "parsed_total_minor", "currency", "errors",
               "commit_result", "supersedes_batch_id", "created_at", "committed_at")


class ImportIn(BaseModel):
    file_id: str = Field(min_length=1, max_length=64)
    kind: str = "ISSUANCE"


def _batch_out(cur: Any, b: dict) -> dict:
    out = {k: b.get(k) for k in _BATCH_KEYS}
    out["row_counts"] = ingest.row_counts(cur, b["id"])
    return out


def _import_batch(cur: Any, slug: str, batch_id: int, *, for_update: bool = False) -> dict:
    b = ingest.get_batch(cur, slug, batch_id, for_update=for_update)
    if b["kind"] not in IMPORT_KINDS:
        raise not_found("Import not found", "import_not_found")
    return b


@router.post("/issuance/import", status_code=201)
def import_issuance(body: ImportIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    if body.kind not in IMPORT_KINDS:
        raise bad_request("kind must be ISSUANCE or SUPPLIER_INVOICE", "invalid_kind")
    with db.db() as cur:
        b = ingest.create_batch(cur, ctx.slug, file_id=body.file_id, kind=body.kind, via="WEB", uploaded_by=_uid(ctx))
        if b["kind"] not in IMPORT_KINDS:
            raise bad_request("That file was already imported as another kind", "file_already_imported")
        audit(cur, ctx, "amenities.import.create", "import_batch", b["id"], meta={"kind": body.kind})
        return _batch_out(cur, b)


@router.get("/issuance/import/{batch_id}")
def get_import(batch_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db(readonly=True) as cur:
        return _batch_out(cur, _import_batch(cur, ctx.slug, batch_id))


@router.post("/issuance/import/{batch_id}/commit")
def commit_import(batch_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        _import_batch(cur, ctx.slug, batch_id)
        b = ingest.commit_batch(cur, ctx.slug, batch_id, actor=ctx)
        return _batch_out(cur, b)


# ─── Requests and stock counts ───


@router.get("/requests/{request_id}/items")
def request_items(request_id: int, ctx: HotelCtx = Depends(VIEW_OR_ISSUE)) -> list[dict]:
    with db.db(readonly=True) as cur:
        if not requests.get_request(cur, ctx.slug, request_id):
            raise not_found("Request not found", "request_not_found")
        return [requests.line_out(r) for r in requests.lines_for(cur, ctx.slug, request_id)]


@router.post("/requests/{request_id}/items")
def set_request_items(request_id: int, body: requests.LinesIn, ctx: HotelCtx = Depends(ISSUE)) -> list[dict]:
    """A person states what the request consumed: the list replaces every
    line (MANUAL outranks the tool, aliases and the LLM)."""
    with db.db() as cur:
        before = [requests.line_out(r) for r in requests.lines_for(cur, ctx.slug, request_id)]
        rows = requests.set_manual_lines(cur, ctx.slug, request_id, body)
        out = [requests.line_out(r) for r in rows]
        audit(cur, ctx, "amenities.request_lines.set", "staff_request", request_id, before={"lines": before},
              after={"lines": out})
        return out


@router.post("/stock-counts", status_code=201)
def add_counts(body: stock.CountsIn, request: Request, ctx: HotelCtx = Depends(ISSUE)) -> dict:
    with db.db() as cur:
        rows = stock.add_counts(cur, ctx.slug, body, user_id=_uid(ctx))
        audit(cur, ctx, "amenities.count.create", "amn_stock_count", rows[0]["id"], meta={"lines": len(rows)})
        return {"counts": [stock.count_out(r) for r in rows]}


@router.get("/stock-counts")
def list_counts(ctx: HotelCtx = Depends(VIEW_OR_ISSUE), location_id: int | None = None,
                item_id: int | None = None, limit: int = Query(200, ge=1, le=1000)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [stock.count_out(r) for r in stock.list_counts(cur, ctx.slug, location_id=location_id,
                                                               item_id=item_id, limit=limit)]


@router.get("/stock-counts/consumption")
def count_consumption(location_id: int, item_id: int, ctx: HotelCtx = Depends(VIEW), start: date | None = FROM,
                      end: date | None = TO) -> dict:
    """Exact consumption between consecutive counts (opening + received -
    sent out - closing)."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end or ctime.local_date(hotel), 30)
        stock.get_location(cur, ctx.slug, location_id)
        catalog.require_item(cur, ctx.slug, item_id)
        t0, t1 = ctime.local_day_bounds(hotel, s)[0], ctime.local_day_bounds(hotel, e)[1]
        return {"location_id": location_id, "item_id": item_id,
                "periods": stock.count_consumption(cur, ctx.slug, location_id, item_id, t0, t1)}


# ─── Reports ───


@router.get("/overview")
def overview(ctx: HotelCtx = Depends(VIEW), end: date | None = TO) -> dict:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        return reports.overview(cur, hotel, end or ctime.local_date(hotel) - timedelta(days=1))


@router.get("/report")
def report(ctx: HotelCtx = Depends(VIEW), start: date | None = FROM, end: date | None = TO,
           group: str = "item") -> dict:
    if group not in reports.GROUPS:
        raise bad_request(f"group must be one of {', '.join(reports.GROUPS)}", "invalid_group")
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end)
        return reports.report(cur, hotel, s, e, group)


@router.get("/cpor")
def cpor(ctx: HotelCtx = Depends(VIEW), start: date | None = FROM, end: date | None = TO) -> dict:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end)
        return reports.cpor(cur, hotel, s, e)


@router.get("/minibar")
def minibar(ctx: HotelCtx = Depends(VIEW), start: date | None = FROM, end: date | None = TO) -> dict:
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.slug)
        s, e = _range(hotel, start, end, 7)
        return reports.minibar(cur, hotel, s, e)


@router.get("/rates")
def list_rates(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return rates.list_rates(cur, ctx.slug)


@router.get("/benchmarks")
def get_benchmarks(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return benchmarks.for_hotel(cur, get_hotel(cur, ctx.slug))


class RollupIn(BaseModel):
    start: date | None = Field(None, alias="from")
    end: date | None = Field(None, alias="to")


@router.post("/rollup")
def run_rollup(body: RollupIn | None = None, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Recompute the rollup now (default: the last 14 days) and run the
    detectors for the latest closed day; at most 93 days per call."""
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.slug)
        last = ctime.local_date(hotel) - timedelta(days=1)
        e = min(body.end, last) if body and body.end else last
        s = body.start if body and body.start else e - timedelta(days=13)
        if s > e or (e - s).days > 92:
            raise bad_request("from must be on or before to, at most 93 days", "invalid_range")
        alias = requests.alias_pass(cur, ctx.slug)
        roll = variance.rollup(cur, hotel, s, e)
        det = detectors.run_all(cur, hotel, last)
        audit(cur, ctx, "amenities.rollup.run", "amn_variance_daily", ctx.slug,
              meta={"from": s.isoformat(), "to": e.isoformat()})
        return {"from": s.isoformat(), "to": e.isoformat(), "alias": alias, "rollup": roll, "detectors": det}
