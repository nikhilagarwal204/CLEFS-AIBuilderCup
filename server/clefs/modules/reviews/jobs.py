"""
Reviews registrations: signal kinds, the PII registry, the inbound-email
handler and the scheduled jobs (03 spec 4.1). Imported by module.py, so
jobs.load_all() and create_app() both see them.

Jobs (per hotel; skipped for hotels with the module off):
- reviews.sync         daily 05:30 hotel time: every connected source.
- reviews.analyze      every 15 min: analyse, draft, signal, act.
- reviews.raw_purge    daily 03:30 hotel time: raw text and author names
                       older than 30 days are nulled; derived data stays.
- reviews.theme_trends Mondays 06:00 hotel time (Canon 18: weekly cadence).
"""

from __future__ import annotations

from typing import Any

from clefs import jobs
from clefs.core.privacy import register_pii
from clefs.modules.reviews import pipeline, store, sync, trends
from clefs.notify import on_inbound_email

ANALYZE_BATCH = 10

# Signal kinds are registered next to their detectors (pipeline.py,
# trends.py), so any import path that can emit one has registered it.


# ─── Personal data ───
# The reviewer's name and words are personal data we keep 30 days (GBP
# policy; one rule for every platform). reviews.raw_purge enforces it daily;
# the registry lets Trust's retention and DSAR engines see the same rule.

register_pii("reviews", "rev_reviews",
             subject_columns={"author_display": "name", "raw_text": "free_text", "title": "free_text"},
             ts_column="ingested_at", retention_days=30, strategy="redact",
             redact_columns=("author_display", "raw_text", "title"))


# ─── Forwarded review emails ───


@on_inbound_email("reviews", priority=50)
def _reviews_email(email: Any) -> dict:
    from clefs.core.hotels import module_enabled
    from clefs.modules.reviews.sources import email_forward

    if not module_enabled(email.cur, email.hotel_slug, "reviews"):
        return {"handled": False, "reason": "module_disabled"}
    return email_forward.handle(email)


# ─── Scheduled jobs ───


@jobs.scheduled("reviews.sync", jobs.daily_at("05:30"), timeout_s=300)
def _sync(ctx: jobs.JobCtx) -> dict:
    return sync.sync_hotel(ctx.cur, ctx.hotel)


@jobs.scheduled("reviews.analyze", jobs.every(minutes=15), timeout_s=240)
def _analyze(ctx: jobs.JobCtx) -> dict:
    # Up to two AI calls per review (analysis, draft): 10 a run stays well
    # inside the timeout and still clears ~960 reviews a day.
    return pipeline.run_pending(ctx.cur, ctx.hotel, limit=ANALYZE_BATCH)


@jobs.scheduled("reviews.raw_purge", jobs.daily_at("03:30"))
def _raw_purge(ctx: jobs.JobCtx) -> dict:
    return {"purged": store.purge_raw(ctx.cur, now=ctx.now, hotel_slug=ctx.hotel.slug)}


@jobs.scheduled("reviews.theme_trends", jobs.weekly_at(1, "06:00"))
def _theme_trends(ctx: jobs.JobCtx) -> dict:
    from clefs.core.time import local_date

    return trends.detect(ctx.cur, ctx.hotel, today=local_date(ctx.hotel, ctx.now))


@jobs.task("reviews.sync_now", max_attempts=2, timeout_s=300)
def _sync_now(ctx: jobs.JobCtx, payload: dict) -> dict:
    """The console's "sync now": queued, so a slow vendor never holds a web
    request. payload {source_id?}: one source, else all of them."""
    sid = payload.get("source_id")
    if sid is None:
        res = sync.sync_hotel(ctx.cur, ctx.hotel)
    else:
        res = sync.sync_source(ctx.cur, ctx.hotel, store.get_source(ctx.cur, ctx.hotel.slug, int(sid)))
    # New reviews are analysed right away rather than at the next 15-minute slot.
    res["analysis"] = pipeline.run_pending(ctx.cur, ctx.hotel, limit=ANALYZE_BATCH)
    return res
