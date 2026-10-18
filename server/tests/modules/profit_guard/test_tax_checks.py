"""Tourism Dirham compliance (02 spec 3.3 T1-T7, TX1), the TD ledger, the
remittance reminder and the reconciliation export. Dubai 4-star: TD is
AED 15 per room-night, capped at 30 consecutive nights, outside VAT."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from clefs.core import tax
from clefs.modules.profit_guard import tax_checks as tc
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
SEP = date(2026, 9, 1)
TD = 1500


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def post(cur, slug, rid, code, amount, day, *, qty=1, reversal=False, n=[0]):
    n[0] += 1
    cur.execute(
        "INSERT INTO folio_charges (hotel_slug, reservation_id, posted_date, stay_date, code, qty, amount_minor, "
        "currency, is_reversal, pms_txn_id) VALUES (%s, %s, %s, %s, %s, %s, %s, 'AED', %s, %s)",
        (slug, rid, day, day, code, qty, amount, reversal, f"tx-{rid}-{n[0]}"),
    )


def stay(cur, slug, pms, *, nights=3, rooms=1, check_in=SEP, status="CHECKED_OUT", td=TD, **kw):
    rid = pg.reservation(cur, slug, pms, channel="DIRECT", check_in=check_in, nights=nights, status=status,
                         rooms_count=rooms, **kw)
    for i in range(nights):
        if td:
            post(cur, slug, rid, "TD", td, check_in + timedelta(days=i))
    return rid


def by_det(cur, slug, det):
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND detector = %s ORDER BY id", (slug, det))
    return [dict(r) for r in cur.fetchall()]


def run(cur, slug, months=(SEP,)):
    return tc.run_tax_checks(cur, slug, trigger="manual", months=list(months))


def test_rate_is_the_dubai_four_star_rule(cur, hotel_uae):
    assert tc.TaxDays(cur, hotel_uae.slug, SEP, SEP).td_rate_minor(SEP) == TD


def test_clean_stay_raises_nothing(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", rooms=2, td=2 * TD)            # one line for both rooms is fine (a multiple of the rate)
    out = run(cur, s)
    assert out["findings"] == 0


def test_t1_charged_per_reservation_not_per_room(cur, hotel_uae):
    s = hotel_uae.slug
    rid = stay(cur, s, "P1", rooms=2)                  # 2 rooms, TD posted once a night
    run(cur, s)
    [f] = by_det(cur, s, "T1")
    assert f["amount_minor"] == 3 * TD and f["class"] == "COMPLIANCE" and f["reservation_id"] == rid
    assert f["subject_ref"] == f"month:2026-09|res:{rid}"
    assert f["deadline_at"].astimezone(hotel_uae.tz).date() == date(2026, 10, 16)   # end of the 15th


def test_t2_night_31_across_a_back_to_back_chain(cur, hotel_uae):
    s = hotel_uae.slug
    first = stay(cur, s, "P1", nights=20, check_in=date(2026, 8, 5))                 # Aug 5-24
    second = stay(cur, s, "P2", nights=14, check_in=date(2026, 8, 25))               # Aug 25 - Sep 7
    cur.execute("UPDATE reservations SET stay_chain_id = %s WHERE id IN (%s, %s)", (first, first, second))
    run(cur, s)
    [f] = by_det(cur, s, "T2")
    assert f["reservation_id"] == second and f["amount_minor"] == 4 * TD            # Sep 4-7 are nights 31-34
    assert by_det(cur, s, "T5") == []


def test_t3_wrong_rate(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", td=1000)
    run(cur, s)
    [f] = by_det(cur, s, "T3")
    assert f["amount_minor"] == 3 * 500


def test_t5_duplicate_posting_is_owed_to_the_guest(cur, hotel_uae):
    s = hotel_uae.slug
    rid = stay(cur, s, "P1")
    post(cur, s, rid, "TD", TD, SEP)
    run(cur, s)
    [f] = by_det(cur, s, "T5")
    assert f["amount_minor"] == TD and f["class"] == "RECOVERABLE"
    assert by_det(cur, s, "T1") == []


def test_reversal_cancels_a_duplicate(cur, hotel_uae):
    s = hotel_uae.slug
    rid = stay(cur, s, "P1")
    post(cur, s, rid, "TD", TD, SEP)
    post(cur, s, rid, "TD", -TD, SEP, reversal=True)
    assert run(cur, s)["findings"] == 0


def test_t6_no_show_and_cancelled(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", status="NO_SHOW", nights=2)
    stay(cur, s, "P2", status="CANCELLED", nights=2)
    run(cur, s)
    [f] = by_det(cur, s, "T6")
    # base 0.95 x the Beta(4, 1) prior feedback (0.8) with no history yet
    assert f["amount_minor"] == 2 * TD and float(f["confidence"]) == pytest.approx(0.76)


def test_t4_vat_charged_on_the_td(cur, hotel_uae):
    s = hotel_uae.slug
    rid = stay(cur, s, "P1", nights=1)
    post(cur, s, rid, "ROOM", 35000, SEP)
    post(cur, s, rid, "SC", 3500, SEP)
    post(cur, s, rid, "MF", 2695, SEP)
    post(cur, s, rid, "VAT", 2060 + 75, SEP)         # 5% x (350 + 35 + 26.95) = 20.60, plus 5% x 15 on the TD
    run(cur, s)
    t4 = by_det(cur, s, "T4")
    assert len(t4) == 1 and t4[0]["amount_minor"] == 75


def test_t4_not_raised_for_correct_vat(cur, hotel_uae):
    s = hotel_uae.slug
    rid = stay(cur, s, "P2", nights=1)
    post(cur, s, rid, "ROOM", 35000, SEP)
    post(cur, s, rid, "SC", 3500, SEP)
    post(cur, s, rid, "MF", 2695, SEP)
    post(cur, s, rid, "VAT", 2060, SEP)
    run(cur, s)
    assert by_det(cur, s, "T4") == []


def test_tx1_only_when_the_owner_applied_the_exemption(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", nights=2)
    run(cur, s)
    assert by_det(cur, s, "TX1") == []
    tax.set_override(cur, s, "dxb_2026_exempt_td", apply=True)
    run(cur, s)
    [f] = by_det(cur, s, "TX1")
    assert f["amount_minor"] == 2 * TD


# ─── Ledger, T7, reminders, export ───


def test_ledger_rollup_and_remittance_mismatch(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", rooms=2, td=2 * TD)             # 6 unit-nights, AED 90
    row = tc.rollup_month(cur, s, SEP)
    assert (row["liable_room_nights"], row["td_expected_minor"], row["td_collected_minor"]) == (6, 9000, 9000)
    assert row["due_date"] == date(2026, 10, 15) and row["status"] == "OPEN"
    row = tc.record_remittance(cur, s, SEP, amount_minor=8950, remitted_on=date(2026, 10, 10))
    assert row["status"] == "REMITTED"                 # within AED 1
    row = tc.record_remittance(cur, s, SEP, amount_minor=8000, remitted_on=date(2026, 10, 10))
    assert row["status"] == "MISMATCH"
    tc.run_tax_checks(cur, s, trigger="deadline")
    [f] = by_det(cur, s, "T7")
    assert f["amount_minor"] == 1000
    tc.record_remittance(cur, s, SEP, amount_minor=9000, remitted_on=date(2026, 10, 11))
    tc.run_tax_checks(cur, s, trigger="deadline")
    [f] = by_det(cur, s, "T7")
    assert f["status"] == "DISMISSED" and f["dismiss_reason"] == "ALREADY_HANDLED"


def test_t7_unremitted_with_five_days_left(cur, hotel_uae, clock):
    s = hotel_uae.slug
    stay(cur, s, "P1")
    tc.rollup_month(cur, s, SEP)
    clock.freeze(datetime(2026, 10, 9, 6, 0, tzinfo=UTC))    # 6 days before the 15th
    tc.run_tax_checks(cur, s, trigger="deadline")
    assert by_det(cur, s, "T7") == []
    clock.freeze(datetime(2026, 10, 10, 6, 0, tzinfo=UTC))   # 5 days
    tc.run_tax_checks(cur, s, trigger="deadline")
    [f] = by_det(cur, s, "T7")
    assert f["amount_minor"] == 3 * TD
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = 'tax:T7:month:2026-09'", (s,))
    sig = cur.fetchone()
    assert sig["kind"] == "tax.td.remittance_due" and sig["section"] == "compliance"


def test_remit_reminder_signal_and_resolution(cur, hotel_uae, clock):
    s = hotel_uae.slug
    stay(cur, s, "P1")
    clock.freeze(datetime(2026, 10, 10, 5, 0, tzinfo=UTC))
    assert tc.remit_reminder(cur, hotel_uae) == ["2026-09"]
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = 'tax:td:remit:2026-09'", (s,))
    sig = dict(cur.fetchone())
    assert sig["impact_minor"] == 3 * TD and "Oct" not in sig["title"] and "Sep 2026" in sig["title"]
    tc.record_remittance(cur, s, SEP, amount_minor=3 * TD, remitted_on=date(2026, 10, 12))
    cur.execute("SELECT status FROM signals WHERE id = %s", (sig["id"],))
    assert cur.fetchone()["status"] == "resolved"
    assert tc.remit_reminder(cur, hotel_uae) == []


def test_grouped_signal_and_close_when_fixed(cur, hotel_uae):
    s = hotel_uae.slug
    a = stay(cur, s, "P1", rooms=2)
    stay(cur, s, "P2", rooms=2)
    run(cur, s)
    cur.execute("SELECT * FROM signals WHERE hotel_slug = %s AND dedupe_key = 'tax:T1:month:2026-09'", (s,))
    sig = dict(cur.fetchone())
    assert sig["title"].startswith("2 Tourism Dirham under-collections, Sep 2026")
    assert sig["impact_minor"] == 6 * TD and sig["section"] == "compliance"
    cur.execute("DELETE FROM reservation_nights WHERE reservation_id = %s", (a,))
    cur.execute("DELETE FROM folio_charges WHERE reservation_id = %s", (a,))
    run(cur, s)
    open_ = [f for f in by_det(cur, s, "T1") if f["status"] == "DETECTED"]
    assert len(open_) == 1
    cur.execute("SELECT impact_minor FROM signals WHERE id = %s", (sig["id"],))
    assert cur.fetchone()["impact_minor"] == 3 * TD


def test_export_csv_rows_and_total(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1", rooms=2)
    stay(cur, s, "=HYPERLINK", nights=1)
    text = tc.export_csv(cur, s, SEP)
    lines = text.strip().split("\n")
    assert lines[0].startswith("reservation_id,pms_res_id")
    assert any(",'=HYPERLINK," in ln for ln in lines)             # formula injection neutralised
    total = lines[-1].split(",")
    assert total[0] == "TOTAL" and total[7] == "7" and total[9] == "105.00" and total[10] == "60.00"


# ─── tax.td_remit: the remittance action verifies from the ledger ───


def _verify_remit(cur, hotel, month="2026-09-01"):
    from clefs.core import actions
    from clefs.core.actions import ActionCtx
    from clefs.modules.profit_guard import actions as pg_actions

    pg_actions.register()   # the file alone never imported the registering module
    t = actions.get_action_type("tax.td_remit")
    return t.verifier(ActionCtx(cur=cur, hotel=hotel, action={"params": {"month": month}}, action_type=t))


def test_remit_signal_recommends_the_verifiable_action(cur, hotel_uae, clock):
    s = hotel_uae.slug
    stay(cur, s, "P1")
    clock.freeze(datetime(2026, 10, 10, 5, 0, tzinfo=UTC))
    tc.remit_reminder(cur, hotel_uae)
    cur.execute("SELECT recommended_action FROM signals WHERE hotel_slug = %s AND dedupe_key = 'tax:td:remit:2026-09'",
                (s,))
    act = cur.fetchone()["recommended_action"]
    assert act["type"] == "tax.td_remit" and act["params"]["month"] == "2026-09-01"


def test_td_remit_verifies_on_matching_remittance_with_reference(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1")
    tc.rollup_month(cur, s, SEP)
    assert _verify_remit(cur, hotel_uae).status == "pending"                  # nothing paid yet
    tc.record_remittance(cur, s, SEP, amount_minor=3 * TD, remitted_on=date(2026, 10, 12))
    assert _verify_remit(cur, hotel_uae).status == "pending"                  # no reference
    tc.record_remittance(cur, s, SEP, amount_minor=3 * TD + 100, remitted_on=date(2026, 10, 12), reference="DET-1")
    out = _verify_remit(cur, hotel_uae)                                       # AED 1 off: still fine
    assert out.status == "verified" and out.outcome_minor is None and out.counts_toward_verified_profit is False


def test_td_remit_gap_over_one_dirham_fails(cur, hotel_uae):
    s = hotel_uae.slug
    stay(cur, s, "P1")
    tc.record_remittance(cur, s, SEP, amount_minor=3 * TD - 101, remitted_on=date(2026, 10, 12), reference="DET-1")
    assert _verify_remit(cur, hotel_uae).status == "not_verified"


def test_t4_under_convention_c_uses_vat_on_room_only(cur, hotel_uae):
    """Review t4-ignores-convention: a convention C hotel charges VAT on
    ROOM only; 5% x (350 + 15 TD) must read as VAT on the TD."""
    s = hotel_uae.slug
    cur.execute("UPDATE hotel_tax_profiles SET convention = 'C' WHERE hotel_slug = %s", (s,))
    if cur.rowcount == 0:
        cur.execute("INSERT INTO hotel_tax_profiles (hotel_slug, effective_from, property_category, convention) "
                    "VALUES (%s, '2020-01-01', 'HOTEL_4', 'C')", (s,))
    rid = stay(cur, s, "P1", nights=1)
    post(cur, s, rid, "ROOM", 35000, SEP)
    post(cur, s, rid, "SC", 3500, SEP)
    post(cur, s, rid, "MF", 3500, SEP)
    post(cur, s, rid, "VAT", 1750 + 75, SEP)         # 5% x 350 on ROOM, plus 5% x 15 on the TD
    run(cur, s)
    t4 = by_det(cur, s, "T4")
    assert len(t4) == 1 and t4[0]["amount_minor"] == 75
