"""Statement-line detectors L1-L5 and L12: a positive case, a decoy that
must not fire and a boundary case each (02 spec 3.12). Amounts in fils."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from clefs.core import stays
from clefs.modules.profit_guard import contracts
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)   # 10:00 in Dubai
MF_TD = [{"type": "MUNICIPALITYFEE", "amount_minor": 7000}, {"type": "TOURISMFEE", "amount_minor": 4500}]


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def booking_line(**kw):
    base = {"ota_res_id": "4000000001", "gross_minor": 110000, "commissionable_minor": 110000,
            "commission_rate": Decimal("0.15"), "commission_minor": 16500, "charges": MF_TD}
    return {**base, **kw}


# ─── L1: fees inside Booking's commission base ───


def test_l1_flags_excluded_charges_in_the_base(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [booking_line()])
    (d,) = pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1")
    # expected base 110000 - 11500 = 98500; excess 11500; 15% -> 1725
    assert (d.amount_minor, d.klass, d.base_confidence) == (1725, "RECOVERABLE", Decimal("0.9"))
    assert d.doc == "INV-1" and d.group == "doc:INV-1" and d.res_norm == "4000000001"
    # Booking's dispute window: check-out 00:00 Dubai + 48 h
    assert d.deadline_at == datetime(2026, 9, 6, 0, 0, tzinfo=hotel_uae.tz)


def test_l1_decoy_base_already_excludes_the_fees(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [booking_line(commissionable_minor=98500, commission_minor=14775)])
    assert pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1") == []


def test_l1_boundary_at_tolerance(cur, hotel_uae):
    # tol = max(100, 0.2% x 98500 = 197) = 197: an excess of exactly 197 stays quiet
    pg.statement(cur, hotel_uae.slug, [booking_line(commissionable_minor=98500 + 197)], doc_no="INV-A")
    assert pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1") == []
    pg.statement(cur, hotel_uae.slug, [booking_line(commissionable_minor=98500 + 198, ota_res_id="4000000002")],
                 doc_no="INV-B")
    (d,) = pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1")
    assert d.res_norm == "4000000002" and d.amount_minor == 30  # 15% x 198 rounds to 30


def test_l1_inferred_through_uae_tax_is_less_confident(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [booking_line(charges=[])])
    (d,) = pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1")
    assert d.base_confidence == Decimal("0.7") and d.amount_minor > 0
    assert d.calc[0]["inputs"]["source"] == "uae_tax"


def test_l1b_when_the_fees_are_not_configured_as_charges(cur, hotel_uae):
    pg.channel_cfg(cur, hotel_uae.slug, mf=False, td=False)
    pg.statement(cur, hotel_uae.slug, [booking_line()])
    ds = pg.drafts(cur, hotel_uae.slug, "L1")
    assert pg.only(ds, "L1") == []  # commission on the fees is due under the contract
    (b,) = pg.only(ds, "L1b")
    # 15% x 11500 = 1725 over 90 days -> 575 a month
    assert (b.amount_minor, b.klass, b.base_confidence, b.doc) == (575, "PREVENTABLE", Decimal("0.85"), "2026-10")


def test_l1b_decoy_configured_hotel(cur, hotel_uae):
    pg.channel_cfg(cur, hotel_uae.slug, mf=True, td=True)
    pg.statement(cur, hotel_uae.slug, [booking_line(commissionable_minor=98500, commission_minor=14775)])
    assert pg.drafts(cur, hotel_uae.slug, "L1") == []


def test_l1_agoda_commission_on_taxes_is_structural(cur, hotel_uae):
    line = {"ota_res_id": "987654", "gross_minor": 100000, "commissionable_minor": 100000,
            "commission_rate": Decimal("0.18"), "commission_minor": 18000,
            "charges": [{"type": "VAT", "amount_minor": 5000}, {"type": "MUNICIPALITYFEE", "amount_minor": 7000}]}
    pg.statement(cur, hotel_uae.slug, [line], ota="AGODA", doc_no="AG-1")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L1")
    assert (d.klass, d.signal, d.amount_minor) == ("STRUCTURAL", False, 2160)  # 18% x 12000


# ─── L2: effective percent above contract + uplifts ───


def pct_line(commission, **kw):
    return {"ota_res_id": "4000000009", "gross_minor": 100000, "commissionable_minor": 100000,
            "commission_minor": commission, **kw}


def test_l2_flags_a_higher_percent(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [pct_line(15300)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L2")
    assert (d.amount_minor, d.base_confidence) == (300, Decimal("0.85"))
    # invoice 1 Oct 2026 + 14 business days (Sat-Sun weekend) = 21 Oct, due end of that day
    assert d.deadline_at == datetime(2026, 10, 22, tzinfo=hotel_uae.tz)


def test_l2_decoy_contract_percent_and_uplift(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [pct_line(15000)])
    assert pg.drafts(cur, hotel_uae.slug, "L2") == []
    pg.program(cur, hotel_uae.slug, "PREFERRED", "3", kind="COMMISSION_UPLIFT")
    pg.statement(cur, hotel_uae.slug, [pct_line(18000, ota_res_id="4000000010")], doc_no="INV-2")
    assert pg.drafts(cur, hotel_uae.slug, "L2") == []


def test_l2_boundary_exactly_a_tenth_of_a_point(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [pct_line(15100)])
    assert pg.drafts(cur, hotel_uae.slug, "L2") == []
    pg.statement(cur, hotel_uae.slug, [pct_line(15101, ota_res_id="4000000011")], doc_no="INV-2")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L2")
    assert d.res_norm == "4000000011" and d.amount_minor == 101


def test_l2_visibility_booster_with_unknown_country_lowers_confidence(cur, hotel_uae):
    pg.program(cur, hotel_uae.slug, "VISIBILITY_BOOSTER", "5", kind="COMMISSION_UPLIFT", countries=("GB",))
    pg.statement(cur, hotel_uae.slug, [pct_line(20000)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L2")
    assert d.base_confidence == Decimal("0.5") and d.missing == ["booker_country"]
    pg.statement(cur, hotel_uae.slug, [pct_line(20000, ota_res_id="4000000012", booker_country="GB")], doc_no="INV-3")
    assert [x.res_norm for x in pg.drafts(cur, hotel_uae.slug, "L2")] == ["4000000009"]


# ─── L3: commission on a no-show or a waived cancellation ───


def ns_line(rid, commission=15750, **kw):
    return {"ota_res_id": "4000000020", "gross_minor": 105000, "commissionable_minor": 105000,
            "commission_rate": Decimal("0.15"), "commission_minor": commission, "reservation_id": rid,
            "match_status": "LINKED", "match_score": Decimal("1"), **kw}


def test_l3_flags_commission_on_a_pms_no_show(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P20", ota_id="4000000020", status="NO_SHOW")
    pg.statement(cur, hotel_uae.slug, [ns_line(rid)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L3")
    assert (d.amount_minor, d.base_confidence, d.match_score, d.reservation_id) == (15750, Decimal("0.9"), 1, rid)
    assert d.doc == "M2026-09" and d.group == "doc:INV-1"   # fingerprint by stay month, signal by statement


def test_l3_decoys_fee_collected_or_guest_stayed(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P20", ota_id="4000000020", status="NO_SHOW")
    pg.folio(cur, hotel_uae.slug, rid, "NOSHOW_FEE", 105000)      # full fee collected: commission is due
    stayed = pg.reservation(cur, hotel_uae.slug, "P21", ota_id="4000000021", status="CHECKED_OUT")
    pg.statement(cur, hotel_uae.slug, [ns_line(rid), ns_line(stayed, ota_res_id="4000000021")])
    assert pg.drafts(cur, hotel_uae.slug, "L3") == []


def test_l3_boundary_on_the_fee_collected(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P20", ota_id="4000000020", status="NO_SHOW", no_show_fee_minor=99334)
    pg.statement(cur, hotel_uae.slug, [ns_line(rid, commission=15000)])
    assert pg.drafts(cur, hotel_uae.slug, "L3") == []          # 15000 - 14900 = 100 = tol
    cur.execute("UPDATE reservations SET no_show_fee_minor = 99327 WHERE id = %s", (rid,))
    (d,) = pg.drafts(cur, hotel_uae.slug, "L3")
    assert d.amount_minor == 101


def test_l3_from_the_statement_status_and_unmatched_lines(cur, hotel_uae):
    pg.reservation(cur, hotel_uae.slug, "P30", ota_id="4000000030")           # PMS has data for those dates
    pg.statement(cur, hotel_uae.slug, [
        ns_line(None, ota_res_id="4000000031", line_status="NO_SHOW", match_status="UNMATCHED", match_score=None),
        ns_line(None, ota_res_id="4000000032", match_status="UNMATCHED", match_score=None),
        ns_line(None, ota_res_id="4000000033", match_status="REVIEW", match_score=None),
    ])
    by = {d.res_norm: d for d in pg.drafts(cur, hotel_uae.slug, "L3")}
    assert set(by) == {"4000000031", "4000000032"}
    assert by["4000000031"].match_score == 1 and by["4000000031"].missing == ["fee_collected"]
    assert by["4000000032"].match_score == Decimal("0.5") and by["4000000032"].missing == ["pms_reservation"]


def test_l3_nets_off_a_credit_note(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P20", ota_id="4000000020", status="NO_SHOW")
    pg.statement(cur, hotel_uae.slug, [ns_line(rid)])
    pg.statement(cur, hotel_uae.slug, [ns_line(rid, commission=-15750)], doc_no="CN-1", doc_kind="CREDIT_NOTE")
    assert pg.drafts(cur, hotel_uae.slug, "L3") == []


# ─── L4: commission on unstayed nights ───


def ed_line(rid, **kw):
    return {"ota_res_id": "4000000040", "gross_minor": 105000, "commissionable_minor": 105000,
            "commission_rate": Decimal("0.15"), "commission_minor": 15750, "reservation_id": rid,
            "match_status": "LINKED", "match_score": Decimal("1"), **kw}


def early(cur, slug):
    return pg.reservation(cur, slug, "P40", ota_id="4000000040", actual_check_out=pg.CI + timedelta(days=1))


def test_l4_flags_the_unstayed_nights(cur, hotel_uae):
    rid = early(cur, hotel_uae.slug)
    pg.statement(cur, hotel_uae.slug, [ed_line(rid)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L4")
    assert (d.amount_minor, d.base_confidence) == (10500, Decimal("0.8"))    # 2 of 3 equal nights


def test_l4_decoy_the_ota_already_shortened_the_stay(cur, hotel_uae):
    rid = early(cur, hotel_uae.slug)
    pg.statement(cur, hotel_uae.slug, [ed_line(rid, room_nights=1, check_out=pg.CI + timedelta(days=1),
                                               commissionable_minor=35000, commission_minor=5250)])
    assert pg.drafts(cur, hotel_uae.slug, "L4") == []


def test_l4_boundary_with_an_early_departure_fee(cur, hotel_uae):
    rid = early(cur, hotel_uae.slug)
    pg.folio(cur, hotel_uae.slug, rid, "CXL_FEE", 69333)       # 15% = 10400 -> 10500 - 10400 = 100 = tol
    pg.statement(cur, hotel_uae.slug, [ed_line(rid)])
    assert pg.drafts(cur, hotel_uae.slug, "L4") == []
    cur.execute("UPDATE folio_charges SET amount_minor = 69320 WHERE reservation_id = %s", (rid,))
    (d,) = pg.drafts(cur, hotel_uae.slug, "L4")
    assert d.amount_minor == 102


# ─── L5: OTA base vs PMS base through core/tax ───


def pms_base(cur, slug, rid):
    return contracts.expected_commission(cur, slug, ota_code="BOOKING", breakdown=stays.reservation_breakdown(cur, rid),
                                         on=pg.CI)["base_minor"]


def l5_line(rid, commissionable, **kw):
    return {"ota_res_id": "4000000050", "gross_minor": commissionable, "commissionable_minor": commissionable,
            "commission_rate": Decimal("0.15"), "commission_minor": commissionable * 15 // 100, "reservation_id": rid,
            "match_status": "LINKED", "match_score": Decimal("0.9"), **kw}


def test_l5_flags_an_ota_base_above_the_pms(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P50", ota_id="4000000050")
    base = pms_base(cur, hotel_uae.slug, rid)
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base + 5000)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L5")
    assert (d.amount_minor, d.base_confidence, d.match_score) == (750, Decimal("0.75"), Decimal("0.9"))


def test_l5_decoy_and_boundary(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P50", ota_id="4000000050")
    base = pms_base(cur, hotel_uae.slug, rid)
    t = max(100, round(base * 0.002))
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base)], doc_no="INV-A")
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base + t)], doc_no="INV-B")
    assert pg.drafts(cur, hotel_uae.slug, "L5") == []
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base + t + 1)], doc_no="INV-C")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L5")
    assert d.doc == "INV-C"


def test_l5_pms_higher_is_a_service_risk_signal_not_a_claim(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P50", ota_id="4000000050")
    base = pms_base(cur, hotel_uae.slug, rid)
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base - 10000)])
    assert pg.drafts(cur, hotel_uae.slug, "L5") == []
    (s,) = pg.drafts.last_ctx.side_signals
    assert s["kind"] == "pg.folio.overcharge_risk" and s["impact_minor"] == 10000


# ─── L12: refund without a pro-rata commission credit ───


def refund_payout(cur, slug, refund_commission, ref="PO-R"):
    return pg.payout(cur, slug, ref, [
        {"ota_res_id": "4000000060", "total_amount_paid_minor": 100000, "commissionable_minor": 100000,
         "commission_minor": 15000, "partner_payout_minor": 85000},
        {"ota_res_id": "4000000060", "line_kind": "REFUND", "total_amount_paid_minor": -40000,
         "commission_minor": refund_commission, "partner_payout_minor": -40000 - refund_commission},
    ])


def test_l12_flags_a_refund_without_commission_reduction(cur, hotel_uae):
    refund_payout(cur, hotel_uae.slug, 0)
    (d,) = pg.drafts(cur, hotel_uae.slug, "L12")
    assert (d.amount_minor, d.base_confidence, d.doc) == (6000, Decimal("0.8"), "PO-R")   # 15% x 40000


def test_l12_decoy_reduced_pro_rata(cur, hotel_uae):
    refund_payout(cur, hotel_uae.slug, -6000)
    assert pg.drafts(cur, hotel_uae.slug, "L12") == []


def test_l12_boundary(cur, hotel_uae):
    refund_payout(cur, hotel_uae.slug, -5900)            # short 100 = tol
    assert pg.drafts(cur, hotel_uae.slug, "L12") == []
    cur.execute("UPDATE pfg_ota_payout_lines SET commission_minor = -5899 WHERE line_kind = 'REFUND'")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L12")
    assert d.amount_minor == 101


def test_l12_statement_refund_line_counts_its_own_negative_commission(cur, hotel_uae):
    pg.statement(cur, hotel_uae.slug, [
        {"ota_res_id": "4000000061", "gross_minor": 100000, "commissionable_minor": 100000, "commission_minor": 15000},
        {"ota_res_id": "4000000061", "gross_minor": -20000, "commissionable_minor": -20000, "commission_minor": -3000,
         "line_status": "ADJUSTMENT"},
    ])
    assert pg.drafts(cur, hotel_uae.slug, "L12") == []
    cur.execute("UPDATE pfg_ota_statement_lines SET commission_minor = 0 WHERE gross_minor < 0")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L12")
    assert d.amount_minor == 3000


def test_l5_service_signal_does_not_roll_back_the_run(cur, hotel_uae):
    """QA OWN-04: the side signal had no confidence, SignalIn raised at the
    very end of detectors.run and the whole run rolled back."""
    from clefs.modules.profit_guard import detectors

    rid = pg.reservation(cur, hotel_uae.slug, "P50", ota_id="4000000050")
    base = pms_base(cur, hotel_uae.slug, rid)
    pg.statement(cur, hotel_uae.slug, [l5_line(rid, base - 10000), booking_line(ota_res_id="4000000099")])
    out = detectors.run(cur, hotel_uae.slug, trigger="manual")
    assert out["side_signals"] == 1 and out["findings"] >= 1
    cur.execute("SELECT confidence FROM signals WHERE hotel_slug = %s AND kind = 'pg.folio.overcharge_risk'",
                (hotel_uae.slug,))
    assert float(cur.fetchone()["confidence"]) > 0


def test_a_bad_side_signal_is_skipped_not_fatal(cur, hotel_uae):
    from clefs.modules.profit_guard.detectors import signals as pg_signals

    pg.statement(cur, hotel_uae.slug, [booking_line()])
    pg.drafts(cur, hotel_uae.slug, "L1")
    ctx = pg.drafts.last_ctx
    ctx.side_signals[:] = [{"kind": "pg.folio.overcharge_risk", "dedupe_key": "x", "title": ""},
                           {"kind": "pg.folio.overcharge_risk", "dedupe_key": "pg:test:ok", "title": "ok"}]
    assert pg_signals.raise_side_signals(ctx) == 1


def test_l12_one_refund_in_payout_and_statement_counts_once(cur, hotel_uae):
    """Review l12-refund-double-count: the same 400 refund as a payout
    REFUND line and a negative-gross statement line is 400, not 800."""
    refund_payout(cur, hotel_uae.slug, 0)
    pg.statement(cur, hotel_uae.slug, [
        {"ota_res_id": "4000000060", "gross_minor": -40000, "commissionable_minor": -40000, "commission_minor": 0,
         "line_status": "ADJUSTMENT"}], doc_no="INV-R")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L12")
    assert d.amount_minor == 6000 and d.doc == "INV-R"   # 15% x 40000 once; the oldest document names it
    # a credit on the statement side is the ledger's measure: max, not sum
    cur.execute("UPDATE pfg_ota_statement_lines SET commission_minor = -2000 WHERE gross_minor < 0")
    cur.execute("UPDATE pfg_ota_payout_lines SET commission_minor = -2000 WHERE line_kind = 'REFUND'")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L12")
    assert (d.amount_minor, d.credited_minor) == (4000, 2000)


# ─── Review fixes: one booking, one netting; L1 + L2 never double count ───


def test_a_two_line_booking_nets_its_credit_once(cur, hotel_uae):
    """Review merge-double-nets: two lines of 300 each on one booking with
    a 400 credit is 600 - 400 = 200 (it was 0: each line netted 400)."""
    pg.statement(cur, hotel_uae.slug, [pct_line(15300), pct_line(15300)])
    pg.statement(cur, hotel_uae.slug, [pct_line(-400, gross_minor=0, commissionable_minor=0)], doc_no="CN-1",
                 doc_kind="CREDIT_NOTE")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L2")
    assert (d.amount_minor, d.credited_minor, d.gross_minor) == (200, 400, 600)
    assert d.calc[-1]["label"] == "Lines combined" and len(d.evidence) == 4


def test_a_two_line_no_show_nets_the_fee_once(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P20", ota_id="4000000020", status="NO_SHOW")
    pg.folio(cur, hotel_uae.slug, rid, "NOSHOW_FEE", 100000)      # 15% x 1000 = 150 due once
    pg.statement(cur, hotel_uae.slug, [ns_line(rid), ns_line(rid)])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L3")
    assert d.amount_minor == 2 * 15750 - 15000


def test_l1_and_l2_on_one_booking_add_up_to_the_true_overcharge(cur, hotel_uae):
    """Review l1-l2-overlap, golden-style numbers: 15% contract, 18%
    charged, base 1,008.68 vs 927.08 expected. True overcharge 42.50."""
    reported, expected = 100868, 92708
    line = booking_line(gross_minor=reported, commissionable_minor=reported, commission_rate=Decimal("0.18"),
                        commission_minor=18156, charges=[{"type": "MUNICIPALITYFEE", "amount_minor": 5160},
                                                         {"type": "TOURISMFEE", "amount_minor": 3000}])
    pg.statement(cur, hotel_uae.slug, [line])
    (l1,) = pg.only(pg.drafts(cur, hotel_uae.slug, "L1"), "L1")
    (l2,) = pg.drafts(cur, hotel_uae.slug, "L2")
    assert l1.amount_minor == 1224            # 15% x 81.60, not 18%
    assert l2.amount_minor == 3026            # 3 pp x 1,008.68
    true = 18156 - round(Decimal("0.15") * expected)
    assert abs(l1.amount_minor + l2.amount_minor - true) <= 1
