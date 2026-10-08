"""PDF statements and contracts: AI extraction into canonical rows with page
and line references, then the printed-total gate (02 spec 2.2 step 5).

The text layer is read locally (pypdf) page by page and sent with page
markers, so every extracted line can say where it came from and a person can
check it against the PDF. A statement whose lines do not add up to its
printed total (within one major unit, AED 1) cannot be committed unless a
person overrides the gate, which is audited.
"""

from __future__ import annotations

import io
import json
from typing import Any

from pydantic import BaseModel

from clefs import ai
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.core.ingest import transforms as tf
from clefs.core.ingest.kinds import schema_for
from clefs.core.ingest.stage import StagedRow, canonical_row, mark_duplicates

AI_TASK = "ingest.extract_pdf"
MAX_PAGES = 30
MAX_CHARS = 120_000
MIN_TEXT_CHARS = 40  # below this the PDF is a scan with no text layer


def page_texts(data: bytes, *, max_pages: int = MAX_PAGES) -> list[str]:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            reader.decrypt("")  # many statements are "protected" with an empty password
        pages = reader.pages
        if len(pages) > max_pages:
            raise ClefsError(413, f"At most {max_pages} pages per PDF", "too_many_pages")
        return [(p.extract_text() or "").strip() for p in pages]
    except ClefsError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as exc:
        raise ClefsError(400, "The PDF could not be read", "unreadable_file") from exc
    except Exception as exc:  # pypdf raises assorted errors (DependencyError for AES) on odd files
        raise ClefsError(400, "The PDF could not be read", "unreadable_file") from exc


# ─── Extraction schema (strict: every field present, "" for unknown) ───


class PdfCell(BaseModel):
    field: str
    value: str


class PdfLine(BaseModel):
    page: int
    line_ref: str
    cells: list[PdfCell]


class PdfExtraction(BaseModel):
    lines: list[PdfLine]
    printed_total: str
    currency: str
    doc_no: str
    period_start: str
    period_end: str


SYSTEM = (
    "You extract the line items of a hotel document (an OTA commission statement, payout statement, credit "
    "note, supplier invoice, P&L or contract) into canonical fields. You get the document kind, the "
    "canonical fields with their types and the text of each page between '--- page N ---' markers. Return "
    "one line per item row of the document (never the totals rows), with its page number and a short "
    "line_ref (the row's first printed words or its row number) so a person can find it, and one cell per "
    "canonical field you can read, values copied as printed. printed_total is the document's printed grand "
    "total for the field named total_field, as printed ('' when there is none). doc_no, currency, "
    "period_start and period_end are '' when not printed. For a contract, return one line per clause that "
    "sets commission, payment, dispute or card rules, with clause_ref and the clause text."
)


def extract(data: bytes, *, kind: str, hotel_slug: str | None) -> tuple[PdfExtraction, int]:
    """(extraction, page count). Raises ClefsError for unreadable or text-
    less PDFs and when AI is off (the batch goes FAILED with that reason)."""
    pages = page_texts(data)
    if sum(len(p) for p in pages) < MIN_TEXT_CHARS:
        raise ClefsError(422, "This PDF is a scan with no text; upload the CSV or Excel export instead",
                         "pdf_no_text")
    text = "\n".join(f"--- page {i} ---\n{p}" for i, p in enumerate(pages, start=1))
    if len(text) > MAX_CHARS:
        raise ClefsError(413, "The PDF has too much text to extract; upload the CSV or Excel export instead",
                         "pdf_too_large")
    schema = schema_for(kind)
    payload = {
        "kind": kind,
        "total_field": schema.total_field,
        "fields": [{"name": f.name, "type": f.type, "required": f.required} for f in schema.fields],
        "text": text,
    }
    out = ai.structured(AI_TASK, system=SYSTEM, input=json.dumps(payload, ensure_ascii=False),
                        schema=PdfExtraction, hotel_slug=hotel_slug, max_output_tokens=16000, timeout_s=60.0)
    return out, len(pages)


def stage_extraction(ext: PdfExtraction, *, kind: str, currency: str, page_count: int) -> list[StagedRow]:
    """Extracted lines as staged rows. The raw of a PDF row is its cells as
    the model read them; a page outside the document is a WARN."""
    schema = schema_for(kind)
    identity = {f: f for f in schema.names}
    rows: list[StagedRow] = []
    for i, line in enumerate(ext.lines, start=1):
        raw = {c.field: c.value for c in line.cells if c.field in schema.names and c.value.strip()}
        canon, status, msgs = canonical_row(raw, schema, identity, {}, currency=currency)
        if not 1 <= line.page <= page_count:
            msgs.append(f"page {line.page} is not in the document")
            status = "WARN" if status == "OK" else status
        rows.append(StagedRow(i, raw, canon, status, msgs, page=line.page, line_ref=line.line_ref[:120] or None))
    mark_duplicates(rows, kind)
    return rows


# ─── Printed-total gate ───


def tolerance_minor(currency: str) -> int:
    """One major unit (AED 1 = 100 fils) in the currency's minor units."""
    return 10 ** money.minor_units(currency)


def printed_total_minor(value: Any, currency: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return tf.parse_money(value, currency)
    except ValueError:
        return None


def gate(rows: list[StagedRow], *, total_field: str | None, printed_minor: int | None, currency: str,
         required: bool) -> tuple[str, int | None]:
    """(gate_status, parsed_total_minor). `required` is True for PDFs: a
    missing printed total blocks the commit too; a CSV without a Total row
    is simply not gated ('n/a')."""
    if not total_field:
        return "n/a", None
    parsed = sum(r.canonical.get(total_field) or 0 for r in rows
                 if r.status in ("OK", "WARN") and isinstance(r.canonical.get(total_field), int))
    if printed_minor is None:
        return ("missing" if required else "n/a"), parsed
    return ("ok" if abs(parsed - printed_minor) <= tolerance_minor(currency) else "mismatch"), parsed
