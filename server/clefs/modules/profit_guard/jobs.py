"""Profit Guard jobs outside the leak detectors (02 spec 3.9). Importing this
file registers everything this part owns: signal kinds (signals.py), action
types (actions.py), metrics (metrics.py) and the jobs below, and installs
the auto-verify commit hook. The manifest imports it (FOUNDATION-REQUEST in
the lane result until module.py does).

| Job | When | What |
|---|---|---|
| pg.auto_verify (task) | every OTA_STATEMENT, CREDIT_NOTE, OTA_PAYOUT commit | ledger.auto_verify for the batch's OTA |
| pg.auto_verify | daily 04:30 local | every OTA; the 45-day timeout; stuck-import signal |
| pg.channel_economics_rollup | daily 03:00 local | last and this month into pfg_channel_economics |
| pg.dispute_followup | daily 09:00 local | reminders every 10 business days; brief approvals adopted |
| tax.td_checks | daily 02:10 local | T1-T6, TX1 for last and this month, T7 over the ledger |
| tax.td_deadline_watch | hourly | T7 (remittance within 5 days, collected vs remitted) |
| tax.td_ledger_rollup | the 1st, 04:00 local | the month that closed into pfg_td_ledger, then its checks |
| tax.td_remit_reminder | daily 09:05 local, acts on the 10th and 14th | tax.td.remittance_due per unremitted month |
| tax.unverified_rule_banner | Mondays 08:00 local | tax.rule.unverified while an announced rule is unverified |

The detectors part owns pg.detect_on_commit, pg.deadline_watch,
pg.nightly_detect and pg.feedback_tune (detectors/jobs.py); the T-series
runs here under tax.* names because those jobs call the L-series runner
only. pg.public_audit_purge belongs to the public-audit part.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.ingest import post_commit_hook_names, register_post_commit_hook
from clefs.modules.profit_guard import actions as _actions  # noqa: F401  (registers the action types)
from clefs.modules.profit_guard import metrics as _metrics  # noqa: F401  (registers the metrics)
from clefs.modules.profit_guard import signals as pg_signals

VERIFY_TASK = "pg.auto_verify"
VERIFY_KINDS = ("OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT")
REMIND_DAYS = (10, 14)


# ─── Commit hook ───


def enqueue_verify(cur: Any, batch: dict, result: Any = None) -> int | None:
    """Queue auto-verification in the committing transaction (a rolled-back
    commit enqueues nothing; the public audit's sandbox never does)."""
    slug = batch.get("hotel_slug")
    if not slug or batch.get("kind") not in VERIFY_KINDS:
        return None
    ota = result.get("ota_code") if isinstance(result, dict) else None
    return jobs.enqueue(VERIFY_TASK, {"batch_id": batch["id"], "kind": batch["kind"], "ota_code": ota},
                        hotel_slug=slug, cur=cur, dedupe_key=f"pg.verify:{batch['id']}")


def install_commit_hook() -> list[str]:
    """Register the auto-verify hook for each verify kind. Returns the kinds
    that did not have it yet (idempotent). A post-commit hook, not a handler
    wrapper, so re-registering the plain handler can never drop it."""
    name = f"{enqueue_verify.__module__}.{enqueue_verify.__qualname__}"
    done = []
    for kind in VERIFY_KINDS:
        if name not in post_commit_hook_names(kind):
            done.append(kind)
        register_post_commit_hook(kind, enqueue_verify)
    return done


# ─── Recovery ledger and disputes ───


@jobs.task(VERIFY_TASK, max_attempts=3, backoff_s=60, timeout_s=120)
def auto_verify_task(ctx: Any, payload: dict) -> dict:
    from clefs.modules.profit_guard import ledger

    out = ledger.auto_verify(ctx.cur, ctx.hotel_slug, ota_code=payload.get("ota_code"))
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}


@jobs.scheduled(VERIFY_TASK, jobs.daily_at("04:30"), timeout_s=300)
def auto_verify_daily(ctx: Any) -> dict:
    from clefs.modules.profit_guard import ledger

    out = ledger.auto_verify(ctx.cur, ctx.hotel_slug)
    pg_signals.refresh_import_review(ctx.cur, ctx.hotel, now=clock.now())
    return {k: (len(v) if isinstance(v, list) else v) for k, v in out.items()}


@jobs.scheduled("pg.channel_economics_rollup", jobs.daily_at("03:00"), timeout_s=300)
def channel_economics_rollup(ctx: Any) -> dict:
    from clefs.modules.profit_guard import economics

    return economics.nightly(ctx.cur, ctx.hotel)


@jobs.scheduled("pg.dispute_followup", jobs.daily_at("09:00"), timeout_s=120)
def dispute_followup(ctx: Any) -> dict:
    from clefs.modules.profit_guard import disputes

    return disputes.follow_up(ctx.cur, ctx.hotel)


# ─── Tourism Dirham ───


@jobs.scheduled("tax.td_checks", jobs.daily_at("02:10"), timeout_s=300)
def td_checks(ctx: Any) -> dict:
    from clefs.modules.profit_guard import tax_checks

    return tax_checks.run_tax_checks(ctx.cur, ctx.hotel_slug, trigger="nightly")


@jobs.scheduled("tax.td_deadline_watch", jobs.every(hours=1), timeout_s=120)
def td_deadline_watch(ctx: Any) -> dict:
    from clefs.modules.profit_guard import tax_checks

    return tax_checks.run_tax_checks(ctx.cur, ctx.hotel_slug, trigger="deadline")


@jobs.scheduled("tax.td_ledger_rollup", jobs.monthly_at(1, "04:00"), timeout_s=300)
def td_ledger_rollup(ctx: Any) -> dict:
    from clefs.modules.profit_guard import tax_checks

    today = htime.local_date(ctx.hotel, clock.now())
    closed = tax_checks.month_start(today.replace(day=1) - timedelta(days=1))
    row = tax_checks.rollup_month(ctx.cur, ctx.hotel_slug, closed)
    run = tax_checks.run_tax_checks(ctx.cur, ctx.hotel_slug, trigger="manual", months=[closed])
    return {"month": closed.isoformat(), "td_collected_minor": row["td_collected_minor"],
            "td_expected_minor": row["td_expected_minor"], "findings": run["findings"]}


@jobs.scheduled("tax.td_remit_reminder", jobs.daily_at("09:05"), timeout_s=120)
def td_remit_reminder(ctx: Any) -> dict:
    """One daily slot that acts on the 10th and the 14th only (one job name
    for both reminder days, as 3.9 lists it)."""
    from clefs.modules.profit_guard import tax_checks

    today = htime.local_date(ctx.hotel, clock.now())
    if today.day not in REMIND_DAYS:
        return {"skipped": today.isoformat()}
    return {"reminded": tax_checks.remit_reminder(ctx.cur, ctx.hotel)}


@jobs.scheduled("tax.unverified_rule_banner", jobs.weekly_at(1, "08:00"), timeout_s=120)
def unverified_rule_banner(ctx: Any) -> dict:
    from clefs.core import tax

    today: date = htime.local_date(ctx.hotel, clock.now())
    notices = tax.notices(ctx.cur, ctx.hotel_slug, start=today, end=today + timedelta(days=90))
    sig = pg_signals.refresh_rule_banner(ctx.cur, ctx.hotel, notices)
    return {"notices": len(notices), "signal": bool(sig)}


install_commit_hook()
