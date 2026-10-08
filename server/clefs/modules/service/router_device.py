"""
Device routes for Service (Canon 13: /device/...). The paired device is the
principal (clefs_device cookie); its floors and departments scope the feed.
Pairing, PIN login and sessions are the foundation's (/auth/devices/...).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from clefs import db
from clefs.auth.deps import HotelCtx, require_device
from clefs.modules.service import config, state
from clefs.modules.service.router import event_out

router = APIRouter(prefix="/device/service", tags=["service-device"])


@router.get("/feed")
def device_feed(
    cursor: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    ctx: HotelCtx = Depends(require_device()),
) -> dict:
    """request_events after `cursor` inside the device's scope (its floors
    and departments; an unscoped device sees the whole hotel). Returns at
    once; the device polls every few seconds and on an SSE wake-up."""
    p = ctx.principal
    slug = p.device_hotel
    with db.db(readonly=True) as cur:
        depts = [k for k in (config.department_for(cur, slug, d) for d in p.device_departments) if k]
        evs = state.events_after(cur, slug, cursor, floors=list(p.device_floors), departments=depts, limit=limit)
    return {"events": [event_out(e) for e in evs], "cursor": evs[-1]["id"] if evs else cursor}
