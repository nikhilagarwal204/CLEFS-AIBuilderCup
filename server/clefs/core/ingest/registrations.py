"""What core/ingest declares on import (jobs.OPTIONAL_REGISTRANTS): its task,
its daily sweep and the core/stays commit handlers.

- ingest.process  (task)  run a batch from its file; the email path queues it
  so the webhook answers Resend at once.
- ingest.sweep    (03:45 UTC daily, platform-wide) contact columns out of
  batches never committed; staged rows past retention; sandbox batches of
  the public audit after 30 days (02 spec 3.11).

import_rows is not in the privacy registry: Trust's retention targets need a
hotel_slug column on the table itself, and import_rows reaches the hotel
through its batch. The sweep below owns its retention instead
(FOUNDATION-REQUEST: let a privacy registration name a join to the tenant
key, so Trust's DSAR export can list staged rows too).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core.ingest import batches, commit, handlers  # noqa: F401 (handlers register on import)

PROCESS_TASK = "ingest.process"
ROW_RETENTION_DAYS = 1095  # as reservations: staged rows are the evidence behind committed figures
SANDBOX_RETENTION_DAYS = 30


@jobs.task(PROCESS_TASK, max_attempts=3, backoff_s=60, timeout_s=120)
def process_task(ctx: jobs.JobCtx, payload: dict) -> dict[str, Any]:
    batch_id = int(payload["batch_id"])
    try:
        batch = batches.get_batch(ctx.cur, ctx.hotel_slug, batch_id)
    except Exception:  # discarded before the task ran
        return {"batch_id": batch_id, "status": "gone"}
    if batch["status"] not in ("UPLOADED", "FAILED"):
        return {"batch_id": batch_id, "status": batch["status"]}
    out = batches.process_batch(ctx.cur, batch_id, hotel_slug=ctx.hotel_slug, use_ai=True)
    out = batches._link_supersede(ctx.cur, out)
    out = commit.maybe_auto_commit(ctx.cur, out)
    return {"batch_id": batch_id, "status": out["status"]}


@jobs.scheduled("ingest.sweep", jobs.daily_at("03:45", tz="utc"), per_hotel=False, timeout_s=60)
def sweep(ctx: jobs.JobCtx) -> dict[str, Any]:
    cur = ctx.cur
    redacted = commit.redact_stale(cur, now=ctx.now)
    cur.execute(
        """
        DELETE FROM import_rows r USING import_batches b
         WHERE r.batch_id = b.id AND b.status IN ('COMMITTED', 'SUPERSEDED', 'FAILED') AND b.updated_at < %s
        """,
        (ctx.now - timedelta(days=ROW_RETENTION_DAYS),),
    )
    rows = cur.rowcount
    cur.execute(
        "UPDATE import_batches SET supersedes_batch_id = NULL WHERE supersedes_batch_id IN "
        "(SELECT id FROM import_batches WHERE hotel_slug IS NULL AND created_at < %s)",
        (ctx.now - timedelta(days=SANDBOX_RETENTION_DAYS),),
    )
    cur.execute(
        "UPDATE import_batches SET superseded_by = NULL WHERE superseded_by IN "
        "(SELECT id FROM import_batches WHERE hotel_slug IS NULL AND created_at < %s)",
        (ctx.now - timedelta(days=SANDBOX_RETENTION_DAYS),),
    )
    cur.execute("DELETE FROM import_batches WHERE hotel_slug IS NULL AND created_at < %s",
                (ctx.now - timedelta(days=SANDBOX_RETENTION_DAYS),))
    return {"redacted_batches": redacted, "rows_deleted": rows, "sandbox_batches_deleted": cur.rowcount}
