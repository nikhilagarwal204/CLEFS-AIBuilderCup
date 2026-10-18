"""
Menu import (03 spec 2.5 console: "CSV/PDF import") on core/ingest's MENU kind.

Why core/ingest: a menu file then gets what every other import gets, namely
one import_batches row per file (history, the same bytes never imported
twice), header mapping through the alias map ingest/aliases/menu.yaml, a
saved template once a person confirms a mapping, the AI column-mapping
proposal when required columns are not recognised, staged rows a person can
review in the Imports console, and a commit through the handler registered
here for MENU (commit_menu). The console route POST /upsell/import keeps its
one-call behaviour on top of that pipeline (import_csv below).

Canonical MENU fields (all text; parsed here): outlet, outlet_name, sku,
category, category_ar, name_en, name_ar, desc_en, desc_ar, price,
available_from, available_to, days, dietary, alcohol, prep_min, active,
sort. name_en and price are required (a row without them is ERROR at
staging); outlet is required unless the commit names a default outlet.

Rows upsert on (outlet, sku) when a sku is given, else (outlet, name_en
case-insensitive). Commit options (batch.options.commit): default_outlet,
create_outlets (default true), strict (all or nothing: any refused row
aborts the commit and the batch stays in REVIEW).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from clefs import files
from clefs.core import ingest
from clefs.core.errors import ClefsError, bad_request
from clefs.core.hotels import get_hotel
from clefs.modules.upsell import menu

KIND = "MENU"
SOURCE = "menu"
FILE_PURPOSE = "upsell.menu_import"
MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 2000

DAY_NAMES = {"mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6, "sun": 7}
_TRUE = {"1", "y", "yes", "true", "t"}
_FALSE = {"0", "n", "no", "false", "f", ""}


# ─── Parsing ───


def parse_bool(v: str, field: str) -> bool:
    s = (v or "").strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise ValueError(f"{field}: use yes or no")


def parse_days(v: str) -> int:
    """'' / daily / all -> 127; '31' -> 31; 'Mon-Fri'; 'Sat,Sun'; 'Fri-Sun'."""
    s = (v or "").strip().lower()
    if s in ("", "daily", "all", "every day", "everyday"):
        return menu.ALL_DAYS
    if s.isdigit():
        n = int(s)
        if 0 <= n <= menu.ALL_DAYS:
            return n
        raise ValueError("days: a mask 0-127")
    mask = 0
    for part in re.split(r"[,;/ ]+", s):
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            da, db = DAY_NAMES.get(a[:3]), DAY_NAMES.get(b[:3])
            if not da or not db:
                raise ValueError(f"days: unknown day in {part!r}")
            d = da
            while True:
                mask |= 1 << (d - 1)
                if d == db:
                    break
                d = d % 7 + 1
        else:
            d = DAY_NAMES.get(part[:3])
            if not d:
                raise ValueError(f"days: unknown day {part!r}")
            mask |= 1 << (d - 1)
    return mask


# ─── Rows ───


def _row_fields(row: dict) -> dict:
    """One canonical MENU row -> ItemFields kwargs (raises ValueError with a
    message). Cells are text after staging; str() keeps a hand-edited
    staged value (PATCH .../rows) safe too."""
    row = {k: (str(v) if v is not None and not k.startswith("_") else v) for k, v in row.items()}
    f: dict[str, Any] = {}
    for k in ("sku", "category", "category_ar", "name_en", "name_ar", "desc_en", "desc_ar", "price",
              "available_from", "available_to"):
        v = (row.get(k) or "").strip()
        if v:
            f[k] = v
    if not f.get("name_en"):
        raise ValueError("name_en is required")
    if not f.get("price"):
        raise ValueError("price is required")
    f["price"] = re.sub(r"(?i)^(aed|usd|eur|gbp|sar)\s*", "", f["price"]).replace(",", "")
    if "days" in row:
        f["days_mask"] = parse_days(row["days"])
    if (row.get("dietary") or "").strip():
        f["dietary"] = [t for t in re.split(r"[;,|/]+", row["dietary"]) if t.strip()]
    for b in ("alcohol", "active"):
        if b in row and (row[b] or "").strip():
            f[b] = parse_bool(row[b], b)
    for n in ("prep_min", "sort"):
        if (row.get(n) or "").strip():
            try:
                f[n] = int(float(row[n]))
            except ValueError:
                raise ValueError(f"{n}: a whole number") from None
    return f


def _find_existing(cur: Any, hotel_slug: str, outlet_id: int, sku: str | None, name_en: str) -> int | None:
    if sku:
        cur.execute("SELECT id FROM ups_menu_items WHERE hotel_slug = %s AND outlet_id = %s AND sku = %s",
                    (hotel_slug, outlet_id, sku))
    else:
        cur.execute(
            "SELECT id FROM ups_menu_items WHERE hotel_slug = %s AND outlet_id = %s AND lower(name_en) = lower(%s)"
            " ORDER BY id LIMIT 1",
            (hotel_slug, outlet_id, name_en),
        )
    row = cur.fetchone()
    return row["id"] if row else None


def _message(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        e = exc.errors()[0]
        loc = ".".join(str(x) for x in e.get("loc", ()) if x != "__root__")
        return f"{loc}: {e.get('msg')}" if loc else str(e.get("msg"))
    return getattr(exc, "detail", None) or str(exc)


def _line(batch: dict, row_no: int) -> int:
    """The file line of a data row, as a person counts it in their sheet
    (header on line 1 means the first data row is line 2)."""
    return int(row_no) + int(batch.get("header_row") or 0) + 1


def _options(batch: dict) -> dict:
    """The MENU commit options (batch.options.commit), with their defaults."""
    opts = (batch.get("options") or {}).get("commit") or {}
    return {
        "default_outlet": opts.get("default_outlet") or None,
        "create_outlets": opts.get("create_outlets", True) is not False,
        "strict": bool(opts.get("strict")),
    }


def apply_rows(cur: Any, hotel_slug: str, currency: str, batch: dict, rows: list[dict], *, default_outlet: str | None,
               create_outlets: bool) -> dict:
    """Write canonical MENU rows. Each row runs in its own savepoint: a row
    the menu refuses is rolled back alone, flagged ERROR on its import row
    (ingest.mark_row_error) and listed in errors[{row, error}] by file line."""
    report: dict[str, Any] = {"created": 0, "updated": 0, "errors": [], "outlets_created": []}
    outlets: dict[str, int] = {o["key"]: o["id"] for o in menu.list_outlets(cur, hotel_slug)}
    for row in rows:
        cur.execute("SAVEPOINT ups_import_row")
        try:
            key = (row.get("outlet") or default_outlet or "").strip().lower()
            if not key:
                raise ValueError("outlet is required (or choose a default outlet)")
            if key not in outlets:
                if not create_outlets:
                    raise ValueError(f"unknown outlet {key!r}")
                o = menu.create_outlet(cur, hotel_slug, menu.OutletIn(
                    key=key, name_en=(row.get("outlet_name") or key.replace("-", " ").replace("_", " ").title())))
                outlets[key] = o["id"]
                report["outlets_created"].append(key)
            fields = _row_fields(row)
            existing = _find_existing(cur, hotel_slug, outlets[key], fields.get("sku"), fields["name_en"])
            if existing is None:
                menu.create_item(cur, hotel_slug, currency, menu.ItemIn(outlet_id=outlets[key], **fields))
                report["created"] += 1
            else:
                menu.update_item(cur, hotel_slug, currency, existing, menu.ItemPatch(**fields))
                report["updated"] += 1
            cur.execute("RELEASE SAVEPOINT ups_import_row")
        except Exception as exc:  # one bad row must not stop the rest
            cur.execute("ROLLBACK TO SAVEPOINT ups_import_row")
            cur.execute("RELEASE SAVEPOINT ups_import_row")
            msg = _message(exc)[:300]
            ingest.mark_row_error(cur, row["_row_id"], msg)
            report["errors"].append({"row": _line(batch, row["_row_no"]), "error": msg})
    return report


def commit_menu(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """The MENU commit handler (clefs.core.ingest.register_commit_handler).
    Prices are read in the batch currency, else the hotel's. Strict: any
    refused row raises (outside a dry run), so the commit rolls back and the
    batch stays in REVIEW with nothing written."""
    slug = batch["hotel_slug"]
    if not slug:
        raise ClefsError(409, "A menu import belongs to a hotel", "hotel_required")
    opts = _options(batch)
    currency = batch.get("currency") or get_hotel(cur, slug).currency
    # Strict undoes its own writes before raising: ingest's contract is that
    # the caller's transaction rolls back on a handler error, but a caller
    # that catches the error (a savepoint, a job) must not keep half a menu.
    cur.execute("SAVEPOINT ups_menu_commit")
    report = apply_rows(cur, slug, currency, batch, rows, default_outlet=opts["default_outlet"],
                        create_outlets=opts["create_outlets"])
    if opts["strict"] and report["errors"] and not batch.get("dry_run"):
        cur.execute("ROLLBACK TO SAVEPOINT ups_menu_commit")
        cur.execute("RELEASE SAVEPOINT ups_menu_commit")
        first = report["errors"][0]
        raise ClefsError(409, f"{len(report['errors'])} menu rows were refused (line {first['row']}: {first['error']})",
                         "menu_rows_refused")
    cur.execute("RELEASE SAVEPOINT ups_menu_commit")
    return report


ingest.register_commit_handler(KIND, commit_menu)


# ─── One-call import (POST /upsell/import) ───


def _stage_errors(cur: Any, batch: dict) -> list[dict]:
    """Rows staging refused (name_en or price missing), by file line."""
    out = []
    for r in ingest.list_rows(cur, batch["id"], status="ERROR", limit=MAX_ROWS):
        msgs = [m.replace(": missing", " is required") for m in (r.get("messages") or [])]
        out.append({"row": _line(batch, r["row_no"]), "error": "; ".join(msgs)[:300] or "row not readable"})
    return out


def _merge(report: dict, result: dict | None) -> dict:
    result = result or {}
    report["created"] = int(result.get("created") or 0)
    report["updated"] = int(result.get("updated") or 0)
    report["outlets_created"] = list(result.get("outlets_created") or [])
    report["errors"] = sorted(report["errors"] + list(result.get("errors") or []), key=lambda e: e["row"])
    return report


def _user_id(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    return getattr(principal, "user_id", None) if principal is not None else None


def import_csv(
    cur: Any,
    hotel_slug: str,
    currency: str,
    data: bytes | str,
    *,
    default_outlet: str | None = None,
    create_outlets: bool = True,
    dry_run: bool = False,
    strict: bool = False,
    actor: Any = None,
    filename: str | None = None,
) -> dict:
    """A menu file (CSV text or bytes; XLSX bytes work too) through the MENU
    pipeline in one call: store the file, stage it as a MENU batch, then
    commit it (or only preview the commit when dry_run).

    -> {rows, created, updated, errors[{row, error}], outlets_created[],
        dry_run, applied, batch_id, duplicates, already_imported,
        mapping_required[]}

    - The same bytes are one batch: a file imported before is not written
      again (already_imported, with its earlier result); a dry run leaves
      its batch in REVIEW and the real import of the same file commits it.
    - Columns the alias map cannot place leave the batch MAPPING_REQUIRED
      (mapping_required lists the fields): nothing is written, and the
      Imports console can map them (with the AI proposal) and commit.
    - strict: previewed first; any refused row means nothing is written."""
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if len(raw) > MAX_BYTES:
        raise bad_request("The file is larger than 2 MB", "file_too_large")
    if not raw.strip():
        raise bad_request("The file has no rows", "empty_import")
    ref = files.put_file(cur, hotel_slug, raw, filename=filename or "menu.csv", content_type="text/csv",
                         purpose=FILE_PURPOSE, source="import", actor=actor)
    batch = ingest.create_batch(cur, hotel_slug, file_id=ref.id, kind=KIND, source_code=SOURCE, currency=currency,
                                uploaded_by=_user_id(actor))
    if batch["kind"] != KIND:
        raise bad_request(f"This file was already imported as {batch['kind']}", "import_kind_mismatch")
    if batch["status"] == "FAILED":
        err = (batch.get("errors") or [{}])[0]
        raise ClefsError(400, err.get("detail") or "The file could not be read", err.get("code") or "import_failed")
    if int(batch.get("row_count") or 0) > MAX_ROWS:
        raise bad_request(f"At most {MAX_ROWS} rows per import", "too_many_rows")
    counts = ingest.row_counts(cur, batch["id"])
    report: dict[str, Any] = {
        "rows": int(batch.get("row_count") or 0), "created": 0, "updated": 0, "errors": [], "outlets_created": [],
        "dry_run": dry_run, "applied": False, "batch_id": batch["id"], "duplicates": counts.get("DUP", 0),
        "already_imported": False, "mapping_required": [],
    }
    if batch["status"] in ("COMMITTED", "SUPERSEDED"):
        report["already_imported"] = True
        return _merge(report, batch.get("commit_result"))
    if batch["status"] == "MAPPING_REQUIRED":
        missing = list((batch.get("stats") or {}).get("missing") or [])
        report["mapping_required"] = missing
        report["errors"] = [{"row": _line(batch, 0), "error": "columns not recognised: " + ", ".join(missing)
                             + " (map them in Imports)"}]
        return report
    report["errors"] = _stage_errors(cur, batch)
    if not (counts.get("OK", 0) + counts.get("WARN", 0)):
        return report
    opts = {"default_outlet": default_outlet, "create_outlets": create_outlets, "strict": strict}
    if dry_run or strict:
        preview = ingest.commit_batch(cur, hotel_slug, batch["id"], actor=actor, skip_errors=True, options=opts,
                                      dry_run=True)
        _merge(report, preview.get("commit_result"))
        if dry_run or report["errors"]:
            return report
        report["created"] = report["updated"] = 0
    done = ingest.commit_batch(cur, hotel_slug, batch["id"], actor=actor, skip_errors=not strict, options=opts)
    report["applied"] = True
    return _merge(report, done.get("commit_result"))
