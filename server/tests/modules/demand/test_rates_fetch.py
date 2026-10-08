"""Comp set, the comp-rate writer and the SerpApi fetch run (02 spec 4.1-4.2, 04 spec F5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.core import serpapi
from clefs.modules.demand import api as demand_api
from clefs.modules.demand import comp_rates as cr

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
TODAY = date(2026, 9, 29)


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def comps(cur, hotel_uae, clock):
    clock.freeze(NOW)
    a, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "Hyatt Regency Dubai Creek Heights",
                                                "property_token": "tokHYATT", "stars": 5})
    b, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "Sheraton Dubai Creek", "property_token": "tokSHER",
                                                "aliases": ["Sheraton Creek"]})
    return a, b


def _page(hyatt=612, sher=None):
    props = [{"type": "hotel", "name": "Hyatt Regency Dubai Creek Heights", "property_token": "tokHYATT",
              "rate_per_night": {"extracted_lowest": hyatt}}]
    if sher is not None:
        props.append({"type": "hotel", "name": "Sheraton Dubai Creek", "property_token": "tokSHER",
                      **({"rate_per_night": {"extracted_lowest": sher}} if sher else {})})
    return {"properties": props}


def test_fetch_plan_is_about_sixty_nights():
    dates = cr.fetch_dates(TODAY)
    assert dates[:31] == [TODAY + timedelta(days=i) for i in range(31)]
    far = dates[31:]
    assert all((d - TODAY).days % 3 == 0 and 31 <= (d - TODAY).days <= 120 for d in far)
    assert len(dates) == 61
    # Tomorrow's far nights are the ones not fetched today (every night comes up every third day).
    assert not set(cr.fetch_dates(TODAY + timedelta(days=1))[31:]) & set(far)


def test_comp_set_crud_and_api(cur, hotel_uae, comps):
    a, b = comps
    assert a["comp_id"] == "hyatt-regency-dubai-creek-heights" and a["stars"] == 5.0
    with pytest.raises(Exception) as e:
        cr.upsert_comp(cur, hotel_uae.slug, {"name": "Dup", "property_token": "tokSHER"})
    assert e.value.code == "comp_exists"
    c, created = cr.upsert_comp(cur, hotel_uae.slug, {"name": "Hyatt Regency Dubai Creek Heights"})
    assert created and c["comp_id"] == "hyatt-regency-dubai-creek-heights-2"
    cr.deactivate_comp(cur, hotel_uae.slug, c["comp_id"])
    api_rows = demand_api.comp_set(cur, hotel_uae.slug)
    assert [r["comp_id"] for r in api_rows] == [a["comp_id"], b["comp_id"]]
    assert set(api_rows[0]) == {"comp_id", "name", "property_token", "place_id", "lat", "lng", "stars", "aliases"}
    assert cr.match_comp(cr.list_comps(cur, hotel_uae.slug), name="The Sheraton Creek Hotel")["comp_id"] == b["comp_id"]


def test_area_queries_default_and_settings(cur, hotel_uae):
    assert demand_api.area_queries(cur, hotel_uae.slug) == ["hotels in Dubai"]
    s = cr.put_rate_settings(cur, hotel_uae.slug,
                             {"area_queries": [" hotels in  Deira Dubai ", "HOTELS IN DEIRA DUBAI"]}, user_id=None)
    assert s["area_queries"] == ["hotels in Deira Dubai"]
    assert demand_api.area_queries(cur, hotel_uae.slug) == ["hotels in Deira Dubai"]
    assert demand_api.area_queries(cur, "no-such-hotel") == []
    with pytest.raises(Exception) as e:
        cr.put_rate_settings(cur, hotel_uae.slug, {"area_queries": ["a", "b", "c", "d"]}, user_id=None)
    assert e.value.code == "invalid_settings"


def test_run_fetch_through_core_serpapi(cur, hotel_uae, comps, env):
    env(SERPAPI_KEY="test-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    fake = serpapi.use_fake(_page(612, 0), _page(640, 505))
    try:
        out = cr.run_fetch(cur, hotel_uae, dates=[TODAY + timedelta(days=1), TODAY + timedelta(days=2)])
    finally:
        serpapi.set_transport(None)
    assert out["nights"] == 2 and out["written"] == 4 and out["adapters"]["SERPAPI"]["area_calls"] == 2
    sent = fake.requests[0]
    assert sent["engine"] == "google_hotels" and sent["q"] == "hotels in Dubai" and sent["gl"] == "ae"
    assert sent["check_in_date"] == "2026-09-30" and sent["check_out_date"] == "2026-10-01"
    assert sent["currency"] == "AED" and sent["adults"] == "2" and sent["api_key"] == "***"
    cur.execute("SELECT purpose, billed FROM serpapi_calls ORDER BY id")
    calls = cur.fetchall()
    assert {r["purpose"] for r in calls} == {"demand.comp_rates"}
    night1 = demand_api.comp_rates(cur, hotel_uae.slug, TODAY + timedelta(days=1))
    assert [(r["competitor"][:5], r["rate"], r["sold_out"]) for r in night1] == [
        ("Hyatt", "612.00", False), ("Shera", None, True)]
    assert night1[0]["indicative"] and night1[0]["label"] == "Indicative" and night1[0]["source"] == "SERPAPI"
    snap = demand_api.comp_rates_snapshot(cur, hotel_uae.slug, stay_dates=[TODAY + timedelta(days=2), TODAY])
    assert list(snap) == ["2026-10-01"] and {r["rate"] for r in snap["2026-10-01"]} == {"640.00", "505.00"}
    cal = cr.calendar(cur, hotel_uae.slug, TODAY, TODAY + timedelta(days=5))
    assert cal[0]["sold_out"] == 1 and cal[0]["median"] == "612.00"
    assert cal[1]["min"] == "505.00" and cal[1]["max"] == "640.00" and cal[1]["median"] == "572.50"


def test_same_day_refetch_replaces_and_budget_skips(cur, hotel_uae, comps, env, clock):
    env(SERPAPI_KEY="test-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    fake = serpapi.use_fake(_page(600, 500), _page(590, 480))
    d = TODAY + timedelta(days=3)

    def count():
        cur.execute("SELECT count(*) AS n FROM dr_comp_rates WHERE stay_date = %s", (d,))
        return cur.fetchone()["n"]

    try:
        cr.run_fetch(cur, hotel_uae, dates=[d])
        cr.run_fetch(cur, hotel_uae, dates=[d])  # served from the shared cache, same capture day
        assert fake.calls == 1 and count() == 2
        clock.advance(hours=21)  # past the 20 h cache: the next local day
        cr.run_fetch(cur, hotel_uae, dates=[d])
    finally:
        serpapi.set_transport(None)
    assert fake.calls == 2 and count() == 4  # one row per comp per capture day: the history is kept
    assert [r["rate"] for r in demand_api.comp_rates(cur, hotel_uae.slug, d)] == ["480.00", "590.00"]
    env(SERPAPI_MONTHLY_BUDGET_USD="0")
    out = cr.run_fetch(cur, hotel_uae, dates=[TODAY + timedelta(days=9), TODAY + timedelta(days=10)])
    assert out["adapters"]["SERPAPI"]["stopped"] == "budget_exhausted" and out["written"] == 0


def test_not_configured_and_no_comp_set(cur, hotel_uae, env, clock):
    clock.freeze(NOW)
    assert cr.run_fetch(cur, hotel_uae, dates=[TODAY]) == {"skipped": "no_comp_set"}
    cr.upsert_comp(cur, hotel_uae.slug, {"name": "X", "property_token": "tokX"})
    env(SERPAPI_KEY=None)
    out = cr.run_fetch(cur, hotel_uae, dates=[TODAY])
    assert out["adapters"]["SERPAPI"]["stopped"] == "not_configured"
    cr.put_rate_settings(cur, hotel_uae.slug, {"comp_fetch_enabled": False}, user_id=None)
    assert cr.run_fetch(cur, hotel_uae, dates=[TODAY]) == {"skipped": "disabled"}


def test_missing_comp_gets_a_budgeted_property_call(cur, hotel_uae, comps, env):
    env(SERPAPI_KEY="test-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    cr.put_rate_settings(cur, hotel_uae.slug, {"fallback_calls_max": 1}, user_id=None)

    def answer(params):
        if params.get("property_token") == "tokSHER":
            return {"type": "hotel", "name": "Sheraton Dubai Creek",
                    "prices": [{"source": "Agoda", "rate_per_night": {"extracted_lowest": 455}}]}
        return _page(612)  # the area page lists only the Hyatt

    serpapi.use_fake(answer, answer, answer, answer)
    try:
        near, far = TODAY + timedelta(days=2), TODAY + timedelta(days=20)
        out = cr.run_fetch(cur, hotel_uae, dates=[near, far, TODAY + timedelta(days=3)])
    finally:
        serpapi.set_transport(None)
    rep = out["adapters"]["SERPAPI"]
    assert rep["area_calls"] == 3 and rep["fallback_calls"] == 1  # capped, and never beyond 14 days
    got = {r["competitor"][:5]: r["rate"] for r in demand_api.comp_rates(cur, hotel_uae.slug, near)}
    assert got == {"Hyatt": "612.00", "Shera": "455.00"}
    assert [r["competitor"][:5] for r in demand_api.comp_rates(cur, hotel_uae.slug, far)] == ["Hyatt"]
