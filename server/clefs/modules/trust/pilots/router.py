"""
Pilot scorecard, ROI report and benchmark routes (03 spec 5.5), under
/hotels/{slug}/trust/. Cohort administration is platform data and lives
under the same prefix but needs trust.benchmarks.admin (Clefs admins only).
The HTTP contract is listed in the module README.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from clefs import db
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import time as htime
from clefs.core.hotels import get_hotel
from clefs.modules.trust.pilots import benchmarks, checkpoints, report, store

router = APIRouter(prefix="/hotels/{slug}/trust", tags=["trust"])

VIEW = "trust.pilots.view"
MANAGE = "trust.pilots.manage"
ADMIN = "trust.benchmarks.admin"


# ─── Pilots ───


@router.get("/pilots")
def get_pilots(ctx: HotelCtx = Depends(require_perm(VIEW))) -> list[dict]:
    with db.db() as cur:
        return store.list_pilots(cur, ctx.hotel.slug)


@router.post("/pilots", status_code=201)
def post_pilot(body: store.PilotIn, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return store.create_pilot(cur, ctx, ctx.hotel.slug, body)


@router.get("/pilots/{pilot_id}")
def get_pilot(pilot_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    """The scorecard: the pilot, a live evaluation to yesterday and its
    checkpoint history."""
    with db.db() as cur:
        pilot = store.get_pilot(cur, ctx.hotel.slug, pilot_id)
        hotel = get_hotel(cur, ctx.hotel.slug)
        cur.execute('SELECT date, "values", score, on_track FROM trs_pilot_checkpoints WHERE pilot_id = %s ORDER BY date',
                    (pilot_id,))
        history = [{**dict(r), "score": None if r["score"] is None else str(r["score"])} for r in cur.fetchall()]
    live = pilot["outcome"] if pilot["status"] in ("completed", "converted") else store.evaluate(hotel, pilot, until=htime.local_date(hotel))
    return {**pilot, "scorecard": live, "checkpoints": history}


@router.patch("/pilots/{pilot_id}")
def patch_pilot(pilot_id: int, body: store.PilotPatch, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return store.update_pilot(cur, ctx, ctx.hotel.slug, pilot_id, body)


class StatusIn(BaseModel):
    status: Literal["active", "completed", "converted", "cancelled"]


@router.post("/pilots/{pilot_id}/status")
def post_status(pilot_id: int, body: StatusIn, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    with db.db() as cur:
        return store.set_status(cur, ctx, ctx.hotel.slug, pilot_id, body.status)


@router.post("/pilots/{pilot_id}/checkpoint")
def post_checkpoint(pilot_id: int, ctx: HotelCtx = Depends(require_perm(MANAGE))) -> dict:
    """Run today's checkpoint now (the job runs at 07:00 for yesterday)."""
    with db.db() as cur:
        pilot = store.get_pilot(cur, ctx.hotel.slug, pilot_id)
        hotel = get_hotel(cur, ctx.hotel.slug)
        day = min(htime.local_date(hotel), pilot["end_date"])
        return checkpoints.checkpoint(cur, ctx.hotel.slug, pilot, day=day)


@router.get("/pilots/{pilot_id}/report")
def get_report(pilot_id: int, ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db() as cur:
        return report.build(cur, ctx.hotel.slug, pilot_id)


# ─── Benchmarks ───


class OptInIn(BaseModel):
    opted_in: bool


@router.get("/benchmarks")
def get_benchmarks(period: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}$"),
                   ctx: HotelCtx = Depends(require_perm(VIEW))) -> dict:
    with db.db() as cur:
        return benchmarks.for_hotel(cur, ctx.hotel.slug, period=period)


@router.put("/benchmarks/opt-in")
def put_opt_in(body: OptInIn, ctx: HotelCtx = Depends(require_perm("trust.manage"))) -> dict:
    with db.db() as cur:
        return benchmarks.set_opt_in(cur, ctx, ctx.hotel.slug, body.opted_in)


@router.get("/benchmarks/cohorts")
def get_cohorts(ctx: HotelCtx = Depends(require_perm(ADMIN))) -> list[dict]:
    with db.db() as cur:
        return benchmarks.list_cohorts(cur)


@router.put("/benchmarks/cohorts")
def put_cohort(body: benchmarks.CohortIn, ctx: HotelCtx = Depends(require_perm(ADMIN))) -> dict:
    with db.db() as cur:
        return benchmarks.upsert_cohort(cur, ctx, body)


class SnapshotIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")


@router.post("/benchmarks/snapshots")
def post_snapshots(body: SnapshotIn, ctx: HotelCtx = Depends(require_perm(ADMIN))) -> list[dict]:
    with db.db() as cur:
        return benchmarks.run_snapshots(cur, body.period)
