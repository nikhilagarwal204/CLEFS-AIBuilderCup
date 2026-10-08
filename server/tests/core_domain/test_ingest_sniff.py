"""core/ingest sniffing: encodings, delimiters, workbooks, header row,
totals rows and the header fingerprint (02 spec 2.2 steps 1-2). Pure."""

from __future__ import annotations

import io
from datetime import datetime

import pytest

from clefs.core.errors import ClefsError
from clefs.core.ingest import sniff

STATEMENT = (
    "Reservation statement\n"
    "Property: Test Hotel Dubai\n"
    "\n"
    "Book number;Guest name(s);Check-in;Price;Commission amount\n"
    "123;Jane Doe;01/10/2025;1.234,50;185,18\n"
    "124;محمد علي;02/10/2025;500,00;75,00\n"
    "Total;;;1.734,50;260,18\n"
)


def test_title_block_header_row_and_total_row_are_found():
    t = sniff.sniff(STATEMENT.encode("utf-8"), filename="stmt.csv")
    assert t.file_format == "csv" and t.delimiter == ";" and t.encoding == "utf-8"
    assert t.header_row == 3
    assert t.headers == ["Book number", "Guest name(s)", "Check-in", "Price", "Commission amount"]
    assert [r[0] for r in t.rows] == ["123", "124"]
    assert t.total_rows == [["Total", None, None, "1.734,50", "260,18"]]
    assert t.preamble[0] == ["Reservation statement"]


@pytest.mark.parametrize(
    ("data", "encoding", "delimiter"),
    [
        ("﻿a,b\n1,2\n".encode("utf-8"), "utf-8-sig", ","),
        ("a\tb\n1\t2\n".encode("utf-16"), "utf-16", "\t"),  # Excel "Unicode text"
        ("a|b\n1|2\n".encode("utf-8"), "utf-8", "|"),
        ("الاسم,المبلغ\nعلي,5\n".encode("cp1256"), "cp1256", ","),  # Arabic Windows CSV
    ],
)
def test_encodings_and_delimiters(data, encoding, delimiter):
    t = sniff.sniff(data)
    assert (t.encoding, t.delimiter) == (encoding, delimiter)
    assert len(t.headers) == 2 and len(t.rows) == 1
    if encoding == "cp1256":
        assert t.headers == ["الاسم", "المبلغ"] and t.rows[0][0] == "علي"


def test_utf16_without_bom_is_recognised():
    text, enc = sniff.decode_text("a\tb\n1\t2\n".encode("utf-16-le"))
    assert enc == "utf-16-le" and text.startswith("a\tb")


def test_one_cell_title_is_never_a_header():
    rows = [["Agoda finance report"], [None], ["Booking ID", "Customer name", "Check-in"], ["1", "A", "2025-10-01"]]
    assert sniff.detect_header_row(rows) == 2
    assert sniff.detect_header_row([["1", "2"], ["3", "4"]]) is None


def test_headers_are_deduplicated_and_blank_ones_named():
    t = sniff.build_table([["Amount", "amount", None], ["1", "2", "3"]], file_format="csv")
    assert t.headers == ["Amount", "amount (2)", "column_3"]


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    import openpyxl

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_picks_the_sheet_with_most_rows_and_keeps_native_types():
    data = _xlsx({
        "Notes": [["Exported by Extranet"]],
        "Small": [["Date", "Rooms sold"], [datetime(2025, 10, 1), 40]],
        "Main": [["Booking ID", "Customer name", "Check-in"], ["1", "A", datetime(2025, 10, 1)],
                 ["2", "B", datetime(2025, 10, 2)]],
    })
    t = sniff.sniff(data, filename="agoda.xlsx")
    assert t.file_format == "xlsx" and t.sheet_name == "Main" and t.sheets == ["Notes", "Small", "Main"]
    assert t.rows[1][2] == datetime(2025, 10, 2)
    small = sniff.sniff(data, sheet_name="Small")
    assert small.headers == ["Date", "Rooms sold"] and small.rows == [[datetime(2025, 10, 1), 40]]
    with pytest.raises(ClefsError) as e:
        sniff.sniff(data, sheet_name="Nope")
    assert e.value.code == "unknown_sheet"


def test_formats_by_magic_bytes():
    assert sniff.detect_format(b"%PDF-1.7 ...", "x.csv") == "pdf"
    assert sniff.detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "x.csv") == "xls"
    assert sniff.detect_format(b"BEGIN:VCALENDAR\r\n", None) == "ics"
    assert sniff.detect_format(b"<?xml version='1.0'?><Invoice/>", None) == "xml"
    with pytest.raises(ClefsError) as e:
        sniff.sniff(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1garbage", filename="old.xls")
    assert e.value.code == "unreadable_file"
    with pytest.raises(ClefsError) as e:
        sniff.sniff(b"%PDF-1.4")
    assert e.value.code == "not_tabular"


def test_fingerprint_ignores_order_case_and_punctuation():
    a = sniff.header_fingerprint(["Book number", "Check-in", "Commission %"])
    b = sniff.header_fingerprint(["commission  %", "CHECK IN", "book number"])
    assert a == b
    assert sniff.header_fingerprint(["Book number"]) != a
    assert sniff.normalize_header("Commission %") == "commission pct"
    assert sniff.normalize_header("Booking #") == "booking no"
    assert sniff.normalize_header("  Guest name(s) ") == "guest name s"


def test_numeric_cells_and_totals():
    assert sniff.is_numeric_cell("AED 1,234.50") and sniff.is_numeric_cell("15%") and sniff.is_numeric_cell("(12)")
    assert sniff.is_numeric_cell("01/10/2025") and not sniff.is_numeric_cell("Jane")
    assert sniff.is_total_row(["Grand Total", None]) and sniff.is_total_row([None, "الإجمالي"])
    assert not sniff.is_total_row(["Totally Resorts", "1"])
    assert sniff.number_or_none("1.234,50") == sniff.number_or_none("1,234.50")
