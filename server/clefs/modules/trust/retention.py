"""
Retention policies and the nightly purge (03 spec 5.3).

Data classes come from two places:
- built-in classes for tables the spec names and the foundation owns
  (conversation transcripts, request free text, device sessions, the audit
  log, Trust's own deletion log and DSR records), and
- every entry of clefs.core.privacy.pii_registry(): a module declares its
  personal-data table there (register_pii) with a retention and a strategy,
  and it shows up here as data class "<module>.<table>" with that default.
  This is how call audio (voice), guest profiles (guests), GBP raw text
  (visibility/reviews) and eval runs reach the purge without Trust importing
  another module.

A hotel may change keep_days within the class's bounds (audit log at least
365 days, call audio at most 90, ...); the action is the class's own.

`trust.retention_purge` runs at 02:30 hotel time. Each class is purged in
batches of 5,000 rows, each batch its own transaction that also adds its
count to trs_deletion_log, so the log equals what committed and never holds
contents. A failure raises signal `trust.retention_purge_failed` and the run
moves on to the next class.
"""

from __future__ import annotations

import time as _time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from clefs import db, jobs
from clefs.core import clock, privacy, signals
from clefs.core.errors import ClefsError
from clefs.db import Json
from clefs.modules.trust.subject import GUEST_WORDS

BATCH = 5000
BUDGET_S = 240.0
ANONYMIZED = "[anonymized]"
ERASED = "[erased]"
SIGNAL_FAILED = "trust.retention_purge_failed"

signals.register_signal_kind(
    SIGNAL_FAILED,
    label="Retention purge failed",
    default_owner_role="owner",
    section="compliance",
    cadence="daily",
    icon="trash",
)

# ─── Catalogue ───

Action = Literal["delete", "anonymize"]


@dataclass(frozen=True)
class Target:
    table: str
    ts_column: str
    action: Action
    # anonymize: column -> SQL value; the row is skipped once every column holds it
    set_values: dict[str, str] = field(default_factory=dict)
    where: str | None = None
    hotel_column: str | None = "hotel_slug"


@dataclass(frozen=True)
class DataClass:
    key: str
    label: str
    default_keep_days: int
    action: Action
    targets: tuple[Target, ...]
    min_keep_days: int = 1
    max_keep_days: int = 3650
    source: str = "builtin"
    note: str | None = None


def _keep_blank_or_marked(col: str) -> str:
    """SQL value that anonymizes a free-text column but leaves NULL, an
    already-anonymized value and an erasure marker as they are. WHY a CASE and
    not a WHERE: several columns share one row, and _batch_sql skips a row
    only when every column already equals its value. Written as a CASE on the
    column itself, that test is true for NULL / marked cells, so a row with
    one NULL comment is not re-picked every night, and a row erased by a DSR
    keeps its "[erased]" marker. It also references only its own column, so a
    schema without one of the columns still purges the others."""
    return f"CASE WHEN {col} IS NULL OR {col} IN ('{ANONYMIZED}', '{ERASED}') THEN {col} ELSE '{ANONYMIZED}' END"


BUILTIN: tuple[DataClass, ...] = (
    DataClass(
        "transcripts", "Concierge transcripts", 365, "delete",
        (Target("conversation_logs", "timestamp", "delete"),),
    ),
    DataClass(
        "request_free_text", "What guests wrote in service requests", 365, "anonymize",
        (Target("staff_requests", "timestamp", "anonymize",
                {c: _keep_blank_or_marked(c) for c in GUEST_WORDS["staff_requests"]}),),
        note="The request row stays for service statistics; only the guest's words go "
             "(the staff summary, the guest's original wording and their tracker comment).",
    ),
    DataClass(
        "device_session_logs", "Staff device shift sessions", 180, "delete",
        (Target("device_sessions", "started_at", "delete", where="ended_at IS NOT NULL"),),
    ),
    DataClass(
        "audit_log", "Audit log", 730, "delete",
        (Target("audit_log", "at", "delete"),),
        min_keep_days=365,
        note="Removed oldest first and only once verified; the chain keeps an anchor.",
    ),
    DataClass(
        "deletion_log", "Deletion log", 1095, "delete",
        (Target("trs_deletion_log", "at", "delete"),),
        min_keep_days=365,
    ),
    DataClass(
        "dsr_requests", "Closed privacy requests", 1095, "delete",
        (Target("trs_dsr_requests", "completed_at", "delete",
                where="status IN ('completed', 'rejected', 'cancelled')"),),
        min_keep_days=365,
    ),
)

REDACT = "__redact__"  # placeholder in set_values, resolved from the column's type at run time


def _registry_class(spec: privacy.PiiSpec) -> DataClass:
    action: Action = "delete" if spec.strategy == "delete" else "anonymize"
    target = Target(
        spec.table,
        spec.ts_column,
        action,
        {c: REDACT for c in spec.redact_columns} if action == "anonymize" else {},
        where=spec.where,
    )
    # 03 spec 5.3: call audio is kept at most 90 days whatever a hotel sets.
    max_keep = 90 if "audio" in spec.table else 3650
    return DataClass(
        f"{spec.module}.{spec.table}",
        f"{spec.table.replace('_', ' ').capitalize()} ({spec.module})",
        min(spec.retention_days, max_keep),
        action,
        (target,),
        max_keep_days=max_keep,
        source="registry",
    )


def catalogue() -> dict[str, DataClass]:
    """Every data class: built-ins first, then one per PII registry entry."""
    out = {dc.key: dc for dc in BUILTIN}
    for spec in privacy.pii_registry():
        dc = _registry_class(spec)
        out.setdefault(dc.key, dc)
    return out


# ─── Policies ───


def _overrides(cur: Any, hotel_slug: str) -> dict[str, dict]:
    cur.execute("SELECT data_class, keep_days, action, updated_at FROM trs_retention_policies WHERE hotel_slug = %s",
                (hotel_slug,))
    return {r["data_class"]: dict(r) for r in cur.fetchall()}


def effective_keep_days(dc: DataClass, override: dict | None) -> int:
    keep = int(override["keep_days"]) if override else dc.default_keep_days
    return max(dc.min_keep_days, min(dc.max_keep_days, keep))


def list_policies(cur: Any, hotel_slug: str) -> list[dict]:
    """The hotel's policy per data class, with bounds and the last run's counts."""
    over = _overrides(cur, hotel_slug)
    cur.execute(
        """
        SELECT DISTINCT ON (data_class) data_class, run_id, at,
               SUM(row_count) OVER (PARTITION BY data_class, run_id) AS rows
        FROM trs_deletion_log WHERE hotel_slug = %s ORDER BY data_class, at DESC
        """,
        (hotel_slug,),
    )
    last = {r["data_class"]: {"at": r["at"], "rows": int(r["rows"] or 0)} for r in cur.fetchall()}
    return [
        {
            "data_class": dc.key,
            "label": dc.label,
            "keep_days": effective_keep_days(dc, over.get(dc.key)),
            "action": dc.action,
            "default_keep_days": dc.default_keep_days,
            "min_keep_days": dc.min_keep_days,
            "max_keep_days": dc.max_keep_days,
            "source": dc.source,
            "tables": [t.table for t in dc.targets],
            "overridden": dc.key in over,
            "note": dc.note,
            "last_run": last.get(dc.key),
        }
        for dc in catalogue().values()
    ]


def set_policy(cur: Any, hotel_slug: str, data_class: str, keep_days: int | None, *, user_id: int | None) -> dict:
    """Set (or with keep_days None, reset to default) one class's keep_days.
    Out-of-bounds values are refused rather than clamped: an owner who asks
    for 30 days of audit log must learn the floor is 365."""
    dc = catalogue().get(data_class)
    if dc is None:
        raise ClefsError(404, f"unknown data class {data_class!r}", "unknown_data_class")
    before = _overrides(cur, hotel_slug).get(data_class)
    if keep_days is None:
        cur.execute("DELETE FROM trs_retention_policies WHERE hotel_slug = %s AND data_class = %s", (hotel_slug, data_class))
    else:
        if not dc.min_keep_days <= keep_days <= dc.max_keep_days:
            raise ClefsError(
                400, f"{dc.label} must be kept between {dc.min_keep_days} and {dc.max_keep_days} days", "keep_days_out_of_bounds"
            )
        cur.execute(
            """
            INSERT INTO trs_retention_policies (hotel_slug, data_class, keep_days, action, updated_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, data_class) DO UPDATE SET keep_days = EXCLUDED.keep_days,
                action = EXCLUDED.action, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
            """,
            (hotel_slug, data_class, int(keep_days), dc.action, user_id, clock.now()),
        )
    return {"before": before and {"keep_days": before["keep_days"]}, "after": {"keep_days": keep_days}}


# ─── Purge engine ───


def _columns(cur: Any, table: str) -> dict[str, dict] | None:
    """{column: {data_type, nullable}} or None when the table is absent (a
    module that is not deployed, or not migrated yet)."""
    cur.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    return {r["column_name"]: {"type": r["data_type"], "nullable": r["is_nullable"] == "YES"} for r in rows}


def _redacted_value(col: dict) -> str:
    # NULL when the column allows it; otherwise the type's empty value, so a
    # NOT NULL payload column is emptied rather than the UPDATE failing.
    if col["nullable"]:
        return "NULL"
    return {"jsonb": "'{}'::jsonb", "json": "'{}'::json", "bytea": "''::bytea", "ARRAY": "'{}'"}.get(
        col["type"], f"'{ANONYMIZED}'"
    )


def _cutoff_param(col: dict, cutoff: datetime) -> Any:
    # Legacy tables keep epoch milliseconds (Canon 3).
    return int(cutoff.timestamp() * 1000) if col["type"] in ("bigint", "integer") else cutoff


def _log(cur: Any, hotel_slug: str | None, run_id: str, dc: DataClass, t: Target, n: int, criteria: dict) -> None:
    cur.execute(
        """
        INSERT INTO trs_deletion_log (hotel_slug, run_id, data_class, table_name, action, row_count, criteria, at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (run_id, data_class, table_name)
        DO UPDATE SET row_count = trs_deletion_log.row_count + EXCLUDED.row_count, at = EXCLUDED.at
        """,
        (hotel_slug, run_id, dc.key, t.table, t.action, n, Json(criteria), clock.now()),
    )


def _batch_sql(t: Target, cols: dict[str, dict], hotel_slug: str | None) -> str | None:
    """One batch's statement (named params hotel, cutoff), or None when the
    table cannot be purged per hotel (no hotel or timestamp column)."""
    if t.hotel_column and t.hotel_column not in cols:
        return None
    if t.ts_column not in cols:
        return None
    preds = []
    if t.hotel_column:
        preds.append(f"{t.hotel_column} = %(hotel)s" if hotel_slug is not None else f"{t.hotel_column} IS NULL")
    preds.append(f"{t.ts_column} < %(cutoff)s")
    if t.where:
        preds.append(f"({t.where})")
    if t.action == "anonymize":
        sets = {c: (_redacted_value(cols[c]) if v == REDACT else v) for c, v in t.set_values.items() if c in cols}
        if not sets:
            return None
        preds.append("NOT (" + " AND ".join(f"{c} IS NOT DISTINCT FROM {v}" for c, v in sets.items()) + ")")
        inner = f"SELECT ctid FROM {t.table} WHERE {' AND '.join(preds)} LIMIT {BATCH}"
        assignments = ", ".join(f"{c} = {v}" for c, v in sets.items())
        return f"UPDATE {t.table} SET {assignments} WHERE ctid IN ({inner})"
    inner = f"SELECT ctid FROM {t.table} WHERE {' AND '.join(preds)} LIMIT {BATCH}"
    return f"DELETE FROM {t.table} WHERE ctid IN ({inner})"


def purge_target(hotel_slug: str | None, dc: DataClass, t: Target, keep_days: int, run_id: str, *,
                 now: datetime, deadline: float) -> int:
    """Purge one table for one class in BATCH-row transactions. Returns rows."""
    cutoff = now - timedelta(days=keep_days)
    total = 0
    while True:
        with db.db() as cur:
            cols = _columns(cur, t.table)
            built = _batch_sql(t, cols, hotel_slug) if cols else None
            if built is None:
                return total
            cur.execute(built, {"hotel": hotel_slug, "cutoff": _cutoff_param(cols[t.ts_column], cutoff)})
            n = cur.rowcount or 0
            if n:
                _log(cur, hotel_slug, run_id, dc, t, n,
                     {"keep_days": keep_days, "older_than": cutoff.isoformat(), "action": t.action})
        total += n
        if n < BATCH or _time.monotonic() > deadline:
            return total


def purge_audit_log(hotel_slug: str, dc: DataClass, keep_days: int, run_id: str, *, now: datetime, deadline: float) -> int:
    """Remove the oldest audit rows, but only a verified prefix of the chain:
    the chain is verified first (a broken chain is evidence and is never
    purged), each batch deletes ids up to the last verified row older than
    the cutoff, and the anchor moves so later verification still links."""
    from clefs.modules.trust import chain

    cutoff = now - timedelta(days=keep_days)
    t = dc.targets[0]
    with db.db() as cur:
        if chain.verify_chain(cur, hotel_slug)["status"] != "ok":
            raise ClefsError(409, "the audit chain is broken; audit rows are held until it is investigated", "audit_chain_broken")
    total = 0
    while _time.monotonic() <= deadline:
        with db.db() as cur:
            cur.execute("SELECT last_id FROM trs_audit_chain_state WHERE hotel_slug = %s", (hotel_slug,))
            st = cur.fetchone()
            if st is None or st["last_id"] is None:
                return total
            cur.execute(
                "SELECT id, row_hash, at FROM audit_log WHERE hotel_slug = %s AND id <= %s ORDER BY id LIMIT %s",
                (hotel_slug, st["last_id"], BATCH),
            )
            prefix = []
            for r in cur.fetchall():
                if r["at"] >= cutoff:
                    break
                prefix.append(r)
            if not prefix:
                return total
            last = prefix[-1]
            cur.execute("SET LOCAL clefs.audit_maintenance = 'on'")
            cur.execute("DELETE FROM audit_log WHERE hotel_slug = %s AND id <= %s", (hotel_slug, last["id"]))
            n = cur.rowcount or 0
            cur.execute("SET LOCAL clefs.audit_maintenance = 'off'")
            chain.set_anchor(cur, hotel_slug, anchor_id=last["id"], anchor_hash=bytes(last["row_hash"]))
            _log(cur, hotel_slug, run_id, dc, t, n, {"keep_days": keep_days, "older_than": cutoff.isoformat(), "action": "delete"})
        total += n
        if len(prefix) < BATCH:
            return total
    return total


def _raise_failed(hotel_slug: str, failures: list[dict], run_id: str) -> None:
    with db.db() as cur:
        signals.upsert_signal(
            cur,
            hotel_slug,
            signals.SignalIn(
                module="trust",
                kind=SIGNAL_FAILED,
                dedupe_key=f"{SIGNAL_FAILED}:{hotel_slug}",
                title="Personal data was not purged on schedule",
                summary="The nightly retention purge failed for: "
                + ", ".join(f"{f['data_class']} ({f['error']})" for f in failures)
                + ". Data past its retention period is still stored; it will be retried tonight.",
                severity=3,
                confidence=1.0,
                owner_role="owner",
                evidence=[signals.Evidence(kind="row", label="Purge run", value=run_id)],
            ),
        )


def run_purge(hotel_slug: str, *, now: datetime | None = None, budget_s: float = BUDGET_S,
              only: list[str] | None = None) -> dict:
    """Purge every data class for one hotel under its policies. Opens its own
    transactions (one per batch). Returns {run_id, counts, failures}."""
    now = now or clock.now()
    run_id = f"{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    deadline = _time.monotonic() + budget_s
    with db.db(readonly=True) as cur:
        over = _overrides(cur, hotel_slug)
    counts: dict[str, int] = {}
    failures: list[dict] = []
    for dc in catalogue().values():
        if only and dc.key not in only:
            continue
        keep = effective_keep_days(dc, over.get(dc.key))
        try:
            if dc.key == "audit_log":
                n = purge_audit_log(hotel_slug, dc, keep, run_id, now=now, deadline=deadline)
            else:
                n = sum(purge_target(hotel_slug, dc, t, keep, run_id, now=now, deadline=deadline) for t in dc.targets)
            counts[dc.key] = n
        except Exception as exc:  # one class failing must not stop the others
            code = getattr(exc, "code", None) or type(exc).__name__
            failures.append({"data_class": dc.key, "error": str(code)})
    if failures:
        _raise_failed(hotel_slug, failures, run_id)
    else:
        with db.db() as cur:
            signals.resolve_signal(cur, hotel_slug, f"{SIGNAL_FAILED}:{hotel_slug}", resolution="purge succeeded")
    return {"run_id": run_id, "counts": counts, "failures": failures}


def run_platform_purge(*, now: datetime | None = None, budget_s: float = BUDGET_S) -> dict:
    """Rows with no hotel (unrouted webhooks, platform AI calls) under each
    registry class's default retention."""
    now = now or clock.now()
    run_id = f"platform-{now.strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"
    deadline = _time.monotonic() + budget_s
    counts = {}
    for dc in catalogue().values():
        if dc.source != "registry":
            continue
        counts[dc.key] = sum(
            purge_target(None, dc, t, dc.default_keep_days, run_id, now=now, deadline=deadline) for t in dc.targets
        )
    return {"run_id": run_id, "counts": counts}


# ─── Jobs ───


@jobs.scheduled("trust.retention_purge", jobs.daily_at("02:30"), timeout_s=300)
def _purge_job(ctx: jobs.JobCtx) -> dict:
    return run_purge(ctx.hotel_slug, now=ctx.now)


@jobs.scheduled("trust.retention_purge_platform", jobs.daily_at("02:45", tz="utc"), per_hotel=False, timeout_s=300)
def _platform_purge_job(ctx: jobs.JobCtx) -> dict:
    return run_platform_purge(now=ctx.now)
