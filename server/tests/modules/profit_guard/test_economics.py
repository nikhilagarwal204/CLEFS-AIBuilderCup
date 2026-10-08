"""Net ADR by channel (02 spec 3.6): the monthly rollup, commission on tax
(Booking K2 L1 case, Agoda structural), FX, programme and AGX costs, the
waterfall and the stored reads. Dubai 4-star night at AED 350: SC 35.00,
MF 26.95, VAT 20.60, TD 15.00."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from clefs.modules.profit_guard import economics as ec
from clefs.modules.profit_guard import repo
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)
SEP = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def booking_stay(cur, slug, *, commission=20140, programs=(), gross=None, ota="BOOKING", pms="B1", ota_id="4000000020"):
    rid = pg.reservation(cur, slug, pms, channel=ota, ota_id=ota_id)
    pg.statement(cur, slug, [{"ota_res_id": ota_id, "reservation_id": rid, "commission_minor": commission,
                              "gross_minor": gross or 3 * 44755, "programs": list(programs)}],
                 ota=ota, doc_no=f"INV-{pms}")
    return rid


def row(rows, channel):
    return next(r for r in rows if r["channel"] == channel)


def test_booking_commission_on_fees_not_configured_as_charges(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=False, td=False)
    booking_stay(cur, s)
    r = row(ec.compute_month(cur, s, SEP), "BOOKING")
    assert r["room_nights"] == 3 and r["commission_minor"] == 20140 and r["commission_basis"] == "statement"
    # 15% x (MF 26.95 + TD 15.00) x 3 nights = 18.88 above the K2 base (room + SC + VAT = 182.52)
    assert r["commission_on_tax_minor"] == 1888
    assert r["commission_on_tax_trace"]["base_ex_tax_minor"] == 3 * 40560
    assert r["room_rev_minor"] == 3 * (35000 + 3500)
    assert r["net_minor"] == r["room_rev_minor"] - 20140 - r["payment_cost_minor"]


def test_booking_fees_configured_as_charges_leave_no_tax_commission(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=True, td=True)
    booking_stay(cur, s, commission=18252)
    assert row(ec.compute_month(cur, s, SEP), "BOOKING")["commission_on_tax_minor"] == 0


def test_booking_l1_findings_count_when_fees_are_configured(cur, hotel_uae):
    s = hotel_uae.slug
    booking_stay(cur, s)                             # default: configured as charges, yet commissioned
    cur.execute("SELECT id FROM pfg_ota_statement_lines WHERE hotel_slug = %s", (s,))
    line = cur.fetchone()["id"]
    repo.upsert_finding(cur, s, {"detector": "L1", "fingerprint": "l1", "ota_code": "BOOKING", "class": "RECOVERABLE",
                                 "subject_ref": "doc:INV-B1|4000000020", "amount_minor": 1888, "currency": "AED",
                                 "confidence": "0.9", "statement_line_id": line})
    r = row(ec.compute_month(cur, s, SEP), "BOOKING")
    assert r["commission_on_tax_minor"] == 1888 and r["commission_on_tax_trace"]["l1_findings_minor"] == 1888


def test_agoda_commission_on_tax_is_structural(cur, hotel_uae):
    s = hotel_uae.slug
    # 18% of the tax-inclusive RSR (room + SC + MF + VAT = 432.55 a night; TD is paid at the property)
    booking_stay(cur, s, ota="AGODA", commission=23358, pms="A1", ota_id="AG1")
    r = row(ec.compute_month(cur, s, SEP), "AGODA")
    assert r["commission_on_tax_minor"] == round(Decimal("0.18") * (2695 + 2060) * 3)
    ec.rollup_month(cur, s, SEP)
    out = ec.read_months(cur, s, SEP, SEP)
    assert row(out["rows"], "AGODA")["commission_on_tax_kind"] == "structural"


def test_rate_overcharge_is_not_called_tax(cur, hotel_uae):
    s = hotel_uae.slug
    pg.channel_cfg(cur, s, mf=True, td=True)
    booking_stay(cur, s, commission=25000)          # 20% instead of 15%: L2's business, not tax
    assert row(ec.compute_month(cur, s, SEP), "BOOKING")["commission_on_tax_minor"] == 0


def test_fx_agx_and_programme_costs(cur, hotel_uae):
    s = hotel_uae.slug
    booking_stay(cur, s, programs=("GENIUS",), gross=90000)
    pg.program(cur, s, "GENIUS", "10")
    booking_stay(cur, s, ota="AGODA", commission=23358, pms="A1", ota_id="AG1", programs=("AGX",))
    cur.execute("INSERT INTO pfg_ota_programs (hotel_slug, ota_code, program, kind, fixed_minor, currency) "
                "VALUES (%s, 'AGODA', 'AGX', 'PER_BOOKING_COST', 2000, 'AED')", (s,))
    repo.upsert_finding(cur, s, {"detector": "L11", "fingerprint": "fx1", "ota_code": "BOOKING", "class": "PREVENTABLE",
                                 "subject_ref": "month:2026-09|fx", "amount_minor": 500, "currency": "AED",
                                 "confidence": "0.8"})
    rows = ec.compute_month(cur, s, SEP)
    b, a = row(rows, "BOOKING"), row(rows, "AGODA")
    assert b["fx_loss_minor"] == 500 and b["program_cost_minor"] == 10000     # 900 sold at 10% off a 1,000 public
    assert b["avg_discount_depth"] == Decimal("0.1000")
    assert a["cancellation_cost_minor"] == 2000
    assert b["net_minor"] == b["room_rev_minor"] - b["commission_minor"] - b["payment_cost_minor"] - 500


def test_direct_channel_pays_the_booking_engine_fee(cur, hotel_uae):
    s = hotel_uae.slug
    cur.execute("INSERT INTO pfg_channel_config (hotel_slug, ota_code, configured_from, booking_engine_fee_pct, "
                "card_fee_pct) VALUES (%s, 'DIRECT', '2026-01-01', 3, 2)", (s,))
    pg.reservation(cur, s, "D1", channel="DIRECT")
    r = row(ec.compute_month(cur, s, SEP), "DIRECT")
    assert r["commission_minor"] == 0 and r["commission_on_tax_minor"] == 0
    assert r["payment_cost_minor"] == round(Decimal(r["gross_minor"]) * Decimal("0.05"))


def test_rollup_stores_reads_and_heals(cur, hotel_uae):
    s = hotel_uae.slug
    booking_stay(cur, s)
    pg.reservation(cur, s, "D1", channel="DIRECT")
    assert len(ec.rollup_month(cur, s, SEP)) == 2
    ec.rollup_month(cur, s, SEP)                      # idempotent
    cur.execute("SELECT COUNT(*) AS n FROM pfg_channel_economics WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 2
    out = ec.read_months(cur, s, SEP, SEP)
    b = row(out["rows"], "BOOKING")
    assert b["month"] == "2026-09" and b["net_adr_minor"] == round(Decimal(b["net_minor"]) / 3)
    assert b["waterfall"][0]["label"] == "Gross" and b["waterfall"][-1]["value_minor"] == b["net_minor"]
    assert b["gross_minor"] + sum(st["value_minor"] for st in b["waterfall"][1:-1]) == b["net_minor"]   # steps add up
    cur.execute("UPDATE reservations SET status = 'CANCELLED' WHERE hotel_slug = %s AND pms_res_id = 'D1'", (s,))
    ec.rollup_month(cur, s, SEP)
    cur.execute("SELECT channel FROM pfg_channel_economics WHERE hotel_slug = %s", (s,))
    assert [r["channel"] for r in cur.fetchall()] == ["BOOKING"]
