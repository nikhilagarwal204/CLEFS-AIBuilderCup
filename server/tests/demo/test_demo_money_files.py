"""Demo seed, money part: the rendered documents read back through core/ingest's
sniffer and alias maps with no saved template (no database needed)."""

from __future__ import annotations

import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
from pypdf import PdfReader

from clefs.core.ingest import resolve_aliases, sniff_file
from clefs.core.ingest.stage import stage_table
from clefs.seed.demo_uae import money


def _line(i: int) -> dict:
    return {"ota_res_id": f"41234567{i:02d}", "guest": "Omar Haddad", "check_in": date(2026, 8, 3 + i),
            "check_out": date(2026, 8, 5 + i), "nights": 2, "rooms": 1, "status": "OK", "gross": 123456 + i,
            "base": 100000 + i, "pct": Decimal("15.000"), "commission": 15000, "country": "AE", "rate_plan": "BAR",
            "room_type": "Standard King Room", "programs": "GENIUS, MOBILE" if i else "",
            "booked_on": date(2026, 7, 1), "collect_model": "HOTEL_COLLECT", "remarks": ""}


@pytest.mark.parametrize("variant,encoding,delimiter", [(1, "utf-8", ","), (2, "utf-16", "\t"), (3, "utf-8-sig", ";")])
def test_booking_variants_map_and_stage_without_a_template(variant, encoding, delimiter):
    data, filename, _ctype = money.render_booking([_line(0), _line(1)], variant=variant, doc_no="BDC-202608-40136")
    table = sniff_file(data, filename=filename)
    assert table.encoding.lower().replace("_", "-").startswith(encoding.split("-sig")[0])
    assert table.delimiter == delimiter and len(table.rows) == 2 and table.total_rows
    mapping, transforms = resolve_aliases(table.headers, "OTA_STATEMENT", "booking_res_statement")
    for field in ("ota_res_id", "check_in", "check_out", "gross_minor", "commissionable_minor", "commission_pct",
                  "commission_minor", "programs", "doc_no", "room_type", "booker_country"):
        assert field in mapping, (variant, field)
    rows = stage_table(table, "OTA_STATEMENT", mapping, transforms, currency="AED")
    assert [r.status for r in rows] == ["OK", "OK"]
    first = rows[0].canonical
    assert (first["gross_minor"], first["commissionable_minor"], first["commission_minor"]) == (123456, 100000, 15000)
    assert Decimal(first["commission_pct"]) == Decimal("0.15") and first["check_in"] == "2026-08-03"


def test_agoda_xlsx_and_expedia_pdf_read_back():
    data, filename, _ctype = money.render_agoda([_line(0)], doc_no="AGD-202608-7731", month=date(2026, 8, 1))
    assert data == money.render_agoda([_line(0)], doc_no="AGD-202608-7731", month=date(2026, 8, 1))[0]
    assert b"2026-09-05T08:00:00Z" in zipfile.ZipFile(io.BytesIO(data)).read("docProps/core.xml")  # no save time
    table = sniff_file(data, filename=filename)
    mapping, _t = resolve_aliases(table.headers, "OTA_STATEMENT", "agoda_finance")
    assert table.header_row == 2 and {"ota_res_id", "gross_minor", "commission_minor"} <= set(mapping)
    pdf, _name, _ctype = money.render_expedia_pdf([_line(0), _line(1)], doc_no="EXP-HC-202608-0912",
                                                  month=date(2026, 8, 1), hotel_name="Al Rigga Crest Hotel")
    ext = money.expedia_extraction([_line(0), _line(1)], doc_no="EXP-HC-202608-0912", month=date(2026, 8, 1),
                                   pdf_bytes=pdf)
    text = PdfReader(io.BytesIO(pdf)).pages[0].extract_text()
    assert "4123456700" in text and "Total commission due: AED 300.00" in text
    assert ext["printed_total"] == "300.00" and {ln["page"] for ln in ext["lines"]} == {1}


def test_placeholder_screenshot_is_a_png():
    png = money.placeholder_png("Agoda 2026-10-07")
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in png[-12:]
