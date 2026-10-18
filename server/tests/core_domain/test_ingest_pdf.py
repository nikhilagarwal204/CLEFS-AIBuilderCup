"""core/ingest PDFs: AI extraction with page references, the printed-total
gate (within AED 1), its audited override, scans and AI outages."""

from __future__ import annotations

import pytest

from tests.core_domain.conftest import swap_commit_handler
from fpdf import FPDF

from clefs import ai, files
from clefs.core.errors import ClefsError
from clefs.core.ingest import batches, commit, kinds, pdf


def _pdf(pages: list[list[str]]) -> bytes:
    doc = FPDF()
    doc.set_font("helvetica", size=10)
    for lines in pages:
        doc.add_page()
        for ln in lines:
            doc.cell(0, 8, ln, new_x="LMARGIN", new_y="NEXT")
    return bytes(doc.output())


STATEMENT_PDF = [
    ["Booking.com commission invoice 2025-10", "5001 Jane Doe 01/10/2025 1000.00 150.00"],
    ["5002 Omar Ali 02/10/2025 500.00 75.00", "Total commission AED 225.00"],
]


def _line(page, ref, res, ci, gross, comm):
    return {"page": page, "line_ref": ref, "cells": [
        {"field": "ota_res_id", "value": res}, {"field": "check_in", "value": ci},
        {"field": "gross_minor", "value": gross}, {"field": "commission_minor", "value": comm},
        {"field": "nonsense", "value": "x"}]}


def _extraction(total="225.00", comm2="75.00"):
    return {"lines": [_line(1, "5001 Jane Doe", "5001", "01/10/2025", "1000.00", "150.00"),
                      _line(2, "5002 Omar Ali", "5002", "02/10/2025", "500.00", comm2)],
            "printed_total": total, "currency": "AED", "doc_no": "INV-2025-10", "period_start": "2025-10-01",
            "period_end": "2025-10-31"}


@pytest.fixture
def owner(cur, make_user, hotel_uae):
    from clefs.auth.deps import Principal, resolve_ctx

    user = make_user("owner", hotel_uae.slug)
    return resolve_ctx(Principal(kind="user", user_id=user["id"], label="o", is_clefs_admin=False,
                                 session_kind="web"), hotel_uae.slug, cur=cur)


def _upload(cur, hotel, data: bytes, **kw):
    ref = files.put_file(cur, hotel, data, filename="statement.pdf", content_type="application/pdf",
                         purpose="ingest.upload")
    return batches.create_batch(cur, hotel, file_id=ref.id, kind="OTA_STATEMENT", ota_code="BOOKING", **kw)


def test_pdf_lines_carry_page_refs_and_pass_the_gate(cur, hotel_uae, fake_ai):
    fake_ai.push(_extraction())
    b = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF))
    assert b["status"] == "REVIEW" and b["file_format"] == "pdf" and b["mapping_source"] == "ai"
    assert (b["printed_total_minor"], b["parsed_total_minor"], b["gate_status"]) == (22500, 22500, "ok")
    assert b["doc_no"] == "INV-2025-10" and str(b["period_end"]) == "2025-10-31" and b["stats"]["pages"] == 2
    rows = batches.list_rows(cur, b["id"])
    assert [(r["page"], r["line_ref"]) for r in rows] == [(1, "5001 Jane Doe"), (2, "5002 Omar Ali")]
    assert "nonsense" not in rows[0]["raw"] and rows[1]["canonical"]["commission_minor"] == 7500
    [call] = fake_ai.calls
    assert call.task == pdf.AI_TASK and "--- page 2 ---" in call.input and call.store is False


def test_one_aed_tolerance_then_the_gate_blocks(cur, hotel_uae, fake_ai, owner):
    fake_ai.push(_extraction(total="225.99"))
    ok = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF))
    assert ok["gate_status"] == "ok"  # 0.99 off is within AED 1
    fake_ai.push(_extraction(total="227.50"))
    bad = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF + [["p3"]]))
    assert bad["gate_status"] == "mismatch"
    with swap_commit_handler("OTA_STATEMENT", lambda cur, batch, rows: {"n": len(rows)}):
        with pytest.raises(ClefsError) as e:
            commit.commit_batch(cur, hotel_uae.slug, bad["id"], actor=owner)
        assert e.value.code == "gate_blocked"
        commit.override_gate(cur, hotel_uae.slug, bad["id"], actor=owner, reason="Credit note pending")
        assert commit.commit_batch(cur, hotel_uae.slug, bad["id"], actor=owner)["status"] == "COMMITTED"


def test_a_pdf_with_no_printed_total_needs_an_override(cur, hotel_uae, fake_ai):
    fake_ai.push(_extraction(total=""))
    b = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF))
    assert b["gate_status"] == "missing" and b["printed_total_minor"] is None


def test_a_page_outside_the_document_warns(cur, hotel_uae, fake_ai):
    ext = _extraction()
    ext["lines"][1]["page"] = 7
    fake_ai.push(ext)
    b = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF))
    rows = batches.list_rows(cur, b["id"])
    assert rows[1]["status"] == "WARN" and "page 7 is not in the document" in rows[1]["messages"]


def test_scans_and_ai_outages_fail_with_a_reason(cur, hotel_uae, fake_ai):
    scan = _upload(cur, hotel_uae.slug, _pdf([[]]))
    assert scan["status"] == "FAILED" and scan["errors"][0]["code"] == "pdf_no_text"
    assert fake_ai.calls == []
    fake_ai.push(ai.AIError("model unavailable"))
    off = _upload(cur, hotel_uae.slug, _pdf(STATEMENT_PDF))
    assert off["status"] == "FAILED" and off["errors"][0]["code"] == "ai_error"
    assert batches.list_rows(cur, off["id"]) == []
    broken = files.put_file(cur, hotel_uae.slug, b"%PDF-1.4 not really", filename="b.pdf",
                            content_type="application/pdf", purpose="ingest.upload")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=broken.id, kind="OTA_STATEMENT")
    assert b["status"] == "FAILED" and b["errors"][0]["code"] == "unreadable_file"


def test_contract_pdfs_are_not_gated(cur, hotel_uae, fake_ai):
    fake_ai.push({"lines": [{"page": 1, "line_ref": "2.3.2", "cells": [
        {"field": "clause_ref", "value": "GDT 2.3.2(ii)"}, {"field": "text", "value": "Commission is 15%"},
        {"field": "page", "value": "1"}]}], "printed_total": "", "currency": "", "doc_no": "", "period_start": "",
        "period_end": ""})
    ref = files.put_file(cur, hotel_uae.slug, _pdf([["General Delivery Terms", "2.3.2 Commission is 15%"]]),
                         filename="gdt.pdf", content_type="application/pdf", purpose="ingest.upload")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=ref.id, kind="CONTRACT_PDF")
    assert b["status"] == "REVIEW" and b["gate_status"] == "n/a"
    assert batches.list_rows(cur, b["id"])[0]["canonical"]["clause_ref"] == "GDT 2.3.2(ii)"
