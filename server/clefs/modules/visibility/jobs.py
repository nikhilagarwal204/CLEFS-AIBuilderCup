"""
Visibility jobs (04 spec 5.11), registered at import (module.py imports this).

| Job | Schedule |
|---|---|
| visibility.google_hotels_daily | daily 05:30 (after Demand's 04:00 fetch, so page 1 is a cache hit) |
| visibility.rank_grid_weekly | Monday 04:00: one visibility.rank_grid_keyword task per keyword |
| visibility.rank_calibrate_monthly | first Monday 04:30: one visibility.rank_calibrate_keyword task per keyword |
| visibility.gbp_daily | daily 06:00 |
| visibility.gbp_kpis_monthly | day 3, 06:30 (previous month) |
| visibility.gbp_keywords_monthly | day 5, 06:00 (previous month) |
| visibility.lodging_check | daily 06:15 |
| visibility.site_health_weekly | Sunday 03:00 |
| visibility.attribution_monthly | day 2, 07:00 (previous month) |
| visibility.competitors_sync | daily 03:30, from demand.api.comp_set |
| visibility.gbp_raw_expire | daily 03:45 UTC, platform-wide |

| visibility.ai_prompts_refresh | Sunday 20:00 |
| visibility.ai_radar_weekly | Monday 01:00: plans the batch |
| visibility.ai_batch_step | task: <= 4 runs per round, re-enqueues itself |
| visibility.ai_weekly_rollup | task after the batch: cells, SoV, facts, signals |
| visibility.snapshot_purge | daily 03:55 UTC: snapshot batches past expires_at |
| visibility.public_check_purge | daily 04:05 UTC: public checks past expires_at |

Per-keyword tasks keep each unit of work (49 Places calls, or 9+9
calibration calls) inside the worker's 25 s tick budget; the AI step and
rollup tasks do the same with rounds of 4 calls and chunks of fact checks.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs import jobs
from clefs.core.oauth import NotConnected
from clefs.core.privacy import register_pii
from clefs.core.time import local_date
from clefs.modules.visibility import attribution, gbp, google_hotels, lodging, rank_grid, site_health, store
from clefs.modules.visibility.http import VendorError

# ─── Retention ───
# Raw GBP API rows expire 30 days after fetch (expires_at); the registry lets
# Trust's retention and DSAR views see the same rule. They hold no guest
# identity, only the hotel's own listing data.
for _table in ("vis_gbp_daily", "vis_gbp_keywords", "vis_lodging_snapshots"):
    register_pii("visibility", _table, subject_columns={}, ts_column="fetched_at", retention_days=30,
                 strategy="delete")


def previous_month(hotel: Any) -> date:
    return (local_date(hotel).replace(day=1) - timedelta(days=1)).replace(day=1)


def _gbp_guard(fn: Any, *args: Any) -> dict:
    """A revoked grant or a non-transient Google answer is a result, not a
    failure (retrying cannot help); a transient one raises so the slot retries."""
    try:
        return fn(*args)
    except NotConnected as exc:
        return {"skipped": "not_connected", "detail": exc.detail}
    except VendorError as exc:
        if exc.transient:
            raise
        return {"error": exc.detail}


# ─── Google Hotels ───


@jobs.scheduled("visibility.google_hotels_daily", jobs.daily_at("05:30"), timeout_s=120)
def _google_hotels(ctx: jobs.JobCtx) -> dict:
    return google_hotels.run_daily(ctx.cur, ctx.hotel)


# ─── Rank grid ───


@jobs.scheduled("visibility.rank_grid_weekly", jobs.weekly_at(1, "04:00"))
def _rank_weekly(ctx: jobs.JobCtx) -> dict:
    profile = store.get_profile(ctx.cur, ctx.hotel_slug)
    kws = rank_grid.keywords(profile, ctx.hotel)
    week = local_date(ctx.hotel).isoformat()
    for kw in kws:
        jobs.enqueue("visibility.rank_grid_keyword", {"keyword": kw}, hotel_slug=ctx.hotel_slug,
                     dedupe_key=f"visibility.rank_grid:{ctx.hotel_slug}:{week}:{kw}", cur=ctx.cur)
    return {"keywords": len(kws)}


@jobs.task("visibility.rank_grid_keyword", max_attempts=3, backoff_s=300, timeout_s=60)
def _rank_keyword(ctx: jobs.JobCtx, payload: dict) -> dict:
    kw = str(payload.get("keyword") or "")
    out = rank_grid.run_keyword(ctx.cur, ctx.hotel, store.get_profile(ctx.cur, ctx.hotel_slug), kw)
    if out.get("run_id"):
        out["drop"] = rank_grid.check_drop(ctx.cur, ctx.hotel, kw)
    return out


@jobs.scheduled("visibility.rank_calibrate_monthly", jobs.nth_weekday_at(1, 1, "04:30"))
def _rank_calibrate(ctx: jobs.JobCtx) -> dict:
    profile = store.get_profile(ctx.cur, ctx.hotel_slug)
    if not profile["serpapi_enabled"]:
        return {"skipped": "serpapi_disabled"}
    kws = rank_grid.keywords(profile, ctx.hotel)
    month = local_date(ctx.hotel).replace(day=1).isoformat()
    for kw in kws:
        jobs.enqueue("visibility.rank_calibrate_keyword", {"keyword": kw}, hotel_slug=ctx.hotel_slug,
                     dedupe_key=f"visibility.rank_calibrate:{ctx.hotel_slug}:{month}:{kw}", cur=ctx.cur)
    return {"keywords": len(kws)}


@jobs.task("visibility.rank_calibrate_keyword", max_attempts=2, backoff_s=600, timeout_s=90)
def _rank_calibrate_keyword(ctx: jobs.JobCtx, payload: dict) -> dict:
    return rank_grid.calibrate(ctx.cur, ctx.hotel, only=[str(payload.get("keyword") or "")])


# ─── Google Business Profile ───


@jobs.scheduled("visibility.gbp_daily", jobs.daily_at("06:00"))
def _gbp_daily(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.visibility import vsignals

    # The setup nudge rides on the daily job; it is a weekly-cadence signal,
    # so the brief shows it once a week however often it is refreshed.
    vsignals.gbp_setup(ctx.cur, ctx.hotel_slug, gbp.connection_state(ctx.cur, ctx.hotel_slug))
    return _gbp_guard(gbp.run_daily, ctx.cur, ctx.hotel)


@jobs.scheduled("visibility.gbp_kpis_monthly", jobs.monthly_at(3, "06:30"))
def _gbp_kpis(ctx: jobs.JobCtx) -> dict:
    return _gbp_guard(gbp.compute_kpis, ctx.cur, ctx.hotel, previous_month(ctx.hotel))


@jobs.scheduled("visibility.gbp_keywords_monthly", jobs.monthly_at(5, "06:00"))
def _gbp_keywords(ctx: jobs.JobCtx) -> dict:
    return _gbp_guard(gbp.run_keywords, ctx.cur, ctx.hotel, previous_month(ctx.hotel))


@jobs.scheduled("visibility.lodging_check", jobs.daily_at("06:15"))
def _lodging(ctx: jobs.JobCtx) -> dict:
    return _gbp_guard(lodging.run_check, ctx.cur, ctx.hotel)


@jobs.scheduled("visibility.gbp_raw_expire", jobs.daily_at("03:45", tz="utc"), per_hotel=False)
def _gbp_expire(ctx: jobs.JobCtx) -> dict:
    return gbp.expire_raw(ctx.cur)


# ─── Site health, attribution, competitors ───


@jobs.scheduled("visibility.site_health_weekly", jobs.weekly_at(7, "03:00"), timeout_s=120)
def _site_health(ctx: jobs.JobCtx) -> dict:
    return site_health.run_weekly(ctx.cur, ctx.hotel)


@jobs.scheduled("visibility.attribution_monthly", jobs.monthly_at(2, "07:00"))
def _attribution(ctx: jobs.JobCtx) -> dict:
    return attribution.compute(ctx.cur, ctx.hotel, previous_month(ctx.hotel))


@jobs.scheduled("visibility.competitors_sync", jobs.daily_at("03:30"))
def _competitors(ctx: jobs.JobCtx) -> dict:
    return store.sync_competitors(ctx.cur, ctx.hotel_slug)


# ─── AI radar (5.5) ───
# ai_prompts_refresh (Sunday 20:00) versions the prompts; ai_radar_weekly
# (Monday 01:00) plans the batch; ai_batch_step runs <= 4 calls per tick and
# re-enqueues itself; ai_weekly_rollup computes the cells, then checks facts
# in resumable chunks. Snapshot batches (no hotel) share the step task.


@jobs.scheduled("visibility.ai_prompts_refresh", jobs.weekly_at(7, "20:00"))
def _ai_prompts(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.visibility import prompts

    return prompts.refresh(ctx.cur, ctx.hotel)


@jobs.scheduled("visibility.ai_radar_weekly", jobs.weekly_at(1, "01:00"))
def _ai_weekly(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.visibility import ai_radar

    return ai_radar.create_weekly_batch(ctx.cur, ctx.hotel)


@jobs.task("visibility.ai_batch_step", max_attempts=3, backoff_s=60, timeout_s=25)
def _ai_step(ctx: jobs.JobCtx, payload: dict) -> dict:
    from clefs.modules.visibility import ai_radar

    return ai_radar.step(ctx.cur, int(payload["batch_id"]))


@jobs.task("visibility.ai_weekly_rollup", max_attempts=3, backoff_s=120, timeout_s=25)
def _ai_rollup(ctx: jobs.JobCtx, payload: dict) -> dict:
    from clefs.modules.visibility import stats

    return stats.rollup_task(ctx.cur, int(payload["batch_id"]), phase=str(payload.get("phase") or "cells"),
                             n=int(payload.get("n") or 0))


@jobs.scheduled("visibility.snapshot_purge", jobs.daily_at("03:55", tz="utc"), per_hotel=False)
def _snapshot_purge(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.visibility import snapshot

    return snapshot.purge_expired(ctx.cur)


# ─── Public Google check (5.9) ───

# The lead's contacts are sealed (core.privacy.seal), so only the name is a
# searchable subject column; all three lead columns are cleared at 30 days
# and the whole row goes at expires_at (visibility.public_check_purge).
register_pii("visibility", "vis_public_checks", subject_columns={"lead_name": "name"}, ts_column="created_at",
             retention_days=30, strategy="redact",
             redact_columns=("lead_name", "lead_email_enc", "lead_whatsapp_enc"))


@jobs.scheduled("visibility.public_check_purge", jobs.daily_at("04:05", tz="utc"), per_hotel=False)
def _public_purge(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.visibility import public_check

    return public_check.purge_expired(ctx.cur)


@jobs.task("visibility.public_lead_followup", max_attempts=3, backoff_s=600, timeout_s=30)
def _public_lead_followup(ctx: jobs.JobCtx, payload: dict) -> dict:
    from clefs.modules.visibility import public_check

    return public_check.whatsapp_followup(ctx.cur, str(payload.get("check_id") or ""))
