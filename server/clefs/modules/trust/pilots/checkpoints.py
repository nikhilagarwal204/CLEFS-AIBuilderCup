"""
Pilot checkpoints (03 spec 5.5): trust.pilot_checkpoint runs at 07:00 hotel
time, before the owner brief, for every active pilot. It stores the day's
criteria values and emits trust.pilot_criteria_met (every criterion met) or
trust.pilot_at_risk (off track on two checkpoints in a row, after the first
week: one noisy day is not a risk).

A pilot past its end date is completed by the same job, which freezes its
final evaluation for the report.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs import jobs
from clefs.core import signals
from clefs.core import time as htime
from clefs.core.actions import SYSTEM
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.trust.pilots import store

SIGNAL_AT_RISK = "trust.pilot_at_risk"
SIGNAL_MET = "trust.pilot_criteria_met"
GRACE_DAYS = 7

signals.register_signal_kind(SIGNAL_AT_RISK, label="Pilot off track", default_owner_role="owner",
                             section="verified", cadence="daily", icon="flag")
signals.register_signal_kind(SIGNAL_MET, label="Pilot success criteria met", default_owner_role="owner",
                             section="verified", cadence="daily", icon="flag")


def _threshold(pilot: dict) -> Decimal:
    raw = (pilot.get("terms") or {}).get("on_track_threshold")
    try:
        v = Decimal(str(raw)) if raw is not None else store.DEFAULT_ON_TRACK
    except Exception:  # noqa: BLE001 - owner-typed terms; fall back
        v = store.DEFAULT_ON_TRACK
    return min(max(v, Decimal(0)), Decimal(1))


def checkpoint(cur: Any, hotel_slug: str, pilot: dict, *, day: date) -> dict:
    """Evaluate one pilot up to `day` (inclusive), store the checkpoint and
    raise its signals. Idempotent per (pilot, day)."""
    hotel = get_hotel(cur, hotel_slug)
    ev = store.evaluate(hotel, pilot, until=day)
    s = Decimal(ev["score"]) if ev["score"] is not None else None
    on_track = bool(ev["all_met"] or (s is not None and s >= _threshold(pilot)))
    values = {c["metric_key"]: {k: c[k] for k in ("value", "baseline", "target", "met", "status", "change_pct")}
              for c in ev["criteria"]}
    cur.execute(
        """
        INSERT INTO trs_pilot_checkpoints (pilot_id, date, "values", score, on_track) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (pilot_id, date) DO UPDATE SET "values" = EXCLUDED."values", score = EXCLUDED.score,
            on_track = EXCLUDED.on_track, created_at = NOW()
        """,
        (pilot["id"], day, Json(values), s, on_track),
    )
    cur.execute("UPDATE trs_pilots SET outcome = %s, updated_at = NOW() WHERE id = %s", (Json(ev), pilot["id"]))
    raised = None
    if ev["all_met"]:
        raised = _emit(cur, hotel_slug, pilot, SIGNAL_MET, ev, severity=2,
                       title=f"{pilot['name']}: every success criterion is met",
                       summary="All pilot criteria are met on today's figures. The ROI report is ready to share.")
    elif not on_track and (day - pilot["start_date"]).days >= GRACE_DAYS:
        # Both bad checkpoints must fall after the grace week.
        cur.execute("SELECT on_track FROM trs_pilot_checkpoints WHERE pilot_id = %s AND date < %s AND date >= %s "
                    "ORDER BY date DESC LIMIT 1",
                    (pilot["id"], day, pilot["start_date"] + timedelta(days=GRACE_DAYS)))
        prev = cur.fetchone()
        if prev is not None and not prev["on_track"]:
            missed = [c["label"] for c in ev["criteria"] if not c["met"]]
            raised = _emit(cur, hotel_slug, pilot, SIGNAL_AT_RISK, ev, severity=3,
                           title=f"{pilot['name']} is off track",
                           summary=f"Behind on: {', '.join(missed[:4])}. {(pilot['end_date'] - day).days} days left.")
    return {"pilot_id": pilot["id"], "date": day.isoformat(), "score": ev["score"], "on_track": on_track,
            "all_met": ev["all_met"], "signal": raised}


def _emit(cur: Any, hotel_slug: str, pilot: dict, kind: str, ev: dict, *, severity: int, title: str, summary: str) -> str:
    signals.upsert_signal(
        cur, hotel_slug,
        signals.SignalIn(
            module="trust", kind=kind, dedupe_key=f"{kind}:{pilot['id']}", title=title, summary=summary,
            severity=severity, confidence=0.9, owner_role="owner", subject_type="trs_pilot", subject_id=str(pilot["id"]),
            evidence=[signals.Evidence(kind="metric", label=c["label"], value=c["value"],
                                       ref={"metric": c["metric_key"], "start": ev["period_start"], "end": ev["period_end"]})
                      for c in ev["criteria"][:8]],
        ),
    )
    return kind


@jobs.scheduled("trust.pilot_checkpoint", jobs.daily_at("07:00"), timeout_s=120)
def _checkpoint_job(ctx: jobs.JobCtx) -> dict:
    hotel = get_hotel(ctx.cur, ctx.hotel_slug)
    today = htime.local_date(hotel)
    # The checkpoint covers the last full day: at 07:00 today has barely begun.
    day = today - timedelta(days=1)
    out = []
    for p in store.list_pilots(ctx.cur, ctx.hotel_slug):
        if p["status"] != "active" or day < p["start_date"]:
            continue
        out.append(checkpoint(ctx.cur, ctx.hotel_slug, p, day=min(day, p["end_date"])))
        if day >= p["end_date"]:
            store.set_status(ctx.cur, SYSTEM, ctx.hotel_slug, p["id"], "completed")
    return {"checkpoints": out}
