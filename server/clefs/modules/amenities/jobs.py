"""
Amenity jobs (02 spec 5.3), plus the imports that register this module's
signals, action types, metrics, ingest commit handlers and PII entries.
module.py imports this file, so jobs.load_all() and create_app() see them.

Schedule (hotel-local unless noted):
- am.link_requests_llm  02:30 daily   alias pass, then the LLM batch for what is left
- am.variance_rollup    03:30 daily   rollup (14 days, 63 on the first run), then every detector
- am.learn_rates        Mon 04:00     shrinkage-adjusted on-request rates
- am.par_recompute      Mon 04:30     par levels (after the rates)
- am.benchmark_rollup   2nd 05:00 UTC system scope, the previous month
- am.rollup_range       task          re-rolls the dates a CSV import touched
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core import time as ctime
from clefs.core.privacy import register_pii
from clefs.modules.amenities import actions as _actions  # noqa: F401  registers action types
from clefs.modules.amenities import benchmarks, detectors, par, rates, requests, variance
from clefs.modules.amenities import issuance as _issuance  # noqa: F401  registers ingest commit handlers
from clefs.modules.amenities import metrics as _metrics  # noqa: F401  registers metrics

ROLLUP_DAYS = 14
BACKFILL_DAYS = 63

# ─── Personal data ───
# Staff names on issuance lines are staff data kept for the control log;
# redacted after two years (the counts stay for trends and benchmarks).
register_pii("amenities", "amn_issuance", subject_columns={"staff_name": "staff_name"}, ts_column="created_at",
             retention_days=730, strategy="redact", redact_columns=("staff_name",))


# ─── Helpers ───


def last_closed_day(hotel: Any) -> date:
    """Yesterday in hotel-local time: the rollup's last business date."""
    return ctime.local_date(hotel) - timedelta(days=1)


def rollup_and_detect(cur: Any, hotel: Any, end: date, *, days: int | None = None) -> dict:
    if days is None:
        cur.execute("SELECT MIN(business_date) AS first FROM amn_daily_totals WHERE hotel_slug = %s", (hotel.slug,))
        first = cur.fetchone()["first"]
        days = ROLLUP_DAYS if first and (end - first).days >= BACKFILL_DAYS - 7 else BACKFILL_DAYS
    roll = variance.rollup(cur, hotel, end - timedelta(days=days - 1), end)
    return {"rollup": roll, "detectors": detectors.run_all(cur, hotel, end)}


# ─── Scheduled ───


@jobs.scheduled("am.link_requests_llm", jobs.daily_at("02:30"))
def link_requests_llm(ctx: jobs.JobCtx) -> dict:
    alias = requests.alias_pass(ctx.cur, ctx.hotel_slug)
    llm = requests.llm_pass(ctx.cur, ctx.hotel_slug)
    return {"alias": alias, "llm": llm}


@jobs.scheduled("am.variance_rollup", jobs.daily_at("03:30"), timeout_s=240)
def variance_rollup(ctx: jobs.JobCtx) -> dict:
    hotel = ctx.hotel
    alias = requests.alias_pass(ctx.cur, hotel.slug)
    return {"alias": alias, **rollup_and_detect(ctx.cur, hotel, last_closed_day(hotel))}


@jobs.scheduled("am.learn_rates", jobs.weekly_at(1, "04:00"))
def learn_rates(ctx: jobs.JobCtx) -> dict:
    return rates.learn(ctx.cur, ctx.hotel, last_closed_day(ctx.hotel))


@jobs.scheduled("am.par_recompute", jobs.weekly_at(1, "04:30"))
def par_recompute(ctx: jobs.JobCtx) -> dict:
    return par.recompute(ctx.cur, ctx.hotel, ctime.local_date(ctx.hotel))


@jobs.scheduled("am.benchmark_rollup", jobs.monthly_at(2, "05:00", tz="utc"), per_hotel=False, timeout_s=240)
def benchmark_rollup(ctx: jobs.JobCtx) -> dict:
    prev_month = clock.now().date().replace(day=1) - timedelta(days=1)
    return benchmarks.rollup(ctx.cur, prev_month)


# ─── Tasks ───


@jobs.task("am.rollup_range", max_attempts=3)
def rollup_range(ctx: jobs.JobCtx, payload: dict) -> dict:
    """Re-roll [start, end] (a committed CSV or a back-dated manual line),
    then run the detectors for the latest closed day."""
    hotel = ctx.hotel
    last = last_closed_day(hotel)
    start = date.fromisoformat(payload["start"])
    end = min(date.fromisoformat(payload["end"]), last)
    if start > end:
        return {"skipped": "nothing_closed"}
    roll = variance.rollup(ctx.cur, hotel, start, end)
    return {"rollup": roll, "detectors": detectors.run_all(ctx.cur, hotel, last)}

