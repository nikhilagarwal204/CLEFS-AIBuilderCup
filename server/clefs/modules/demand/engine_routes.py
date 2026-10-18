"""
Demand Radar engine routes (02 spec 4.4): the demand calendar and day view,
relevance feedback (dismiss, mute), calibration, engine settings, rate
recommendations and decisions, the weekly top 5, outcomes, graph guidance,
campaign drafts (export only) and the Google Hotel Center checklist, all
under /hotels/{slug}/demand (Canon 13). The public demand-preview lead is on
`public_router` under /public/leads.

Permissions: demand.view reads, demand.manage writes; every write is
audited as demand.<entity>.<verb>. The HTTP contract is in README.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from clefs import db, ratelimit
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core.errors import ClefsError
from clefs.core.money import money_str, to_minor
from clefs.core.time import local_date
from clefs.modules.demand import bar as bar_mod
from clefs.modules.demand import campaigns, comp_rates, events, ghc, graph_actions, leads, measure
from clefs.modules.demand import demand_index as di
from clefs.modules.demand import recommend as rec_mod
from clefs.modules.demand import relevance as rel

router = APIRouter(prefix="/hotels/{slug}/demand", tags=["demand-engine"])
public_router = APIRouter(prefix="/public/leads", tags=["demand-public"])

VIEW = require_perm("demand.view")
MANAGE = require_perm("demand.manage")
CALENDAR_DAYS = (30, 60, 90)
RECOMPUTE_PER_HOUR = 6
REC_STATUSES = ("PROPOSED", "APPROVED", "ADJUSTED", "REJECTED", "SUPERSEDED", "EXPIRED", "APPLIED")


def _uid(ctx: HotelCtx) -> int | None:
    return ctx.principal.user_id


def _minor(v: Any, ccy: str, field: str) -> int:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError) as exc:
        raise ClefsError(400, f"{field} is a decimal amount", "invalid_amount") from exc
    if d <= 0:
        raise ClefsError(400, f"{field} must be positive", "invalid_amount")
    return to_minor(d, ccy)


# ─── Calendar and day view ───


def _nights(cur: Any, hotel: Any, start: date, days: int) -> list[dict]:
    """Stored nights, with any missing ones computed live (not stored)."""
    end = start + timedelta(days=days - 1)
    have = di.stored(cur, hotel.slug, start, end)
    out = {d: di.day_out(r) for d, r in have.items()}
    missing = [start + timedelta(days=i) for i in range(days) if start + timedelta(days=i) not in have]
    if missing:
        inp = di.Inputs(cur, hotel, min(missing), max(missing), today=local_date(hotel))
        for d in missing:
            n = di.night(inp, d)
            out[d] = {**n, "computed_at": None, "components": {**n["components"], "signal_free": n["signal_free"]}}
    return [out[d] for d in sorted(out)]


@router.get("/calendar")
def calendar(days: int = Query(30), from_: date | None = Query(None, alias="from"),
             ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Heatmap cells: DI, coverage, event badges, the open recommendation
    and the indicative comp band for each night."""
    if days not in CALENDAR_DAYS:
        raise ClefsError(400, "days is 30, 60 or 90", "invalid_days")
    with db.db() as cur:  # read-write: list_recs first syncs decisions taken on the core action
        start = from_ or local_date(ctx.hotel)
        nights = _nights(cur, ctx.hotel, start, days)
        end = start + timedelta(days=days - 1)
        recs = {}
        for r in rec_mod.list_recs(cur, ctx.slug, statuses=["PROPOSED", "APPROVED", "ADJUSTED", "APPLIED"],
                                   start=start, end=end):
            recs.setdefault(r["stay_date"], r)
        comps = {c["stay_date"]: c for c in comp_rates.calendar(cur, ctx.slug, start, end)}
        ref = bar_mod.reference_room_type(cur, ctx.slug)
        bars = {b["stay_date"]: b for b in bar_mod.list_bar(cur, ctx.slug, start, end, ref)} if ref else {}
        cells = []
        for n in nights:
            d = n["stay_date"]
            comp = n["components"]
            cells.append({
                "stay_date": d, "demand_index": None if comp.get("signal_free") else n["demand_index"],
                "coverage": n["coverage"], "weekend": date.fromisoformat(d).isoweekday() in (5, 6),
                "events": [{"event_id": e["event_id"], "title": e["title"], "category": e["category"],
                            "night_score": e["night_score"], "verification": e.get("verification")}
                           for e in comp.get("events", [])[:3]],
                "market_holidays": comp.get("market_holidays", []),
                "recommendation": ({k: recs[d][k] for k in ("id", "status", "current_bar", "recommended", "change_pct")}
                                   if d in recs else None),
                "comp": comps.get(d), "bar": (bars.get(d) or {}).get("bar"),
            })
        return {"from": start.isoformat(), "to": end.isoformat(), "days": days, "currency": ctx.hotel.currency,
                "reference_room_type": ref, "comp_label": comp_rates.INDICATIVE_LABEL, "cells": cells}


def _pace_series(cur: Any, hotel: Any, stay: date) -> dict:
    """The night's OTB by days-before, and comparable nights' mean at the
    same lead (same weekday, trailing 12 weeks, excluded days skipped)."""
    from clefs.core.stays import pace as stays_pace

    own = [{"days_before": int(p["days_before"]), "rooms_otb": p["rooms_otb"]} for p in stays_pace(cur, hotel.slug, stay)]
    inp = di.Inputs(cur, hotel, stay, stay, today=local_date(hotel))
    base = []
    for p in own:
        vals = [by[p["days_before"]] for d, by in inp.past_snaps.items()
                if d.isoweekday() == stay.isoweekday() and p["days_before"] in by]
        base.append({"days_before": p["days_before"], "rooms_otb": round(sum(vals) / len(vals), 2) if vals else None})
    return {"own": own, "baseline": base, "label": "Comparable nights: same weekday, trailing 12 weeks"}


@router.get("/days/{stay_date}")
def day(stay_date: date, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The day drawer: the index and its components, the events with source,
    confidence, last check and change history, the indicative comp strip
    with the own BAR, pace against comparable nights, recommendations,
    guardrails and guidance."""
    with db.db(readonly=True) as cur:
        [night] = _nights(cur, ctx.hotel, stay_date, 1)
        rows = rel.night_scores(rel.relevance_rows(cur, ctx.slug, stay_date, stay_date, include_dismissed=True),
                                stay_date)
        evs = []
        for r, s in rows:
            ev = events.get_event(cur, ctx.slug, str(r["event_id"]))
            evs.append({**ev, "night_score": round(s, 4), "relevance": float(r["score"]),
                        "distance_km": None if r["distance_km"] is None else float(r["distance_km"]),
                        "travel_band": r["travel_band"], "corridor_match": r["corridor_match"],
                        "features": r["features"], "dismissed": r["dismissed"], "dismiss_reason": r["dismiss_reason"]})
        ref = bar_mod.reference_room_type(cur, ctx.slug)
        comp_rows = comp_rates.rates_for_night(cur, ctx.slug, stay_date)
        from clefs.modules.demand import api as demand_api

        return {
            "stay_date": stay_date.isoformat(), "demand_index": night, "events": evs,
            "comp": {"label": comp_rates.INDICATIVE_LABEL, "rates": comp_rows,
                     "summary": comp_rates.night_summary(comp_rates.latest_rows(cur, ctx.slug, stay_date, stay_date))},
            "bar": bar_mod.list_bar(cur, ctx.slug, stay_date, stay_date), "reference_room_type": ref,
            "pace": _pace_series(cur, ctx.hotel, stay_date),
            "recommendations": rec_mod.open_for_night(cur, ctx.slug, stay_date),
            "guardrails": demand_api.price_guardrails(cur, ctx.slug, stay_date, None),
            "guidance": [g for g in graph_actions.list_guidance(cur, ctx.slug) if g["stay_date"] in
                         (stay_date.isoformat(), (stay_date + timedelta(days=1)).isoformat())],
        }


# ─── Relevance feedback, calibration, engine settings ───


class DismissIn(BaseModel):
    reason: str | None = Field(None, max_length=300)
    scope: Literal["event", "class", "venue"] = "event"


@router.post("/events/{event_id}/dismiss")
def dismiss_event(event_id: str, body: DismissIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = rel.dismiss(cur, ctx.slug, event_id, reason=body.reason, scope=body.scope)
        audit(cur, ctx, "demand.event.dismiss", "dr_event", out["event_id"], meta={"scope": body.scope,
                                                                                    "reason": body.reason})
        return out


@router.delete("/events/{event_id}/dismiss")
def undismiss_event(event_id: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        ok = rel.undismiss(cur, ctx.slug, event_id) if events._is_uuid(event_id) else False
        if not ok:
            raise ClefsError(404, "Nothing dismissed for that event", "not_dismissed")
        audit(cur, ctx, "demand.event.undismiss", "dr_event", event_id)
        return {"event_id": event_id, "dismissed": False}


@router.get("/relevance")
def relevance_list(from_: date | None = Query(None, alias="from"), to: date | None = None,
                   include_dismissed: bool = False, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    start = from_ or local_date(ctx.hotel)
    end = to or start + timedelta(days=90)
    if end < start or (end - start).days > 400:
        raise ClefsError(400, "A window is at most 400 days", "invalid_range")
    with db.db(readonly=True) as cur:
        return [{"event_id": str(r["event_id"]), "title": r["title"], "category": r["category"],
                 "start": r["start_date"].isoformat(), "end": r["end_date"].isoformat(), "status": r["status"],
                 "score": float(r["score"]), "distance_km": None if r["distance_km"] is None else float(r["distance_km"]),
                 "travel_band": r["travel_band"], "corridor_match": r["corridor_match"],
                 "expected_rn_uplift": None if r["expected_rn_uplift"] is None else float(r["expected_rn_uplift"]),
                 "nights": r["nights"], "features": r["features"], "dismissed": r["dismissed"],
                 "dismiss_reason": r["dismiss_reason"], "computed_at": r["computed_at"].isoformat()}
                for r in rel.relevance_rows(cur, ctx.slug, start, end, include_dismissed=include_dismissed)]


@router.get("/class-prefs")
def class_prefs(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return rel.list_class_prefs(cur, ctx.slug)


class MuteIn(BaseModel):
    muted: bool


@router.put("/class-prefs/{class_key}")
def set_class_pref(class_key: str, body: MuteIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    key = class_key.strip().upper()
    cat = key.split(":")[0]
    if cat not in events.CATEGORIES:
        raise ClefsError(400, "class_key is CATEGORY or CATEGORY:VENUE", "invalid_class")
    with db.db() as cur:
        out = rel.set_muted(cur, ctx.slug, key, body.muted)
        audit(cur, ctx, "demand.class.mute" if body.muted else "demand.class.unmute", "dr_event_class_prefs", key)
        return out


@router.get("/calibration")
def calibration(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {"min_own_events": rel.MIN_OWN_EVENTS, "baseline_years": list(rel.BASELINE_YEARS),
                "exclusions": [{"from": a.isoformat(), "to": b.isoformat()} for a, b in rel.excluded_ranges(cur, ctx.slug)],
                "classes": rel.calibration(cur, ctx.slug)}


class EngineSettingsIn(BaseModel):
    corridor_tags: list[str] | None = None
    travel_minutes: dict[str, float] | None = None
    stayover_ratio: float | None = None
    credits_per_attendant: float | None = None
    commission_share: float | None = None


@router.get("/engine-settings")
def get_engine_settings(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {**rel.get_settings(cur, ctx.slug), "known_corridors": sorted(
            {t for v in events.venues(cur) for t in (v.get("corridor_tags") or [])})}


@router.put("/engine-settings")
def put_engine_settings(body: EngineSettingsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    changes = body.model_dump(exclude_unset=True)
    with db.db() as cur:
        before = rel.get_settings(cur, ctx.slug)
        out = rel.put_settings(cur, ctx.slug, changes, user_id=_uid(ctx))
        audit(cur, ctx, "demand.engine_settings.update", "dr_engine_settings", ctx.slug, before=before, after=out)
        return out


@router.post("/recompute")
def recompute(ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Relevance, the index and recommendations now (rate-limited; the
    nightly jobs do the same). Calibration runs with it."""
    with db.db() as cur:
        if not ratelimit.hit(cur, f"demand:recompute:{ctx.slug}", limit=RECOMPUTE_PER_HOUR, window_s=3600):
            raise ClefsError(429, "Too many recomputes; try again within the hour", "rate_limited")
        today = local_date(ctx.hotel)
        calib = rel.calibrate(cur, ctx.hotel, today=today)
        r = rel.compute(cur, ctx.hotel, today=today)
        nights = di.compute(cur, ctx.hotel, start=today, days=rec_mod.HORIZON_DAYS, today=today)
        out = rec_mod.run(cur, ctx.hotel, today=today, nights=nights)
        out["pace"] = rec_mod.raise_pace_signals(cur, ctx.hotel, nights, today=today)
        out["graph"] = graph_actions.run(cur, ctx.hotel, today=today)
        report = {"relevance": r, "calibrated_classes": sum(1 for c in calib.values() if c["source"] == "own"),
                  "nights": len(nights), "recommendations": out}
        audit(cur, ctx, "demand.engine.recompute", "dr_demand_days", ctx.slug,
              meta={"nights": len(nights), "created": out["created"]})
        return report


# ─── Recommendations ───


@router.get("/recommendations")
def list_recommendations(status: str | None = Query(None, description="comma-separated statuses"),
                         from_: date | None = Query(None, alias="from"), to: date | None = None,
                         limit: int = Query(200, ge=1, le=1000), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    sts = [s.strip().upper() for s in (status or "").split(",") if s.strip()]
    if any(s not in REC_STATUSES for s in sts):
        raise ClefsError(400, f"status is one of {', '.join(REC_STATUSES)}", "invalid_status")
    with db.db() as cur:
        return rec_mod.list_recs(cur, ctx.slug, statuses=sts or None, start=from_, end=to, limit=limit)


@router.get("/recommendations/{rec_id}")
def get_recommendation(rec_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        rec_mod.sync_from_actions(cur, ctx.slug)
        r = rec_mod.get(cur, ctx.slug, rec_id)
        return {**rec_mod.serialize(r), "facts": r["facts"], "baseline": r["baseline"]}


class DecideIn(BaseModel):
    verb: Literal["approve", "adjust", "reject"]
    recommended: str | None = Field(None, description="adjust: the rate in major units (Decimal string)")
    recommended_minor: int | None = Field(None, description="adjust: the rate in minor units (alternative)")
    note: str | None = Field(None, max_length=300)


@router.post("/recommendations/{rec_id}/decide")
def decide_recommendation(rec_id: int, body: DecideIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        r = rec_mod.get(cur, ctx.slug, rec_id)
        amount = body.recommended_minor
        if body.recommended is not None:
            amount = _minor(body.recommended, r["currency"], "recommended")
        out = rec_mod.decide(cur, ctx, rec_id, body.verb, recommended_minor=amount, note=body.note)
        audit(cur, ctx, f"demand.recommendation.{body.verb}", "dr_rate_recommendation", rec_id,
              before={"status": r["status"], "recommended": money_str(r["recommended_minor"], r["currency"])},
              after={"status": out["status"], "decided": out["decided"]})
        return out


@router.get("/top5")
def top5(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        return {"label": "Estimated opportunity x confidence; at most 2 per event",
                "items": measure.top5(cur, ctx.slug, today=local_date(ctx.hotel))}


@router.get("/outcomes")
def outcomes(from_: date | None = Query(None, alias="from"), to: date | None = None,
             ctx: HotelCtx = Depends(VIEW)) -> dict:
    today = local_date(ctx.hotel)
    # Decisions on future nights count toward actionability too.
    start, end = from_ or today - timedelta(days=90), to or today + timedelta(days=90)
    if end < start or (end - start).days > 400:
        raise ClefsError(400, "A window is at most 400 days", "invalid_range")
    with db.db() as cur:
        return {"from": start.isoformat(), "to": end.isoformat(),
                **measure.outcomes_report(cur, ctx.slug, start=start, end=end)}


# ─── Graph guidance ───


@router.get("/guidance")
def guidance(kind: Literal["STAFFING", "KB_UPDATE", "PRE_ARRIVAL_OFFER"] | None = None,
             event_id: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    if event_id and not events._is_uuid(event_id):
        raise ClefsError(400, "event_id is a uuid", "invalid_event_id")
    with db.db(readonly=True) as cur:
        return graph_actions.list_guidance(cur, ctx.slug, kind=kind, event_id=event_id)


@router.post("/events/{event_id}/guidance")
def event_guidance(event_id: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Build the event's guidance now (staffing, pre-arrival offer, KB)."""
    with db.db() as cur:
        ev = events.get_event(cur, ctx.slug, event_id)
        row = db.fetch_one("SELECT * FROM dr_events WHERE id = %s", (ev["id"],), cur=cur)
        relrow = db.fetch_one("SELECT * FROM dr_event_relevance WHERE hotel_slug = %s AND event_id = %s",
                              (ctx.slug, ev["id"]), cur=cur)
        out = graph_actions.for_event(cur, ctx.hotel, row, relrow)
        audit(cur, ctx, "demand.guidance.generate", "dr_event", ev["id"], meta={"staffing": len(out["staffing"])})
        return {**out, "guidance": graph_actions.list_guidance(cur, ctx.slug, event_id=ev["id"])}


@router.post("/guidance/{guidance_id}/dismiss")
def dismiss_guidance(guidance_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = graph_actions.dismiss_guidance(cur, ctx.slug, guidance_id)
        audit(cur, ctx, "demand.guidance.dismiss", "dr_guidance", guidance_id)
        return out


# ─── Campaign drafts (export only) ───


class CampaignIn(BaseModel):
    event_id: str
    channels: list[Literal["WHATSAPP", "INSTAGRAM", "EMAIL"]] | None = None
    send_at: datetime | None = None


class CampaignPatch(BaseModel):
    copy_: dict[str, str] | None = Field(None, alias="copy")
    send_at: datetime | None = None
    status: Literal["DRAFT", "APPROVED", "ARCHIVED"] | None = None


@router.get("/campaigns")
def list_campaigns(event_id: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    if event_id and not events._is_uuid(event_id):
        raise ClefsError(400, "event_id is a uuid", "invalid_event_id")
    with db.db(readonly=True) as cur:
        return campaigns.list_drafts(cur, ctx.slug, event_id=event_id)


@router.post("/campaigns", status_code=201)
def create_campaigns(body: CampaignIn, ctx: HotelCtx = Depends(MANAGE)) -> list[dict]:
    with db.db() as cur:
        out = campaigns.create_drafts(cur, ctx.hotel, event_id=body.event_id, channels=body.channels,
                                      send_at=body.send_at, user_id=_uid(ctx))
        audit(cur, ctx, "demand.campaign.create", "dr_campaign_drafts", out[0]["id"],
              meta={"event_id": body.event_id, "channels": [o["channel"] for o in out]})
        return out


@router.patch("/campaigns/{draft_id}")
def patch_campaign(draft_id: int, body: CampaignPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = campaigns.update_draft(cur, ctx.slug, draft_id, copy=body.copy_, send_at=body.send_at,
                                     status=body.status)
        audit(cur, ctx, "demand.campaign.update", "dr_campaign_drafts", draft_id,
              meta={"status": body.status, "copy_changed": bool(body.copy_), "send_at": out["suggested_send_at"]})
        return out


@router.get("/campaigns/{draft_id}/export.csv")
def export_campaign(draft_id: int, ctx: HotelCtx = Depends(MANAGE)) -> Response:
    """Export-only: a CSV of contact refs (never addresses) plus the copy.
    Marks the draft EXPORTED. Needs demand.manage: it lists guest refs."""
    with db.db() as cur:
        name, text = campaigns.export_csv(cur, ctx.slug, draft_id)
        # The consent re-check's counts (kept, dropped) go on the audit row
        # too, so who was left off a send list is traceable per export.
        chk = (campaigns.get_draft(cur, ctx.slug, draft_id)["audience"] or {}).get("export_check") or {}
        audit(cur, ctx, "demand.campaign.export", "dr_campaign_drafts", draft_id,
              meta={k: chk.get(k) for k in ("exported_count", "dropped_count", "recheck")})
    return Response(text, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# ─── Google Hotel Center checklist ───


class GhcItemIn(BaseModel):
    item_code: str
    status: Literal["TODO", "DONE", "NA"]
    note: str | None = Field(None, max_length=500)


class GhcIn(BaseModel):
    items: list[GhcItemIn]


@router.get("/ghc-checklist")
def get_ghc(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return ghc.checklist(cur, ctx.hotel)


@router.put("/ghc-checklist")
def put_ghc(body: GhcIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = ghc.update(cur, ctx.hotel, [i.model_dump() for i in body.items], user_id=_uid(ctx))
        audit(cur, ctx, "demand.ghc.update", "dr_ghc_checklist", ctx.slug,
              after={i.item_code: i.status for i in body.items})
        return out


# ─── Public: demand-preview lead ───


@public_router.post("/demand-preview", status_code=202)
def demand_preview_lead(body: leads.LeadIn) -> dict:
    with db.db() as cur:
        return leads.submit(cur, body)
