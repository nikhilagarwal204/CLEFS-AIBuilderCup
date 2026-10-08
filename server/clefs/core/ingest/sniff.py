"""Sniffing: bytes -> a table of cells with a detected header row (02 spec
2.2 step 1 and 2).

Pure functions, no database. OTA and PMS exports arrive as UTF-8 CSV, Excel
"Unicode text" (UTF-16 with a BOM, tab separated), Arabic Windows CSV
(cp1256), XLSX and old XLS. Most carry a title block above the header row
and a "Total" row under the data, so both are found rather than assumed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs.core.errors import ClefsError

MAX_ROWS = 20_000
MAX_COLS = 200
HEADER_SCAN_ROWS = 30
HEADER_SHARE = 0.6  # a header row has at least 60% non-numeric cells (02 spec 2.2)
DELIMITERS = (",", ";", "\t", "|")
# Words that open a totals row in English and Arabic exports.
TOTAL_WORDS = ("total", "grand total", "totals", "sum", "الإجمالي", "الاجمالي", "المجموع", "إجمالي", "اجمالي")


@dataclass
class Table:
    headers: list[str]
    rows: list[list[Any]]
    header_row: int | None  # 0-based index of the header row in the sheet
    file_format: str
    encoding: str | None = None
    delimiter: str | None = None
    sheet_name: str | None = None
    sheets: list[str] = field(default_factory=list)
    # Rows under the data whose first cells say "Total": the printed totals.
    total_rows: list[list[Any]] = field(default_factory=list)
    # Rows above the header (title block): period and document numbers live here.
    preamble: list[list[Any]] = field(default_factory=list)


# ─── Format and encoding ───


def detect_format(data: bytes, filename: str | None = None, content_type: str | None = None) -> str:
    """Magic bytes first (a renamed file is still what its bytes say), then
    the extension, then the content type."""
    head = data[:8]
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "xlsx"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "xls"
    name = (filename or "").lower()
    ctype = (content_type or "").lower()
    try:
        text, _ = decode_text(data[:4096], strict=True)
    except ClefsError:
        return "other"
    stripped = text.lstrip("﻿ \r\n\t")
    if stripped.upper().startswith("BEGIN:VCALENDAR") or name.endswith(".ics"):
        return "ics"
    if stripped.startswith("<") or name.endswith(".xml") or "xml" in ctype:
        return "xml"
    if stripped[:1] in "{[" and (name.endswith(".json") or "json" in ctype or not name):
        return "json"
    return "csv"


def decode_text(data: bytes, *, strict: bool = False) -> tuple[str, str]:
    """(text, encoding). BOMs decide; else UTF-8; else UTF-16 without a BOM
    (every other byte NUL); else cp1256, the Arabic Windows code page UAE
    back-office PCs save CSV in. `strict` refuses binary-looking input."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"
    # UTF-16 without a BOM: NUL bytes are valid UTF-8, so the NUL pattern is
    # checked first (every odd byte NUL for Latin text in little endian).
    sample = data[:2000]
    if len(sample) >= 4:
        if sample[1::2].count(0) > len(sample) // 4 and sample[0::2].count(0) < len(sample) // 8:
            return data.decode("utf-16-le", errors="replace"), "utf-16-le"
        if sample[0::2].count(0) > len(sample) // 4 and sample[1::2].count(0) < len(sample) // 8:
            return data.decode("utf-16-be", errors="replace"), "utf-16-be"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError as exc:
        # A cut in the middle of a multi-byte character at the end of a
        # sample is still UTF-8.
        if exc.start >= len(data) - 3 and exc.reason.startswith("unexpected end"):
            return data[: exc.start].decode("utf-8"), "utf-8"
    if strict and sample.count(0) > 0:
        raise ClefsError(400, "The file is not text", "unreadable_file")
    return data.decode("cp1256", errors="replace"), "cp1256"


def sniff_delimiter(text: str) -> str:
    """The delimiter that splits the first lines into the most, and the most
    consistent, columns. csv.Sniffer guesses wrong on semicolon files with
    decimal commas, so this counts through the csv reader instead."""
    lines = [ln for ln in text.splitlines()[:40] if ln.strip()]
    if not lines:
        return ","
    best, best_score = ",", -1.0
    for d in DELIMITERS:
        widths = [len(r) for r in csv.reader(lines, delimiter=d)]
        common = max(set(widths), key=widths.count)
        if common < 2:
            continue
        score = common * widths.count(common) / len(widths)
        if score > best_score:
            best, best_score = d, score
    return best


# ─── Readers ───


def _clean(v: Any) -> Any:
    if isinstance(v, str):
        v = v.replace(" ", " ").strip()
        return v or None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def read_csv(data: bytes) -> tuple[list[list[Any]], str, str]:
    """(rows, encoding, delimiter)."""
    text, encoding = decode_text(data)
    text = text.lstrip("﻿")
    delimiter = sniff_delimiter(text)
    rows = []
    for r in csv.reader(io.StringIO(text, newline=""), delimiter=delimiter):
        rows.append([_clean(c) for c in r[:MAX_COLS]])
        if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
            raise ClefsError(413, f"At most {MAX_ROWS} rows per file", "too_many_rows")
    return rows, encoding, delimiter


def read_xlsx(data: bytes) -> dict[str, list[list[Any]]]:
    """Every sheet's rows, values not formulas (data_only: Excel caches the
    computed value, which is what the person saw)."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # zipfile.BadZipFile, KeyError, InvalidFileException
        raise ClefsError(400, "The Excel file could not be read", "unreadable_file") from exc
    out: dict[str, list[list[Any]]] = {}
    try:
        for ws in wb.worksheets:
            rows: list[list[Any]] = []
            for r in ws.iter_rows(values_only=True, max_col=MAX_COLS):
                rows.append([_clean(c) for c in r])
                if len(rows) > MAX_ROWS + HEADER_SCAN_ROWS:
                    raise ClefsError(413, f"At most {MAX_ROWS} rows per file", "too_many_rows")
            out[ws.title] = rows
    finally:
        wb.close()
    return out


def read_xls(data: bytes) -> dict[str, list[list[Any]]]:
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data)
    except Exception as exc:  # xlrd.XLRDError and friends
        raise ClefsError(400, "The Excel file could not be read", "unreadable_file") from exc
    out: dict[str, list[list[Any]]] = {}
    for sh in book.sheets():
        if sh.nrows > MAX_ROWS + HEADER_SCAN_ROWS:
            raise ClefsError(413, f"At most {MAX_ROWS} rows per file", "too_many_rows")
        rows = []
        for i in range(sh.nrows):
            cells = []
            for j, c in enumerate(sh.row(i)[:MAX_COLS]):
                if c.ctype == xlrd.XL_CELL_DATE:
                    try:
                        cells.append(xlrd.xldate_as_datetime(c.value, book.datemode))
                    except Exception:  # an out-of-range serial stays a number
                        cells.append(c.value)
                elif c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    cells.append(None)
                elif c.ctype == xlrd.XL_CELL_NUMBER and float(c.value).is_integer():
                    cells.append(int(c.value))
                else:
                    cells.append(_clean(c.value))
            rows.append(cells)
        out[sh.name] = rows
    return out


# ─── Header row ───


def is_numeric_cell(v: Any) -> bool:
    """Numbers, dates and money-looking text ("1,234.50", "AED 30", "15%")."""
    if isinstance(v, (int, float, Decimal, date, datetime)) and not isinstance(v, bool):
        return True
    if not isinstance(v, str):
        return False
    s = re.sub(r"^[A-Z]{3}\s*|\s*[A-Z]{3}$", "", v.strip())
    s = s.replace(",", "").replace(" ", "").rstrip("%").strip("()")
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        return True
    return bool(re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T].*)?", v.strip()))


def detect_header_row(rows: list[list[Any]]) -> int | None:
    """The first row whose non-numeric cells fill at least 60% of the
    table's width and number at least two. Width is the widest row among
    the first rows, so a one-cell title ("Reservation statement") never
    passes as a header."""
    scan = rows[:HEADER_SCAN_ROWS]
    width = max((sum(c is not None for c in r) for r in scan), default=0)
    if width < 2:
        return None
    need = max(2, math.ceil(HEADER_SHARE * width))
    for i, r in enumerate(scan):
        texts = sum(1 for c in r if c is not None and not is_numeric_cell(c))
        if texts >= need:
            return i
    return None


# ─── Headers and fingerprint ───


def normalize_header(h: Any) -> str:
    """Lowercase, strip punctuation, collapse spaces (02 spec 2.2 step 2).
    "%" and "#" carry meaning in exports ("Commission %" next to "Commission
    amount", "Booking #"), so they become words before punctuation goes."""
    s = str(h or "").replace("%", " pct ").replace("#", " no ").lower()
    s = re.sub(r"[^\w]+", " ", s, flags=re.UNICODE).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def header_fingerprint(headers: list[str]) -> str:
    """sha1 of the sorted normalized headers: one export layout, one
    template, whatever order the columns come in."""
    norm = sorted(n for n in (normalize_header(h) for h in headers) if n)
    return hashlib.sha1("|".join(norm).encode("utf-8")).hexdigest()


def _headers(row: list[Any]) -> list[str]:
    """Header texts, blanks named column_<n> and repeats suffixed " (2)" so
    every raw value keeps its own key in import_rows.raw."""
    out: list[str] = []
    seen: dict[str, int] = {}
    for i, c in enumerate(row):
        h = str(c).strip() if c is not None else f"column_{i + 1}"
        if isinstance(c, (datetime, date)):
            h = c.strftime("%Y-%m") if isinstance(c, datetime) or c.day == 1 else c.isoformat()
        key = h.lower()
        seen[key] = seen.get(key, 0) + 1
        out.append(h if seen[key] == 1 else f"{h} ({seen[key]})")
    return out


def is_total_row(row: list[Any]) -> bool:
    """A row whose first text cell (in the first three) opens with "Total"."""
    for c in row[:3]:
        if isinstance(c, str):
            n = normalize_header(c)
            return any(n == w or n.startswith(w + " ") for w in TOTAL_WORDS)
    return False


def build_table(rows: list[list[Any]], *, file_format: str, header_row: int | None = None, **meta: Any) -> Table:
    """Split raw rows into preamble, header, data and total rows. Empty rows
    are dropped; data rows are padded or cut to the header width."""
    hr = detect_header_row(rows) if header_row is None else header_row
    if hr is None or hr >= len(rows):
        return Table(headers=[], rows=[], header_row=None, file_format=file_format, **meta)
    headers = _headers(rows[hr])
    # Trailing blank headers with no data under them are not columns.
    width = len(headers)
    body = [r for r in rows[hr + 1:] if any(c is not None for c in r)]
    used = max((max((i for i, c in enumerate(r) if c is not None), default=-1) for r in body), default=-1)
    while width > 1 and width - 1 > used and headers[width - 1].startswith("column_"):
        width -= 1
    headers = headers[:width]
    data, totals = [], []
    for r in body:
        r = (list(r) + [None] * width)[:width]
        # A subtotal per section is a totals row too; data may resume under
        # it. The last totals row is the printed grand total (gate).
        (totals if is_total_row(r) else data).append(r)
    if len(data) > MAX_ROWS:
        raise ClefsError(413, f"At most {MAX_ROWS} rows per file", "too_many_rows")
    return Table(headers=headers, rows=data, header_row=hr, file_format=file_format, total_rows=totals,
                 preamble=[r for r in rows[:hr] if any(c is not None for c in r)], **meta)


def sniff(data: bytes, *, filename: str | None = None, content_type: str | None = None,
          sheet_name: str | None = None, header_row: int | None = None) -> Table:
    """The table in a CSV, XLSX or XLS file. For a workbook, the named sheet,
    else the sheet with a header row and the most data rows."""
    fmt = detect_format(data, filename, content_type)
    if fmt == "csv":
        rows, encoding, delimiter = read_csv(data)
        return build_table(rows, file_format="csv", header_row=header_row, encoding=encoding, delimiter=delimiter)
    if fmt not in ("xlsx", "xls"):
        raise ClefsError(400, f"A {fmt} file is not a table", "not_tabular")
    sheets = read_xlsx(data) if fmt == "xlsx" else read_xls(data)
    names = list(sheets)
    if sheet_name is not None:
        if sheet_name not in sheets:
            raise ClefsError(400, f"No sheet named {sheet_name!r}", "unknown_sheet")
        return build_table(sheets[sheet_name], file_format=fmt, header_row=header_row, sheet_name=sheet_name,
                           sheets=names)
    best: Table | None = None
    for name in names:
        t = build_table(sheets[name], file_format=fmt, header_row=header_row, sheet_name=name, sheets=names)
        if t.headers and (best is None or len(t.rows) > len(best.rows)):
            best = t
    return best or Table(headers=[], rows=[], header_row=None, file_format=fmt, sheets=names)


def number_or_none(v: Any) -> Decimal | None:
    """A loose number reader for printed totals ("AED 1,234.50", "(12.00)")."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float, Decimal)):
        return Decimal(str(v))
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", "") if re.search(r",\d{3}$", s) else s.replace(",", ".")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return -d if neg else d
