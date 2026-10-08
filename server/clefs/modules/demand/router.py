"""
Demand Radar console routes. Every path here lives under /hotels/{slug}/demand/
(Canon 13); public, device and webhook routes go on separate routers under
/public/, /device/ and /webhooks/ and are added to MODULE.routers.

Events side (02 spec 4.4): the event calendar (global feed and curated rows
plus the hotel's own manual, ICS and AI rows), venues, holidays, source
markets, PredictHQ features and the adapters' sync status.
Permissions (module.py): demand.view reads; demand.manage writes. Every
write is audited as demand.<entity>.<verb>. The HTTP contract is in README.md.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from clefs import ai, db, ratelimit
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core.errors import ClefsError
from clefs.core.time import local_date
from clefs.modules.demand import curated, events, ics, markets
from clefs.modules.demand.adapters import ai_web, phq_events, phq_features, ticketmaster

router = APIRouter(prefix="/hotels/{slug}/demand", tags=["demand"])

VIEW = require_perm("demand.view")
MANAGE = require_perm("demand.manage")
MAX_WINDOW_DAYS = 400
ICS_MAX_BYTES = 2_000_000


def _window(ctx: HotelCtx, start: date | None, end: date | None, default_days: int = 90) -> tuple[date, date]:
    start = start or local_date(ctx.hotel)
    end = end or start + timedelta(days=default_days)
    if end < start:
        raise ClefsError(400, "`to` is before `from`", "invalid_range")
    if (end - start).days > MAX_WINDOW_DAYS:
        raise ClefsError(400, f"A window is at most {MAX_WINDOW_DAYS} days", "invalid_range")
    return start, end


# ─── Events: reads ───


@router.get("/events")
def list_events(
    from_: date | None = Query(None, alias="from"), to: date | None = None,
    category: str | None = Query(None, description="comma-separated categories"),
    include_cancelled: bool = True, limit: int = Query(500, ge=1, le=1000),
    ctx: HotelCtx = Depends(VIEW),
) -> list[dict]:
    start, end = _window(ctx, from_, to)
    cats = [c.strip().upper() for c in (category or "").split(",") if c.strip()]
    bad = [c for c in cats if c not in events.CATEGORIES]
    if bad:
        raise ClefsError(400, f"Unknown category {bad[0]!r}", "invalid_category")
    with db.db(readonly=True) as cur:
        return events.list_events(cur, ctx.slug, start=start, end=end, categories=cats,
                                  include_cancelled=include_cancelled, limit=limit)


@router.get("/events/lookup")
def lookup_event(q: str = Query(..., min_length=2, max_length=200), ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The free-text resolver Inquiry Guard uses ("GITEX week")."""
    with db.db(readonly=True) as cur:
        return {"match": events.find(cur, ctx.slug, text=q, today=local_date(ctx.hotel))}


@router.get("/events/upcoming")
def upcoming(days: int = Query(60, ge=1, le=365), min_score: float = Query(0.3, ge=0, le=1),
             ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return events.upcoming(cur, ctx.hotel, days=days, min_score=min_score, today=local_date(ctx.hotel))


@router.get("/events/{event_id}")
def get_event(event_id: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return events.get_event(cur, ctx.slug, event_id)


# ─── Events: writes (manual entry, ICS, confirmation) ───

Category = Literal["CONFERENCE", "EXPO", "SPORTS", "CONCERT", "FESTIVAL", "PUBLIC_HOLIDAY", "SCHOOL_HOLIDAY",
                   "RELIGIOUS", "SOURCE_MARKET_HOLIDAY", "COMMUNITY", "CITYWIDE"]
Status = Literal["SCHEDULED", "RESCHEDULED", "POSTPONED", "CANCELLED"]


class ManualEventIn(BaseModel):
    title: str = Field(..., min_length=2, max_length=300)
    category: Category
    start: date
    end: date | None = None
    venue_codes: list[str] | None = Field(None, max_length=5)
    venue_text: str | None = Field(None, max_length=300)
    attendance: int | None = Field(None, ge=0, le=5_000_000)
    description: str | None = Field(None, max_length=4000)
    urls: list[str] | None = Field(None, max_length=10)
    moon_dependent: bool | None = None
    uncertainty_days: int | None = Field(None, ge=0, le=5)


class ManualEventPatch(BaseModel):
    title: str | None = Field(None, min_length=2, max_length=300)
    category: Category | None = None
    start: date | None = None
    end: date | None = None
    status: Status | None = None
    venue_codes: list[str] | None = Field(None, max_length=5)
    venue_text: str | None = Field(None, max_length=300)
    attendance: int | None = Field(None, ge=0, le=5_000_000)
    description: str | None = Field(None, max_length=4000)
    urls: list[str] | None = Field(None, max_length=10)
    moon_dependent: bool | None = None
    uncertainty_days: int | None = Field(None, ge=0, le=5)
    reason: Literal["rescheduled", "correction"] | None = None


class IcsIn(BaseModel):
    content: str = Field(..., min_length=10, max_length=ICS_MAX_BYTES)
    filename: str | None = Field(None, max_length=200)


@router.post("/events", status_code=201)
def create_event(body: ManualEventIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        ev = events.create_manual(cur, ctx.slug, body.model_dump(exclude_none=True))
        audit(cur, ctx, "demand.event.create", "dr_event", ev["id"],
              after={"title": ev["title"], "start": ev["start_date"], "end": ev["end_date"], "matched": ev["matched"]})
        return ev


@router.patch("/events/{event_id}")
def update_event(event_id: str, body: ManualEventPatch, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    patch = body.model_dump(exclude_none=True)
    reason = patch.pop("reason", None)
    with db.db() as cur:
        before = events.get_event(cur, ctx.slug, event_id)
        ev = events.update_manual(cur, ctx.slug, event_id, patch, reason=reason)
        audit(cur, ctx, "demand.event.update", "dr_event", ev["id"],
              before={k: before[k] for k in ("title", "start_date", "end_date", "status")},
              after={k: ev[k] for k in ("title", "start_date", "end_date", "status")})
        return ev


@router.delete("/events/{event_id}")
def delete_event(event_id: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        out = events.remove_own(cur, ctx.slug, event_id)
        audit(cur, ctx, "demand.event.delete", "dr_event", out["event_id"], meta=out)
        return out


@router.post("/events/{event_id}/confirm")
def confirm_event(event_id: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        ev = events.confirm(cur, ctx.slug, event_id, user_id=ctx.principal.user_id)
        audit(cur, ctx, "demand.event.confirm", "dr_event", ev["id"], after={"verification": ev["verification"]})
        return ev


@router.post("/events/import-ics")
async def import_ics(request: Request, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """JSON {content, filename?} or a multipart upload with a `file` part."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        part = form.get("file")
        if part is None or not hasattr(part, "read"):
            raise ClefsError(400, "Attach the calendar as `file`", "missing_file")
        data: bytes | str = await part.read(ICS_MAX_BYTES + 1)
        filename = getattr(part, "filename", None)
    else:
        try:
            body = IcsIn.model_validate(await request.json())
        except (ValueError, ValidationError) as exc:
            raise ClefsError(422, "Send {content: <the .ics text>} or a multipart `file`", "invalid_body") from exc
        data, filename = body.content, body.filename
    if len(data) > ICS_MAX_BYTES:
        raise ClefsError(413, "The calendar file is too large (2 MB at most)", "file_too_large")

    def _run() -> dict:
        with db.db() as cur:
            out = ics.import_ics(cur, ctx.slug, data, tz=ctx.hotel.tz)
            audit(cur, ctx, "demand.event.import_ics", "dr_event", None,
                  meta={"filename": filename, **{k: v for k, v in out.items() if k != "event_ids"}})
            return out

    # The database work is blocking: keep it off the event loop.
    return await run_in_threadpool(_run)


# ─── Reference data: venues, holidays, source markets, features ───


@router.get("/venues")
def list_venues(ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [{"code": v["code"], "name": v["name"], "lat": v["lat"], "lon": v["lon"], "emirate": v["emirate"],
                 "corridor_tags": list(v["corridor_tags"] or []), "capacity": v["capacity"],
                 "coords_verified": v["coords_verified"], "feed_available": v["feed_available"],
                 "last_verified_at": v["last_verified_at"].isoformat() if v["last_verified_at"] else None,
                 "source_url": v["source_url"]} for v in events.venues(cur)]


@router.get("/holidays")
def list_holidays(
    from_: date | None = Query(None, alias="from"), to: date | None = None,
    country: str | None = Query(None, description="comma-separated ISO2 codes"),
    markets_only: bool = False, ctx: HotelCtx = Depends(VIEW),
) -> list[dict]:
    start, end = _window(ctx, from_, to, default_days=180)
    with db.db(readonly=True) as cur:
        if markets_only:
            return markets.market_holidays(cur, ctx.slug, start=start, end=end)
        codes = [c.strip().upper() for c in (country or "").split(",") if c.strip()]
        return markets.list_holidays(cur, start=start, end=end, countries=codes or None)


class MarketShare(BaseModel):
    market_code: str = Field(..., min_length=2, max_length=8)
    share_pct: float = Field(..., ge=0, le=100)


class MarketsIn(BaseModel):
    markets: list[MarketShare] = Field(..., max_length=40)


@router.get("/source-markets")
def get_source_markets(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return markets.get_shares(cur, ctx.slug)


@router.put("/source-markets")
def put_source_markets(body: MarketsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = markets.get_shares(cur, ctx.slug)
        out = markets.set_shares(cur, ctx.slug, [m.model_dump() for m in body.markets], user_id=ctx.principal.user_id)
        audit(cur, ctx, "demand.source_markets.update", "dr_source_markets", ctx.slug,
              before={m["market_code"]: m["share_pct"] for m in before["markets"]},
              after={m["market_code"]: m["share_pct"] for m in out["markets"]})
        return out


@router.get("/features")
def get_features(from_: date | None = Query(None, alias="from"), to: date | None = None,
                 ctx: HotelCtx = Depends(VIEW)) -> dict:
    start, end = _window(ctx, from_, to)
    with db.db(readonly=True) as cur:
        return {"from": start.isoformat(), "to": end.isoformat(),
                "days": phq_features.features_for(cur, ctx.slug, start, end)}


# ─── Sources: status and a manual refresh ───

SYNC_LIMIT_PER_HOUR = 4


@router.get("/sources/status")
def sources_status(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        rows = db.fetch_all("SELECT source, scope, cursor, last_run_at, last_result FROM dr_sync_state "
                            "ORDER BY source, scope", cur=cur)
    return {
        "configured": {"predicthq": bool(phq_events.token()), "ticketmaster": bool(ticketmaster.api_key()),
                       "ai_discovery": bool(ai.enabled())},
        "runs": [{"source": r["source"], "scope": r["scope"],
                  "last_run_at": r["last_run_at"].isoformat() if r["last_run_at"] else None,
                  "last_result": r["last_result"]} for r in rows],
    }


class SyncIn(BaseModel):
    source: Literal["curated", "predicthq", "ai_discovery"]


@router.post("/sources/sync")
def sync_source(body: SyncIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Run one source now for this hotel (rate-limited: vendor calls cost
    money). Ticketmaster is global and weekly only."""
    with db.db() as cur:
        if not ratelimit.hit(cur, f"demand:sync:{ctx.slug}", limit=SYNC_LIMIT_PER_HOUR, window_s=3600):
            raise ClefsError(429, "Too many refreshes; try again within the hour", "rate_limited")
        if body.source == "curated":
            out = curated.sync(cur)
        elif body.source == "predicthq":
            out = phq_events.sync_hotel(cur, ctx.hotel)
        else:
            out = ai_web.discover(cur, ctx.hotel)
        audit(cur, ctx, "demand.source.sync", "dr_sync_state", body.source, meta=_short(out))
        return {"source": body.source, "result": out}


def _short(out: Any) -> dict:
    return {k: v for k, v in (out or {}).items() if isinstance(v, (int, float, str, bool)) or v is None}



# ─── Rates side (02 spec 4.1-4.2): comp set, indicative comp rates, BAR, guardrails, weather ───
# Domain code lives in comp_rates.py, bar.py, guardrails.py, weather.py and
# adapters/{serpapi_hotels,csv_rates,xotelo,makcorps}.py; these are thin routes.

from clefs.modules.demand import bar as bar_mod  # noqa: E402
from clefs.modules.demand import comp_rates, guardrails, weather  # noqa: E402
from clefs.core import clock, serpapi  # noqa: E402
from clefs.core.money import money_str  # noqa: E402
from clefs.modules.demand.adapters import csv_rates, serpapi_hotels  # noqa: E402  (csv_rates registers COMP_RATES)

RATES_IMPORT_MAX_BYTES = 2_000_000
COMP_FETCH_LIMIT_PER_HOUR = 2


class RateSettingsIn(BaseModel):
    area_queries: list[str] | None = Field(None, max_length=3)
    self_property_token: str | None = Field(None, max_length=200)
    reference_room_type: str | None = Field(None, max_length=32)
    comp_fetch_enabled: bool | None = None
    fallback_calls_max: int | None = Field(None, ge=0, le=20)


@router.get("/rates/settings")
def get_rates_settings(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {**comp_rates.get_rate_settings(cur, ctx.slug),
                "effective_area_queries": comp_rates.area_queries(cur, ctx.hotel)}


@router.put("/rates/settings")
def put_rates_settings(body: RateSettingsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    changes = body.model_dump(exclude_unset=True)
    with db.db() as cur:
        before = comp_rates.get_rate_settings(cur, ctx.slug)
        after = comp_rates.put_rate_settings(cur, ctx.slug, changes, user_id=ctx.principal.user_id)
        audit(cur, ctx, "demand.rate_settings.update", "dr_rate_settings", ctx.slug, before=before, after=after)
        return {**after, "effective_area_queries": comp_rates.area_queries(cur, ctx.hotel)}


# ─── Comp set ───


class CompIn(BaseModel):
    name: str | None = Field(None, max_length=200)
    property_token: str | None = Field(None, max_length=200)
    place_id: str | None = Field(None, max_length=200)
    xotelo_key: str | None = Field(None, max_length=100)
    makcorps_id: str | None = Field(None, max_length=100)
    lat: float | None = Field(None, ge=-90, le=90)
    lng: float | None = Field(None, ge=-180, le=180)
    stars: float | None = Field(None, ge=1, le=5)
    rooms: int | None = Field(None, ge=1, le=10000)
    aliases: list[str] | None = Field(None, max_length=10)
    is_demo: bool | None = None
    active: bool | None = None


@router.get("/comp-set")
def get_comp_set(include_inactive: bool = False, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return comp_rates.list_comps(cur, ctx.slug, include_inactive=include_inactive)


@router.post("/comp-set", status_code=201)
def add_comp(body: CompIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        comp, _ = comp_rates.upsert_comp(cur, ctx.slug, body.model_dump(exclude_unset=True))
        audit(cur, ctx, "demand.comp.create", "dr_comp_set", comp["comp_id"], after=comp)
        return comp


@router.patch("/comp-set/{comp_id}")
def edit_comp(comp_id: str, body: CompIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        before = comp_rates.get_comp(cur, ctx.slug, comp_id)
        comp, _ = comp_rates.upsert_comp(cur, ctx.slug, body.model_dump(exclude_unset=True), comp_id=comp_id)
        audit(cur, ctx, "demand.comp.update", "dr_comp_set", comp_id, before=before, after=comp)
        return comp


@router.delete("/comp-set/{comp_id}")
def remove_comp(comp_id: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        comp = comp_rates.deactivate_comp(cur, ctx.slug, comp_id)
        audit(cur, ctx, "demand.comp.deactivate", "dr_comp_set", comp_id)
        return comp


class DiscoverIn(BaseModel):
    check_in: date | None = None
    query: str | None = Field(None, max_length=120)


@router.post("/comp-set/discover")
def discover_comps(body: DiscoverIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Hotels on one Google Hotels area page, to pick a comp set from. One
    SerpApi search (cached and shared with the nightly fetch)."""
    with db.db(readonly=True) as cur:
        settings = comp_rates.get_rate_settings(cur, ctx.slug)
        queries = comp_rates.area_queries(cur, ctx.hotel)
        comps = comp_rates.list_comps(cur, ctx.slug, include_inactive=True)
    q = (body.query or "").strip() or (queries[0] if queries else "")
    if not q:
        raise ClefsError(400, "Set an area query or a hotel city first", "no_area_query")
    check_in = body.check_in or local_date(ctx.hotel) + timedelta(days=7)
    try:
        found = serpapi_hotels.discover(ctx.hotel, q=q, check_in=check_in, comps=comps,
                                        self_token=settings["self_property_token"])
    except serpapi.BudgetExhausted as exc:
        raise ClefsError(503, "The SerpApi budget for this month is spent", "budget_exhausted") from exc
    ccy = str(ctx.hotel.currency)
    for c in found:
        c["rate"] = money_str(c.pop("rate_minor"), ccy)
    return {"query": q, "check_in": check_in.isoformat(), "currency": ccy, "candidates": found,
            "indicative": True, "label": comp_rates.INDICATIVE_LABEL}


# ─── Comp rates (indicative) ───


@router.get("/comp-rates")
def get_comp_rates(from_: date | None = Query(None, alias="from"), to: date | None = None,
                   ctx: HotelCtx = Depends(VIEW)) -> dict:
    start, end = _window(ctx, from_, to, default_days=30)
    with db.db(readonly=True) as cur:
        return {"from": start.isoformat(), "to": end.isoformat(), "indicative": True,
                "label": comp_rates.INDICATIVE_LABEL,
                "nights": comp_rates.calendar(cur, ctx.slug, start, end)}


@router.get("/comp-rates/{stay_date}")
def get_comp_rates_night(stay_date: date, history: bool = False, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        rows = comp_rates.latest_rows(cur, ctx.slug, stay_date, stay_date)
        out = {"stay_date": stay_date.isoformat(), **comp_rates.night_summary(rows),
               "rates": comp_rates.rates_for_night(cur, ctx.slug, stay_date)}
        if history:
            out["history"] = comp_rates.night_history(cur, ctx.slug, stay_date)
        return out


async def _file_text(request: Request, max_bytes: int) -> tuple[str, dict]:
    """JSON {content, ...} or multipart with a `file` part (plus form fields)."""
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        form = await request.form()
        part = form.get("file")
        if part is None or not hasattr(part, "read"):
            raise ClefsError(422, "Attach the file as `file`", "invalid_body")
        raw = await part.read(max_bytes + 1)
        fields = {k: v for k, v in form.items() if isinstance(v, str)}
        fields.setdefault("filename", getattr(part, "filename", None))
    else:
        try:
            fields = await request.json()
        except ValueError as exc:
            raise ClefsError(422, "Send JSON {content} or a multipart file", "invalid_body") from exc
        if not isinstance(fields, dict) or not isinstance(fields.get("content"), str):
            raise ClefsError(422, "Send JSON {content} or a multipart file", "invalid_body")
        raw = fields["content"].encode("utf-8")
    if len(raw) > max_bytes:
        raise ClefsError(413, "The file is too large", "file_too_large")
    try:
        return raw.decode("utf-8-sig"), fields
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace"), fields


@router.post("/comp-rates/import")
async def import_comp_rates(request: Request, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """A Lighthouse or RateGain rate-shop CSV (long or wide). `source`
    (lighthouse|rategain) or the file name picks the label."""
    text, fields = await _file_text(request, RATES_IMPORT_MAX_BYTES)
    hint = f"{fields.get('source') or ''} {fields.get('filename') or ''}"

    def _run() -> dict:
        with db.db() as cur:
            out = csv_rates.import_csv(cur, ctx.hotel, text, source_hint=hint, currency=fields.get("currency"))
            audit(cur, ctx, "demand.comp_rates.import", "dr_comp_rates", out["source"], meta=_short(out))
            return out

    return await run_in_threadpool(_run)


@router.post("/comp-rates/fetch", status_code=202)
def fetch_comp_rates(days: int = Query(14, ge=1, le=30), ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Queue a fetch of the next `days` nights (rate-limited: SerpApi calls
    are billed, though a night fetched in the last 20 hours is served from
    the shared cache). The worker runs it, so no vendor call holds a request."""
    from clefs import jobs

    with db.db() as cur:
        if not ratelimit.hit(cur, f"demand:comp_fetch:{ctx.slug}", limit=COMP_FETCH_LIMIT_PER_HOUR, window_s=3600):
            raise ClefsError(429, "Too many refreshes; try again within the hour", "rate_limited")
        today = local_date(ctx.hotel)
        dates = [(today + timedelta(days=i)).isoformat() for i in range(days)]
        job_id = jobs.enqueue("dr.comp_rates_fetch_more", {"dates": dates}, hotel_slug=ctx.slug,
                              dedupe_key=f"dr.comp_rates_fetch_now:{ctx.slug}:{today.isoformat()}:{days}", cur=cur)
        audit(cur, ctx, "demand.comp_rates.fetch", "dr_comp_rates", ctx.slug, meta={"nights": days})
        return {"queued": job_id is not None, "nights": days, "from": dates[0], "to": dates[-1]}


# ─── BAR calendar ───


class BarRowIn(BaseModel):
    stay_date: date
    room_type_code: str = Field(..., max_length=32)
    bar: str | float = Field(..., description="major units, Decimal string")
    note: str | None = Field(None, max_length=300)


class BarPutIn(BaseModel):
    rows: list[BarRowIn] = Field(..., min_length=1, max_length=bar_mod.MAX_ROWS)


@router.get("/bar")
def get_bar_calendar(from_: date | None = Query(None, alias="from"), to: date | None = None,
                     room_type: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> dict:
    start, end = _window(ctx, from_, to)
    with db.db(readonly=True) as cur:
        code = comp_rates.normalize_code(room_type) if room_type else None
        return {"from": start.isoformat(), "to": end.isoformat(), "currency": ctx.hotel.currency,
                "reference_room_type": bar_mod.reference_room_type(cur, ctx.slug),
                "rows": bar_mod.list_bar(cur, ctx.slug, start, end, code)}


@router.put("/bar")
def put_bar(body: BarPutIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    rows = [r.model_dump(mode="json") for r in body.rows]
    with db.db() as cur:
        out = bar_mod.put_rows(cur, ctx.hotel, rows, source="MANUAL", user_id=ctx.principal.user_id)
        audit(cur, ctx, "demand.bar.update", "dr_bar_calendar", ctx.slug, meta=_short(out))
        return out


@router.post("/bar/import")
async def import_bar(request: Request, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """A rate export: long (date, room type, rate) or wide (date, then one
    column per room type code)."""
    text, _ = await _file_text(request, RATES_IMPORT_MAX_BYTES)

    def _run() -> dict:
        with db.db() as cur:
            out = bar_mod.import_csv(cur, ctx.hotel, text, user_id=ctx.principal.user_id)
            audit(cur, ctx, "demand.bar.import", "dr_bar_calendar", ctx.slug, meta=_short(out))
            return out

    return await run_in_threadpool(_run)


@router.get("/bar/changes")
def get_bar_changes(stay_date: date, room_type: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        code = comp_rates.normalize_code(room_type) if room_type else None
        return bar_mod.changes(cur, ctx.slug, stay_date, code)


@router.delete("/bar/{stay_date}/{room_type}")
def delete_bar(stay_date: date, room_type: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        if not bar_mod.delete_bar(cur, ctx.hotel, stay_date, room_type, user_id=ctx.principal.user_id):
            raise ClefsError(404, "No BAR for that night and room type", "bar_not_found")
        audit(cur, ctx, "demand.bar.delete", "dr_bar_calendar", f"{stay_date.isoformat()}:{room_type}")
        return {"deleted": True, "stay_date": stay_date.isoformat(), "room_type_code": room_type.strip().upper()}


# ─── Guardrails ───


class GuardrailIn(BaseModel):
    room_type_code: str = Field("*", max_length=32)
    month: int | None = Field(None, ge=1, le=12)
    dow: int | None = Field(None, ge=1, le=7)
    floor: str | float
    ceiling: str | float
    currency: str | None = Field(None, min_length=3, max_length=3)
    max_step_pct: float | None = Field(None, gt=0, le=100)
    note: str | None = Field(None, max_length=300)


class GuardrailsPutIn(BaseModel):
    rows: list[GuardrailIn] = Field(..., min_length=1, max_length=200)


@router.get("/guardrails")
def get_guardrails(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return {"currency": ctx.hotel.currency, "rows": guardrails.list_rows(cur, ctx.slug)}


@router.put("/guardrails")
def put_guardrails(body: GuardrailsPutIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    rows = [r.model_dump() for r in body.rows]
    with db.db() as cur:
        before = guardrails.list_rows(cur, ctx.slug)
        after = guardrails.upsert_rows(cur, ctx.hotel, rows, user_id=ctx.principal.user_id)
        audit(cur, ctx, "demand.guardrails.update", "dr_rate_guardrails", ctx.slug,
              before={"rows": len(before)}, after={"rows": len(after), "written": rows})
        return {"currency": ctx.hotel.currency, "rows": after}


@router.delete("/guardrails/{row_id}")
def delete_guardrail(row_id: int, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        gone = guardrails.delete_row(cur, ctx.slug, row_id)
        audit(cur, ctx, "demand.guardrails.delete", "dr_rate_guardrails", row_id, before=gone)
        return gone


@router.get("/guardrails/preview")
def preview_guardrails(stay_date: date, room_type: str | None = None, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Exactly what demand.api.price_guardrails answers Inquiry Guard."""
    with db.db(readonly=True) as cur:
        return {"stay_date": stay_date.isoformat(),
                "guardrails": guardrails.price_guardrails(cur, ctx.slug, stay_date, room_type)}


# ─── Weather ───


@router.get("/weather")
def get_weather(from_: date | None = Query(None, alias="from"), to: date | None = None,
                ctx: HotelCtx = Depends(VIEW)) -> dict:
    start, end = _window(ctx, from_, to, default_days=7)
    with db.db(readonly=True) as cur:
        days = weather.daily(cur, ctx.hotel, start, end)
        info = weather.status(cur, ctx.hotel)
    return {"from": start.isoformat(), "to": end.isoformat(), "days": days,
            "now_c": weather.outdoor_temp_c(ctx.hotel, clock.now()), **info}
