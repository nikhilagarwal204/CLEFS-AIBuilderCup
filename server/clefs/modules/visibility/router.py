"""
Visibility console routes: Google side (04 spec 5.11). Every path lives
under /hotels/{slug}/visibility/ (Canon 13). The AI radar routes (ai,
ai/runs/{id}, ai/facts...) and the public lead magnet (/public/visibility/...)
live in their own router files, added to MODULE.routers by those parts.

Permissions (module.py): visibility.view reads; visibility.manage edits
settings and lists GBP locations; visibility.act proposes the Google actions
(and is decide_perm of every visibility.* action type). Writes are audited as
visibility.<entity>.<verb>. The HTTP contract is in README.md.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from clefs import db
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import actions
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.visibility import attribution, gbp, google_hotels, lodging, rank_grid, site_health, store
from clefs.modules.visibility.http import VendorError

router = APIRouter(prefix="/hotels/{slug}/visibility", tags=["visibility"])
VIEW = require_perm("visibility.view")
MANAGE = require_perm("visibility.manage")
ACT = require_perm("visibility.act")


# ─── Tabs ───


@router.get("/google")
def google_tab(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return google_hotels.overview(cur, get_hotel(cur, ctx.slug))


@router.get("/rank")
def rank_tab(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return rank_grid.overview(cur, get_hotel(cur, ctx.slug))


@router.get("/gbp")
def gbp_tab(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return gbp.overview(cur, get_hotel(cur, ctx.slug))


@router.get("/gbp/locations")
def gbp_locations(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """The location picker (5.4): accounts.list + accounts.locations.list on
    the hotel's google_business connection. 409 not_connected before connect
    or project approval."""
    with db.db() as cur:
        st = gbp.connection_state(cur, ctx.slug)
        if st["status"] in ("project_not_approved", "not_connected"):
            raise ClefsError(409, st["message"], st["status"])
        try:
            locations = gbp.list_locations(cur, ctx.slug)
        except VendorError as exc:
            raise ClefsError(502, exc.detail, "gbp_error") from exc
        return {"locations": locations, "selected": st["location"]}


@router.get("/site-health")
def site_health_tab(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return site_health.overview(cur, get_hotel(cur, ctx.slug))


@router.get("/attribution")
def attribution_tab(month: date | None = Query(None), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """One month (`month`, any day in it) or the last 6 computed months."""
    with db.db(readonly=True) as cur:
        if month is not None:
            row = attribution.get_month(cur, ctx.slug, month.replace(day=1))
            return {"months": [row] if row else [], "label": attribution.CAPTION}
        return {"months": attribution.recent(cur, ctx.slug), "label": attribution.CAPTION}


# ─── Settings ───


def _settings_view(cur, slug: str) -> dict:
    p = store.get_profile(cur, slug)
    return {**{k: p[k] for k in store.PROFILE_COLUMNS}, "updated_at": p["updated_at"].isoformat()
            if p.get("updated_at") else None, "gbp": gbp.connection_state(cur, slug),
            "budget": store.budget_state(cur, slug, profile=p)}


@router.get("/settings")
def get_settings_route(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return _settings_view(cur, ctx.slug)


@router.put("/settings")
def put_settings(body: store.ProfileUpdate, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Only the keys sent change (explicit nulls clear a field)."""
    changes = body.model_dump(exclude_unset=True)
    with db.db() as cur:
        before = store.get_profile(cur, ctx.slug)
        store.update_profile(cur, ctx.slug, changes, user_id=ctx.principal.user_id)
        audit(cur, ctx, "visibility.settings.update", "vis_profile", ctx.slug,
              before={k: before.get(k) for k in changes}, after=changes)
        return _settings_view(cur, ctx.slug)


# ─── Actions (proposed through the ledger; the dial decides the level) ───


@router.post("/gbp/draft-replies")
def draft_review_replies(ctx: HotelCtx = Depends(ACT)) -> dict:
    """'Draft replies' on the review tile: delegates to reviews.api.draft_reply
    for the newest unanswered Google reviews. Drafts only; the owner approves
    them in the Reviews console, and Visibility never posts (04 spec 5.4).
    visibility.act because each draft proposes a Reviews reply action."""
    with db.db() as cur:
        out = gbp.draft_replies(cur, ctx.slug)
        audit(cur, ctx, "visibility.review_drafts.request", "hotel", ctx.slug,
              after={"drafted": out["drafted"], "considered": out["considered"]})
        return out


@router.post("/actions/utm-patch", status_code=201)
def propose_utm_patch(ctx: HotelCtx = Depends(ACT)) -> dict:
    with db.db() as cur:
        st = gbp.connection_state(cur, ctx.slug)
        if st["status"] != "connected":
            raise ClefsError(409, st["message"] or "GBP not connected", "gbp_" + st["status"])
        act = actions.create_action(cur, ctx.slug, type="visibility.gbp_utm_patch",
                                    title="Tag the Google Business Profile website link for attribution",
                                    params={"location": st["location"]}, owner_role="gm", actor=ctx, via="console",
                                    dedupe_key=f"visibility.gbp_utm_patch:{st['location']}")
        audit(cur, ctx, "visibility.utm_patch.propose", "action", act.get("id"), after={"status": act.get("status")})
        return act


@router.post("/actions/lodging-sync", status_code=201)
def propose_lodging_sync(ctx: HotelCtx = Depends(ACT)) -> dict:
    with db.db() as cur:
        st = gbp.connection_state(cur, ctx.slug)
        if st["status"] != "connected":
            raise ClefsError(409, st["message"] or "GBP not connected", "gbp_" + st["status"])
        try:
            out = lodging.propose(cur, get_hotel(cur, ctx.slug), actor=ctx)
        except VendorError as exc:
            raise ClefsError(502, exc.detail, "gbp_error") from exc
        audit(cur, ctx, "visibility.lodging_sync.propose", "action", out.get("action_id"), after=out)
        return out
