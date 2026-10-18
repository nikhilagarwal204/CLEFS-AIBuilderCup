"""
Owner Brief console routes. Every path here lives under /hotels/{slug}/brief/
(Canon 13); public, device and webhook routes go on separate routers under
/public/, /device/ and /webhooks/ and are added to MODULE.routers.

Decisions are NOT here: the foundation's POST /hotels/{slug}/actions/{id}/decide
runs Canon 7 + K1 and the autonomy dial (magic-link sessions from the brief
decide the actions the run surfaced). These routes read the brief, preview
it, send it, configure it and show an action's timeline with the ladder.
"""

from __future__ import annotations

from datetime import date as Date
from datetime import datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from clefs import db, files
from clefs.audit import audit
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import actions as core_actions
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel, set_module_settings, update_hotel
from clefs.modules.brief import brief, signal_decide, weekly
from clefs.modules.brief.quiet import QuietWindow

router = APIRouter(prefix="/hotels/{slug}/brief", tags=["brief"])

VIEW = require_perm("brief.view")
MANAGE = require_perm("brief.manage")
CadenceQ = Literal["daily", "weekly", "monthly"]


def _uid(ctx: HotelCtx) -> int | None:
    return ctx.principal.user_id


# ─── Today, dates, runs ───


@router.get("/today")
def get_today(cadence: CadenceQ = "daily", ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Today's stored run of that cadence (the newest, send-now included), else
    a live preview marked `preview: true` so the page is never empty."""
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        day = htime.local_date(hotel)
        run = brief.find_run(cur, hotel.slug, cadence, day)
        if run:
            return {**run, "preview": False}
        return {**brief.assemble(cur, hotel, cadence, day=day), "id": None, "preview": True}


@router.get("/runs")
def list_runs(cadence: CadenceQ | None = None, limit: int = Query(30, ge=1, le=200),
              ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return brief.list_runs(cur, ctx.hotel.slug, cadence=cadence, limit=limit)


@router.get("/runs/{run_id}")
def get_run(run_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        run = brief.get_run(cur, ctx.hotel.slug, run_id)
    if run is None:
        raise ClefsError(404, "Brief not found", "brief_not_found")
    return run


# ─── Preview and send-now ───


class PreviewIn(BaseModel):
    cadence: CadenceQ = "daily"
    date: Date | None = None


@router.post("/preview")
def preview(body: PreviewIn, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The brief as it would be generated now, without storing or sending."""
    with db.db() as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        return {**brief.assemble(cur, hotel, body.cadence, day=body.date), "id": None, "preview": True}


class SendNowIn(BaseModel):
    cadence: CadenceQ = "daily"
    only_me: bool = False  # send the test to the caller only


@router.post("/send-now")
def send_now(body: SendNowIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        uid = _uid(ctx) if body.only_me else None
        if body.only_me and uid is None:
            raise ClefsError(400, "only_me needs a signed-in user", "user_required")
        out = brief.push_now(cur, ctx.hotel.slug, cadence=body.cadence, user_id=uid)
        audit(cur, ctx, "brief.send_now", "brf_runs", out.get("brief_id"),
              meta={"cadence": body.cadence, "only_me": body.only_me, "sent": len(out.get("sent_to") or [])})
        return out


# ─── Settings ───


class SettingsIn(BaseModel):
    """Partial: only the fields sent change. `brief_time_local` is the core
    hotel column the scheduler reads ("HH:MM", hotel timezone)."""

    brief_time_local: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    daily_enabled: bool | None = None
    weekly_enabled: bool | None = None
    monthly_enabled: bool | None = None
    recipient_user_ids: list[int] | None = None
    channels: list[Literal["email", "whatsapp"]] | None = None
    send_when_empty: bool | None = None
    top_n: int | None = Field(default=None, ge=1, le=10)
    # The whole list replaces the stored one; [] clears every window.
    quiet_hours: list[QuietWindow] | None = Field(default=None, max_length=100)


def _settings_view(cur, slug: str) -> dict:
    hotel = get_hotel(cur, slug, fresh=True)
    s = brief.load_settings(cur, slug)
    t = hotel.brief_time_local
    return {
        **s.model_dump(),
        "brief_time_local": t.strftime("%H:%M") if isinstance(t, time) else str(t)[:5],
        "timezone": hotel.timezone,
        "recipients": [
            {"user_id": r["id"], "name": r["name"], "role": r["role"], "has_email": bool(r["email"]),
             "has_whatsapp": bool(r["phone_e164"])}
            for r in brief.recipients(cur, slug, s)
        ],
    }


@router.get("/settings")
def get_settings_route(ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db() as cur:
        return _settings_view(cur, ctx.hotel.slug)


@router.put("/settings")
def put_settings(body: SettingsIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    slug = ctx.hotel.slug
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    with db.db() as cur:
        t = changes.pop("brief_time_local", None)
        if "recipient_user_ids" in changes:
            cur.execute(
                "SELECT user_id FROM hotel_memberships WHERE hotel_slug = %s AND is_active AND user_id = ANY(%s)",
                (slug, changes["recipient_user_ids"]),
            )
            members = {r["user_id"] for r in cur.fetchall()}
            unknown = [u for u in changes["recipient_user_ids"] if u not in members]
            if unknown:
                raise ClefsError(400, f"Not a member of this hotel: {unknown[0]}", "invalid_recipient")
        if "quiet_hours" in changes:
            ids = [int(w["user_id"]) for w in changes["quiet_hours"]]
            if len(set(ids)) != len(ids):
                raise ClefsError(400, "One quiet window per person", "invalid_quiet_hours")
            cur.execute(
                "SELECT user_id FROM hotel_memberships WHERE hotel_slug = %s AND is_active AND user_id = ANY(%s)",
                (slug, ids),
            )
            members = {r["user_id"] for r in cur.fetchall()}
            unknown = [u for u in ids if u not in members]
            if unknown:
                raise ClefsError(400, f"Not a member of this hotel: {unknown[0]}", "invalid_recipient")
        if changes:
            merged = {**brief.load_settings(cur, slug).model_dump(), **changes}
            set_module_settings(cur, slug, "brief", brief.BriefSettings(**merged), actor=ctx)
        if t is not None:
            hh, mm = (int(x) for x in t.split(":"))
            before, after = update_hotel(cur, slug, {"brief_time_local": time(hh, mm)})
            audit(cur, ctx, "brief.settings.time", "hotels", slug,
                  before={"brief_time_local": str(before.brief_time_local)[:5]}, after={"brief_time_local": t})
        return _settings_view(cur, slug)


# ─── Actions (reads; decisions go to /hotels/{slug}/actions/{id}/decide) ───


@router.get("/actions")
def list_actions(state: str | None = None, type: str | None = None, overdue: bool = False,  # noqa: A002
                 limit: int = Query(50, ge=1, le=500), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    """Actions by state (comma list), type (comma list) and the overdue flag
    (deadline passed while approved, assigned or in progress), each with its
    escalation level."""
    states = [s.strip() for s in state.split(",") if s.strip()] if state else None
    types = [t.strip() for t in type.split(",") if t.strip()] if type else None
    with db.db(readonly=True) as cur:
        rows = core_actions.list_actions(cur, ctx.hotel.slug, status=states, types=types, overdue=overdue, limit=limit)
    now = clock.now()
    for r in rows:
        r["overdue"] = bool(r.get("due_at") and r["due_at"] < now and r["status"] in brief.OPEN_WORK)
    return rows


@router.get("/actions/{action_id}/timeline")
def action_timeline(action_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The action with one merged, time-ordered list: its state events (the
    foundation's action_events), the briefs that surfaced it, and every
    escalation level that fired."""
    with db.db(readonly=True) as cur:
        a = core_actions.get_action(cur, ctx.hotel.slug, action_id)
        cur.execute(
            """
            SELECT r.id AS run_id, r.cadence, r.brief_date, r.generated_at, i.item_no, i.kind
            FROM brf_items i JOIN brf_runs r ON r.id = i.run_id
            WHERE i.action_id = %s AND r.hotel_slug = %s ORDER BY r.generated_at
            """,
            (action_id, ctx.hotel.slug),
        )
        surfaced = [dict(r) for r in cur.fetchall()]
        cur.execute("SELECT level, due_at, at, notified FROM brf_escalations WHERE action_id = %s ORDER BY level",
                    (action_id,))
        esc = [dict(r) for r in cur.fetchall()]
    events = a.pop("events", [])
    timeline = [{"at": e["at"], "kind": "event", "event": e["event"], "from_status": e["from_status"],
                 "to_status": e["to_status"], "via": e["via"], "actor_type": e["actor_type"],
                 "actor_label": e["actor_label"]} for e in events]
    timeline += [{"at": s["generated_at"], "kind": "surfaced", "run_id": s["run_id"], "cadence": s["cadence"],
                  "brief_date": s["brief_date"].isoformat(), "item_no": s["item_no"], "pinned": s["kind"] == "overdue"}
                 for s in surfaced]
    timeline += [{"at": x["at"], "kind": "escalation", "level": x["level"],
                  "notified": [{k: n.get(k) for k in ("channel", "ok", "status")} for n in x["notified"] or []]}
                 for x in esc]
    timeline.sort(key=lambda x: x["at"])
    return {"action": a, "timeline": timeline}


# ─── Signal-only items (propose / assign / snooze / dismiss) ───


class SignalDecideIn(BaseModel):
    decision: Literal["propose", "assign", "snooze", "dismiss"]
    assignee_user_id: int | None = None
    assignee_role: str | None = Field(default=None, max_length=40)
    snooze_until: datetime | None = None  # timezone-aware; default 24 h
    note: str | None = Field(default=None, max_length=2000)


@router.post("/signals/{signal_id}/decide")
def decide_signal(signal_id: int, body: SignalDecideIn, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """A brief item with no action yet. propose/assign create the signal's
    recommended action through core create_action (the type's decide_perm or
    core.actions.decide, and the dial); snooze/dismiss close the signal.
    -> {signal, action}."""
    with db.db() as cur:
        out = signal_decide.decide_signal(
            cur, ctx, signal_id, body.decision, assignee_user_id=body.assignee_user_id,
            assignee_role=body.assignee_role, snooze_until=body.snooze_until, note=body.note,
        )
        audit(cur, ctx, f"brief.signal.{body.decision}", "signals", signal_id,
              meta={"action_id": (out.get("action") or {}).get("id")})
        return out


# ─── Autonomy block ───


@router.get("/autonomy")
def autonomy(ctx: HotelCtx = Depends(VIEW)) -> dict:
    """Open promotion suggestions and the last 7 days' demotions (Canon 17).
    Accept or dismiss through /hotels/{slug}/autonomy/suggestions/{id}/*."""
    with db.db(readonly=True) as cur:
        hotel = get_hotel(cur, ctx.hotel.slug)
        return brief.autonomy_block(cur, hotel, now=clock.now())


# ─── Weekly verified-value report ───


def _report(cur, ctx: HotelCtx, iso_week: str) -> dict:
    start, end = weekly.week_bounds(iso_week)
    hotel = get_hotel(cur, ctx.hotel.slug)
    if end >= htime.local_date(hotel):
        raise ClefsError(409, "That week has not ended yet", "week_not_over")
    return weekly.ensure_report(cur, hotel, iso_week=iso_week)


@router.get("/reports/weekly")
def list_reports(limit: int = Query(12, ge=1, le=104), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        cur.execute(
            """
            SELECT period_key, period_start, period_end, currency, (payload->>'verified_minor')::BIGINT AS verified_minor,
                   (payload->>'identified_minor')::BIGINT AS identified_minor, generated_at
            FROM brf_reports WHERE hotel_slug = %s AND kind = 'weekly' ORDER BY period_start DESC LIMIT %s
            """,
            (ctx.hotel.slug, limit),
        )
        rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        r["period_start"], r["period_end"] = r["period_start"].isoformat(), r["period_end"].isoformat()
    return rows


@router.get("/reports/weekly/{iso_week}")
def get_report(iso_week: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The report for a finished ISO week ("2026-W40"), built on first read."""
    with db.db() as cur:
        rep = _report(cur, ctx, iso_week)
    return {k: rep[k] for k in ("id", "period_key", "period_start", "period_end", "currency", "payload", "html",
                                "pdf_file_id", "generated_at")}


@router.get("/reports/weekly/{iso_week}/pdf")
def get_report_pdf(iso_week: str, ctx: HotelCtx = Depends(VIEW)) -> Response:
    with db.db() as cur:
        rep = _report(cur, ctx, iso_week)
        if not rep.get("pdf_file_id"):
            raise ClefsError(404, "No PDF for that week", "report_pdf_missing")
        _, data = files.get_file(cur, ctx.hotel.slug, rep["pdf_file_id"])
    return Response(
        content=data, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="clefs-weekly-{iso_week}.pdf"',
                 "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


# ─── A date (declared last: the literal paths above win) ───


@router.get("/{day}")
def get_day(day: Date, cadence: CadenceQ = "daily", ctx: HotelCtx = Depends(VIEW)) -> dict:
    """The newest stored run of that cadence for a hotel-local date."""
    with db.db(readonly=True) as cur:
        run = brief.find_run(cur, ctx.hotel.slug, cadence, day)
    if run is None:
        raise ClefsError(404, "No brief for that date", "brief_not_found")
    return {**run, "preview": False}
