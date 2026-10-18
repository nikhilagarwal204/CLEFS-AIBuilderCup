"""
Audit hash-chain verification (03 spec 5.1): `trust.audit_chain_verify` runs
at 03:30 hotel time and walks each hotel's audit_log chain from its last
verified row. A row is good when

  1. row_hash = audit_row_hash(prev_hash, row)   (the row was not edited), and
  2. prev_hash = row_hash of the previous surviving row  (none was removed or
     inserted), where the previous row of the first survivor after a
     retention purge is the purge's anchor (trs_audit_chain_state).

The hash itself is recomputed by the SQL function from 0005, never by Python
(CONTRACTS 8: the verifier must call it rather than re-implement it), so the
two can never disagree about canonical text or timestamp precision.

A break raises signal `trust.audit_chain_break` (compliance, never in the
daily top 5) and the checkpoint stays on the last good row, so every later run
re-reports until someone investigates; the dedupe key names the broken row, so
one break is one open signal.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock, signals

BATCH = 5000
FULL_EVERY_DAYS = 7
SIGNAL_KIND = "trust.audit_chain_break"

signals.register_signal_kind(
    SIGNAL_KIND,
    label="Audit log tampering detected",
    default_owner_role="owner",
    section="compliance",
    cadence="daily",
    icon="shield",
)

# ─── State ───


def get_state(cur: Any, hotel_slug: str) -> dict:
    cur.execute("SELECT * FROM trs_audit_chain_state WHERE hotel_slug = %s", (hotel_slug,))
    r = cur.fetchone()
    if r is None:
        return {"hotel_slug": hotel_slug, "status": "unverified", "last_id": None, "rows_checked": 0,
                "verified_at": None, "broken_at_id": None, "broken_reason": None, "anchor_id": None}
    out = dict(r)
    for k in ("last_hash", "anchor_hash"):
        out[k] = bytes(out[k]).hex() if out.get(k) is not None else None
    return out


def _save(cur: Any, hotel_slug: str, **fields: Any) -> None:
    cols = ["hotel_slug", *fields, "updated_at"]
    vals = [hotel_slug, *fields.values(), clock.now()]
    sets = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[1:])
    cur.execute(
        f"INSERT INTO trs_audit_chain_state ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))}) "
        f"ON CONFLICT (hotel_slug) DO UPDATE SET {sets}",
        vals,
    )


def set_anchor(cur: Any, hotel_slug: str, *, anchor_id: int, anchor_hash: bytes) -> None:
    """Called by the retention purge after it deleted every row up to
    anchor_id: the first surviving row's prev_hash must equal anchor_hash.
    A checkpoint older than the anchor jumps to it (those rows are gone)."""
    cur.execute(
        """
        INSERT INTO trs_audit_chain_state (hotel_slug, anchor_id, anchor_hash, last_id, last_hash, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug) DO UPDATE SET
            anchor_id = EXCLUDED.anchor_id, anchor_hash = EXCLUDED.anchor_hash,
            last_id = CASE WHEN trs_audit_chain_state.last_id IS NULL OR trs_audit_chain_state.last_id < EXCLUDED.anchor_id
                           THEN EXCLUDED.anchor_id ELSE trs_audit_chain_state.last_id END,
            last_hash = CASE WHEN trs_audit_chain_state.last_id IS NULL OR trs_audit_chain_state.last_id < EXCLUDED.anchor_id
                             THEN EXCLUDED.anchor_hash ELSE trs_audit_chain_state.last_hash END,
            updated_at = EXCLUDED.updated_at
        """,
        (hotel_slug, anchor_id, anchor_hash, anchor_id, anchor_hash, clock.now()),
    )


# ─── Verify ───


def _raise_break(cur: Any, hotel_slug: str, row_id: int, reason: str) -> None:
    signals.upsert_signal(
        cur,
        hotel_slug,
        signals.SignalIn(
            module="trust",
            kind=SIGNAL_KIND,
            dedupe_key=f"{SIGNAL_KIND}:{hotel_slug}:{row_id}",
            title="The audit log failed its integrity check",
            summary=(
                f"Audit entry {row_id} does not match the tamper-evident chain ({reason}). "
                "An entry was edited, removed or inserted outside the application. "
                "Keep the database untouched and contact Clefs support."
            ),
            severity=4,
            confidence=1.0,
            owner_role="owner",
            subject_type="audit_log",
            subject_id=str(row_id),
            evidence=[signals.Evidence(kind="row", label="First bad audit entry", value=row_id,
                                       ref={"table": "audit_log", "id": row_id})],
        ),
    )


def verify_chain(cur: Any, hotel_slug: str, *, max_rows: int | None = None, full: bool = False) -> dict:
    """Verify the hotel's chain from its checkpoint (or from the anchor or
    the start with full=True). Returns {status, rows_checked, last_id,
    broken_at_id, reason}. Advances the checkpoint only over good rows."""
    state = get_state(cur, hotel_slug)
    cur.execute("SELECT last_id, last_hash, anchor_id, anchor_hash FROM trs_audit_chain_state WHERE hotel_slug = %s",
                (hotel_slug,))
    raw = cur.fetchone() or {}
    anchor_id, anchor_hash = raw.get("anchor_id"), raw.get("anchor_hash")
    if full or raw.get("last_id") is None:
        last_id, last_hash = anchor_id, anchor_hash
    else:
        last_id, last_hash = raw["last_id"], raw["last_hash"]
    last_hash = bytes(last_hash) if last_hash is not None else None
    checked = 0
    broken: tuple[int, str] | None = None
    while broken is None and (max_rows is None or checked < max_rows):
        n = BATCH if max_rows is None else min(BATCH, max_rows - checked)
        cur.execute(
            """
            SELECT a.id, a.prev_hash, a.row_hash, audit_row_hash(a.prev_hash, a) AS expect
            FROM audit_log a WHERE a.hotel_slug = %s AND a.id > %s ORDER BY a.id LIMIT %s
            """,
            (hotel_slug, last_id if last_id is not None else 0, n),
        )
        rows = cur.fetchall()
        for r in rows:
            prev = bytes(r["prev_hash"]) if r["prev_hash"] is not None else None
            if bytes(r["expect"]) != bytes(r["row_hash"]):
                broken = (r["id"], "row content does not match its hash")
                break
            if prev != last_hash:
                broken = (r["id"], "previous-row link does not match")
                break
            last_id, last_hash = r["id"], bytes(r["row_hash"])
            checked += 1
        if len(rows) < n:
            break
    now = clock.now()
    if broken is not None:
        _save(cur, hotel_slug, last_id=last_id, last_hash=last_hash, status="broken", broken_at_id=broken[0],
              broken_reason=broken[1], rows_checked=int(state.get("rows_checked") or 0) + checked, verified_at=now)
        _raise_break(cur, hotel_slug, broken[0], broken[1])
        return {"status": "broken", "rows_checked": checked, "last_id": last_id, "broken_at_id": broken[0],
                "reason": broken[1]}
    extra = {"last_full_at": now} if full else {}
    _save(cur, hotel_slug, last_id=last_id, last_hash=last_hash, status="ok", broken_at_id=None, broken_reason=None,
          rows_checked=int(state.get("rows_checked") or 0) + checked, verified_at=now, **extra)
    return {"status": "ok", "rows_checked": checked, "last_id": last_id, "broken_at_id": None, "reason": None}


# ─── Job ───


@jobs.scheduled("trust.audit_chain_verify", jobs.daily_at("03:30"), timeout_s=300)
def _verify_job(ctx: jobs.JobCtx) -> dict:
    ctx.cur.execute("SELECT last_full_at FROM trs_audit_chain_state WHERE hotel_slug = %s", (ctx.hotel_slug,))
    r = ctx.cur.fetchone()
    full = r is None or r["last_full_at"] is None or r["last_full_at"] < ctx.now - timedelta(days=FULL_EVERY_DAYS)
    return verify_chain(ctx.cur, ctx.hotel_slug, full=full)
