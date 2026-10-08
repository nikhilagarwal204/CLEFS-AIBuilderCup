"""HTTP routes of core/stays under /hotels/{slug}/stays (mounted through
app.CORE_ROUTER_HOOKS). Reads need core.stays.view (the analyst roles),
writes core.stays.manage (owner, gm). Guest names go through
core.pii by role; contact values never leave the server here.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import pii
from clefs.core.errors import ClefsError
from clefs.core.stays import breakdown, contacts, events, folio, matching, occupancy, otb, reservations
from clefs.core.time import business_date

router = APIRouter(tags=["stays"])
VIEW = "core.stays.view"
MANAGE = "core.stays.manage"
MAX_DAYS = 400


def _range(start: date | None, end: date | None, ctx: HotelCtx, *, default_days: int = 30) -> tuple[date, date]:
    end = end or business_date(ctx.hotel)
    start = start or end - timedelta(days=default_days - 1)
    if end < start:
        raise ClefsError(400, "end is before start", "invalid_range")
    if (end - start).days > MAX_DAYS:
        raise ClefsError(400, f"at most {MAX_DAYS} days", "invalid_range")
    return start, end


# ─── Occupancy ───


class OccupancyBody(BaseModel):
    rooms_occupied: int | None = Field(default=None, ge=0)
    rooms_available: int | None = Field(default=None, ge=0)
    rooms_ooo: int | None = Field(default=None, ge=0)
    arrivals: int | None = Field(default=None, ge=0)
    departures: int | None = Field(default=None, ge=0)
    room_revenue_minor: int | None = Field(default=None, ge=0)


@router.get("/hotels/{slug}/stays/occupancy")
def get_occupancy(
    start: date | None = None,
    end: date | None = None,
    by_floor: bool = False,
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> dict:
    s, e = _range(start, end, ctx)
    with db.db() as cur:
        out: dict[str, Any] = {"start": s, "end": e, "days": occupancy.get_occupancy(cur, ctx.slug, s, e)}
        if by_floor:
            out["by_floor"] = occupancy.occupancy_by_floor(cur, ctx.slug, s, e)
        return out


@router.post("/hotels/{slug}/stays/occupancy/derive")
def derive_occupancy(
    start: date | None = None, end: date | None = None, ctx: HotelCtx = Depends(require_perm(MANAGE))
) -> dict:
    s, e = _range(start, end, ctx, default_days=7)
    with db.db() as cur:
        return occupancy.derive_occupancy(cur, ctx.slug, s, e)


@router.put("/hotels/{slug}/stays/occupancy/{day}")
def put_occupancy(day: date, body: OccupancyBody, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    values = body.model_dump(exclude_none=True)
    if not values:
        raise ClefsError(400, "nothing to set", "invalid_occupancy")
    with db.db() as cur:
        row = occupancy.set_occupancy(cur, ctx.slug, day, values, source="MANUAL", actor=ctx)
        if row is None:  # unreachable for MANUAL (the top rank); kept for the shape
            raise ClefsError(409, "a higher-ranked figure exists", "occupancy_locked")
        return row


@router.delete("/hotels/{slug}/stays/occupancy/{day}")
def delete_occupancy_override(day: date, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return occupancy.clear_override(cur, ctx.slug, day, actor=ctx)


# ─── On the books ───


@router.get("/hotels/{slug}/stays/otb")
def get_otb(
    snapshot_date: date | None = None,
    start: date | None = None,
    end: date | None = None,
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> dict:
    with db.db() as cur:
        snap = snapshot_date or otb.latest_snapshot_date(cur, ctx.slug)
        if snap is None:
            return {"snapshot_date": None, "rows": []}
        s = start or snap
        e = end or s + timedelta(days=89)
        if e < s or (e - s).days > MAX_DAYS:
            raise ClefsError(400, "invalid stay-date range", "invalid_range")
        return {"snapshot_date": snap, "rows": otb.get_otb(cur, ctx.slug, snapshot_date=snap, start=s, end=e)}


@router.get("/hotels/{slug}/stays/otb/pace/{stay_date}")
def get_pace(stay_date: date, days_out: int = Query(default=90, ge=1, le=365),
             ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db() as cur:
        return otb.pace(cur, ctx.slug, stay_date, days_out=days_out)


# ─── OTA claim matches (review queue) ───

_MATCH_NAMES = {"candidates.reservation.guest_name": pii.GUEST_NAME}


class ConfirmBody(BaseModel):
    reservation_ids: list[int] = Field(min_length=1, max_length=20)


@router.get("/hotels/{slug}/stays/matches")
def list_matches(
    status: str = "REVIEW",
    limit: int = Query(default=100, ge=1, le=500),
    ctx: HotelCtx = Depends(require_perm(VIEW)),
) -> list[dict]:
    with db.db() as cur:
        rows = matching.review_queue(cur, ctx.slug, status=status.upper(), limit=limit)
    for r in rows:
        r.pop("query", None)  # the OTA-side name stays server-side
    return pii.mask_for_ctx(rows, ctx, field_classes=_MATCH_NAMES)


@router.post("/hotels/{slug}/stays/matches/{match_id}/confirm")
def confirm_match(match_id: int, body: ConfirmBody, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        row = matching.confirm_match(cur, ctx.slug, match_id, reservation_ids=body.reservation_ids, actor=ctx)
    row.pop("query", None)
    return row


@router.post("/hotels/{slug}/stays/matches/{match_id}/reject")
def reject_match(match_id: int, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        row = matching.reject_match(cur, ctx.slug, match_id, actor=ctx)
    row.pop("query", None)
    return row


# ─── Reservations ───

_RES_NAMES = {"reservation.guest_name": pii.GUEST_NAME}
_RES_FIELDS = ("id", "source_system", "pms_res_id", "channel_code", "ota_res_id", "guest_name", "guest_country",
               "guest_language", "booked_at", "check_in", "check_out", "actual_check_out", "nights", "rooms_count",
               "room_type_code", "room_number", "rate_plan_code", "status", "cancelled_at", "checked_in_at",
               "checked_out_at", "collect_model", "payment_method", "gross_minor", "room_revenue_minor", "rate_basis",
               "currency", "segment", "adults", "children", "stay_chain_id", "promo_code", "booking_engine_ref",
               "updated_at")


@router.get("/hotels/{slug}/stays/reservations/{reservation_id}")
def get_reservation(reservation_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """One stay with its nights, folio totals, contact metadata (never a
    value), stay events and tax breakdown."""
    with db.db() as cur:
        res = reservations.get_reservation(cur, reservation_id)
        if res is None or res["hotel_slug"] != ctx.slug:
            raise ClefsError(404, "reservation not found", "reservation_not_found")
        cur.execute(
            """
            SELECT stay_date, room_seq, room_number, room_type_code, rooms, rate_minor, sc_minor, mf_minor, tf_minor,
                   vat_minor, td_minor, currency, stayed
            FROM reservation_nights WHERE reservation_id = %s ORDER BY stay_date, room_seq
            """,
            (reservation_id,),
        )
        nights = [dict(r) for r in cur.fetchall()]
        b = breakdown.reservation_breakdown(cur, reservation_id)
        out = {
            "reservation": {k: res.get(k) for k in _RES_FIELDS},
            "nights": nights,
            "folio_totals": folio.folio_totals(cur, ctx.slug, reservation_id),
            "contacts": [{k: c[k] for k in ("id", "kind", "provenance", "alias_domain", "usable_until", "purge_after",
                                            "purged_at", "has_value")}
                         for c in contacts.contacts_for(cur, ctx.slug, reservation_id, include_purged=True)],
            "events": events.events_for(cur, ctx.slug, reservation_id),
            "breakdown": _breakdown_dict(b),
        }
    return pii.mask_for_ctx(out, ctx, field_classes=_RES_NAMES)


def _breakdown_dict(b: Any) -> dict:
    from dataclasses import asdict

    return asdict(b)


@router.get("/hotels/{slug}/stays/reservations/{reservation_id}/breakdown")
def get_breakdown(reservation_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db() as cur:
        res = reservations.get_reservation(cur, reservation_id)
        if res is None or res["hotel_slug"] != ctx.slug:
            raise ClefsError(404, "reservation not found", "reservation_not_found")
        return _breakdown_dict(breakdown.reservation_breakdown(cur, reservation_id))


# ─── Baseline exclusions ───


class ExclusionBody(BaseModel):
    from_date: date
    to_date: date
    reason: str = Field(min_length=1, max_length=200)


@router.get("/hotels/{slug}/stays/baseline-exclusions")
def list_exclusions(ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db() as cur:
        return occupancy.baseline_exclusions(cur, ctx.slug)


@router.post("/hotels/{slug}/stays/baseline-exclusions", status_code=201)
def add_exclusion(body: ExclusionBody, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return occupancy.add_exclusion(cur, ctx.slug, body.from_date, body.to_date, body.reason, actor=ctx)


@router.delete("/hotels/{slug}/stays/baseline-exclusions/{exclusion_id}")
def remove_exclusion(exclusion_id: int, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        if not occupancy.remove_exclusion(cur, ctx.slug, exclusion_id, actor=ctx):
            raise ClefsError(404, "no such exclusion of this hotel", "exclusion_not_found")
    return {"ok": True}
