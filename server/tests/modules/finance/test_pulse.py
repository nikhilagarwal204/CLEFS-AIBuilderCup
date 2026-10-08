"""Weekly pulse (04 spec 6.1): goldens, n/a denominators, SC treatment."""

from __future__ import annotations

from datetime import date

from clefs.modules.finance import pulse
from tests.modules.finance.conftest import dist_row

WEEK = date(2026, 9, 14)  # a Monday


def test_pulse_golden(cur, hotel_uae, occupancy, neighbours):
    # ARN 560 = 80 x 7, OccRN 350 = 50 x 7, RoomsRev 98,000 = 14,000 x 7
    occupancy(hotel_uae.slug, WEEK, 7, 80, 50, 1_400_000)
    neighbours.dist = [
        dist_row(WEEK, "BOOKING", True, 1_050_000, 84_000),
        dist_row(WEEK, "DIRECT", False, 0, 100_000, 26_000),
    ]
    neighbours.cxl = [{"cancel_date": WEEK, "channel_code": "BOOKING", "cost_minor": 115_000, "currency": "AED"}]
    neighbours.recovery = 20_000
    p = pulse.compute_week(cur, hotel_uae.slug, WEEK)
    assert p["available_rn"] == 560 and p["occupied_rn"] == 350
    assert p["rooms_revenue_minor"] == 9_800_000
    assert p["distribution_cost_minor"] == 1_260_000
    assert p["nrevpar_minor"] == 15_250          # 152.50
    assert p["revpar_minor"] == 17_500           # 175.00
    assert p["ota_cost_por_minor"] == 3_240      # 32.40
    assert p["cancellation_leakage_minor"] == 115_000
    assert p["recovery_cost_minor"] == 20_000
    api = pulse.to_api(p)
    assert api["nrevpar"] == "152.50" and api["ota_cost_por"] == "32.40"
    pulse.store_week(cur, hotel_uae.slug, p)
    again = pulse.stored_week(cur, hotel_uae.slug, WEEK)
    assert again["nrevpar_minor"] == 15_250 and again["ota_cost_minor"] == 1_134_000


def test_pulse_zero_denominators(cur, hotel_uae, occupancy, neighbours):
    # No occupancy rows at all: ARN is NULL, every ratio n/a, no error.
    p = pulse.compute_week(cur, hotel_uae.slug, WEEK)
    assert p["available_rn"] is None and p["nrevpar_minor"] is None and p["revpar_minor"] is None
    # ARN = 0 and OccRN = 0
    occupancy(hotel_uae.slug, WEEK, 7, 0, 0, 0)
    neighbours.dist = [dist_row(WEEK, "BOOKING", True, 1000)]
    p = pulse.compute_week(cur, hotel_uae.slug, WEEK)
    assert p["available_rn"] == 0 and p["nrevpar_minor"] is None and p["ota_cost_por_minor"] is None
    pulse.store_week(cur, hotel_uae.slug, p)
    assert pulse.to_api(p)["nrevpar"] is None


def test_pulse_stub_neighbours_give_na(cur, hotel_uae, occupancy, neighbours):
    occupancy(hotel_uae.slug, WEEK, 7, 80, 50, 1_400_000)
    p = pulse.compute_week(cur, hotel_uae.slug, WEEK)   # [] from Profit Guard: Dist and leakage n/a
    assert p["distribution_cost_minor"] is None and p["nrevpar_minor"] is None
    assert p["cancellation_leakage_minor"] is None and p["revpar_minor"] == 17_500


def test_api_defaults_for_an_empty_hotel(cur, hotel_uae):
    from clefs.modules.finance import api

    assert api.weekly_pulse(cur, hotel_uae.slug, week_start=WEEK) is None
    assert api.monthly_summary(cur, hotel_uae.slug, month=date(2026, 8, 1)) is None


def test_weekly_pulse_sc_follows_profit_guard(cur, hotel_uae, occupancy, neighbours):
    from clefs.modules.finance import api

    occupancy(hotel_uae.slug, WEEK, 7, 10, 1)
    cur.execute("""INSERT INTO reservations (hotel_slug, pms_res_id, channel_code, check_in, check_out, nights,
                       status, currency) VALUES (%s, 'R1', NULL, %s, %s, 1, 'CHECKED_OUT', 'AED') RETURNING id""",
                (hotel_uae.slug, WEEK, date(2026, 9, 15)))
    rid = cur.fetchone()["id"]
    cur.execute("""INSERT INTO reservation_nights (hotel_slug, reservation_id, stay_date, rate_minor, sc_minor, vat_minor,
                       td_minor, currency, stayed) VALUES (%s, %s, %s, 50000, 5000, 2750, 2000, 'AED', TRUE)""",
                (hotel_uae.slug, rid, WEEK))
    neighbours.sc = True
    assert pulse.compute_week(cur, hotel_uae.slug, WEEK)["rooms_revenue_minor"] == 55_000
    neighbours.sc = False
    assert pulse.compute_week(cur, hotel_uae.slug, WEEK)["rooms_revenue_minor"] == 50_000   # taxes never count
    assert api.weekly_pulse(cur, hotel_uae.slug, week_start=WEEK)["rooms_revenue"] == "500.00"
