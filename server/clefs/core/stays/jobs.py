"""core/stays jobs (keys "stays.*"; registered when the package is imported,
through jobs.OPTIONAL_REGISTRANTS).

- stays.dispatch_events  every minute, platform-wide: stay events to consumers
- stays.contact_purge    03:30 UTC daily, platform-wide: contacts past purge_after
- stays.occupancy_derive 04:30 hotel-local daily: stayed flags, occupancy (yesterday and today)
- stays.otb_snapshot     23:55 hotel-local daily: on-the-books photograph for pace
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core.stay_events import DISPATCH_JOB
from clefs.core.time import business_date
from clefs.core.stays import contacts, events, nights, occupancy, otb

# The tick route gives a run about 25 s; dispatch keeps inside 20 s (02 spec 0.4).
DISPATCH_BUDGET_S = 18.0
# Late PMS corrections (a night audit re-run) land within a week.
DERIVE_LOOKBACK_DAYS = 7


@jobs.scheduled(DISPATCH_JOB, jobs.every(minutes=1), per_hotel=False, timeout_s=30)
def dispatch_events(ctx: jobs.JobCtx) -> dict[str, Any]:
    return {"consumers": events.dispatch(ctx.cur, budget_s=DISPATCH_BUDGET_S)}


@jobs.scheduled("stays.contact_purge", jobs.daily_at("03:30", tz="utc"), per_hotel=False, timeout_s=60)
def contact_purge(ctx: jobs.JobCtx) -> dict[str, Any]:
    total = {"purged": 0, "ota": 0, "direct": 0}
    for _ in range(20):  # 20 x 5000 rows per run; the rest waits for tomorrow
        out = contacts.purge_contacts(ctx.cur, now=ctx.now)
        for k in total:
            total[k] += out[k]
        if out["purged"] < 5000:
            break
    return total


# After the 04:00 business-day cutoff: yesterday's night closes, and
# IN_HOUSE nights of today become stayed.
@jobs.scheduled("stays.occupancy_derive", jobs.daily_at("04:30"), timeout_s=60)
def occupancy_derive(ctx: jobs.JobCtx) -> dict[str, Any]:
    today = business_date(ctx.hotel, ctx.now)
    changed = nights.refresh_stayed(ctx.cur, ctx.hotel_slug, today=today)
    out = occupancy.derive_occupancy(ctx.cur, ctx.hotel_slug, today - timedelta(days=DERIVE_LOOKBACK_DAYS), today,
                                     today=today)
    return {"stayed_changed": changed, **out}


# 02 spec 4.4: 23:55 local, before Demand Radar's demand index reads it.
@jobs.scheduled("stays.otb_snapshot", jobs.daily_at("23:55"), timeout_s=60)
def otb_snapshot(ctx: jobs.JobCtx) -> dict[str, Any]:
    today = business_date(ctx.hotel, ctx.now)
    return {"rows": otb.snapshot_otb(ctx.cur, ctx.hotel_slug, snapshot_date=today)}
