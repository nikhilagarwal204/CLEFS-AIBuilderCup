"""Variance rollup, the rolling 7-day window and the three-condition spike
rule (02 spec 5.2 and the 5.3 test list)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from clefs.core import signals
from clefs.modules.amenities import catalog, detectors, variance

from .conftest import at

END = date(2026, 9, 28)                      # yesterday (the rollup's last business date)
START = END - timedelta(days=62)             # 9 weeks


def _soap(cur, slug):
    return catalog.create_item(cur, slug, "AED", catalog.ItemIn(
        key="TEST_SOAP", name_en="Test soap", category="BATH", basis="PER_OCCUPIED_ROOM_NIGHT",
        std_qty=Decimal("1"), unit_cost=Decimal("1.00")))


def _history(issue, item, *, extra_last_week: int = 0, monday_buffer: bool = False):
    """50 rooms a night, one soap each: issue exactly what is used, except
    the options for the current week."""
    for i in range(63):
        d = START + timedelta(days=i)
        in_last_week = d > END - timedelta(days=7)
        qty = 50
        if monday_buffer and in_last_week:
            # Two days on the Monday (22 Sep), nothing the next day.
            qty = 100 if d == date(2026, 9, 22) else (0 if d == date(2026, 9, 23) else 50)
        if qty:
            issue(item, qty, at(d))
    if extra_last_week:
        issue(item, extra_last_week, at(END))


def test_closet_buffer_two_days_on_monday_does_not_spike(cur, hotel_uae, frozen, occ_range, issue):
    item = _soap(cur, hotel_uae.slug)
    occ_range(START, 63, 50)
    _history(issue, item, monday_buffer=True)
    variance.rollup(cur, hotel_uae, START, END)
    cur.execute("SELECT variance_units FROM amn_variance_daily WHERE item_id = %s AND business_date = '2026-09-22'",
                (item["id"],))
    assert cur.fetchone()["variance_units"] == 50          # the day itself looks high...
    out = detectors.detect_spikes(cur, hotel_uae, END)
    assert out["fired"] == 0                               # ...the 7-day window does not


def test_spike_fires_when_all_three_conditions_hold(cur, hotel_uae, frozen, occ_range, issue):
    item = _soap(cur, hotel_uae.slug)
    occ_range(START, 63, 50)
    _history(issue, item, extra_last_week=200)
    variance.rollup(cur, hotel_uae, START, END)
    out = detectors.detect_spikes(cur, hotel_uae, END)
    assert out["fired"] == 1
    sig = signals.list_signals(cur, hotel_uae.slug, modules=["amenities"])
    s = next(x for x in sig if x["kind"] == detectors.SPIKE)
    assert s["impact_minor"] == 20000 and s["section"] == "money_lost"      # 200 x AED 1, 7-day impact
    assert "a month" in s["summary"]                                        # projection only in the text
    assert s["recommended_action"]["type"] == detectors.INVESTIGATE


def test_spike_needs_money_above_aed_100(cur, hotel_uae, frozen, occ_range, issue):
    item = _soap(cur, hotel_uae.slug)
    occ_range(START, 63, 50)
    _history(issue, item, extra_last_week=90)               # 26% over, z high, but AED 90
    variance.rollup(cur, hotel_uae, START, END)
    assert detectors.detect_spikes(cur, hotel_uae, END)["fired"] == 0


def test_evaluate_spike_rule_is_three_conditions():
    cur = {"expected": Decimal(350), "issued": Decimal(420), "variance": Decimal(70), "variance_minor": Decimal(15000)}
    steady = [Decimal(v) for v in (0, 5, -5, 3, -2, 4, 1, -1)]
    assert detectors.evaluate_spike(cur, steady, min_minor=10000)["fires"]
    noisy = [Decimal(v) for v in (60, -50, 80, -70, 65, -60, 75, -55)]
    ev = detectors.evaluate_spike(cur, noisy, min_minor=10000)
    assert ev["ratio_ok"] and ev["money_ok"] and not ev["z_ok"] and not ev["fires"]
    assert not detectors.evaluate_spike(cur, steady[:3], min_minor=10000)["fires"]   # too little history
    small = {**cur, "variance": Decimal(40), "issued": Decimal(390)}                 # 11% over
    assert not detectors.evaluate_spike(small, steady, min_minor=10000)["ratio_ok"]


def test_returns_count_as_negative_and_vip_damage_are_excluded(cur, hotel_uae, frozen, occ, issue):
    item = _soap(cur, hotel_uae.slug)
    occ(END, 10)
    issue(item, 15, at(END))
    issue(item, 4, at(END, 11), reason="RETURN")           # typed positive, stored negative
    issue(item, 3, at(END, 12), reason="VIP")
    issue(item, 2, at(END, 13), reason="DAMAGE")
    variance.rollup(cur, hotel_uae, END, END)
    cur.execute("SELECT * FROM amn_variance_daily WHERE item_id = %s", (item["id"],))
    r = cur.fetchone()
    assert r["issued"] == 11 and r["issued_excluded"] == 5 and r["expected_total"] == 10
    assert r["variance_units"] == 1 and r["variance_minor"] == 100


def test_missing_occupancy_day_is_excluded_not_zero_filled(cur, hotel_uae, frozen, occ, issue):
    item = _soap(cur, hotel_uae.slug)
    d1, d2 = END - timedelta(days=1), END
    occ(d1, 10)                                            # no occupancy row for d2
    issue(item, 10, at(d1))
    issue(item, 10, at(d2))
    out = variance.rollup(cur, hotel_uae, d1, d2)
    assert out["days"] == 1 and out["days_missing_occupancy"] == 1
    cur.execute("SELECT business_date FROM amn_variance_daily WHERE item_id = %s", (item["id"],))
    assert [r["business_date"] for r in cur.fetchall()] == [d1]
    cur.execute("SELECT COUNT(*) AS n FROM amn_daily_totals WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 1


def test_requests_add_to_expected_by_floor(cur, hotel_uae, frozen, items, occ, make_request):
    from clefs.modules.amenities import requests

    occ(END, 8, floors={"1": 5, "2": 3})
    rid = make_request("102", subtype="2 toothbrushes", at=at(END, 20))
    requests.alias_pass(cur, hotel_uae.slug, since=at(END - timedelta(days=1)))
    variance.rollup(cur, hotel_uae, END, END)
    cur.execute("SELECT floor, requested, expected_total FROM amn_variance_daily WHERE item_id = %s",
                (items["DENTAL_KIT"]["id"],))
    assert [(r["floor"], r["requested"]) for r in cur.fetchall()] == [("1", 2)]
    cur.execute("SELECT floor, basis_count FROM amn_variance_daily WHERE item_id = %s ORDER BY floor",
                (items["SHAMPOO"]["id"],))
    assert [(r["floor"], r["basis_count"]) for r in cur.fetchall()] == [("1", 5), ("2", 3)]
    assert rid


# ─── Guest-night basis counts stayed nights only ───


def test_guest_nights_leave_out_nights_after_an_early_departure(cur, hotel_uae, frozen, occ, issue):
    """A CHECKED_OUT stay booked for 3 nights that left after 2 keeps its
    third reservation_nights row with stayed = FALSE. That night must not add
    guests to expected use, or the issued units look explained and a leak
    hides behind a room nobody slept in."""
    from clefs.core.stays import upsert_reservation

    d0 = END - timedelta(days=2)
    upsert_reservation(cur, hotel_uae.slug, {
        "pms_res_id": "AMN-EARLY-1", "guest_name": "Early Leaver", "check_in": d0, "check_out": END + timedelta(days=1),
        "actual_check_out": END, "status": "CHECKED_OUT", "adults": 2, "children": 1, "rooms_count": 1,
        "currency": "AED", "room_revenue_minor": 90000,
    })
    upsert_reservation(cur, hotel_uae.slug, {
        "pms_res_id": "AMN-GONE-1", "guest_name": "Never Came", "check_in": END, "check_out": END + timedelta(days=1),
        "status": "NO_SHOW", "adults": 4, "rooms_count": 1, "currency": "AED", "room_revenue_minor": 30000,
    })
    cur.execute("SELECT stay_date, stayed FROM reservation_nights n JOIN reservations r ON r.id = n.reservation_id "
                "WHERE r.pms_res_id = 'AMN-EARLY-1' ORDER BY stay_date")
    assert [(r["stay_date"], r["stayed"]) for r in cur.fetchall()] == [(d0, True), (END - timedelta(days=1), True),
                                                                         (END, False)]
    g = variance.guest_nights(cur, hotel_uae.slug, d0, END)
    assert g == {d0: Decimal(3), END - timedelta(days=1): Decimal(3)}       # END: unstayed and no-show only

    item = catalog.create_item(cur, hotel_uae.slug, "AED", catalog.ItemIn(
        key="TEST_SLIPPERS", name_en="Test slippers", category="BATH", basis="PER_GUEST_NIGHT",
        std_qty=Decimal("1"), unit_cost=Decimal("1.00")))
    d1 = END - timedelta(days=1)
    occ(d1, 1)
    issue(item, 5, at(d1))
    variance.rollup(cur, hotel_uae, d1, d1)
    cur.execute("SELECT basis_count, expected_total, variance_units FROM amn_variance_daily WHERE item_id = %s",
                (item["id"],))
    r = cur.fetchone()
    assert (r["basis_count"], r["expected_total"], r["variance_units"]) == (3, 3, 2)
