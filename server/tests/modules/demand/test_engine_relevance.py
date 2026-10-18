"""Demand Radar relevance model (02 spec 4.3): night mapping, distance decay,
corridor, Hijri widening, class weights, and calibration with the 2026
baseline exclusion."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.demand import events, relevance as rel

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
DEIRA = (25.2697, 55.3095)
DWTC = (25.2285, 55.2866)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    from clefs.core import hotels
    from clefs.modules.demand import curated

    clock.freeze(NOW)
    curated.sync(cur, force=True)  # venues with their corridor tags
    cur.execute("UPDATE hotels SET lat = %s, lng = %s WHERE slug = %s", (*DEIRA, hotel_uae.slug))
    return hotels.get_hotel(cur, hotel_uae.slug, fresh=True)


def _event(cur, hotel_slug=None, **kw):
    base = {"title": "Zephyr Widget Summit", "category": "EXPO", "start": date(2026, 12, 7), "end": date(2026, 12, 11),
            "venue_codes": ["DWTC"], "attendance": 150_000}
    base.update(kw)
    return events.ingest(cur, base, source="CURATED", external_id=f"t:{base['title']}:{base['start']}",
                         hotel_slug=hotel_slug)["event_id"]


def test_mice_night_mapping():
    n = rel.nights_affected("CONFERENCE", date(2026, 12, 7), date(2026, 12, 11))
    assert n == {date(2026, 12, 6): 1.0, date(2026, 12, 7): 1.0, date(2026, 12, 8): 1.0, date(2026, 12, 9): 1.0,
                 date(2026, 12, 10): 0.6}
    assert rel.nights_affected("CONCERT", date(2026, 11, 20), date(2026, 11, 20)) == {date(2026, 11, 20): 1.0}
    fest = rel.nights_affected("FESTIVAL", date(2026, 12, 3), date(2026, 12, 6))  # Thu..Sun
    assert fest[date(2026, 12, 4)] == 1.0 and fest[date(2026, 12, 5)] == 1.0 and fest[date(2026, 12, 3)] == 0.5


def test_midweek_holiday_adds_the_bridge_and_weekend():
    # National Day 2-3 Dec 2026 (Wed-Thu): Fri 4 is the bridge, then Sat-Sun.
    n = rel.nights_affected("PUBLIC_HOLIDAY", date(2026, 12, 2), date(2026, 12, 3))
    assert [d.day for d, w in n.items() if w == 1.0] == [2, 3, 4, 5]
    assert n[date(2026, 12, 1)] == 0.5 and date(2026, 12, 6) not in n
    # A Tuesday holiday bridges the Monday.
    tue = rel.nights_affected("PUBLIC_HOLIDAY", date(2026, 12, 1), date(2026, 12, 1))
    assert date(2026, 11, 30) in tue and tue[date(2026, 11, 28)] == 1.0


def test_hijri_widening():
    fixed = rel.nights_affected("SPORTS", date(2027, 3, 10), date(2027, 3, 10))
    moon = rel.nights_affected("SPORTS", date(2027, 3, 10), date(2027, 3, 10), uncertainty_days=1, moon_dependent=True)
    assert set(fixed) == {date(2027, 3, 10)}
    assert moon == {date(2027, 3, 9): 0.5, date(2027, 3, 10): 1.0, date(2027, 3, 11): 0.5}


def test_distance_decay_and_components():
    assert rel.decay(0, "EXPO", 5000) == 1.0
    assert math.isclose(rel.decay(8, "EXPO", 5000), math.exp(-1))
    assert math.isclose(rel.decay(15, "EXPO", 50_000), math.exp(-1))
    assert math.isclose(rel.decay(25, "CITYWIDE", None), math.exp(-1))
    assert rel.decay(None, "PUBLIC_HOLIDAY", None) == 1.0
    assert rel.scale("EXPO", 100_000, None) == 1.0 and rel.scale("EXPO", None, 70) == 0.7
    assert rel.dow_fit("EXPO", date(2026, 12, 4)) == 0.6 and rel.dow_fit("EXPO", date(2026, 12, 7)) == 1.0
    assert rel.dow_fit("CONCERT", date(2026, 12, 7)) == 0.5 and rel.dow_fit("CONCERT", date(2026, 12, 5)) == 1.0
    assert rel.w_class(0, 0) == pytest.approx(2 / 3) and rel.w_class(0, 0, True) == 0.0
    assert rel.w_class(3, 0) > rel.w_class(0, 0) > rel.w_class(0, 3)
    assert rel.travel_band(20) == ("15-30 min", 0.7)


def test_score_uses_corridor_and_decays_with_distance(cur, hotel):
    near = _event(cur, title="Zephyr Widget Summit")
    far = _event(cur, title="Quokka Pump Fair", venue_codes=["ADNEC"])
    model = rel.HotelModel(cur, hotel, today=NOW.date())
    evs = {str(e["id"]): e for e in rel.visible_events(cur, hotel.slug, NOW.date(), NOW.date() + timedelta(days=90))}
    s_near, s_far = rel.score_event(model, evs[near]), rel.score_event(model, evs[far])
    assert s_near["score"] > 10 * s_far["score"] > 0  # ADNEC spillover to Deira decays
    assert s_near["corridor_match"] is False
    rel.put_settings(cur, hotel.slug, {"corridor_tags": ["deira_burdubai"]}, user_id=None)
    model = rel.HotelModel(cur, hotel, today=NOW.date())
    boosted = rel.score_event(model, evs[near])
    assert boosted["corridor_match"] is True and boosted["score"] == pytest.approx(s_near["score"] * 1.5, rel=1e-3)
    assert boosted["features"]["k_source"] == "prior" and boosted["expected_rn_uplift"] > 0
