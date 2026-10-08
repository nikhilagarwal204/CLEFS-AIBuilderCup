"""Commit: hand a reviewed batch's canonical rows to the handler registered
for its kind, inside the caller's transaction (04 spec D4); apply supersede;
redact contact columns from the staged rows (04 spec D1).

A handler failure raises and the whole transaction rolls back: the batch
stays in REVIEW with nothing half-written.
"""

from __future__ import annotations

import json
from typing import Any

from clefs import db
from clefs.audit import audit
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.ingest import batches
from clefs.core.ingest.kinds import commit_handler, post_commit_hooks, schema_for
from clefs.core.ingest.mapping import _contact_header

GATE_BLOCKING = ("mismatch", "missing")


def _user_id(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    return getattr(principal, "user_id", None) if principal is not None else None


# ─── Contact redaction ───


def contact_keys(batch: dict) -> list[str]:
    """Raw keys that hold contact values: the headers mapped to a contact
    field, any header that reads like one (an unmapped "Email" column still
    holds an email), and the contact field names themselves (PDF rows and
    canonical dicts are keyed by field)."""
    schema = schema_for(batch["kind"])
    mp = batch.get("mapping") or {}
    keys = set(schema.contact_fields)
    keys |= {mp[f] for f in schema.contact_fields if f in mp}
    keys |= {h for h in (batch.get("headers") or []) if _contact_header(h)}
    return sorted(keys)


def redact_contacts(cur: Any, batch: dict) -> int:
    """Drop contact values from the batch's import_rows (raw and canonical).
    The sealed copy in reservation_contacts is the only one left (04 spec
    D1). Returns the rows touched."""
    keys = contact_keys(batch)
    if not keys:
        return 0
    cur.execute(
        "UPDATE import_rows SET raw = raw - %s::text[], canonical = canonical - %s::text[] "
        "WHERE batch_id = %s AND (raw ?| %s::text[] OR canonical ?| %s::text[])",
        (keys, keys, batch["id"], keys, keys),
    )
    n = cur.rowcount
    stats = dict(batch.get("stats") or {})
    stats["redacted_columns"] = keys
    stats["contacts_redacted"] = True
    cur.execute("UPDATE import_batches SET stats = %s WHERE id = %s", (db.Json(stats), batch["id"]))
    return n


# ─── Gate override ───


def override_gate(cur: Any, hotel_slug: str | None, batch_id: int, *, actor: Any, reason: str) -> dict:
    """A person accepts a batch whose lines do not add up to the printed
    total (or that has none). Audited; the reason is kept on the batch."""
    reason = (reason or "").strip()
    if len(reason) < 3:
        raise ClefsError(400, "Say why the totals may differ", "reason_required")
    batch = batches.get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] != "REVIEW":
        raise ClefsError(409, "Only an import in review can be overridden", "batch_closed")
    if batch.get("gate_status") not in GATE_BLOCKING:
        raise ClefsError(409, "The totals gate is not blocking this import", "gate_not_blocking")
    out = batches._update(cur, batch_id, gate_override_by=_user_id(actor), gate_override_reason=reason[:1000],
                          gate_override_at=clock.now())
    audit(cur, actor, "ingest.gate_override", "import_batch", batch_id, hotel_slug=hotel_slug,
          meta={"gate_status": batch["gate_status"], "printed_total_minor": batch.get("printed_total_minor"),
                "parsed_total_minor": batch.get("parsed_total_minor"), "reason": reason[:1000]})
    return out


# ─── Commit ───


def commit_batch(cur: Any, hotel_slug: str | None, batch_id: int, *, actor: Any = None,
                 skip_errors: bool = False, options: dict | None = None, dry_run: bool = False) -> dict:
    """Commit a REVIEW batch: gate checked, rows handed to the kind's
    handler, the batch it supersedes retired, contact columns redacted.

    Commit-handler hooks (both optional, for kinds whose writer takes
    choices, like MENU's default outlet):
    - `options`: handed to the handler as batch["options"]["commit"] and,
      on a real commit, kept on the batch row so its history says how it
      was written. Namespaced so they never clobber the sniff options
      (sheet_name, header_row).
    - `dry_run`: every check and the handler run as for a real commit, but
      inside a savepoint that is rolled back: the batch stays in REVIEW,
      nothing is written or audited, and the returned batch carries the
      handler's would-be `commit_result` with `dry_run: True`. The handler
      sees batch["dry_run"] True, so it can skip work outside the database."""
    if dry_run:
        cur.execute("SAVEPOINT clefs_ingest_dry_run")
        try:
            out = _commit(cur, hotel_slug, batch_id, actor=actor, skip_errors=skip_errors, options=options,
                          dry_run=True)
        finally:
            cur.execute("ROLLBACK TO SAVEPOINT clefs_ingest_dry_run")
            cur.execute("RELEASE SAVEPOINT clefs_ingest_dry_run")
        return {**batches.get_batch(cur, hotel_slug, batch_id), "commit_result": out, "dry_run": True}
    return _commit(cur, hotel_slug, batch_id, actor=actor, skip_errors=skip_errors, options=options, dry_run=False)


def _jsonable(result: Any, n_rows: int) -> dict:
    # JSON round trip: a handler may return dates or Decimals.
    return json.loads(json.dumps(result if isinstance(result, dict) else {"rows": n_rows}, default=str))


def _commit(cur: Any, hotel_slug: str | None, batch_id: int, *, actor: Any, skip_errors: bool,
            options: dict | None, dry_run: bool) -> Any:
    """commit_batch's body. A dry run returns the handler's result as soon
    as the handler is done (the caller rolls everything back)."""
    batch = batches.get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] == "COMMITTED":
        raise ClefsError(409, "This import is already committed", "already_committed")
    if batch["status"] != "REVIEW":
        raise ClefsError(409, f"An import that is {batch['status'].lower()} cannot be committed", "not_ready")
    if batch.get("gate_status") in GATE_BLOCKING and batch.get("gate_override_at") is None:
        raise ClefsError(409, "The lines do not add up to the printed total; check them or override the gate",
                         "gate_blocked")
    counts = batches.row_counts(cur, batch_id)
    if counts.get("ERROR") and not skip_errors:
        raise ClefsError(409, f"{counts['ERROR']} rows have errors: fix or skip them first", "rows_have_errors")
    handler = commit_handler(batch["kind"])
    if handler is None:
        raise ClefsError(409, f"Nothing receives {batch['kind']} rows yet", "no_commit_handler")
    rows = batches.committable_rows(cur, batch_id)
    if not rows:
        raise ClefsError(409, "There are no rows to commit", "nothing_to_commit")
    if options is not None:
        batch = {**batch, "options": {**(batch.get("options") or {}), "commit": dict(options)}}
    result = handler(cur, {**batch, "dry_run": dry_run}, rows)
    if dry_run:
        return _jsonable(result, len(rows))
    for hook in post_commit_hooks(batch["kind"]):
        hook(cur, batch, result)
    if options is not None:
        cur.execute("UPDATE import_batches SET options = %s WHERE id = %s", (db.Json(batch["options"]), batch_id))
    prev_id = batch.get("supersedes_batch_id")
    if prev_id:
        cur.execute(
            "UPDATE import_batches SET status = 'SUPERSEDED', superseded_by = %s, updated_at = %s "
            "WHERE id = %s AND COALESCE(hotel_slug, '') = COALESCE(%s, '') AND status <> 'SUPERSEDED' RETURNING *",
            (batch_id, clock.now(), prev_id, hotel_slug),
        )
        prev = cur.fetchone()
        if prev is not None:
            redact_contacts(cur, dict(prev))
    out = batches._update(
        cur, batch_id, status="COMMITTED", committed_by=_user_id(actor), committed_at=clock.now(),
        commit_result=_jsonable(result, len(rows)),
    )
    redact_contacts(cur, out)
    audit(cur, actor, "ingest.commit", "import_batch", batch_id, hotel_slug=hotel_slug,
          meta={"kind": batch["kind"], "rows": len(rows), "skipped_errors": counts.get("ERROR", 0),
                "supersedes": prev_id, "gate_status": batch.get("gate_status"),
                "gate_overridden": batch.get("gate_override_at") is not None})
    return batches.get_batch(cur, hotel_slug, batch_id)


def maybe_auto_commit(cur: Any, batch: dict) -> dict:
    """Commit without a person when the batch asked for it and nothing needs
    one: a saved template mapped it, the gate is not blocking, no row is in
    error and a handler exists. Otherwise the batch waits in REVIEW."""
    if not batch.get("auto_commit") or batch["status"] != "REVIEW" or batch.get("mapping_source") != "template":
        return batch
    if batch.get("gate_status") in GATE_BLOCKING or commit_handler(batch["kind"]) is None:
        return batch
    if batches.row_counts(cur, batch["id"]).get("ERROR"):
        return batch
    cur.execute("SAVEPOINT clefs_ingest_auto")
    try:
        out = commit_batch(cur, batch["hotel_slug"], batch["id"], actor=None)
        cur.execute("RELEASE SAVEPOINT clefs_ingest_auto")
        return out
    except ClefsError as exc:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_ingest_auto")
        cur.execute("RELEASE SAVEPOINT clefs_ingest_auto")
        batches.log.info("auto-commit of batch %s declined: %s", batch["id"], exc.detail)
        return batches.get_batch(cur, batch["hotel_slug"], batch["id"])


# ─── Discard and stale redaction ───


def discard_batch(cur: Any, hotel_slug: str | None, batch_id: int, *, actor: Any = None) -> None:
    """Delete an uncommitted batch and its rows (the stored file stays under
    its own retention). The same bytes can then be uploaded again."""
    batch = batches.get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] in ("COMMITTED", "SUPERSEDED"):
        raise ClefsError(409, "A committed import cannot be discarded", "batch_closed")
    cur.execute("UPDATE import_batches SET supersedes_batch_id = NULL WHERE supersedes_batch_id = %s", (batch_id,))
    cur.execute("DELETE FROM import_batches WHERE id = %s", (batch_id,))
    audit(cur, actor, "ingest.discard", "import_batch", batch_id, hotel_slug=hotel_slug,
          meta={"kind": batch["kind"], "status": batch["status"], "filename": batch.get("filename")})


STALE_DAYS = 30


def redact_stale(cur: Any, *, now: Any = None, limit: int = 200) -> int:
    """Contact columns must not sit in staging forever either: batches that
    were never committed (open for STALE_DAYS, FAILED or SUPERSEDED) lose
    them too. Returns the batches redacted."""
    from datetime import timedelta

    now = now or clock.now()
    cur.execute(
        """
        SELECT * FROM import_batches b
         WHERE NOT COALESCE((b.stats ->> 'contacts_redacted')::boolean, FALSE)
           AND (b.status IN ('FAILED', 'SUPERSEDED') OR (b.status <> 'COMMITTED' AND b.updated_at < %s))
         ORDER BY id LIMIT %s
        """,
        (now - timedelta(days=STALE_DAYS), limit),
    )
    done = 0
    for b in cur.fetchall():
        b = dict(b)
        redact_contacts(cur, b)
        cur.execute(
            "UPDATE import_batches SET stats = stats || '{\"contacts_redacted\": true}'::jsonb WHERE id = %s",
            (b["id"],),
        )
        done += 1
    return done
