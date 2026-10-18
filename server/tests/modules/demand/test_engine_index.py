"""Demand index (02 spec 4.3): the logistic mix, the 2026 baseline exclusion,
calibration from own pickup with priors below 5 events, pace and comp
inputs, and demand.api.demand_index."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.demand import api as demand_api
from clefs.modules.demand import demand_index as di
from clefs.modules.demand import events, relevance as rel

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
DEIRA = (25.2697, 55.3095)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    from clefs.core import hotels
    from clefs.modules.demand import curated

    clock.freeze(NOW)
    curated.sync(cur, force=True)
    cur.execute("UPDATE hotels SET lat = %s, lng = %s WHERE slug = %s", (*DEIRA, hotel_uae.slug))
    return hotels.get_hotel(cur, hotel_uae.slug, fresh=True)


def _occ(cur, slug, d, occupied, available=13):
    cur.execute(
        "INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, rooms_occupied, room_revenue_minor, currency, "
        "source) VALUES (%s, %s, %s, %s, %s, 'AED', 'MANUAL') ON CONFLICT (hotel_slug, date) DO UPDATE SET "
        "rooms_occupied = EXCLUDED.rooms_occupied", (slug, d, available, occupied, occupied * 40000))


def test_combine_is_a_logistic_mix():
    assert di.combine(0, 0, 0, 0) == 50
    assert di.combine(1, 3, 1, 1) == round(100 / (1 + 2.718281828 ** -(0.35 + 0.9 + 0.2 + 0.15)))
    assert di.combine(-4, 0, -4, -4) < 25


def test_baseline_skips_the_2026_exclusion(cur, hotel):
    for d in (date(2025, 4, 7), date(2025, 4, 14)):   # Mondays in April 2025
        _occ(cur, hotel.slug, d, 10)
    _occ(cur, hotel.slug, date(2026, 4, 6), 1)          # April 2026: conflict months, excluded
    hist = rel.baseline_history(cur, hotel.slug)
    assert date(2026, 4, 6) not in hist
    b = rel.baseline_for(hist, date(2027, 4, 5))
    assert b["n"] == 2 and b["mean"] == pytest.approx(10 / 13)
    full = rel.occupancy_history(cur, hotel.slug, date(2025, 1, 1), date(2026, 12, 31))
    assert date(2026, 4, 6) not in full


def test_calibration_uses_priors_below_five_events(cur, hotel):
    out = rel.calibrate(cur, hotel, today=NOW.date())
    assert out["EXPO"]["source"] == "prior" and out["EXPO"]["k"] == rel.K_PRIORS["EXPO"]
    [row] = [c for c in rel.calibration(cur, hotel.slug) if c["category"] == "EXPO"]
    assert row["confidence_cap"] == 0.6


def test_calibration_from_own_pickup(cur, hotel):
    # Five past Monday-to-Friday expos near the hotel, each lifting its nights to
    # 12/13 against a 2025 baseline of about 6/13 on the same weekdays.
    base_days = [date(2025, 5, 1) + timedelta(days=i) for i in range(0, 60)]
    for d in base_days:
        _occ(cur, hotel.slug, d, 6)
    for i, start in enumerate([date(2025, 5, 5), date(2025, 5, 12), date(2025, 5, 19), date(2025, 5, 26),
                               date(2025, 6, 2)]):
        events.ingest(cur, {"title": f"Pump Trade Expo {i}", "category": "EXPO", "start": start,
                            "end": start + timedelta(days=3), "venue_codes": ["DWTC"], "attendance": 20000},
                      source="CURATED", external_id=f"cal:{i}")
        for k in range(-1, 3):
            _occ(cur, hotel.slug, start + timedelta(days=k), 12)
    out = rel.calibrate(cur, hotel, today=NOW.date())
    assert out["EXPO"]["source"] == "own" and out["EXPO"]["n_events"] == 5 and out["EXPO"]["k"] > 0


def test_index_rises_with_a_nearby_show_and_sold_out_comps(cur, hotel):
    ev = events.ingest(cur, {"title": "Zephyr Widget Summit", "category": "CONFERENCE", "start": date(2026, 10, 13),
                             "end": date(2026, 10, 15), "venue_codes": ["DWTC"], "attendance": 60000},
                       source="CURATED", external_id="zw:1")["event_id"]
    rel.compute(cur, hotel, today=NOW.date())
    nights = {n["stay_date"]: n for n in di.compute(cur, hotel, start=date(2026, 10, 10), days=7)}
    show, quiet = nights["2026-10-13"], nights["2026-10-10"]
    assert show["demand_index"] > quiet["demand_index"] >= 50
    assert show["top_event_ids"][0] == ev and show["components"]["events"][0]["distance_km"] < 6
    assert di.stored(cur, hotel.slug, date(2026, 10, 13), date(2026, 10, 13))[date(2026, 10, 13)]["demand_index"] \
        == show["demand_index"]
    # demand.api.demand_index reads the stored value
    assert demand_api.demand_index(cur, hotel.slug, stay_date=date(2026, 10, 13)) == show["demand_index"]


def test_api_demand_index_is_none_without_any_input(cur, hotel_uae, clock):
    clock.freeze(NOW)
    assert demand_api.demand_index(cur, hotel_uae.slug, stay_date=date(2026, 11, 3)) is None
    assert demand_api.demand_index(cur, "no-such-hotel", stay_date=date(2026, 11, 3)) is None
