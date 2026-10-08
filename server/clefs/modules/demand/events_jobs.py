"""
Demand Radar events: scheduled jobs and registrations (02 spec 4.4 jobs).
Imported by module.py, so jobs.load_all() and create_app() both see them.

Jobs:
- dr.curated_sync            daily 00:30 UTC, global: the curated YAML seed
                             (skipped while the file's sha256 is unchanged).
- dr.phq_events_sync         daily 01:00 UTC, per hotel: PredictHQ Events in
                             a 60 km radius, incremental (updated.gte cursor
                             shared by hotels on the same block).
- dr.phq_features_sync       daily 01:30 UTC, per hotel: PredictHQ Features.
- dr.ticketmaster_sync       weekly Mon 02:00 UTC, global: Ticketmaster AE.
- dr.ai_discovery            weekly Mon 03:00 hotel time, per hotel: AI web
                             discovery (UNVERIFIED, confidence <= 0.6).
- dr.curated_verify_reminder quarterly (1 Jan/Apr/Jul/Oct 05:00 UTC), global:
                             lists seed rows not re-verified for a quarter.

The ICS commit handler for core ingest (kind ICS) is registered here too.
"""

from __future__ import annotations

import logging

from clefs import ai, jobs
from clefs.core import clock
from clefs.modules.demand import curated, ics
from clefs.modules.demand.adapters import VendorError, ai_web, phq_events, phq_features, ticketmaster

log = logging.getLogger("clefs.demand.jobs")

# ─── core ingest (kind ICS) ───

try:
    from clefs.core.ingest import register_commit_handler

    register_commit_handler("ICS", ics.commit_ics)
except ImportError:  # the ingest package is optional to the app (jobs.OPTIONAL_REGISTRANTS)
    log.info("clefs.core.ingest absent: ICS files import only through the demand route")


# ─── Scheduled jobs ───


def _vendor_guard(fn, *args, **kw) -> dict:
    """A vendor outage is a skipped run, not a failed slot: a transient error
    retries on the next tick's catch-up; a permanent one (401, 403) is
    reported and waits for tomorrow instead of burning retries."""
    try:
        return fn(*args, **kw)
    except VendorError as exc:
        if exc.transient:
            raise
        return {"skipped": "vendor_error", "status": exc.status, "vendor": exc.vendor}


@jobs.scheduled("dr.curated_sync", jobs.daily_at("00:30", tz="utc"), per_hotel=False, timeout_s=120)
def _curated_sync(ctx: jobs.JobCtx) -> dict:
    return curated.sync(ctx.cur)


@jobs.scheduled("dr.phq_events_sync", jobs.daily_at("01:00", tz="utc"), timeout_s=240)
def _phq_events(ctx: jobs.JobCtx) -> dict:
    return _vendor_guard(phq_events.sync_hotel, ctx.cur, ctx.hotel)


@jobs.scheduled("dr.phq_features_sync", jobs.daily_at("01:30", tz="utc"), timeout_s=120)
def _phq_features(ctx: jobs.JobCtx) -> dict:
    return _vendor_guard(phq_features.sync_hotel, ctx.cur, ctx.hotel)


@jobs.scheduled("dr.ticketmaster_sync", jobs.weekly_at(1, "02:00", tz="utc"), per_hotel=False, timeout_s=240)
def _ticketmaster(ctx: jobs.JobCtx) -> dict:
    return _vendor_guard(ticketmaster.sync, ctx.cur)


@jobs.scheduled("dr.ai_discovery", jobs.weekly_at(1, "03:00"), timeout_s=180)
def _ai_discovery(ctx: jobs.JobCtx) -> dict:
    try:
        return ai_web.discover(ctx.cur, ctx.hotel)
    except ai.AIError as exc:
        # A malformed answer will not improve on a retry within the hour.
        return {"skipped": "ai_error", "code": exc.code}


@jobs.scheduled("dr.curated_verify_reminder", jobs.monthly_at(1, "05:00", tz="utc"), per_hotel=False)
def _verify_reminder(ctx: jobs.JobCtx) -> dict:
    today = clock.now().date()
    if today.month not in (1, 4, 7, 10):
        return {"skipped": "not_a_quarter_start"}
    seed, _ = curated.load_seed()
    stale = curated.stale_rows(seed, today)
    if stale:
        log.warning("demand curated seed: %d rows need re-verification: %s", len(stale),
                    ", ".join(r["key"] or "?" for r in stale[:20]))
    return {"stale": len(stale), "keys": [r["key"] for r in stale[:100]]}
