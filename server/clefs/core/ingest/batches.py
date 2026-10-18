"""Import batches: create from a stored file, process (sniff, map, stage or
extract), review edits, reads (02 spec 2.2; 04 spec D4).

Every function joins the caller's transaction. Processing never raises for
a bad file: the batch goes FAILED with the reason in `errors`, so an email
attachment that cannot be read still leaves a trace a person can see.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from psycopg2.extras import execute_values

from clefs import db, files
from clefs.core import clock, hotels
from clefs.core.errors import ClefsError
from clefs.core.ingest import aliases, mapping as mapping_mod, pdf, sniff
from clefs.core.ingest import transforms as tf
from clefs.core.ingest.kinds import KINDS, OPEN_STATUSES, schema_for
from clefs.core.ingest.stage import StagedRow, canonical_row, month_columns, stage_table

log = logging.getLogger("clefs.ingest")

VIAS = ("WEB", "EMAIL", "API", "DEMO", "PUBLIC_AUDIT")
# The date field that spans a batch's period, per kind (supersede + review).
PERIOD_FIELDS: dict[str, tuple[str, str]] = {
    "OTA_STATEMENT": ("check_in", "check_out"), "PMS_RESERVATIONS": ("check_in", "check_out"),
    "OTA_PAYOUT": ("payout_date", "payout_date"), "PMS_FOLIO": ("posted_date", "posted_date"),
    "OCCUPANCY": ("date", "date"), "BANK": ("value_date", "value_date"), "ISSUANCE": ("issued_at", "issued_at"),
    "CREDIT_NOTE": ("check_in", "check_in"), "VCC_REPORT": ("expiration_date", "expiration_date"),
    "PL_STATEMENT": ("period_month", "period_month"), "COMP_RATES": ("stay_date", "stay_date"),
    "SUPPLIER_INVOICE": ("invoice_date", "invoice_date"), "ICS": ("starts_on", "ends_on"),
}
SAMPLE_FOR_AI = 5
# Row messages that only warn (the rest make a row ERROR after an edit).
WARN_MARKERS = ("unknown status", "section unknown", "is not in the document", "currency:", "sign kept")


# ─── Reads ───


def get_batch(cur: Any, hotel_slug: str | None, batch_id: int, *, for_update: bool = False) -> dict:
    cur.execute(
        "SELECT * FROM import_batches WHERE id = %s AND COALESCE(hotel_slug, '') = COALESCE(%s, '')"
        + (" FOR UPDATE" if for_update else ""),
        (batch_id, hotel_slug),
    )
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Import not found", "batch_not_found")
    return dict(row)


def row_counts(cur: Any, batch_id: int) -> dict[str, int]:
    cur.execute("SELECT status, COUNT(*) AS n FROM import_rows WHERE batch_id = %s GROUP BY status", (batch_id,))
    return {r["status"]: r["n"] for r in cur.fetchall()}


def list_batches(cur: Any, hotel_slug: str, *, kind: str | None = None, status: str | None = None,
                 limit: int = 50, before_id: int | None = None) -> list[dict]:
    cur.execute(
        """
        SELECT * FROM import_batches
         WHERE hotel_slug = %s AND (%s::text IS NULL OR kind = %s) AND (%s::text IS NULL OR status = %s)
           AND (%s::bigint IS NULL OR id < %s)
         ORDER BY id DESC LIMIT %s
        """,
        (hotel_slug, kind, kind, status, status, before_id, before_id, limit),
    )
    return [dict(r) for r in cur.fetchall()]


def list_rows(cur: Any, batch_id: int, *, status: str | None = None, limit: int = 200,
              offset: int = 0) -> list[dict]:
    cur.execute(
        """
        SELECT id, row_no, raw, canonical, status, messages, page, line_ref, edited_by, edited_at
          FROM import_rows WHERE batch_id = %s AND (%s::text IS NULL OR status = %s)
         ORDER BY row_no LIMIT %s OFFSET %s
        """,
        (batch_id, status, status, limit, offset),
    )
    return [dict(r) for r in cur.fetchall()]


def committable_rows(cur: Any, batch_id: int) -> list[dict]:
    """The canonical dicts a commit handler receives: OK and WARN rows in
    file order, each with _row_id / _row_no (and _page for PDFs)."""
    cur.execute(
        "SELECT id, row_no, canonical, page FROM import_rows WHERE batch_id = %s AND status IN ('OK', 'WARN') "
        "ORDER BY row_no",
        (batch_id,),
    )
    out = []
    for r in cur.fetchall():
        row = dict(r["canonical"] or {})
        row["_row_id"], row["_row_no"] = r["id"], r["row_no"]
        if r["page"] is not None:
            row["_page"] = r["page"]
        out.append(row)
    return out


# ─── Row storage ───


def store_rows(cur: Any, batch_id: int, rows: list[StagedRow]) -> None:
    """Replace the batch's staged rows (a re-map restages from scratch)."""
    cur.execute("DELETE FROM import_rows WHERE batch_id = %s", (batch_id,))
    if not rows:
        return
    now = clock.now()
    execute_values(
        cur,
        "INSERT INTO import_rows (batch_id, row_no, raw, canonical, status, messages, page, line_ref, created_at) "
        "VALUES %s",
        [(batch_id, r.row_no, db.Json(r.raw), db.Json(r.canonical), r.status, r.messages, r.page, r.line_ref, now)
         for r in rows],
        page_size=500,
    )


def _update(cur: Any, batch_id: int, **cols: Any) -> dict:
    cols["updated_at"] = clock.now()
    sets = ", ".join(f"{k} = %s" for k in cols)
    vals = [db.Json(v) if isinstance(v, (dict, list)) else v for v in cols.values()]
    cur.execute(f"UPDATE import_batches SET {sets} WHERE id = %s RETURNING *", (*vals, batch_id))
    return dict(cur.fetchone())


# ─── Processing helpers ───


def _period(kind: str, rows: list[StagedRow]) -> tuple[date | None, date | None]:
    lo_f, hi_f = PERIOD_FIELDS.get(kind, (None, None))
    if not lo_f:
        return None, None
    los = [r.canonical.get(lo_f) for r in rows if r.status in ("OK", "WARN") and r.canonical.get(lo_f)]
    his = [r.canonical.get(hi_f) for r in rows if r.status in ("OK", "WARN") and r.canonical.get(hi_f)]
    if not los:
        return None, None
    return date.fromisoformat(min(los)[:10]), date.fromisoformat(max(his or los)[:10])


def _doc_no(rows: list[StagedRow]) -> str | None:
    """A statement number all lines share (Booking prints it per line)."""
    nos = {r.canonical.get("doc_no") for r in rows if r.canonical.get("doc_no")}
    return nos.pop() if len(nos) == 1 else None


def _stats(rows: list[StagedRow], **extra: Any) -> dict:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {"rows": counts, **extra}


def _batch_currency(cur: Any, batch: dict) -> str:
    if batch.get("currency"):
        return batch["currency"]
    if batch.get("hotel_slug"):
        h = hotels.find_hotel(cur, batch["hotel_slug"])
        if h is not None and h.currency:
            return h.currency
    return "AED"


def _printed_from_table(table: sniff.Table, kind: str, mapping: dict, currency: str) -> int | None:
    """The last "Total" row's figure under the kind's total field."""
    total_field = schema_for(kind).total_field
    header = mapping.get(total_field or "")
    if not header or not table.total_rows or header not in table.headers:
        return None
    return pdf.printed_total_minor(table.total_rows[-1][table.headers.index(header)], currency)


def _covered(kind: str, mapping: dict, headers: list[str]) -> list[str]:
    """Required fields still unmapped. A multi-month P&L carries its
    amounts in the month columns, so amount_minor needs no mapping there."""
    missing = aliases.missing_required(mapping, kind)
    if kind == "PL_STATEMENT" and month_columns(headers):
        missing = [f for f in missing if f != "amount_minor"]
    return missing


def _raw_samples(table: sniff.Table) -> list[dict]:
    from clefs.core.ingest.stage import raw_dict

    return [raw_dict(table.headers, r) for r in table.rows[:SAMPLE_FOR_AI]]


def _fail(cur: Any, batch_id: int, exc: ClefsError | Exception) -> dict:
    err = {"code": getattr(exc, "code", "processing_failed"), "detail": getattr(exc, "detail", str(exc))[:500]}
    return _update(cur, batch_id, status="FAILED", errors=[err])


# ─── Tabular files ───


def _process_table(cur: Any, batch: dict, data: bytes, *, use_ai: bool) -> dict:
    opts = batch.get("options") or {}
    table = sniff.sniff(data, filename=batch.get("filename"), sheet_name=opts.get("sheet_name"),
                        header_row=opts.get("header_row"))
    if not table.headers:
        raise ClefsError(422, "No header row found: the file needs a row of column names", "no_header_row")
    kind = batch["kind"]
    fp = sniff.header_fingerprint(table.headers)
    currency = _batch_currency(cur, batch)
    source = batch.get("source_code") or aliases.detect_source(table.headers, kind) or aliases.GENERIC
    tpl = mapping_mod.find_template(cur, batch.get("hotel_slug"), fp, kind=kind,
                                    source_code=batch.get("source_code"))
    suggestion = None
    if tpl is not None:
        mp, tr, origin = dict(tpl["mapping"]), dict(tpl["transforms"] or {}), "template"
        source = tpl["source_code"]
        mapping_mod.touch_template(cur, tpl["id"])
    else:
        mp, tr = aliases.resolve(table.headers, kind, source)
        origin = "alias"
    missing = _covered(kind, mp, table.headers)
    if missing and use_ai:
        suggestion = mapping_mod.suggest_mapping(hotel_slug=batch.get("hotel_slug"), kind=kind,
                                                 headers=table.headers, rows=_raw_samples(table), current=mp)
    src_map = aliases.get_map(source)
    common = dict(
        source_code=source, headers=table.headers, header_row=table.header_row, header_fingerprint=fp,
        sheet_name=table.sheet_name, encoding=table.encoding, file_format=table.file_format,
        ota_code=batch.get("ota_code") or (src_map.ota_code if src_map else None),
        mapping_template_id=tpl["id"] if tpl else None, mapping=mp, transforms=tr, mapping_source=origin,
        mapping_suggestion=suggestion, errors=[],
    )
    if missing:
        # Nothing is staged until a person confirms a mapping that covers
        # every required field: half-mapped rows would all be ERROR.
        store_rows(cur, batch["id"], [])
        stats = {"missing": missing, "data_rows": len(table.rows), "sheets": table.sheets,
                 "unverified_aliases": bool(src_map and src_map.unverified)}
        return _update(cur, batch["id"], status="MAPPING_REQUIRED", row_count=len(table.rows), stats=stats, **common)
    return _stage_and_review(cur, batch, table, mp, tr, currency=currency, extra=common,
                             unverified=bool(src_map and src_map.unverified and origin == "alias"))


def _stage_and_review(cur: Any, batch: dict, table: sniff.Table, mp: dict, tr: dict, *, currency: str,
                      extra: dict, unverified: bool = False) -> dict:
    kind = batch["kind"]
    rows = stage_table(table, kind, mp, tr, currency=currency, period_month=batch.get("period_month"))
    store_rows(cur, batch["id"], rows)
    printed = _printed_from_table(table, kind, mp, currency)
    gate_status, parsed = pdf.gate(rows, total_field=schema_for(kind).total_field, printed_minor=printed,
                                   currency=currency, required=False)
    start, end = _period(kind, rows)
    stats = _stats(rows, data_rows=len(table.rows), sheets=table.sheets, unverified_aliases=unverified)
    return _update(
        cur, batch["id"], status="REVIEW", row_count=len(rows), stats=stats, period_start=start, period_end=end,
        doc_no=batch.get("doc_no") or _doc_no(rows), currency=batch.get("currency") or currency,
        printed_total_minor=printed, parsed_total_minor=parsed, total_field=schema_for(kind).total_field,
        gate_status=gate_status, **extra,
    )


# ─── PDF, ICS, XML ───


def _process_pdf(cur: Any, batch: dict, data: bytes) -> dict:
    kind = batch["kind"]
    _update(cur, batch["id"], status="EXTRACTING", file_format="pdf")
    ext, pages = pdf.extract(data, kind=kind, hotel_slug=batch.get("hotel_slug"))
    currency = batch.get("currency") or (ext.currency.strip().upper()[:3] if len(ext.currency.strip()) == 3 else None) \
        or _batch_currency(cur, batch)
    rows = pdf.stage_extraction(ext, kind=kind, currency=currency, page_count=pages)
    store_rows(cur, batch["id"], rows)
    total_field = schema_for(kind).total_field
    printed = pdf.printed_total_minor(ext.printed_total, currency)
    gate_status, parsed = pdf.gate(rows, total_field=total_field, printed_minor=printed, currency=currency,
                                   required=kind != "CONTRACT_PDF")
    start, end = _period(kind, rows)
    for label in ("period_start", "period_end"):
        text = getattr(ext, label).strip()
        if text:
            try:
                parsed_d = sniff_date(text)
                start, end = (parsed_d, end) if label == "period_start" else (start, parsed_d)
            except ValueError:
                pass
    return _update(
        cur, batch["id"], status="REVIEW", row_count=len(rows), currency=currency, mapping_source="ai",
        stats=_stats(rows, pages=pages), period_start=start, period_end=end,
        doc_no=batch.get("doc_no") or ext.doc_no.strip()[:120] or _doc_no(rows),
        printed_total_minor=printed, parsed_total_minor=parsed, total_field=total_field, gate_status=gate_status,
        errors=[],
    )


def sniff_date(text: str) -> date:
    from clefs.core.ingest.transforms import parse_date

    return parse_date(text)


def _process_ics(cur: Any, batch: dict, data: bytes) -> dict:
    """Calendar files (events for Demand Radar): one row per VEVENT."""
    import icalendar

    try:
        cal = icalendar.Calendar.from_ical(data)
    except ValueError as exc:
        raise ClefsError(400, "The calendar file could not be read", "unreadable_file") from exc
    schema = schema_for("ICS")
    identity = {f: f for f in schema.names}
    rows = []
    for i, ev in enumerate(cal.walk("VEVENT"), start=1):
        start = ev.get("DTSTART")
        end = ev.get("DTEND")
        raw = {"uid": str(ev.get("UID") or f"row-{i}"), "summary": str(ev.get("SUMMARY") or ""),
               "starts_on": start.dt.isoformat() if start else None, "ends_on": end.dt.isoformat() if end else None,
               "location": str(ev.get("LOCATION") or "") or None,
               "description": (str(ev.get("DESCRIPTION") or "")[:2000]) or None}
        raw = {k: v for k, v in raw.items() if v}
        canon, status, msgs = canonical_row(raw, schema, identity, {}, currency="AED")
        canon.pop("currency", None)
        rows.append(StagedRow(i, raw, canon, status, msgs))
    store_rows(cur, batch["id"], rows)
    start, end = _period("ICS", rows)
    return _update(cur, batch["id"], status="REVIEW", file_format="ics", row_count=len(rows), stats=_stats(rows),
                   period_start=start, period_end=end, gate_status="n/a", mapping_source="alias", errors=[])


def _process_xml(cur: Any, batch: dict, data: bytes) -> dict:
    """UAE e-invoices (PINT AE UBL) are the only XML expected; the adapter
    is a stub until the 2027 e-invoicing rollout (02 spec 2.2 step 8)."""
    from clefs.core.ingest.adapters import pint_ae

    try:
        pint_ae.parse_ubl(data.decode("utf-8", errors="replace"))
    except NotImplementedError as exc:
        raise ClefsError(422, "E-invoice XML (PINT AE) is not supported yet", "xml_not_supported") from exc
    raise ClefsError(422, "E-invoice XML (PINT AE) is not supported yet", "xml_not_supported")


# ─── Process ───


def process_batch(cur: Any, batch_id: int, *, hotel_slug: str | None, use_ai: bool = True,
                  data: bytes | None = None) -> dict:
    """Run (or re-run) a batch from its file. Leaves it REVIEW,
    MAPPING_REQUIRED or FAILED; never raises for a bad file. Rolls back to
    a savepoint on failure so half-written rows never stay behind."""
    batch = get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] in ("COMMITTED", "SUPERSEDED"):
        raise ClefsError(409, f"This import is {batch['status'].lower()}", "batch_closed")
    cur.execute("SAVEPOINT clefs_ingest_process")
    try:
        if data is None:
            _ref, data = files.get_file(cur, hotel_slug, str(batch["file_id"]))
        fmt = batch.get("file_format") or sniff.detect_format(data, batch.get("filename"))
        if fmt == "pdf":
            out = _process_pdf(cur, batch, data)
        elif fmt == "ics":
            out = _process_ics(cur, batch, data)
        elif fmt == "xml":
            out = _process_xml(cur, batch, data)
        elif fmt in ("csv", "xlsx", "xls"):
            out = _process_table(cur, batch, data, use_ai=use_ai)
        else:
            raise ClefsError(415, f"A {fmt} file cannot be imported", "unsupported_format")
        cur.execute("RELEASE SAVEPOINT clefs_ingest_process")
        return out
    except (ClefsError, ValueError, KeyError) as exc:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_ingest_process")
        cur.execute("RELEASE SAVEPOINT clefs_ingest_process")
        log.info("ingest batch %s failed: %s", batch_id, exc)
        return _fail(cur, batch_id, exc)


# ─── Create ───


def _detect_kind(data: bytes, filename: str | None) -> tuple[str | None, str | None]:
    """(kind, source_code) for a file nobody labelled (email): the named
    source whose detect headers match, or a calendar."""
    fmt = sniff.detect_format(data, filename)
    if fmt == "ics":
        return "ICS", None
    if fmt not in ("csv", "xlsx", "xls"):
        return None, None
    try:
        table = sniff.sniff(data, filename=filename)
    except ClefsError:
        return None, None
    source = aliases.detect_source(table.headers)
    return (aliases.source_kind(source), source) if source else (None, None)


def create_batch(cur: Any, hotel_slug: str | None, *, file_id: str, kind: str | None = None,
                 source_code: str | None = None, ota_code: str | None = None, via: str = "WEB",
                 uploaded_by: int | None = None, period_month: date | None = None, currency: str | None = None,
                 options: dict | None = None, inbound_message_id: int | None = None, auto_commit: bool = False,
                 supersedes_batch_id: int | None = None, process: bool = True, use_ai: bool = True) -> dict:
    """One batch per stored file (UNIQUE per hotel and sha256: a forwarded
    email and a manual upload of the same statement are one batch). Returns
    the batch with `created` False when those bytes were already imported;
    a FAILED one is re-run with the new labels instead."""
    if via not in VIAS:
        raise ValueError(f"via must be one of {VIAS}")
    ref, data = files.get_file(cur, hotel_slug, file_id)
    if source_code is not None and aliases.get_map(source_code) is None:
        raise ClefsError(400, f"unknown source {source_code!r}", "unknown_source")
    if kind is None and source_code:
        kind = aliases.source_kind(source_code)
    if kind is None:
        kind, detected = _detect_kind(data, ref.filename)
        source_code = source_code or detected
    if kind not in KINDS:
        raise ClefsError(400, "Say what the file is (kind): it could not be recognised", "unknown_kind")
    if source_code and aliases.source_kind(source_code) not in (None, kind):
        raise ClefsError(400, f"source {source_code} does not produce {kind} rows", "source_kind_mismatch")
    if kind == "TRIAL_BALANCE" and period_month is None:
        raise ClefsError(400, "A trial balance needs the month it is for (period_month)", "period_month_required")
    if period_month is not None:
        period_month = period_month.replace(day=1)
    cur.execute(
        "SELECT * FROM import_batches WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') AND sha256 = %s FOR UPDATE",
        (hotel_slug, ref.sha256),
    )
    existing = cur.fetchone()
    if existing is not None and existing["status"] != "FAILED":
        return {**dict(existing), "created": False}
    now = clock.now()
    values = dict(
        kind=kind, source_code=source_code, ota_code=ota_code, file_id=ref.id, filename=ref.filename,
        file_format=sniff.detect_format(data, ref.filename, ref.content_type), status="UPLOADED",
        period_month=period_month, currency=currency, via=via, auto_commit=auto_commit,
        options=db.Json(options or {}), uploaded_by=uploaded_by, inbound_message_id=inbound_message_id,
        supersedes_batch_id=supersedes_batch_id, errors=db.Json([]), updated_at=now,
    )
    if existing is not None:
        sets = ", ".join(f"{k} = %s" for k in values)
        cur.execute(f"UPDATE import_batches SET {sets} WHERE id = %s RETURNING *", (*values.values(), existing["id"]))
    else:
        cols = ", ".join(values)
        cur.execute(
            f"INSERT INTO import_batches (hotel_slug, sha256, created_at, {cols}) "
            f"VALUES (%s, %s, %s, {', '.join(['%s'] * len(values))}) RETURNING *",
            (hotel_slug, ref.sha256, now, *values.values()),
        )
    batch = dict(cur.fetchone())
    if process:
        batch = process_batch(cur, batch["id"], hotel_slug=hotel_slug, use_ai=use_ai, data=data)
        batch = _link_supersede(cur, batch)
    return {**batch, "created": True}


def _link_supersede(cur: Any, batch: dict) -> dict:
    """A corrected re-upload replaces the earlier batch of the same kind,
    source and document (same doc_no, else the same period) rather than
    adding to it. Recorded now so review shows it; applied at commit."""
    if batch.get("supersedes_batch_id") or batch["status"] not in ("REVIEW", "MAPPING_REQUIRED"):
        return batch
    if not batch.get("doc_no") and not batch.get("period_start"):
        return batch
    cur.execute(
        """
        SELECT id FROM import_batches
         WHERE COALESCE(hotel_slug, '') = COALESCE(%s, '') AND id <> %s AND kind = %s
           AND COALESCE(source_code, '') = COALESCE(%s, '') AND status IN ('REVIEW', 'MAPPING_REQUIRED', 'COMMITTED')
           AND superseded_by IS NULL
           AND ((%s::text IS NOT NULL AND doc_no = %s)
                OR (%s::text IS NULL AND period_start = %s AND period_end = %s
                    AND COALESCE(period_month, DATE '1900-01-01') = COALESCE(%s::date, DATE '1900-01-01')))
         ORDER BY id DESC LIMIT 1
        """,
        (batch["hotel_slug"], batch["id"], batch["kind"], batch.get("source_code"), batch.get("doc_no"),
         batch.get("doc_no"), batch.get("doc_no"), batch.get("period_start"), batch.get("period_end"),
         batch.get("period_month")),
    )
    prev = cur.fetchone()
    return _update(cur, batch["id"], supersedes_batch_id=prev["id"]) if prev else batch


# ─── Mapping confirmation and options ───


def _check_mapping(kind: str, headers: list[str], mp: dict, tr: dict) -> None:
    schema = schema_for(kind)
    bad_fields = sorted(set(mp) - set(schema.names))
    if bad_fields:
        raise ClefsError(400, f"not {kind} fields: {bad_fields}", "invalid_mapping")
    bad_headers = sorted({h for h in mp.values() if h not in headers})
    if bad_headers:
        raise ClefsError(400, f"not columns of this file: {bad_headers}", "invalid_mapping")
    dup = sorted({h for h in mp.values() if list(mp.values()).count(h) > 1})
    if dup:
        raise ClefsError(400, f"a column can feed one field only: {dup}", "invalid_mapping")
    for f, t in tr.items():
        if f not in mp or set(t) - set(aliases.TRANSFORM_KEYS):
            raise ClefsError(400, f"invalid transforms for {f}", "invalid_mapping")
    missing = _covered(kind, mp, headers)
    if missing:
        raise ClefsError(400, f"required fields are not mapped: {missing}", "mapping_incomplete")


def confirm_mapping(cur: Any, hotel_slug: str | None, batch_id: int, *, mapping: dict[str, str],
                    transforms: dict[str, dict] | None = None, confirmed_by: int | None = None,
                    save_template: bool = True) -> dict:
    """A person's mapping (often the AI proposal as shown): stage the rows
    with it, and save it as this hotel's template for the layout, so the
    next file like this maps without asking."""
    batch = get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] not in ("MAPPING_REQUIRED", "REVIEW"):
        raise ClefsError(409, f"A {batch['status'].lower()} import cannot be re-mapped", "batch_closed")
    if batch.get("file_format") not in ("csv", "xlsx", "xls"):
        raise ClefsError(409, "Only table files have a column mapping", "not_tabular")
    tr = {f: dict(t) for f, t in (transforms or {}).items() if t}
    mp = {f: h for f, h in mapping.items() if h}
    _check_mapping(batch["kind"], batch["headers"] or [], mp, tr)
    _ref, data = files.get_file(cur, hotel_slug, str(batch["file_id"]))
    opts = batch.get("options") or {}
    table = sniff.sniff(data, filename=batch.get("filename"), sheet_name=opts.get("sheet_name"),
                        header_row=opts.get("header_row"))
    suggestion = batch.get("mapping_suggestion") or {}
    origin = "ai" if suggestion and suggestion.get("mapping") == mp else "manual"
    source = batch.get("source_code") or aliases.GENERIC
    tpl = None
    if save_template:
        tpl = mapping_mod.save_template(
            cur, hotel_slug, kind=batch["kind"], source_code=source, fingerprint=batch["header_fingerprint"],
            headers=table.headers, mapping=mp, transforms=tr, origin=origin, confirmed_by=confirmed_by,
        )
    extra = dict(mapping=mp, transforms=tr, mapping_source=origin, source_code=source,
                 mapping_template_id=tpl["id"] if tpl else batch.get("mapping_template_id"), errors=[])
    out = _stage_and_review(cur, batch, table, mp, tr, currency=_batch_currency(cur, batch), extra=extra)
    return _link_supersede(cur, out)


def set_options(cur: Any, hotel_slug: str | None, batch_id: int, *, sheet_name: str | None = None,
                header_row: int | None = None, use_ai: bool = True) -> dict:
    """Pick another sheet or header row (the sniffer guessed wrong) and
    re-run the batch from its file."""
    batch = get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] not in OPEN_STATUSES and batch["status"] != "FAILED":
        raise ClefsError(409, f"A {batch['status'].lower()} import cannot be changed", "batch_closed")
    opts = dict(batch.get("options") or {})
    for k, v in (("sheet_name", sheet_name), ("header_row", header_row)):
        if v is None:
            opts.pop(k, None)
        else:
            opts[k] = v
    _update(cur, batch_id, options=opts, status="UPLOADED")
    out = process_batch(cur, batch_id, hotel_slug=hotel_slug, use_ai=use_ai)
    return _link_supersede(cur, out)


# ─── Review edits ───


def _is_currency(v: Any) -> bool:
    try:
        tf.parse_currency(v)
        return True
    except ValueError:
        return False


def _refresh_totals(cur: Any, batch: dict) -> dict:
    """Recount rows and re-run the gate after an edit."""
    counts = row_counts(cur, batch["id"])
    total_field = batch.get("total_field")
    parsed = None
    status = batch.get("gate_status")
    if total_field:
        cur.execute(
            "SELECT COALESCE(SUM((canonical ->> %s)::bigint), 0) AS s FROM import_rows "
            "WHERE batch_id = %s AND status IN ('OK', 'WARN') AND jsonb_typeof(canonical -> %s) = 'number'",
            (total_field, batch["id"], total_field),
        )
        parsed = int(cur.fetchone()["s"])
        printed = batch.get("printed_total_minor")
        if printed is not None:
            ok = abs(parsed - printed) <= pdf.tolerance_minor(batch.get("currency") or "AED")
            status = "ok" if ok else "mismatch"
    stats = dict(batch.get("stats") or {})
    stats["rows"] = counts
    return _update(cur, batch["id"], parsed_total_minor=parsed, gate_status=status, stats=stats)


def edit_row(cur: Any, hotel_slug: str | None, batch_id: int, row_id: int, *, values: dict[str, Any] | None = None,
             skip: bool | None = None, edited_by: int | None = None) -> dict:
    """Correct canonical values (typed as a person would, re-read through
    the field's type) or skip / un-skip a row, during review."""
    batch = get_batch(cur, hotel_slug, batch_id, for_update=True)
    if batch["status"] != "REVIEW":
        raise ClefsError(409, "Rows can be edited only while the import is in review", "batch_closed")
    cur.execute("SELECT * FROM import_rows WHERE id = %s AND batch_id = %s FOR UPDATE", (row_id, batch_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Row not found", "row_not_found")
    schema = schema_for(batch["kind"])
    new = dict(row["canonical"] or {})
    currency = new.get("currency") or batch.get("currency") or "AED"
    edited = set(values or {})
    if "currency" in edited and values and values.get("currency"):
        currency = tf.parse_currency(values["currency"]) if _is_currency(values["currency"]) else currency
    # Only the edited fields are read again: the rest are canonical already
    # (money is minor units there and must not be parsed as major units).
    for k, v in (values or {}).items():
        f = schema.get(k)
        if f is None:
            raise ClefsError(400, f"{k} is not a {batch['kind']} field", "invalid_field")
        try:
            coerced, _warn = tf.coerce(v, f.type, currency=currency, status_map=schema.status_map)
        except ValueError as exc:
            raise ClefsError(400, f"{k}: {exc}", "invalid_value") from exc
        if coerced is None:
            new.pop(k, None)
        else:
            new[k] = coerced
    msgs = [m for m in (row["messages"] or [])
            if m.split(":")[0] not in edited and not m.startswith("same record as row") and m != "skipped in review"
            and not (m.endswith(": missing") and m.split(":")[0] in new)]
    missing = [r for r in schema.required if r not in new]
    msgs += [f"{r}: missing" for r in missing if f"{r}: missing" not in msgs]
    errors = [m for m in msgs if not any(w in m for w in WARN_MARKERS)]
    status = "ERROR" if errors else ("WARN" if msgs else "OK")
    if skip:
        status, msgs = "SKIP", msgs + ["skipped in review"]
    elif skip is None and row["status"] == "SKIP" and not edited:
        status = "SKIP"
    cur.execute(
        "UPDATE import_rows SET canonical = %s, status = %s, messages = %s, edited_by = %s, edited_at = %s "
        "WHERE id = %s RETURNING id, row_no, raw, canonical, status, messages, page, line_ref, edited_by, edited_at",
        (db.Json(new), status, msgs, edited_by, clock.now(), row_id),
    )
    out = dict(cur.fetchone())
    _refresh_totals(cur, batch)
    return out
