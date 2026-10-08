"""The detector scheduler (02 spec 3.9).

- `pg.detect_on_commit` (task): enqueued in the committing transaction of
  every OTA_STATEMENT, CREDIT_NOTE, OTA_PAYOUT, BANK and VCC_REPORT batch,
  so findings follow an upload within one worker tick and a rolled-back
  commit enqueues nothing.
- `pg.deadline_watch` (hourly): L8 and L14, the countdowns.
- `pg.nightly_detect` (02:00 hotel-local): L1 (for L1b), L6, L7, L9, L13, L15.
- `pg.feedback_tune` (weekly, Monday 03:30 local): tuning.py.

The commit hook: core ingest keeps one handler per kind (owned by the
imports part) plus post-commit hooks beside it. Detection enqueues from a
post-commit hook, not by wrapping the handler: a wrapper was silently lost
whenever something re-registered the plain handler. install_commit_hook()
is idempotent.
"""

from __future__ import annotations

from typing import Any

from clefs import jobs
from clefs.core.ingest import post_commit_hook_names, register_post_commit_hook

DETECT_TASK = "pg.detect_on_commit"
HOOK_KINDS = ("OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "BANK", "VCC_REPORT")


def _detectors() -> Any:
    from clefs.modules.profit_guard import detectors  # the package imports this module

    return detectors


# ─── Commit hook ───


def enqueue_detection(cur: Any, batch: dict, result: Any = None) -> int | None:
    """Queue a detector run for a committed hotel batch (none for the
    public audit's sandbox batches)."""
    slug = batch.get("hotel_slug")
    if not slug or batch.get("kind") not in HOOK_KINDS:
        return None
    ota = (result or {}).get("ota_code") if isinstance(result, dict) else None
    return jobs.enqueue(DETECT_TASK, {"kind": batch["kind"], "batch_id": batch["id"], "ota_code": ota},
                        hotel_slug=slug, cur=cur, dedupe_key=f"pg.detect:{batch['id']}")


def install_commit_hook() -> list[str]:
    """Register the detection hook for each OTA kind. Returns the kinds that
    did not have it yet (idempotent: re-registering is a no-op)."""
    name = f"{enqueue_detection.__module__}.{enqueue_detection.__qualname__}"
    done = []
    for kind in HOOK_KINDS:
        if name not in post_commit_hook_names(kind):
            done.append(kind)
        register_post_commit_hook(kind, enqueue_detection)
    return done


# ─── Jobs ───


@jobs.task(DETECT_TASK, max_attempts=3, backoff_s=60, timeout_s=120)
def detect_on_commit(ctx: Any, payload: dict) -> dict:
    return _detectors().run(ctx.cur, ctx.hotel_slug, trigger=payload.get("kind") or "manual",
                            batch_id=payload.get("batch_id"), ota_code=payload.get("ota_code"))


@jobs.scheduled("pg.deadline_watch", jobs.every(hours=1), timeout_s=120)
def deadline_watch(ctx: Any) -> dict:
    return _detectors().run(ctx.cur, ctx.hotel_slug, trigger="deadline")


@jobs.scheduled("pg.nightly_detect", jobs.daily_at("02:00"), timeout_s=300)
def nightly_detect(ctx: Any) -> dict:
    return _detectors().run(ctx.cur, ctx.hotel_slug, trigger="nightly")


@jobs.scheduled("pg.feedback_tune", jobs.weekly_at(1, "03:30"), timeout_s=120)
def feedback_tune(ctx: Any) -> dict:
    from clefs.modules.profit_guard.detectors import tuning

    return tuning.feedback_tune(ctx.cur, ctx.hotel)


install_commit_hook()
