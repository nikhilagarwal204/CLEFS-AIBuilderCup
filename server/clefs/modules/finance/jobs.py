"""
Finance jobs (04 spec 6.8). Imported by module.py so the worker registers them.

- finance.weekly_rollup      weekly_at(1, "05:00"), the prior ISO week in hotel time
- finance.stat_inputs        monthly_at(2, "05:30"), the previous month
- finance.connector_sync     monthly_at(3, "06:00"), the previous month (and on demand)
- finance.map_propose        task: the LLM step for accounts rules/memory left open
- finance.anomaly_scan       task: re-scan a published month (publish scans inline)
- finance.memory_rebuild     weekly, platform-wide, non-Xero data only
- finance.register_baseline  task
"""

from __future__ import annotations

from datetime import timedelta
from clefs import jobs
from clefs.core import time as htime
from clefs.modules.finance import anomalies, benchmarks, calc, mapping, pulse, review, summary
from clefs.modules.finance import signals as fin_signals


@jobs.scheduled("finance.weekly_rollup", jobs.weekly_at(1, "05:00"))
def weekly_rollup(ctx: jobs.JobCtx) -> dict:
    today = htime.local_date(ctx.hotel)
    week_start = calc.iso_week_start(today) - timedelta(days=7)
    p = pulse.compute_week(ctx.cur, ctx.hotel_slug, week_start)
    pulse.store_week(ctx.cur, ctx.hotel_slug, p)
    fin_signals.nrevpar_weekly(ctx.cur, ctx.hotel_slug, p)
    return {"week_start": week_start.isoformat(), "nrevpar_minor": p["nrevpar_minor"]}


@jobs.scheduled("finance.stat_inputs", jobs.monthly_at(2, "05:30"))
def stat_inputs(ctx: jobs.JobCtx) -> dict:
    month = calc.prev_month(htime.local_date(ctx.hotel))
    review.store_stat_inputs(ctx.cur, ctx.hotel_slug, month)
    ctx.cur.execute("SELECT id FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s",
                    (ctx.hotel_slug, month))
    for r in ctx.cur.fetchall():
        review.refresh(ctx.cur, ctx.hotel_slug, r["id"])
    return {"month": month.isoformat()}


@jobs.scheduled("finance.connector_sync", jobs.monthly_at(3, "06:00"))
def connector_sync(ctx: jobs.JobCtx) -> dict:
    from clefs.modules.finance.connectors import sync_hotel

    month = calc.prev_month(htime.local_date(ctx.hotel))
    return sync_hotel(ctx.cur, ctx.hotel_slug, month)


@jobs.task("finance.map_propose", max_attempts=3, backoff_s=120)
def map_propose(ctx: jobs.JobCtx, payload: dict) -> dict:
    ids = [int(i) for i in payload.get("account_ids") or []]
    ctx.cur.execute("""SELECT account_id, SUM(amount_minor) AS amount FROM fin_pl_lines
                       WHERE batch_id = %s GROUP BY account_id""", (payload.get("batch_id"),))
    amounts = {r["account_id"]: int(r["amount"]) for r in ctx.cur.fetchall()}
    # Accounts a person mapped meanwhile are skipped.
    open_ids = [i for i in ids if (mapping.live_map(ctx.cur, i) or {}).get("locked") is not True]
    res = mapping.propose_llm(ctx.cur, ctx.hotel_slug, open_ids, amounts=amounts)
    if payload.get("batch_id"):
        review.refresh(ctx.cur, ctx.hotel_slug, int(payload["batch_id"]))
    return res


@jobs.task("finance.anomaly_scan", max_attempts=3)
def anomaly_scan(ctx: jobs.JobCtx, payload: dict) -> dict:
    from clefs.modules.finance import publish

    month = calc.parse_month(str(payload["month"]))
    row = publish.published_summary(ctx.cur, ctx.hotel_slug, month)
    if row is None:
        return {"skipped": "not published"}
    ctx.cur.execute("SELECT * FROM fin_import_batches WHERE id = %s", (row["batch_id"],))
    batch = ctx.cur.fetchone()
    if batch is None:
        return {"skipped": "no batch"}
    lines = review.lines_for_batch(ctx.cur, batch, snapshot=batch["map_versions"] or None)
    s = summary.compute(review.confirmed_totals(lines), arn=row.get("arn"))
    checks = batch["checks"] or {}
    top = anomalies.scan(ctx.cur, ctx.hotel_slug, month, s, checks, row["currency"].strip())
    return {"top": [a["fingerprint"] for a in top]}


@jobs.scheduled("finance.memory_rebuild", jobs.weekly_at(7, "03:30", tz="utc"), per_hotel=False)
def memory_rebuild(ctx: jobs.JobCtx) -> dict:
    return {"rows": mapping.rebuild_memory(ctx.cur)}


@jobs.task("finance.register_baseline", max_attempts=2)
def register_baseline(ctx: jobs.JobCtx, payload: dict) -> dict:
    end = calc.parse_month(payload["end_month"]) if payload.get("end_month") else None
    row = benchmarks.register_baseline(ctx.cur, ctx.hotel_slug, actor_id=payload.get("actor_id"), end_month=end)
    return {"registered_at": row["registered_at"].isoformat()}
