"""The recovery ledger (02 spec 3.5): status moves, auto-verification from
credit notes, corrected invoices and payouts (95% rule, partial credits,
no double counting), the 45-day timeout and the pilot scorecard (3.4)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.profit_guard import ledger, repo
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def finding(cur, slug, ota_id="4000000020", *, amount=15750, detector="L3", doc="INV-1", klass="RECOVERABLE",
            status=None, deadline=None, line_id=None, ota="BOOKING"):
    """One stored finding about one booking (bypasses the detectors)."""
    row, _ = repo.upsert_finding(cur, slug, {
        "detector": detector, "fingerprint": f"{detector}|{ota}|{ota_id}|{doc}", "ota_code": ota,
        "subject_ref": f"doc:{doc}|{ota_id}", "class": klass, "amount_minor": amount, "currency": "AED",
        "confidence": "0.9", "calc": [{"label": "Commission", "formula": "pct x base", "inputs": {},
                                       "result_minor": amount}],
        "evidence": [], "deadline_at": deadline, "statement_line_id": line_id}, run_id="t")
    if status:
        cur.execute("UPDATE pfg_findings SET status = %s WHERE id = %s", (status, row["id"]))
    return row["id"]


def credit_note(cur, slug, ota_id="4000000020", amount=15750, *, doc_no="CN-1", ota="BOOKING"):
    return pg.statement(cur, slug, [{"ota_res_id": ota_id, "commission_minor": -amount, "line_status": "ADJUSTMENT"}],
                        doc_no=doc_no, doc_kind="CREDIT_NOTE", ota=ota)


def get(cur, fid):
    cur.execute("SELECT * FROM pfg_findings WHERE id = %s", (fid,))
    return dict(cur.fetchone())


def events(cur, fid):
    cur.execute("SELECT * FROM pfg_finding_events WHERE finding_id = %s ORDER BY id", (fid,))
    return [dict(r) for r in cur.fetchall()]


def signal(cur, slug, key):
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (slug, key))
    r = cur.fetchone()
    return dict(r) if r else None


# ─── Transitions ───


def test_status_flow_and_illegal_moves(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s)
    assert ledger.transition(cur, s, fid, "ACCEPTED", actor="owner")["status"] == "ACCEPTED"
    with pytest.raises(ClefsError) as e:
        ledger.transition(cur, s, fid, "REJECTED", actor="owner")
    assert e.value.code == "illegal_transition"
    with pytest.raises(ClefsError) as e:
        ledger.dismiss(cur, s, fid, reason="NOPE")
    assert e.value.code == "dismiss_reason_required"
    row = ledger.dismiss(cur, s, fid, reason="too_small", note="AED 5")
    assert row["status"] == "DISMISSED" and row["dismiss_reason"] == "TOO_SMALL"
    back = ledger.transition(cur, s, fid, "DETECTED", actor="owner")
    assert back["dismiss_reason"] is None
    assert [e["to_status"] for e in events(cur, fid)] == ["DETECTED", "ACCEPTED", "DISMISSED", "DETECTED"]


def test_manual_recovery_logs_a_credit_event(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s)
    row = ledger.transition(cur, s, fid, "RECOVERED", actor="owner", recovered_minor=15000)
    assert row["status"] == "RECOVERED" and row["recovered_minor"] == 15000 and row["verified_by"] == "MANUAL"
    credit = [e for e in events(cur, fid) if e["channel"] == "credit"]
    assert len(credit) == 1 and credit[0]["amount_minor"] == 15000
    assert signal(cur, s, f"pg:recovered:BOOKING:{date(2026, 10, 2).isoformat()}")["impact_minor"] == 15000


# ─── Auto-verification ───


def test_full_credit_note_recovers_and_is_idempotent(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s, status="DISPUTED")
    credit_note(cur, s)
    out = ledger.auto_verify(cur, s)
    assert out["recovered"] == [fid] and out["credited_minor"] == 15750
    row = get(cur, fid)
    assert (row["status"], row["recovered_minor"], row["verified_by"]) == ("RECOVERED", 15750, "STATEMENT")
    ev = [e for e in events(cur, fid) if e["channel"] == "credit"][0]
    assert ev["evidence"][0]["ref"]["table"] == "pfg_ota_statement_lines"
    sig = signal(cur, s, "pg:recovered:BOOKING:2026-10-02")
    assert sig["section"] == "verified" and sig["impact_minor"] == 15750
    again = ledger.auto_verify(cur, s)
    assert again["credited_minor"] == 0 and again["recovered"] == []


def test_partial_credit_keeps_state_then_completes(cur, hotel_uae):
    # An unfiled claim is never moved to DISPUTED by a partial credit: no
    # dispute went out, and its alert must stay up for the rest.
    s = hotel_uae.slug
    fid = finding(cur, s, status="ACCEPTED")
    credit_note(cur, s, amount=7000)
    out = ledger.auto_verify(cur, s)
    assert out["partial"] == [fid]
    row = get(cur, fid)
    assert (row["status"], row["recovered_minor"]) == ("ACCEPTED", 7000)
    assert "Partial credit" in events(cur, fid)[-1]["note"]
    credit_note(cur, s, amount=8750, doc_no="CN-2")
    ledger.auto_verify(cur, s)
    assert (get(cur, fid)["status"], get(cur, fid)["recovered_minor"]) == ("RECOVERED", 15750)


def test_ninety_five_percent_rule_boundary(cur, hotel_uae):
    s = hotel_uae.slug
    a = finding(cur, s, "4000000031", amount=10000, status="DISPUTED")
    b = finding(cur, s, "4000000032", amount=10000, status="DISPUTED")
    credit_note(cur, s, "4000000031", 9500, doc_no="CN-A")
    credit_note(cur, s, "4000000032", 9499, doc_no="CN-B")
    ledger.auto_verify(cur, s)
    assert get(cur, a)["status"] == "RECOVERED"
    assert get(cur, b)["status"] == "DISPUTED" and get(cur, b)["recovered_minor"] == 9499


def test_credit_in_note_and_payout_counts_once(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s, status="DISPUTED")
    credit_note(cur, s)
    pg.payout(cur, s, "PO-9", [{"ota_res_id": "4000000020", "commission_minor": -15750, "partner_payout_minor": 15750,
                                "line_kind": "ADJUSTMENT"}])
    ledger.auto_verify(cur, s)
    assert get(cur, fid)["recovered_minor"] == 15750


def test_payout_adjustment_alone_verifies(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s, status="DISPUTED")
    pg.payout(cur, s, "PO-9", [{"ota_res_id": "4000000020", "commission_minor": -15750, "partner_payout_minor": 15750,
                                "line_kind": "ADJUSTMENT"}])
    ledger.auto_verify(cur, s)
    row = get(cur, fid)
    assert row["status"] == "RECOVERED" and row["verified_by"] == "PAYOUT"


def test_credit_spreads_over_two_findings_of_one_booking(cur, hotel_uae):
    s = hotel_uae.slug
    first = finding(cur, s, amount=5000, detector="L1", status="DISPUTED", deadline=NOW + timedelta(days=1))
    second = finding(cur, s, amount=4000, detector="L2", status="DISPUTED", deadline=NOW + timedelta(days=5))
    credit_note(cur, s, amount=6000)
    ledger.auto_verify(cur, s)
    assert get(cur, first)["status"] == "RECOVERED" and get(cur, first)["recovered_minor"] == 5000
    assert get(cur, second)["recovered_minor"] == 1000 and get(cur, second)["status"] == "DISPUTED"


def test_other_booking_and_other_ota_never_credit(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s, status="DISPUTED")
    credit_note(cur, s, "4000000099")
    credit_note(cur, s, "4000000020", doc_no="AG-CN", ota="AGODA")
    ledger.auto_verify(cur, s)
    assert get(cur, fid)["status"] == "DISPUTED" and get(cur, fid)["recovered_minor"] == 0


def test_corrected_invoice_counts_as_credit_after_submission(cur, hotel_uae):
    s = hotel_uae.slug
    [line] = pg.statement(cur, s, [{"ota_res_id": "4000000020", "commission_minor": 15750}])
    fid = finding(cur, s, status="ACCEPTED", line_id=line)
    ledger.transition(cur, s, fid, "DISPUTED", actor="owner", evidence=[
        {"kind": "calc", "label": ledger.INVOICED_LABEL, "value": 15750}])
    cur.execute("UPDATE pfg_ota_statement_lines SET commission_minor = 0 WHERE id = %s", (line,))
    ledger.auto_verify(cur, s)
    row = get(cur, fid)
    assert row["status"] == "RECOVERED"
    assert any(e.get("label") == "invoice corrected" for e in events(cur, fid)[-1]["evidence"])


# ─── Only commission claims take commission credit ───


@pytest.mark.parametrize("other", ["L8", "L9", "L10", "L13"])
def test_vcc_payout_and_chargeback_findings_never_take_a_commission_credit(cur, hotel_uae, other):
    # The card (or payout, or chargeback) finding has the earlier deadline:
    # before the fix it took the L2 credit, left DETECTED as 'Partial
    # credit' and the 'Charge the card' alert went away.
    s = hotel_uae.slug
    card = finding(cur, s, amount=100000, detector=other, doc="VCC-1", deadline=NOW + timedelta(hours=6))
    l2 = finding(cur, s, amount=5000, detector="L2", status="DISPUTED", deadline=NOW + timedelta(days=10))
    credit_note(cur, s, amount=5000)
    out = ledger.auto_verify(cur, s)
    assert out["recovered"] == [l2] and out["credited_minor"] == 5000
    row = get(cur, card)
    assert (row["status"], row["recovered_minor"]) == ("DETECTED", 0)
    assert [e["channel"] for e in events(cur, card)] == [None]


# ─── One credit accounting (never netted and recovered) ───


def test_credit_netted_at_detection_is_not_counted_as_recovery(cur, hotel_uae):
    # The OTA already credited 3000 of a 5000 overcharge when the finding
    # was first stored: the claim is 2000 and that 3000 is its baseline.
    s = hotel_uae.slug
    credit_note(cur, s, amount=3000)
    fid = finding(cur, s, amount=2000, detector="L2", status="DISPUTED")
    assert get(cur, fid)["credit_baseline_minor"] == 3000
    out = ledger.auto_verify(cur, s)
    assert out["credited_minor"] == 0 and get(cur, fid)["recovered_minor"] == 0
    credit_note(cur, s, amount=2000, doc_no="CN-2")                  # the rest comes back later
    ledger.auto_verify(cur, s)
    row = get(cur, fid)
    assert (row["status"], row["recovered_minor"]) == ("RECOVERED", 2000)


def rerun(cur, slug, *, amount, credited, detector="L2"):
    """A detector re-run of the same finding: the draft nets the credit it
    sees now (data.credited) and says so."""
    row, created = repo.upsert_finding(cur, slug, {
        "detector": detector, "fingerprint": f"{detector}|BOOKING|4000000020|INV-1", "ota_code": "BOOKING",
        "subject_ref": "doc:INV-1|4000000020", "class": "RECOVERABLE", "amount_minor": amount, "currency": "AED",
        "confidence": "0.9", "calc": [], "evidence": [], "credit_baseline_minor": credited}, run_id="t2")
    assert not created
    return row


def test_disputed_claim_keeps_its_amount_and_a_credit_is_recovery_once(cur, hotel_uae):
    # Case A: auto_verify first, then the detector re-run tried to shrink
    # the claim to 20; the row read amount 20 / recovered 30 and was stuck.
    s = hotel_uae.slug
    fid = finding(cur, s, amount=5000, detector="L2", status="DISPUTED")
    credit_note(cur, s, amount=3000)
    ledger.auto_verify(cur, s)
    rerun(cur, s, amount=2000, credited=3000)
    row = get(cur, fid)
    assert (row["status"], row["amount_minor"], row["recovered_minor"]) == ("DISPUTED", 5000, 3000)
    credit_note(cur, s, amount=2000, doc_no="CN-2")
    ledger.auto_verify(cur, s)
    assert (get(cur, fid)["status"], get(cur, fid)["recovered_minor"]) == ("RECOVERED", 5000)


def test_detector_first_never_turns_a_partial_credit_into_full_recovery(cur, hotel_uae):
    # Case B: the re-run used to rewrite the claim to 20, then auto_verify
    # took min(20, 30) and marked a 50 claim RECOVERED with 20 still owed.
    s = hotel_uae.slug
    fid = finding(cur, s, amount=5000, detector="L2", status="DISPUTED")
    credit_note(cur, s, amount=3000)
    rerun(cur, s, amount=2000, credited=3000)
    assert get(cur, fid)["amount_minor"] == 5000
    ledger.auto_verify(cur, s)
    row = get(cur, fid)
    assert (row["status"], row["amount_minor"], row["recovered_minor"]) == ("DISPUTED", 5000, 3000)


def test_unfiled_claim_rerun_keeps_the_gross_claim_whatever_the_order(cur, hotel_uae):
    s = hotel_uae.slug
    fid = finding(cur, s, amount=5000, detector="L2")
    credit_note(cur, s, amount=3000)
    rerun(cur, s, amount=2000, credited=3000)                         # detector before the ledger
    row = get(cur, fid)
    assert (row["amount_minor"], row["credit_baseline_minor"]) == (5000, 0)
    ledger.auto_verify(cur, s)
    row = get(cur, fid)
    assert (row["status"], row["recovered_minor"]) == ("DETECTED", 3000)
    rerun(cur, s, amount=2000, credited=3000)                         # frozen once money came back
    assert get(cur, fid)["amount_minor"] == 5000


def test_rerun_refreshes_an_unfiled_claim_on_corrected_data(cur, hotel_uae):
    # No credit involved: a corrected statement still refreshes the claim.
    s = hotel_uae.slug
    fid = finding(cur, s, amount=5000, detector="L2")
    rerun(cur, s, amount=4200, credited=0)
    assert get(cur, fid)["amount_minor"] == 4200


# ─── Timeout ───


def test_forty_five_days_without_credit_and_a_later_invoice(cur, hotel_uae, clock):
    s = hotel_uae.slug
    fid = finding(cur, s, status="ACCEPTED")
    ledger.transition(cur, s, fid, "DISPUTED", actor="owner")
    clock.freeze(NOW + timedelta(days=46))
    assert ledger.auto_verify(cur, s)["unresolved"] == []            # no later invoice yet: keep waiting
    pg.statement(cur, s, [{"ota_res_id": "4000000077", "commission_minor": 100}], doc_no="INV-2",
                 doc_date=date(2026, 11, 5))
    out = ledger.auto_verify(cur, s)
    assert out["unresolved"] == [fid] and get(cur, fid)["status"] == "UNRESOLVED"
    credit_note(cur, s)                                               # a late credit still counts
    ledger.auto_verify(cur, s)
    assert get(cur, fid)["status"] == "RECOVERED"


def test_forty_four_days_is_not_a_timeout(cur, hotel_uae, clock):
    s = hotel_uae.slug
    fid = finding(cur, s, status="ACCEPTED")
    ledger.transition(cur, s, fid, "DISPUTED", actor="owner")
    pg.statement(cur, s, [{"ota_res_id": "4000000077", "commission_minor": 100}], doc_no="INV-2",
                 doc_date=date(2026, 11, 5))
    clock.freeze(NOW + timedelta(days=44))
    assert ledger.auto_verify(cur, s)["unresolved"] == []


# ─── Reads and the pilot scorecard ───


def test_summary_funnel_and_deadlines(cur, hotel_uae):
    s = hotel_uae.slug
    finding(cur, s, "4000000041", amount=10000, deadline=NOW + timedelta(hours=30))
    r = finding(cur, s, "4000000042", amount=20000, status="DISPUTED")
    finding(cur, s, "4000000043", amount=999, klass="COMPLIANCE", detector="T1", doc="2026-09")
    credit_note(cur, s, "4000000042", 20000)
    ledger.auto_verify(cur, s)
    out = ledger.summary(cur, s)
    assert out["found_minor"] == 30000 and out["recovered_minor"] == 20000 and out["open_minor"] == 10000
    assert out["due_72h_count"] == 1 and out["compliance_open"] == 1
    assert out["funnel"]["RECOVERED"]["amount_minor"] == 20000
    dl = ledger.deadlines(cur, s, days=3)
    assert [d["kind"] for d in dl] == ["finding"] and dl[0]["hours_left"] == 30.0
    assert get(cur, r)["status"] == "RECOVERED"


def test_pilot_scorecard_count_and_amount(cur, hotel_uae):
    s = hotel_uae.slug
    for i, st in enumerate(("ACCEPTED", "DISPUTED")):
        finding(cur, s, f"40000001{i}", amount=100000, status=st)
    card = ledger.pilot_scorecard(cur, s)
    assert card["valid_findings"] == 2 and card["success"] is False and card["amount_threshold_minor"] is None
    cur.execute("INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) VALUES "
                "(%s, 'profit_guard', TRUE, %s)", (s, '{"subscription_minor_month": 50000, "pilot_months": 1}'))
    card = ledger.pilot_scorecard(cur, s)
    assert card["amount_threshold_minor"] == 150000 and card["criteria"]["amount"] is True and card["success"]
