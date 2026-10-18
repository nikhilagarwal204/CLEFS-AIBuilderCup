"""
Demand Radar engine jobs (02 spec 4.4). Importing this module registers
them (module.py imports it), plus the signal kinds, action types and
metrics the engine owns.

| Job                   | Schedule (hotel-local unless noted) | What                                           |
|-----------------------|-------------------------------------|------------------------------------------------|
| dr.relevance          | daily 00:05                         | score events for the next 180 days             |
| dr.calibrate          | weekly Mon 00:02                    | k per class from the hotel's own pickup        |
| dr.demand_index       | daily 00:15 (after stays.otb_snapshot 23:55) | the index for the next 120 nights     |
| dr.recommend          | daily 00:30                         | expire, supersede, propose; dr.pace.behind     |
| dr.graph_actions      | daily 00:45                         | staffing, pre-arrival offer, KB suggestion     |
| dr.measure_outcomes   | daily 05:00                         | APPLIED from BAR evidence; observed outcomes   |
| dr.weekly_top5        | weekly Mon 06:00                    | dr.opportunity for the top 5                   |
| dr.ghc_deadline       | daily 07:00                         | dr.ghc.deadline while checklist items are open |
| dr.leads_purge        | daily 03:35 UTC (global)            | demand-preview leads past their 90 days        |

Each per-hotel job runs in its own transaction and skips hotels that
switched the demand module off (clefs.jobs).
"""

from __future__ import annotations

import logging
from datetime import timedelta

from clefs import jobs
from clefs.core import clock
from clefs.core.privacy import register_pii
from clefs.core.time import local_date
from clefs.modules.demand import actions as _actions  # noqa: F401  (registers the action types)
from clefs.modules.demand import campaigns as _campaigns  # noqa: F401  (registers its event ref finder)
from clefs.modules.demand import demand_index as di
from clefs.modules.demand import ghc, graph_actions, measure, metrics as _metrics  # noqa: F401
from clefs.modules.demand import recommend, relevance
from clefs.modules.demand import signals as _signals  # noqa: F401  (registers the signal kinds)

log = logging.getLogger("clefs.demand.jobs")

DI_DAYS = 120
LEAD_RETENTION_DAYS = 90

# Public demand-preview leads hold a name and sealed contacts (dr_leads).
register_pii("demand", "dr_leads", subject_columns={"contact_name": "name"}, ts_column="created_at",
             retention_days=LEAD_RETENTION_DAYS, strategy="delete")


@jobs.scheduled("dr.relevance", jobs.daily_at("00:05"), timeout_s=120)
def _relevance(ctx: jobs.JobCtx) -> dict:
    return relevance.compute(ctx.cur, ctx.hotel, today=local_date(ctx.hotel))


@jobs.scheduled("dr.calibrate", jobs.weekly_at(1, "00:02"), timeout_s=120)
def _calibrate(ctx: jobs.JobCtx) -> dict:
    return relevance.calibrate(ctx.cur, ctx.hotel, today=local_date(ctx.hotel))


@jobs.scheduled("dr.demand_index", jobs.daily_at("00:15"), timeout_s=120)
def _demand_index(ctx: jobs.JobCtx) -> dict:
    out = di.compute(ctx.cur, ctx.hotel, start=local_date(ctx.hotel), days=DI_DAYS)
    return {"nights": len(out)}


@jobs.scheduled("dr.recommend", jobs.daily_at("00:30"), timeout_s=150)
def _recommend(ctx: jobs.JobCtx) -> dict:
    today = local_date(ctx.hotel)
    rows = di.stored(ctx.cur, ctx.hotel.slug, today, today + timedelta(days=recommend.HORIZON_DAYS - 1))
    nights = [di.day_out(r) for r in rows.values()] or None  # None: compute now (the index job has not run)
    report = recommend.run(ctx.cur, ctx.hotel, today=today, nights=nights)
    if nights is None:
        nights = [di.day_out(r) for r in di.stored(ctx.cur, ctx.hotel.slug, today,
                                                   today + timedelta(days=recommend.HORIZON_DAYS - 1)).values()]
    report["pace"] = recommend.raise_pace_signals(ctx.cur, ctx.hotel, nights, today=today)
    return report


@jobs.scheduled("dr.graph_actions", jobs.daily_at("00:45"), timeout_s=120)
def _graph(ctx: jobs.JobCtx) -> dict:
    return graph_actions.run(ctx.cur, ctx.hotel, today=local_date(ctx.hotel))


@jobs.scheduled("dr.measure_outcomes", jobs.daily_at("05:00"), timeout_s=120)
def _measure(ctx: jobs.JobCtx) -> dict:
    today = local_date(ctx.hotel)
    return {**measure.check_applied(ctx.cur, ctx.hotel, today=today),
            **measure.measure_outcomes(ctx.cur, ctx.hotel, today=today)}


@jobs.scheduled("dr.weekly_top5", jobs.weekly_at(1, "06:00"), timeout_s=60)
def _top5(ctx: jobs.JobCtx) -> dict:
    return measure.raise_top5(ctx.cur, ctx.hotel, today=local_date(ctx.hotel))


@jobs.scheduled("dr.ghc_deadline", jobs.daily_at("07:00"), timeout_s=30)
def _ghc(ctx: jobs.JobCtx) -> dict:
    return ghc.check_deadline(ctx.cur, ctx.hotel)


@jobs.scheduled("dr.leads_purge", jobs.daily_at("03:35", tz="utc"), per_hotel=False, timeout_s=30)
def _leads_purge(ctx: jobs.JobCtx) -> dict:
    ctx.cur.execute("DELETE FROM dr_leads WHERE expires_at <= %s", (clock.now(),))
    return {"deleted": ctx.cur.rowcount}
