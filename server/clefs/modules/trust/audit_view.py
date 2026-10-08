"""
Audit viewer (03 spec 5.1): filtered, paged reads of the foundation
audit_log for one hotel, a field-level diff of before/after, and CSV export.
Read-only by construction: audit_log is append-only (0005 trigger) and this
file never writes to it except through clefs.audit (the export is audited).

Paging is keyset on id (newest first) rather than OFFSET: the log only grows,
so an offset page would shift under a reader while new rows land.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from clefs.core import time as ctime
from clefs.core.errors import ClefsError, not_found

LIST_COLUMNS = (
    "id, hotel_slug, at, actor_type, actor_id, actor_label, action, entity_type, entity_id, ip, request_id, meta, "
    "(before IS NOT NULL OR after IS NOT NULL) AS has_diff"
)
CSV_MAX_ROWS = 50_000
CSV_FIELDS = (
    "id", "at", "actor_type", "actor_id", "actor_label", "action", "entity_type", "entity_id",
    "ip", "request_id", "changed_fields", "before", "after",
)

# ─── Filters ───


@dataclass
class AuditFilters:
    actor_type: str | None = None
    actor_id: str | None = None
    action: str | None = None  # exact, or a prefix when it ends with "." or "*"
    entity_type: str | None = None
    entity_id: str | None = None
    since: str | None = None  # ISO date (hotel-local day) or ISO datetime with offset
    until: str | None = None  # inclusive when a date, exclusive when a datetime
    q: str | None = None  # free text over action, entity id and actor label


def _instant(hotel: Any, raw: str, *, end: bool) -> datetime:
    raw = raw.strip()
    try:
        if len(raw) == 10:
            start, stop = ctime.local_day_bounds(hotel, date.fromisoformat(raw))
            return stop if end else start
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClefsError(400, "dates must be YYYY-MM-DD or an ISO datetime with an offset", "invalid_date") from exc
    if dt.tzinfo is None:
        raise ClefsError(400, "a datetime filter needs a timezone offset", "invalid_date")
    return dt


def _like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def where_clause(hotel: Any, f: AuditFilters) -> tuple[str, list[Any]]:
    """SQL predicate and params for one hotel's rows under the filters."""
    sql = ["hotel_slug = %s"]
    params: list[Any] = [hotel.slug]
    for col, val in (("actor_type", f.actor_type), ("actor_id", f.actor_id),
                     ("entity_type", f.entity_type), ("entity_id", f.entity_id)):
        if val:
            sql.append(f"{col} = %s")
            params.append(val.strip())
    if f.action:
        a = f.action.strip()
        if a.endswith("*") or a.endswith("."):
            sql.append("action LIKE %s")
            params.append(_like(a.rstrip("*")) + "%")
        else:
            sql.append("action = %s")
            params.append(a)
    if f.since:
        sql.append("at >= %s")
        params.append(_instant(hotel, f.since, end=False))
    if f.until:
        sql.append("at < %s")
        params.append(_instant(hotel, f.until, end=True))
    if f.q and f.q.strip():
        needle = "%" + _like(f.q.strip()[:200]) + "%"
        sql.append("(action ILIKE %s OR entity_id ILIKE %s OR actor_label ILIKE %s OR entity_type ILIKE %s)")
        params.extend([needle] * 4)
    return " AND ".join(sql), params


# ─── Diff ───

_MISSING = object()


def diff(before: Any, after: Any, path: str = "") -> list[dict]:
    """Field-level changes between two JSON values, flattened to dotted paths:
    [{path, op: added|removed|changed, before, after}]. Lists are compared
    whole (an audit before/after list is short, and index-level diffs of
    reordered lists read as noise). A missing side next to a dict counts as
    an empty dict, so a create lists each field it set."""
    if before is None and isinstance(after, dict):
        before = {}
    if after is None and isinstance(before, dict):
        after = {}
    if isinstance(before, dict) and isinstance(after, dict):
        out: list[dict] = []
        for key in sorted(set(before) | set(after), key=str):
            sub = f"{path}.{key}" if path else str(key)
            b, a = before.get(key, _MISSING), after.get(key, _MISSING)
            if b is _MISSING:
                out.append({"path": sub, "op": "added", "before": None, "after": a})
            elif a is _MISSING:
                out.append({"path": sub, "op": "removed", "before": b, "after": None})
            else:
                out.extend(diff(b, a, sub))
        return out
    if before == after:
        return []
    if before is None or before is _MISSING:
        return [{"path": path or "$", "op": "added", "before": None, "after": after}]
    if after is None or after is _MISSING:
        return [{"path": path or "$", "op": "removed", "before": before, "after": None}]
    return [{"path": path or "$", "op": "changed", "before": before, "after": after}]


# ─── Reads ───


def _row(r: dict) -> dict:
    out = dict(r)
    for k in ("prev_hash", "row_hash"):
        if k in out:
            out[k] = bytes(out[k]).hex() if out[k] is not None else None
    return out


def list_entries(cur: Any, hotel: Any, f: AuditFilters, *, limit: int = 50, before_id: int | None = None) -> dict:
    """{items, next_before_id}: newest first, keyset-paged on id."""
    limit = max(1, min(int(limit), 200))
    where, params = where_clause(hotel, f)
    if before_id is not None:
        where += " AND id < %s"
        params.append(int(before_id))
    cur.execute(f"SELECT {LIST_COLUMNS} FROM audit_log WHERE {where} ORDER BY id DESC LIMIT %s", (*params, limit + 1))
    rows = [_row(r) for r in cur.fetchall()]
    more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "next_before_id": rows[-1]["id"] if more and rows else None}


def get_entry(cur: Any, hotel: Any, entry_id: int) -> dict:
    """One row with before/after, its diff and its chain hashes (hex)."""
    cur.execute(
        "SELECT *, audit_row_hash(prev_hash, a) = row_hash AS hash_ok FROM audit_log a WHERE hotel_slug = %s AND id = %s",
        (hotel.slug, int(entry_id)),
    )
    r = cur.fetchone()
    if r is None:
        raise not_found("Audit entry not found", "audit_entry_not_found")
    out = _row(dict(r))
    out["diff"] = diff(out.get("before"), out.get("after"))
    return out


def facets(cur: Any, hotel: Any) -> dict:
    """Distinct values for the filter menus (bounded: the log may be large,
    so each list comes from the most recent 20k rows)."""
    cur.execute(
        """
        WITH recent AS (SELECT action, entity_type, actor_type FROM audit_log WHERE hotel_slug = %s
                        ORDER BY id DESC LIMIT 20000)
        SELECT ARRAY(SELECT DISTINCT action FROM recent ORDER BY 1) AS actions,
               ARRAY(SELECT DISTINCT entity_type FROM recent WHERE entity_type IS NOT NULL ORDER BY 1) AS entity_types,
               ARRAY(SELECT DISTINCT actor_type FROM recent ORDER BY 1) AS actor_types
        """,
        (hotel.slug,),
    )
    r = cur.fetchone()
    return {k: list(r[k] or []) for k in ("actions", "entity_types", "actor_types")}


# ─── CSV ───

# A spreadsheet runs a cell that starts with one of these as a formula
# (CSV injection); the audit log holds free text written by guests and staff.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)
    elif isinstance(v, datetime):
        s = v.isoformat()
    else:
        s = str(v)
    return "'" + s if s.startswith(_FORMULA_START) else s


def iter_csv(cur: Any, hotel: Any, f: AuditFilters, *, mask_row: Any = None, max_rows: int = CSV_MAX_ROWS) -> Iterator[str]:
    """CSV lines (header first), newest first, at most max_rows. `mask_row`
    (row -> row) applies the viewer's PII policy before a row is written."""
    where, params = where_clause(hotel, f)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_FIELDS)
    yield buf.getvalue()
    last_id: int | None = None
    sent = 0
    while sent < max_rows:
        batch_where, batch_params = where, list(params)
        if last_id is not None:
            batch_where += " AND id < %s"
            batch_params.append(last_id)
        n = min(1000, max_rows - sent)
        cur.execute(
            f"SELECT id, at, actor_type, actor_id, actor_label, action, entity_type, entity_id, ip, request_id, "
            f"before, after FROM audit_log WHERE {batch_where} ORDER BY id DESC LIMIT %s",
            (*batch_params, n),
        )
        rows = [dict(r) for r in cur.fetchall()]
        if not rows:
            return
        for r in rows:
            if mask_row is not None:
                r = mask_row(r)
            changed = ",".join(d["path"] for d in diff(r.get("before"), r.get("after")))
            buf.seek(0)
            buf.truncate()
            w.writerow([_cell(r.get(k)) if k != "changed_fields" else _cell(changed) for k in CSV_FIELDS])
            yield buf.getvalue()
        sent += len(rows)
        last_id = rows[-1]["id"]
        if len(rows) < n:
            return
