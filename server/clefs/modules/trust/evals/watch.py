"""
Change watcher (03 spec 5.4 triggers): a KB approval (10-minute debounce)
or a prompt_version change queues an eval run.

Trust may not hook Concierge internals, so every 5 minutes this job reads
the eval session's prompt_version and a fingerprint of the approved KB
through concierge.api. A change starts the debounce clock; each further
change restarts it (an owner approving ten entries in a row gets one run,
ten minutes after the last approval).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import module_enabled
from clefs.modules.trust.evals import runs
from clefs.modules.trust.evals.sp import savepoint

DEBOUNCE = timedelta(minutes=10)


def observe(cur: Any, hotel_slug: str) -> dict:
    """One watcher step for a hotel. Returns what it saw and did."""
    pv = runs.current_prompt_version(cur, hotel_slug)
    fp = savepoint(cur, "trs_watch_fp", lambda: runs.kb_fingerprint(cur, hotel_slug))
    now = clock.now()
    cur.execute("SELECT * FROM trs_eval_watch WHERE hotel_slug = %s FOR UPDATE", (hotel_slug,))
    row = cur.fetchone()
    if row is None:
        # First sight: nothing to compare with. The nightly run covers it.
        cur.execute(
            "INSERT INTO trs_eval_watch (hotel_slug, prompt_version, kb_fingerprint, changed_at) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (hotel_slug) DO NOTHING",
            (hotel_slug, pv, fp, now),
        )
        return {"state": "initialised"}
    row = dict(row)
    pending = row["pending_trigger"]
    changed_at = row["changed_at"]
    if fp != row["kb_fingerprint"] or pv != row["prompt_version"]:
        # A KB change also moves the prompt_version (the KB is inside the
        # prompt), so the KB is named first.
        pending = "kb_change" if fp != row["kb_fingerprint"] else "prompt_change"
        changed_at = now
        cur.execute(
            "UPDATE trs_eval_watch SET prompt_version = %s, kb_fingerprint = %s, changed_at = %s, pending_trigger = %s, "
            "updated_at = %s WHERE hotel_slug = %s",
            (pv, fp, now, pending, now, hotel_slug),
        )
        return {"state": "changed", "trigger": pending}
    if not pending:
        return {"state": "settled"}
    if changed_at is not None and now - changed_at < DEBOUNCE:
        return {"state": "debouncing", "trigger": pending}
    try:
        run = runs.create_run(cur, hotel_slug, trigger=pending)
    except ClefsError as exc:
        # A run in progress: keep the trigger and try again next tick, so the
        # change still gets its own run after the current one finishes.
        return {"state": "waiting", "reason": exc.code}
    cur.execute("UPDATE trs_eval_watch SET pending_trigger = NULL, last_run_id = %s, updated_at = %s WHERE hotel_slug = %s",
                (run["id"], now, hotel_slug))
    return {"state": "queued", "run_id": run["id"], "trigger": pending}


@jobs.scheduled("trust.eval_watch", jobs.every(minutes=5), timeout_s=60)
def _watch_job(ctx: jobs.JobCtx) -> dict:
    if not module_enabled(ctx.cur, ctx.hotel_slug, "concierge"):
        return {"skipped": "concierge_off"}
    return observe(ctx.cur, ctx.hotel_slug)
