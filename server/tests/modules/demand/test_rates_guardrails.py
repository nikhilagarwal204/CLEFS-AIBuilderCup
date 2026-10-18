"""BAR calendar, rate guardrails and demand.api.price_guardrails (02 spec 4.1, 4.3; 04 spec 4.6-4.7)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.demand import api as demand_api
from clefs.modules.demand import bar, guardrails
from clefs.modules.demand import comp_rates as cr

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
FRI = date(2026, 10, 2)   # ISO weekday 5, October
MON = date(2026, 10, 5)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae


def test_round5_clamp_step():
    assert guardrails.round5(44750, "AED") == 45000 and guardrails.round5(44749, "AED") == 44500
    assert guardrails.round5(123, "JPY") == 125
    assert guardrails.clamp(300, 400, 900) == 400 and guardrails.clamp(1000, 400, 900) == 900
    assert guardrails.clamp(500, None, None) == 500
    assert guardrails.step_cap(50000, 70000, 15) == 57500 and guardrails.step_cap(50000, 30000, 15) == 42500
    assert guardrails.step_cap(50000, 52000, 15) == 52000


def test_bar_set_changes_and_evidence(cur, hotel, clock):
    row, changed = bar.set_bar(cur, hotel, stay_date=FRI, room_type_code="dlx", bar_minor=45000, source="MANUAL")
    assert changed and row["room_type_code"] == "DLX" and row["bar"] == "450.00"
    _, changed = bar.set_bar(cur, hotel, stay_date=FRI, room_type_code="DLX", bar_minor=45000, source="CSV")
    assert not changed
    # Evidence never overrides the owner's figure while it is fresh...
    assert bar.record_rate_evidence(cur, hotel, stay_date=FRI, room_type_code="DLX", amount_minor=47000,
                                    currency="AED")["bar"] == "450.00"
    assert bar.record_rate_evidence(cur, hotel, stay_date=FRI, room_type_code="DLX", amount_minor=47000,
                                    currency="USD") is None
    # ...but does once it is older than a week.
    clock.advance(days=8)
    assert bar.record_rate_evidence(cur, hotel, stay_date=FRI, room_type_code=None, amount_minor=47000,
                                    currency="AED")["source"] == "RATE_EVIDENCE"
    hist = bar.changes(cur, hotel.slug, FRI)
    assert [(h["old"], h["new"], h["source"]) for h in hist] == [(None, "450.00", "MANUAL"),
                                                                 ("450.00", "470.00", "RATE_EVIDENCE")]
    assert bar.delete_bar(cur, hotel, FRI, "dlx", user_id=None) and bar.get_bar(cur, hotel.slug, FRI, "DLX") is None


def test_bar_rows_and_csv(cur, hotel):
    with pytest.raises(Exception) as e:
        bar.put_rows(cur, hotel, [{"stay_date": "2026-10-02", "room_type_code": "DLX", "bar": "0"}],
                     source="MANUAL", user_id=None)
    assert e.value.code == "invalid_bar"
    wide = "Date,DLX,STE\n02/10/2026,450,900\n03/10/2026,,950\n"
    out = bar.import_csv(cur, hotel, wide, user_id=None)
    assert out == {"written": 3, "changed": 3, "nights": 2, "room_types": ["DLX", "STE"]}
    long = "stay_date;room_type;rate\n2026-10-04;DLX;AED 425.50\n"
    assert bar.import_csv(cur, hotel, long, user_id=None)["written"] == 1
    rows = bar.list_bar(cur, hotel.slug, FRI, FRI + timedelta(days=2), "STE")
    assert [(r["stay_date"], r["bar"], r["source"]) for r in rows] == [("2026-10-02", "900.00", "CSV"),
                                                                       ("2026-10-03", "950.00", "CSV")]
    assert bar.reference_room_type(cur, hotel.slug) == "DLX"  # the hotel's only room type


def test_guardrail_rows_and_specificity(cur, hotel):
    rows = guardrails.upsert_rows(cur, hotel, [
        {"room_type_code": "*", "floor": "350", "ceiling": "1200"},
        {"room_type_code": "DLX", "floor": "400", "ceiling": "1500"},
        {"room_type_code": "DLX", "month": 10, "floor": "450", "ceiling": "1600", "max_step_pct": 10},
        {"room_type_code": "DLX", "month": 10, "dow": 5, "floor": "500", "ceiling": "1800"},
    ], user_id=None)
    assert len(rows) == 4
    assert guardrails.resolve(cur, hotel.slug, FRI, "DLX")["floor_minor"] == 50000
    assert guardrails.resolve(cur, hotel.slug, MON, "DLX")["floor_minor"] == 45000
    assert guardrails.resolve(cur, hotel.slug, date(2026, 11, 2), "DLX")["floor_minor"] == 40000
    assert guardrails.resolve(cur, hotel.slug, FRI, "STE")["floor_minor"] == 35000
    # Same scope again replaces rather than duplicates.
    rows = guardrails.upsert_rows(cur, hotel, [{"room_type_code": "*", "floor": "360", "ceiling": "1200"}],
                                  user_id=None)
    assert len(rows) == 4 and rows[0]["floor"] == "360.00"
    for bad in ({"floor": "900", "ceiling": "800"}, {"floor": "400", "ceiling": "800", "dow": 8},
                {"floor": "400", "ceiling": "800", "currency": "USD"}, {"floor": "x", "ceiling": "800"}):
        with pytest.raises(Exception) as e:
            guardrails.upsert_rows(cur, hotel, [bad], user_id=None)
        assert e.value.code == "invalid_guardrail"
    gone = guardrails.delete_row(cur, hotel.slug, rows[0]["id"])
    assert gone["room_type_code"] == "*"


def test_price_guardrails_sources(cur, hotel):
    assert demand_api.price_guardrails(cur, hotel.slug, FRI) is None  # nothing to go on
    assert demand_api.price_guardrails(cur, "nope", FRI) is None
    # fallback: indicative comp rates only
    comp, _ = cr.upsert_comp(cur, hotel.slug, {"name": "Comp A"})
    cr.record_rates(cur, hotel, [cr.CompRate(comp_id=comp["comp_id"], stay_date=FRI, lowest_minor=50000,
                                             currency="AED", source="SERPAPI", captured_at=NOW)])
    g = demand_api.price_guardrails(cur, hotel.slug, FRI)
    assert g["source"] == "fallback" and g["floor"] == "400.00" and g["ceiling"] == "650.00"
    assert g["bar"] is None and "no_bar" in g["reasons"] and g["confidence"] == 0.3
    assert g["comp"]["median"] == "500.00" and g["room_type_code"] == "DLX"
    # bar_only: BAR -10% / +30%, rounded to 5
    bar.set_bar(cur, hotel, stay_date=FRI, room_type_code="DLX", bar_minor=45300, source="MANUAL")
    g = demand_api.price_guardrails(cur, hotel.slug, FRI, "DLX")
    assert (g["source"], g["floor"], g["ceiling"], g["bar"]) == ("bar_only", "410.00", "590.00", "453.00")
    assert g["reasons"] == ["no_guardrails_configured"] and g["recommended"] is None
    assert datetime.fromisoformat(g["bar_captured_at"]) == NOW
    # rate_guardrails: the owner's row, BAR below its floor flagged
    guardrails.upsert_rows(cur, hotel, [{"room_type_code": "DLX", "floor": "480", "ceiling": "1500"}], user_id=None)
    g = demand_api.price_guardrails(cur, hotel.slug, FRI, "dlx")
    assert (g["source"], g["floor"], g["ceiling"], g["max_step_pct"]) == ("rate_guardrails", "480.00", "1500.00", "15")
    assert g["reasons"] == ["bar_below_floor"] and g["confidence"] == 0.9
    # The engine part fills demand_index: comp rates alone give a neutral 50.
    assert g["demand_index"] == 50
    assert set(g) >= {"floor", "ceiling", "recommended", "currency", "confidence", "reasons", "bar",
                      "bar_captured_at", "source", "demand_index"}


def test_price_guardrails_stale_bar_and_recommender(cur, hotel, clock):
    bar.set_bar(cur, hotel, stay_date=MON, room_type_code="DLX", bar_minor=50000, source="MANUAL")
    guardrails.upsert_rows(cur, hotel, [{"room_type_code": "*", "floor": "400", "ceiling": "600"}], user_id=None)
    clock.advance(days=8)
    calls = []

    def rec(c, slug, d, code):
        calls.append((slug, d, code))
        return {"recommended_minor": 99000, "id": "rec-1"}

    guardrails.register_recommender(rec)
    try:
        g = demand_api.price_guardrails(cur, hotel.slug, MON)
    finally:
        guardrails._recommenders.remove(rec)
    assert "bar_stale" in g["reasons"] and g["confidence"] == pytest.approx(0.7)
    assert g["recommended"] == "600.00" and g["recommendation_id"] == "rec-1"  # clamped to the ceiling
    assert calls == [(hotel.slug, MON, "DLX")]
