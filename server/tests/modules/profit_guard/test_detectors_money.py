"""Detectors L6-L11 and L13-L15: a positive case, a decoy that must not
fire and a boundary case each (02 spec 3.12). Clock frozen at
2026-10-02 10:00 Dubai."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from clefs.modules.profit_guard.detectors.base import rnd
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
TODAY = date(2026, 10, 2)


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


# ─── L6: stacked discounts over the owner cap ───


def l6_line(programs, **kw):
    return {"ota_res_id": "4000000070", "gross_minor": 60000, "commissionable_minor": 60000, "commission_minor": 9000,
            "check_in": date(2026, 9, 20), "check_out": date(2026, 9, 23), "programs": programs, **kw}


def test_l6_flags_stacking_over_the_cap(cur, hotel_uae):
    for prog, pct in (("GENIUS", "15"), ("COUNTRY", "15"), ("EARLY_BOOKER", "10")):
        pg.program(cur, hotel_uae.slug, prog, pct)
    pg.statement(cur, hotel_uae.slug, [l6_line(["GENIUS", "COUNTRY", "EARLY_BOOKER"])])
    (d,) = pg.drafts(cur, hotel_uae.slug, "L6")
    keep = Decimal("0.85") * Decimal("0.85") * Decimal("0.9")
    expected = rnd((1 - keep - Decimal("0.25")) * (Decimal(20000) / keep) * 3)
    assert (d.amount_minor, d.klass, d.base_confidence, d.missing) == (expected, "PREVENTABLE", Decimal("0.8"), ["bar"])
    assert d.group == "month:2026-09"


def test_l6_decoy_legitimate_genius_and_mobile(cur, hotel_uae):
    pg.program(cur, hotel_uae.slug, "GENIUS", "10")
    pg.program(cur, hotel_uae.slug, "MOBILE", "10")
    pg.statement(cur, hotel_uae.slug, [l6_line(["GENIUS", "MOBILE"])])     # 19% < 25%
    assert pg.drafts(cur, hotel_uae.slug, "L6") == []


def test_l6_boundary_exactly_the_cap(cur, hotel_uae):
    pg.program(cur, hotel_uae.slug, "GENIUS", "25")
    pg.statement(cur, hotel_uae.slug, [l6_line(["GENIUS"])])
    assert pg.drafts(cur, hotel_uae.slug, "L6") == []
    cur.execute("INSERT INTO pfg_owner_policies (hotel_slug, max_total_discount_pct) VALUES (%s, 24) "
                "ON CONFLICT (hotel_slug) DO NOTHING", (hotel_uae.slug,))
    (d,) = pg.drafts(cur, hotel_uae.slug, "L6")
    assert d.amount_minor == rnd(Decimal("0.01") * Decimal(20000) / Decimal("0.75") * 3)


# ─── L7: parity ───

STAY = date(2026, 10, 20)


def booked_on(cur, slug, channel="BOOKING"):
    pg.reservation(cur, slug, f"PB-{channel}", channel=channel, status="CONFIRMED", check_in=STAY, nights=1)


def test_l7_flags_an_ota_under_the_direct_price(cur, hotel_uae):
    booked_on(cur, hotel_uae.slug)
    pg.evidence(cur, hotel_uae.slug, "DIRECT_WEB", 40000, stay_date=STAY)
    pg.evidence(cur, hotel_uae.slug, "BOOKING", 38000, stay_date=STAY)
    pg.evidence(cur, hotel_uae.slug, "AGODA", 40500, stay_date=STAY)
    (d,) = pg.drafts(cur, hotel_uae.slug, "L7")
    assert (d.ota_code, d.amount_minor, d.base_confidence, d.signal) == ("BOOKING", 2000, Decimal("0.6"), True)
    assert d.doc == "2026-10-20"


def test_l7_screenshot_and_visibility_rows(cur, hotel_uae):
    pg.evidence(cur, hotel_uae.slug, "DIRECT_WEB", 40000, stay_date=STAY)
    pg.evidence(cur, hotel_uae.slug, "BOOKING", 38000, stay_date=STAY, screenshot=True, emit_signal=False)
    (d,) = pg.drafts(cur, hotel_uae.slug, "L7")
    assert d.base_confidence == Decimal("0.75") and d.signal is False and d.amount_minor == 0  # nothing booked yet


def test_l7_decoys_like_for_like_only(cur, hotel_uae):
    pg.evidence(cur, hotel_uae.slug, "DIRECT_WEB", 40000, stay_date=STAY)
    pg.evidence(cur, hotel_uae.slug, "BOOKING", 30000, stay_date=STAY, login="MEMBER")        # member price: other key
    pg.evidence(cur, hotel_uae.slug, "AGODA", 39700, stay_date=STAY)                          # within 1%
    pg.evidence(cur, hotel_uae.slug, "EXPEDIA", 30000, stay_date=date(2026, 9, 30))           # past stay
    assert pg.drafts(cur, hotel_uae.slug, "L7") == []


def test_l7_boundary_at_the_tolerance(cur, hotel_uae):
    pg.evidence(cur, hotel_uae.slug, "DIRECT_WEB", 40000, stay_date=STAY)
    pg.evidence(cur, hotel_uae.slug, "AGODA", 39600, stay_date=STAY)                          # exactly 40000 x 0.99
    assert pg.drafts(cur, hotel_uae.slug, "L7") == []
    pg.evidence(cur, hotel_uae.slug, "AGODA", 39599, stay_date=STAY)                          # newer observation
    (d,) = pg.drafts(cur, hotel_uae.slug, "L7")
    assert d.ota_code == "AGODA"


# ─── L8 / L9: virtual cards ───


def test_l8_money_waiting_on_a_card(cur, hotel_uae):
    pg.vcc(cur, hotel_uae.slug, "V1", deadline=date(2026, 10, 20), ota_res_id="4000000080")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L8")
    assert (d.amount_minor, d.base_confidence, d.urgency_days, d.severity) == (50000, Decimal("0.95"), 18, 2)
    assert d.deadline_at == datetime(2026, 10, 21, tzinfo=hotel_uae.tz) and d.doc == "VCC:V1"


def test_l8_decoys(cur, hotel_uae):
    s = hotel_uae.slug
    pg.vcc(cur, s, "V2", deadline=date(2026, 10, 20), status="FULLY_CHARGED")
    pg.vcc(cur, s, "V3", deadline=date(2026, 10, 20), activation=date(2026, 10, 10))
    pg.vcc(cur, s, "V4", deadline=date(2026, 10, 20), balance=0)
    pg.vcc(cur, s, "V5", deadline=date(2026, 10, 1))
    assert pg.drafts(cur, s, "L8") == []


def test_l8_l9_boundary_on_the_deadline_day(cur, hotel_uae):
    s = hotel_uae.slug
    pg.vcc(cur, s, "V6", deadline=TODAY)
    pg.vcc(cur, s, "V7", deadline=TODAY - timedelta(days=1))
    pg.vcc(cur, s, "V8", deadline=TODAY - timedelta(days=1), status="CANCELLED")
    (d8,) = pg.drafts(cur, s, "L8")
    assert (d8.res_norm, d8.urgency_days, d8.severity) == ("V6", 0, 4)
    (d9,) = pg.drafts(cur, s, "L9")
    assert (d9.res_norm, d9.base_confidence, d9.amount_minor) == ("V7", Decimal("0.9"), 50000)
    assert d9.calc[0]["inputs"]["recovery_confidence"] == "0.3"


# ─── L10: payout shortfalls ───


def po_line(partner, **kw):
    return {"ota_res_id": "4000000100", "total_amount_paid_minor": 100000, "commissionable_minor": 100000,
            "commission_minor": 15000, "charges_minor": 0, "partner_payout_minor": partner, **kw}


def test_l10_line_payout_and_bank_shortfalls(cur, hotel_uae):
    s = hotel_uae.slug
    pid = pg.payout(cur, s, "PO-1", [po_line(84000)])
    pg.bank(cur, s, 83000, payout_id=pid)
    pg.payout(cur, s, "PO-2", [po_line(85000, ota_res_id="4000000101")], amount=80000)
    by = {(d.res_norm, d.doc): d.amount_minor for d in pg.drafts(cur, s, "L10")}
    assert by == {("4000000100", "PO-1"): 1000, ("bank", "PO-1"): 1000, (None, "PO-2:total"): 5000}


def test_l10_near_amount_bank_match(cur, hotel_uae):
    s = hotel_uae.slug
    pg.payout(cur, s, "PO-3", [po_line(85000)])
    pg.bank(cur, s, 84000, value_date=date(2026, 10, 7))       # unlinked, 98.8% of the payout, 2 days later
    (d,) = pg.drafts(cur, s, "L10")
    assert (d.res_norm, d.amount_minor) == ("bank", 1000)
    assert d.calc[-1]["inputs"]["matched_by"] == "amount"


def test_l10_decoys(cur, hotel_uae):
    s = hotel_uae.slug
    pid = pg.payout(cur, s, "PO-4", [po_line(85000), po_line(86000, ota_res_id="4000000102")])  # overpaid is fine
    pg.bank(cur, s, 171000, payout_id=pid)
    pg.bank(cur, s, 9000, value_date=date(2026, 10, 6), key="b2")                               # unrelated credit
    assert pg.drafts(cur, s, "L10") == []


def test_l10_boundary(cur, hotel_uae):
    s = hotel_uae.slug
    pg.payout(cur, s, "PO-5", [po_line(85000 - 170)])        # tol(85000) = 170
    assert pg.drafts(cur, s, "L10") == []
    cur.execute("UPDATE pfg_ota_payout_lines SET partner_payout_minor = 84829")
    cur.execute("UPDATE pfg_ota_payouts SET amount_minor = 84829")
    (d,) = pg.drafts(cur, s, "L10")
    assert d.amount_minor == 171


# ─── L11: FX loss ───


def usd_payout(cur, slug, credited):
    pid = pg.payout(cur, slug, "PO-USD", [po_line(10000, currency="USD")], currency="USD")
    pg.bank(cur, slug, credited, payout_id=pid)


def test_l11_flags_a_conversion_loss(cur, hotel_uae):
    usd_payout(cur, hotel_uae.slug, 36000)                     # USD 100 x 3.6725 = AED 367.25 expected
    (d,) = pg.drafts(cur, hotel_uae.slug, "L11")
    assert (d.amount_minor, d.currency, d.klass, d.base_confidence) == (725, "AED", "PREVENTABLE", Decimal("0.8"))


def test_l11_decoy_small_spread(cur, hotel_uae):
    usd_payout(cur, hotel_uae.slug, 36700)                     # loss 25 fils
    assert pg.drafts(cur, hotel_uae.slug, "L11") == []


def test_l11_boundary_half_a_percent(cur, hotel_uae):
    usd_payout(cur, hotel_uae.slug, 36725 - 184)               # 0.5% of 36725 rounds to 184: not more than it
    assert pg.drafts(cur, hotel_uae.slug, "L11") == []
    cur.execute("UPDATE pfg_bank_credits SET amount_minor = %s", (36725 - 185,))
    (d,) = pg.drafts(cur, hotel_uae.slug, "L11")
    assert d.amount_minor == 185


# ─── L13: chargebacks ───


def chargeback(cur, slug, adjustment=None):
    lines = [po_line(-30000, line_kind="CHARGEBACK", event_date=date(2026, 8, 1))]
    if adjustment is not None:
        lines.append(po_line(adjustment, line_kind="ADJUSTMENT"))
    pg.payout(cur, slug, "PO-CB", lines)


def test_l13_ages_a_chargeback(cur, hotel_uae):
    chargeback(cur, hotel_uae.slug)
    (d,) = pg.drafts(cur, hotel_uae.slug, "L13")
    assert (d.amount_minor, d.base_confidence, d.severity, d.note) == (30000, Decimal("0.7"), 3, "age:61-90")


def test_l13_decoy_reversed_and_boundary(cur, hotel_uae):
    chargeback(cur, hotel_uae.slug, adjustment=30000)
    assert pg.drafts(cur, hotel_uae.slug, "L13") == []
    cur.execute("UPDATE pfg_ota_payout_lines SET partner_payout_minor = 29900 WHERE line_kind = 'ADJUSTMENT'")
    assert pg.drafts(cur, hotel_uae.slug, "L13") == []          # 100 left = tol
    cur.execute("UPDATE pfg_ota_payout_lines SET partner_payout_minor = 29899 WHERE line_kind = 'ADJUSTMENT'")
    (d,) = pg.drafts(cur, hotel_uae.slug, "L13")
    assert d.amount_minor == 101


# ─── L14: report before the OTA's window closes ───


def test_l14_no_show_before_the_deadline(cur, hotel_uae):
    rid = pg.reservation(cur, hotel_uae.slug, "P14", ota_id="4000000140", status="NO_SHOW", check_in=date(2026, 9, 30))
    (d,) = pg.drafts(cur, hotel_uae.slug, "L14")
    # 3 nights x AED 350 at Booking 15% on room + SC + VAT: the 04 spec 4.9 golden AED 182.52
    assert (d.amount_minor, d.base_confidence, d.reservation_id) == (18252, Decimal("0.85"), rid)
    assert d.deadline_at == datetime(2026, 10, 5, tzinfo=hotel_uae.tz)       # check-out 3 Oct 00:00 + 48 h
    assert (d.urgency_days, d.severity) == (3, 2)


def test_l14_deadline_across_month_end_and_timezone(cur, hotel_uae, clock):
    # Check-out 30 Sep: deadline 2 Oct 00:00 Dubai = 1 Oct 20:00 UTC.
    pg.reservation(cur, hotel_uae.slug, "P15", ota_id="4000000150", status="NO_SHOW", check_in=date(2026, 9, 27))
    clock.freeze(datetime(2026, 10, 1, 19, 0, tzinfo=UTC))                   # 23:00 Dubai, one hour left
    (d,) = pg.drafts(cur, hotel_uae.slug, "L14")
    assert d.deadline_at == datetime(2026, 10, 1, 20, 0, tzinfo=UTC)
    assert (d.detector, d.severity, d.urgency_days) == ("L14", 4, 1)
    clock.freeze(datetime(2026, 10, 1, 20, 0, tzinfo=UTC))                   # the window closed: an L3 at x 0.5
    (late,) = pg.drafts(cur, hotel_uae.slug, "L14")
    assert (late.detector, late.base_confidence, late.doc) == ("L3", Decimal("0.450"), "M2026-09")


def test_l14_decoys_reported_or_stayed_or_other_channel(cur, hotel_uae):
    s = hotel_uae.slug
    rid = pg.reservation(cur, s, "P16", ota_id="4000000160", status="NO_SHOW", check_in=date(2026, 9, 30))
    pg.statement(cur, s, [{"ota_res_id": "4000000160", "reservation_id": rid, "line_status": "NO_SHOW",
                           "commission_minor": 0}])
    pg.reservation(cur, s, "P17", ota_id="4000000170", status="CHECKED_OUT", check_in=date(2026, 9, 28))
    pg.reservation(cur, s, "P18", channel="AGODA", ota_id="555", status="NO_SHOW", check_in=date(2026, 9, 30))
    assert pg.drafts(cur, s, "L14") == []


def test_l14_expedia_hotel_collect_and_early_departure(cur, hotel_uae):
    s = hotel_uae.slug
    pg.reservation(cur, s, "P19", channel="EXPEDIA", ota_id="EXP-77", status="NO_SHOW", check_in=date(2026, 9, 27),
                   collect_model="HOTEL_COLLECT")
    pg.reservation(cur, s, "P20", ota_id="4000000200", check_in=date(2026, 9, 29), nights=4,
                   actual_check_out=date(2026, 10, 1), status="CHECKED_OUT")
    by = {d.ota_code: d for d in pg.drafts(cur, s, "L14")}
    assert by["EXPEDIA"].deadline_at == datetime(2026, 10, 4, 12, 0, tzinfo=hotel_uae.tz)
    assert by["BOOKING"].note == "report_early_departure" and 0 < by["BOOKING"].amount_minor < 18252 * 4 // 3


# ─── L15: reverse-charge VAT on a foreign entity's commission ───


def vat_statement(cur, slug, commission, doc_no="INV-9"):
    pg.statement(cur, slug, [{"ota_res_id": "4000000900", "commission_minor": commission}], doc_no=doc_no,
                 doc_date=date(2026, 9, 15))


def test_l15_foreign_entity(cur, hotel_uae):
    pg.contract(cur, hotel_uae.slug, entity_country="NL")
    vat_statement(cur, hotel_uae.slug, 30000)
    (d,) = pg.drafts(cur, hotel_uae.slug, "L15")
    assert (d.amount_minor, d.klass, d.doc) == (1500, "COMPLIANCE", "2026-09")
    assert d.deadline_at == datetime(2026, 10, 29, tzinfo=hotel_uae.tz)       # Q3 return: 28 Oct


def test_l15_decoys_local_or_unknown_entity(cur, hotel_uae):
    vat_statement(cur, hotel_uae.slug, 30000)
    assert pg.drafts(cur, hotel_uae.slug, "L15") == []                      # default terms name no entity
    pg.contract(cur, hotel_uae.slug, entity_country="AE")
    assert pg.drafts(cur, hotel_uae.slug, "L15") == []


def test_l15_boundary(cur, hotel_uae):
    pg.contract(cur, hotel_uae.slug, entity_country="NL")
    vat_statement(cur, hotel_uae.slug, 2000)                                # 5% = 100 = tol
    assert pg.drafts(cur, hotel_uae.slug, "L15") == []
    vat_statement(cur, hotel_uae.slug, 20, doc_no="INV-10")                 # month total 2020 -> 101
    (d,) = pg.drafts(cur, hotel_uae.slug, "L15")
    assert d.amount_minor == 101
