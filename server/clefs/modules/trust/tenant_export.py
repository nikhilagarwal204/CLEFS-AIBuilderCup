"""
Tenant export (03 spec 5.3): the owner asks for everything Clefs holds for
their hotel, and gets a zip through clefs.files with a 7-day link.

What goes in: every table in the schema with a hotel_slug column (found from
information_schema, so a module's new table is included without a change
here), one CSV per table, plus the hotel's own row and a manifest.

What stays out, and why:
- credentials and session material (auth sessions, magic links, pairing
  codes, OAuth state and tokens): an export must not be a login;
- queue plumbing (job queue and runs, rate limits);
- binary columns: contact HMACs mean nothing outside Clefs, sealed values
  are ciphertext, and file bytes would blow the size cap (file metadata is
  included; the files themselves download from the console one by one).

It runs as the task `trust.tenant_export`, because a large hotel's export
takes longer than a request may.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.audit import audit
from clefs.core import clock
from clefs.core.errors import conflict, not_found
from clefs.files import put_file, signed_path
from clefs.settings import get_settings

LINK_DAYS = 7
ROW_CAP = 200_000
EXCLUDED_TABLES = frozenset(
    {
        "auth_sessions", "magic_links", "device_pairing_codes", "device_pin_failures", "oauth_states",
        "oauth_connections", "job_queue", "job_runs", "rate_limits", "worker_heartbeats", "trs_exports",
        "schema_migrations",
    }
)
EXCLUDED_COLUMNS = frozenset({"password_hash", "pin_hash", "token_hash", "code_hash"})

# ─── Build ───


def hotel_tables(cur: Any) -> list[str]:
    cur.execute(
        """
        SELECT c.table_name FROM information_schema.columns c
        JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
        WHERE c.table_schema = current_schema() AND c.column_name = 'hotel_slug' AND t.table_type = 'BASE TABLE'
        ORDER BY c.table_name
        """
    )
    return [r["table_name"] for r in cur.fetchall() if r["table_name"] not in EXCLUDED_TABLES]


def _csv_value(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, default=str)
    return v


def _table_csv(cur: Any, table: str, hotel_slug: str, *, key: str = "hotel_slug") -> tuple[str, int]:
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = current_schema() "
        "AND table_name = %s ORDER BY ordinal_position",
        (table,),
    )
    cols = [r["column_name"] for r in cur.fetchall() if r["data_type"] != "bytea" and r["column_name"] not in EXCLUDED_COLUMNS]
    quoted = ", ".join(f'"{c}"' for c in cols)
    cur.execute(f'SELECT {quoted} FROM "{table}" WHERE "{key}" = %s LIMIT %s', (hotel_slug, ROW_CAP))
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(cols)
    n = 0
    for r in cur.fetchall():
        w.writerow([_csv_value(r[c]) for c in cols])
        n += 1
    return "﻿" + out.getvalue(), n


def build_zip(cur: Any, hotel_slug: str) -> tuple[bytes, dict[str, int]]:
    counts: dict[str, int] = {}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        body, counts["hotels"] = _table_csv(cur, "hotels", hotel_slug, key="slug")
        z.writestr("hotels.csv", body)
        for table in hotel_tables(cur):
            body, n = _table_csv(cur, table, hotel_slug)
            counts[table] = n
            if n:
                z.writestr(f"{table}.csv", body)
        manifest = {
            "hotel_slug": hotel_slug,
            "generated_at": clock.now().isoformat(),
            "tables": counts,
            "row_cap_per_table": ROW_CAP,
            "omitted_tables": sorted(EXCLUDED_TABLES),
            "omitted": "binary columns (hashes, sealed values, file bytes) and credential columns",
        }
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
    return buf.getvalue(), counts


# ─── Requests and the task ───

TASK = "trust.tenant_export"
STALE_AFTER = timedelta(hours=1)


def request_export(cur: Any, ctx: Any) -> dict:
    """Queue an export. One at a time per hotel: a second request while one
    is queued or running returns 409 rather than doubling the work. One
    left queued past STALE_AFTER (its task died after its retries) is marked
    failed first, so it cannot block the owner forever."""
    now = clock.now()
    cur.execute(
        "UPDATE trs_exports SET status = 'failed', error = 'timed_out', finished_at = %s "
        "WHERE hotel_slug = %s AND status IN ('queued', 'running') AND created_at < %s",
        (now, ctx.slug, now - STALE_AFTER),
    )
    cur.execute("SELECT id FROM trs_exports WHERE hotel_slug = %s AND status IN ('queued', 'running') FOR UPDATE",
                (ctx.slug,))
    if cur.fetchone() is not None:
        raise conflict("An export is already being prepared", "export_in_progress")
    cur.execute(
        "INSERT INTO trs_exports (hotel_slug, requested_by, created_at) VALUES (%s, %s, %s) RETURNING *",
        (ctx.slug, ctx.principal.user_id, clock.now()),
    )
    row = dict(cur.fetchone())
    jobs.enqueue(TASK, {"export_id": row["id"]}, hotel_slug=ctx.slug, dedupe_key=f"{TASK}:{row['id']}", cur=cur)
    audit(cur, ctx, "trust.export.request", "tenant_export", row["id"])
    return to_api(row)


def to_api(row: dict) -> dict:
    out = {k: row.get(k) for k in ("id", "status", "tables", "error", "created_at", "finished_at", "expires_at")}
    out["file_id"] = str(row["file_id"]) if row.get("file_id") else None
    out["download_url"] = None
    now = clock.now()
    if row.get("status") == "ready" and row.get("file_id") and row.get("expires_at") and row["expires_at"] > now:
        ttl = int((row["expires_at"] - now).total_seconds())
        out["download_url"] = signed_path(row["hotel_slug"], str(row["file_id"]), ttl_s=max(ttl, 60))
    elif row.get("status") == "ready":
        out["status"] = "expired"
    return out


def list_exports(cur: Any, hotel_slug: str, *, limit: int = 20) -> list[dict]:
    cur.execute("SELECT * FROM trs_exports WHERE hotel_slug = %s ORDER BY created_at DESC, id DESC LIMIT %s",
                (hotel_slug, limit))
    return [to_api(dict(r)) for r in cur.fetchall()]


@jobs.task(TASK, max_attempts=2, backoff_s=60, timeout_s=300)
def _export_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    cur = ctx.cur
    cur.execute("SELECT * FROM trs_exports WHERE id = %s AND hotel_slug = %s FOR UPDATE", (payload["export_id"], ctx.hotel_slug))
    row = cur.fetchone()
    if row is None:
        raise not_found("export not found")
    if row["status"] not in ("queued", "running"):
        return {"skipped": row["status"]}
    now = clock.now()
    blob, counts = build_zip(cur, ctx.hotel_slug)
    if len(blob) > get_settings().FILE_MAX_BYTES:
        cur.execute("UPDATE trs_exports SET status = 'failed', error = %s, tables = %s, finished_at = %s WHERE id = %s",
                    ("export_too_large", json.dumps(counts), now, row["id"]))
        return {"failed": "export_too_large", "bytes": len(blob)}
    ref = put_file(
        cur, ctx.hotel_slug, blob,
        filename=f"clefs-export-{ctx.hotel_slug}-{now:%Y%m%d}.zip", content_type="application/zip",
        purpose="trust.tenant_export", source="generated", uploaded_by=row["requested_by"], retention_days=LINK_DAYS,
    )
    cur.execute(
        "UPDATE trs_exports SET status = 'ready', file_id = %s, tables = %s, finished_at = %s, expires_at = %s WHERE id = %s",
        (ref.id, json.dumps(counts), now, now + timedelta(days=LINK_DAYS), row["id"]),
    )
    audit(cur, None, "trust.export.ready", "tenant_export", row["id"], after={"bytes": len(blob), "tables": len(counts)},
          hotel_slug=ctx.hotel_slug)
    return {"export_id": row["id"], "bytes": len(blob)}

