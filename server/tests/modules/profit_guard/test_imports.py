"""Profit Guard commit handlers through the real core/ingest pipeline:
statements (alias mapping, matching to PMS stays, idempotent re-import,
corrected re-upload), credit notes, payouts and bank credits, virtual cards,
and contract PDFs extracted into a cited draft."""

from __future__ import annotations

from datetime import date

import pytest

from clefs import files
from clefs.core import stays
from clefs.core.ingest import batches, commit
from clefs.modules.profit_guard import imports, repo

STATEMENT = (
    "Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission %,Commission amount,Currency\n"
    "4000012345,AL MANSOURI/AHMED,01/10/2026,03/10/2026,ok,1000.00,15%,150.00,AED\n"
    "4000012346,Omar Ali,02/10/2026,04/10/2026,no_show,500.00,15%,75.00,AED\n"
    "4000012345,AL MANSOURI/AHMED,01/10/2026,03/10/2026,ok,100.00,15%,15.00,AED\n"
    "Total,,,,,1600.00,,240.00,\n"
)


@pytest.fixture(autouse=True)
def handlers():
    """core's own tests unregister OTA_STATEMENT in a fixture; make sure the
    module's handlers are the live ones whatever ran before."""
    imports.register()
    yield


def put(cur, slug, text, name="stmt.csv", ctype="text/csv"):
    data = text.encode() if isinstance(text, str) else text
    return files.put_file(cur, slug, data, filename=name, content_type=ctype, purpose="ingest.upload").id


def upload(cur, slug, text, **kw):
    name = kw.pop("name", "stmt.csv")
    b = batches.create_batch(cur, slug, file_id=put(cur, slug, text, name=name), **kw)
    assert b["status"] == "REVIEW", (b["status"], b.get("errors"))
    return b


def reservation(cur, slug, pms_id, *, name, check_in, check_out, ota_id=None, channel="BOOKING", status="CONFIRMED"):
    return stays.upsert_reservation(cur, slug, {
        "pms_res_id": pms_id, "channel_code": channel, "ota_res_id": ota_id, "guest_name": name,
        "check_in": check_in, "check_out": check_out, "status": status, "gross_minor": 100000, "currency": "AED",
    })["id"]


def test_statement_commit_writes_lines_and_matches_stays(cur, hotel_uae):
    s = hotel_uae.slug
    r1 = reservation(cur, s, "P1", name="Ahmed Al Mansouri", check_in=date(2026, 10, 1), check_out=date(2026, 10, 3),
                     ota_id="4000012345")
    r2 = reservation(cur, s, "P2", name="Omar Ali", check_in=date(2026, 10, 2), check_out=date(2026, 10, 4),
                     status="NO_SHOW")
    b = upload(cur, s, STATEMENT, source_code="booking_res_statement")
    done = commit.commit_batch(cur, s, b["id"])
    res = done["commit_result"]
    assert res["lines"] == 3 and res["commission_minor"] == 24000 and res["ota_code"] == "BOOKING"
    st = repo.get_statement(cur, s, res["statement_id"])
    assert st["doc_kind"] == "RES_STATEMENT" and st["total_minor"] == 24000 and st["line_count"] == 3
    by_key = {ln["line_key"]: ln for ln in st["lines"]}
    # A modified booking's second line is its own row (same id, occurrence 2).
    assert set(by_key) == {"4000012345#1", "4000012346#1", "4000012345#2"}
    first = by_key["4000012345#1"]
    assert first["reservation_id"] == r1 and first["match_status"] == "LINKED"
    assert first["commission_rate"] is not None and str(first["commission_rate"]).startswith("0.15")
    assert first["room_nights"] == 2 and first["currency"] == "AED" and str(first["fx_rate"]) == "1.00000000"
    # Fuzzy: "Omar Ali" with no OTA id on the PMS side still links by name, date, nights.
    assert by_key["4000012346#1"]["line_status"] == "NO_SHOW" and by_key["4000012346#1"]["reservation_id"] == r2
    assert stays.match_for_line(cur, s, first["id"])["method"] == "EXACT"


def test_reimport_is_idempotent_and_a_corrected_file_supersedes(cur, hotel_uae):
    s = hotel_uae.slug
    b1 = upload(cur, s, STATEMENT, source_code="booking_res_statement")
    r1 = commit.commit_batch(cur, s, b1["id"])["commit_result"]
    # The corrected file keeps the period (2026-10-01..04): the OTA dropped the
    # duplicate line and cut the no-show commission.
    corrected = STATEMENT.replace("4000012345,AL MANSOURI/AHMED,01/10/2026,03/10/2026,ok,100.00,15%,15.00,AED\n", "")
    corrected = corrected.replace("500.00,15%,75.00", "500.00,15%,50.00").replace("1600.00,,240.00", "1500.00,,200.00")
    b2 = upload(cur, s, corrected, source_code="booking_res_statement", name="stmt-v2.csv")
    assert b2["supersedes_batch_id"] == b1["id"]
    r2 = commit.commit_batch(cur, s, b2["id"])["commit_result"]
    # Same period -> same derived document: the lines were replaced, not appended.
    assert r2["statement_id"] == r1["statement_id"] and r2["lines"] == 2
    st = repo.get_statement(cur, s, r2["statement_id"])
    assert {ln["line_key"] for ln in st["lines"]} == {"4000012345#1", "4000012346#1"}
    assert st["total_minor"] == 20000 and batches.get_batch(cur, s, b1["id"])["status"] == "SUPERSEDED"
    cur.execute("SELECT COUNT(*) AS n FROM pfg_ota_statement_lines WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 2


def test_statement_needs_an_ota(cur, hotel_uae):
    from clefs.core.errors import ClefsError

    batch = {"id": 1, "hotel_slug": hotel_uae.slug, "kind": "OTA_STATEMENT", "ota_code": None}
    with pytest.raises(ClefsError) as exc:
        imports.commit_statement(cur, batch, [{"ota_res_id": "1", "_row_no": 1}])
    assert exc.value.code == "ota_required"


# ─── Credit notes, payouts, bank, cards, contracts ───

CREDIT_NOTE = (
    "Credit note number,Invoice number,Reservation number,Amount,Currency\n"
    "CN-77,INV-2026-10,4000012346,75.00,AED\n"
    "CN-78,INV-2026-10,4000012347,20.00,AED\n"
)
PAYOUT = (
    "Payout ID,Payout date,Reservation number,Gross amount,Commission,Payments service fee,Payable amount,Type,Currency,"
    "Bank transfer reference\n"
    "PO-1001,05/10/2026,4000012345,1000.00,150.00,11.00,839.00,reservation,AED,BTR883311\n"
    "PO-1001,05/10/2026,4000012350,-200.00,-30.00,0,-170.00,refund,AED,BTR883311\n"
    "PO-1002,12/10/2026,4000012351,500.00,75.00,5.50,419.50,reservation,AED,\n"
)
BANK = (
    "Value date,Amount,Currency,Reference,Description\n"
    "06/10/2026,669.00,AED,TRF BTR883311,Booking.com payout\n"
    "14/10/2026,419.50,AED,INWARD 99812,Booking.com BV\n"
    "15/10/2026,-50.00,AED,CHG,bank charge\n"
)
VCC = (
    "VCC ID,Booking number,Activation date,Expiration date,Initial amount,Current balance,Currency,Status\n"
    "VC-1,4000012345,01/10/2026,01/12/2027,1000.00,1000.00,AED,active\n"
    "VC-2,4000012399,01/10/2026,01/12/2026,500.00,200.00,AED,\n"
)


def test_credit_note_nets_off_commission(cur, hotel_uae):
    s = hotel_uae.slug
    b = upload(cur, s, CREDIT_NOTE, kind="CREDIT_NOTE", ota_code="BOOKING", name="cn.csv")
    res = commit.commit_batch(cur, s, b["id"])["commit_result"]
    notes = {n["doc_no"]: n for n in res["credit_notes"]}
    assert set(notes) == {"CN-77", "CN-78"} and notes["CN-77"]["credited_minor"] == 7500
    st = repo.get_statement(cur, s, notes["CN-77"]["statement_id"])
    assert st["doc_kind"] == "CREDIT_NOTE" and st["related_doc_no"] == "INV-2026-10"
    assert st["lines"][0]["commission_minor"] == -7500 and st["lines"][0]["line_status"] == "ADJUSTMENT"


def test_payouts_group_by_reference_and_link_bank_credits(cur, hotel_uae):
    s = hotel_uae.slug
    b = upload(cur, s, PAYOUT, source_code="booking_payouts", name="payout.csv")
    res = commit.commit_batch(cur, s, b["id"])["commit_result"]
    pays = {p["payout_ref"]: p for p in res["payouts"]}
    assert pays["PO-1001"]["amount_minor"] == 66900 and pays["PO-1002"]["amount_minor"] == 41950
    po = repo.get_payout(cur, s, pays["PO-1001"]["payout_id"])
    assert [ln["line_kind"] for ln in po["lines"]] == ["BOOKING", "REFUND"]
    assert po["bank_transfer_reference"] == "BTR883311" and po["payout_date"] == date(2026, 10, 5)
    bank = upload(cur, s, BANK, kind="BANK", name="bank.csv")
    out = commit.commit_batch(cur, s, bank["id"])["commit_result"]
    # Debits are skipped; one credit links by reference, the other by amount +- 3 days.
    assert out["credits"] == 2 and out["skipped_debits"] == 1 and out["bank_links"] == 2
    credits = {c["reference"]: c for c in repo.list_bank_credits(cur, s)}
    assert credits["TRF BTR883311"]["matched_payout_id"] == pays["PO-1001"]["payout_id"]
    assert credits["INWARD 99812"]["matched_payout_id"] == pays["PO-1002"]["payout_id"]
    assert credits["INWARD 99812"]["hotel_amount_minor"] == 41950
    # The same bank export again adds nothing.
    again = commit.commit_batch(cur, s, upload(cur, s, BANK + "\n", kind="BANK", name="bank2.csv")["id"])
    assert again["commit_result"]["credits"] == 2 and len(repo.list_bank_credits(cur, s)) == 2


def test_vcc_report_sets_deadlines_from_the_stay(cur, hotel_uae):
    s = hotel_uae.slug
    reservation(cur, s, "P1", name="Ahmed Al Mansouri", check_in=date(2026, 10, 1), check_out=date(2026, 10, 3),
                ota_id="4000012345")
    b = upload(cur, s, VCC, kind="VCC_REPORT", ota_code="BOOKING", name="vcc.csv")
    res = commit.commit_batch(cur, s, b["id"])["commit_result"]
    assert res["cards"] == 2
    cards = {c["vcc_id"]: c for c in repo.list_vcc(cur, s)}
    # Matched: Booking's 365 days after check-out beats the card expiry.
    assert cards["VC-1"]["charge_deadline"] == date(2027, 10, 3)
    assert cards["VC-1"]["deadline_basis"] == "charge_within_days_after_checkout"
    assert cards["VC-1"]["status"] == "AVAILABLE" and cards["VC-1"]["reservation_id"] is not None
    # Unmatched: the card's own expiry, status derived from the balance.
    assert cards["VC-2"]["charge_deadline"] == date(2026, 12, 1) and cards["VC-2"]["status"] == "PARTIALLY_CHARGED"
    charged = repo.mark_vcc_charged(cur, s, cards["VC-2"]["id"])
    assert charged["status"] == "FULLY_CHARGED" and charged["current_balance_minor"] == 0
    assert [c["vcc_id"] for c in repo.list_vcc(cur, s, open_only=True)] == ["VC-1"]


def test_contract_pdf_commit_creates_a_cited_draft(cur, hotel_uae):
    from clefs.modules.profit_guard import contracts
    from tests.modules.profit_guard.test_contracts import extraction

    s = hotel_uae.slug
    batch = {"id": None, "hotel_slug": s, "kind": "CONTRACT_PDF", "ota_code": "BOOKING", "file_id": None}
    rows = [{"clause_ref": "2.3.2(ii)", "text": "Commission on room price incl. VAT", "page": 1, "_row_no": 1},
            {"clause_ref": "1.1", "text": "Commission: 15%", "page": 1, "_row_no": 2}]
    from clefs import ai

    fake = ai.set_backend(ai.FakeBackend(extraction(clauses=[
        {"ref": "GDT v2601 2.3.2(ii)", "text": "Commission on room price incl. VAT", "page": 1,
         "field": "commission_base_rule"},
        {"ref": "GDT v2601 1.1", "text": "Commission: 15%", "page": 1, "field": "base_commission_pct"}])))
    try:
        res = imports.commit_contract_pdf(cur, batch, rows)
    finally:
        ai.reset_backend()
    assert res["status"] == "DRAFT" and res["review_flags"] == []
    assert "2.3.2(ii) Commission on room price" in fake.calls[0].input
    assert contracts.get_contract(cur, s, res["contract_id"])["extracted_by"] == "LLM"


def test_every_kind_is_registered_and_sandbox_batches_are_refused(cur):
    from clefs.core.errors import ClefsError
    from clefs.core.ingest import commit_handler, registered_handlers

    names = registered_handlers()
    assert {k: names[k].rsplit(".", 1)[-1] for k in imports.KINDS} == {
        "OTA_STATEMENT": "commit_statement", "CREDIT_NOTE": "commit_credit_note", "OTA_PAYOUT": "commit_payout",
        "VCC_REPORT": "commit_vcc", "BANK": "commit_bank", "CONTRACT_PDF": "commit_contract_pdf"}
    with pytest.raises(ClefsError) as exc:
        commit_handler("OTA_STATEMENT")(cur, {"id": 1, "hotel_slug": None, "kind": "OTA_STATEMENT"}, [])
    assert exc.value.code == "sandbox_batch"
