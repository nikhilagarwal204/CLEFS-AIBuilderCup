"""HTTP routes of the rates side: /hotels/{slug}/demand/... (README.md, rates section)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clefs.core import serpapi

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))


@pytest.fixture
def owner(cur, clock, client_as, hotel_uae, monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.delenv("SERPAPI_MONTHLY_BUDGET_USD", raising=False)
    reload_settings()
    clock.freeze(NOW)
    c = client_as("owner")
    c.base = f"/hotels/{hotel_uae.slug}/demand"
    yield c
    serpapi.set_transport(None)
    monkeypatch.undo()
    reload_settings()


def test_permissions(owner, client_as, hotel_uae):
    base = f"/hotels/{hotel_uae.slug}/demand"
    fd = client_as("front_desk")
    for path in ("/comp-set", "/comp-rates", "/bar", "/guardrails", "/weather", "/rates/settings"):
        assert fd.get(base + path).status_code == 403, path
    rev = client_as("revenue")
    assert rev.get(base + "/guardrails").status_code == 200
    fin = client_as("finance").put(base + "/guardrails", json={"rows": [{"floor": "1", "ceiling": "2"}]})
    assert fin.status_code == 403


def test_settings_and_comp_set(owner):
    r = owner.get(f"{owner.base}/rates/settings").json()
    assert r["area_queries"] == [] and r["effective_area_queries"] == ["hotels in Dubai"]
    r = owner.put(f"{owner.base}/rates/settings", json={"area_queries": ["hotels in Deira Dubai"],
                                                         "self_property_token": "tokSELF"}).json()
    assert r["effective_area_queries"] == ["hotels in Deira Dubai"] and r["fallback_calls_max"] == 5
    assert owner.put(f"{owner.base}/rates/settings", json={"fallback_calls_max": 99}).status_code == 422
    c = owner.post(f"{owner.base}/comp-set", json={"name": "Sheraton Dubai Creek", "property_token": "tokSHER",
                                                     "lat": 25.25, "lng": 55.33, "stars": 5})
    assert c.status_code == 201 and c.json()["lng"] == 55.33
    cid = c.json()["comp_id"]
    dup = owner.post(f"{owner.base}/comp-set", json={"name": "X", "property_token": "tokSHER"})
    assert dup.json()["code"] == "comp_exists"
    edited = owner.patch(f"{owner.base}/comp-set/{cid}", json={"aliases": ["Sheraton Creek"]})
    assert edited.json()["aliases"] == ["Sheraton Creek"]
    assert owner.patch(f"{owner.base}/comp-set/nope", json={"name": "Y"}).json()["code"] == "comp_not_found"
    assert owner.delete(f"{owner.base}/comp-set/{cid}").json()["active"] is False
    assert owner.get(f"{owner.base}/comp-set").json() == []
    assert len(owner.get(f"{owner.base}/comp-set", params={"include_inactive": True}).json()) == 1


def test_discover_and_fetch_queue(owner, run_jobs):
    fake = serpapi.use_fake(default={"properties": [
        {"type": "hotel", "name": "Ad", "property_token": "ad", "sponsored": True},
        {"type": "hotel", "name": "Hyatt Regency", "property_token": "tokH", "extracted_hotel_class": 5,
         "gps_coordinates": {"latitude": 25.23, "longitude": 55.32}, "rate_per_night": {"extracted_lowest": 612}},
        {"type": "hotel", "name": "Test Hotel Dubai", "property_token": "tokSELF"}]})
    owner.put(f"{owner.base}/rates/settings", json={"self_property_token": "tokSELF"})
    r = owner.post(f"{owner.base}/comp-set/discover", json={}).json()
    assert r["query"] == "hotels in Dubai" and r["check_in"] == "2026-10-06" and r["label"] == "Indicative"
    assert [(c["name"], c["rate"], c["is_self"]) for c in r["candidates"]] == [
        ("Hyatt Regency", "612.00", False), ("Test Hotel Dubai", None, True)]
    owner.post(f"{owner.base}/comp-set", json={"name": "Hyatt Regency", "property_token": "tokH"})
    q = owner.post(f"{owner.base}/comp-rates/fetch", params={"days": 3})
    assert q.status_code == 202 and q.json()["queued"] and q.json()["to"] == "2026-10-01"
    run_jobs(["dr.comp_rates_fetch_more"])
    cal = owner.get(f"{owner.base}/comp-rates", params={"from": "2026-09-29", "to": "2026-10-05"}).json()
    assert [n["stay_date"] for n in cal["nights"]] == ["2026-09-29", "2026-09-30", "2026-10-01"]
    assert cal["nights"][0]["median"] == "612.00" and cal["label"] == "Indicative"
    night = owner.get(f"{owner.base}/comp-rates/2026-09-30", params={"history": True}).json()
    assert night["rates"][0]["rate"] == "612.00" and len(night["history"]) == 1
    assert fake.calls == 4  # one discover page (cached for the 06 Oct night) + three nights
    owner.post(f"{owner.base}/comp-rates/fetch")
    assert owner.post(f"{owner.base}/comp-rates/fetch").json()["code"] == "rate_limited"


def test_comp_rates_import(owner):
    wide = "Date,Hyatt Regency,Sheraton Dubai Creek\n2026-10-01,612,Sold out\n"
    r = owner.post(f"{owner.base}/comp-rates/import", json={"content": wide, "source": "rategain"}).json()
    assert r["rates"] == 2 and r["source"] == "CSV_RATEGAIN" and r["sold_out"] == 1
    r = owner.post(f"{owner.base}/comp-rates/import", files={"file": ("lighthouse.csv", wide.encode(), "text/csv")})
    assert r.json()["source"] == "CSV_LIGHTHOUSE"
    assert owner.post(f"{owner.base}/comp-rates/import", json={"nope": 1}).json()["code"] == "invalid_body"
    big = "x" * 2_000_001
    assert owner.post(f"{owner.base}/comp-rates/import", json={"content": big}).status_code == 413


def test_bar_routes(owner):
    one = {"stay_date": "2026-10-02", "room_type_code": "dlx", "bar": "450"}
    r = owner.put(f"{owner.base}/bar", json={"rows": [one]})
    assert r.json() == {"written": 1, "changed": 1, "nights": 1, "room_types": ["DLX"]}
    r = owner.post(f"{owner.base}/bar/import", json={"content": "date,room_type,rate\n2026-10-02,DLX,480\n"})
    assert r.json()["changed"] == 1
    cal = owner.get(f"{owner.base}/bar", params={"from": "2026-10-01", "to": "2026-10-05"}).json()
    assert cal["reference_room_type"] == "DLX"
    assert cal["rows"][0]["bar"] == "480.00" and cal["rows"][0]["source"] == "CSV"
    ch = owner.get(f"{owner.base}/bar/changes", params={"stay_date": "2026-10-02"}).json()
    assert [(c["old"], c["new"]) for c in ch] == [(None, "450.00"), ("450.00", "480.00")]
    assert owner.put(f"{owner.base}/bar", json={"rows": [{"stay_date": "2026-10-02", "room_type_code": "DLX",
                                                          "bar": "-1"}]}).json()["code"] == "invalid_bar"
    assert owner.delete(f"{owner.base}/bar/2026-10-02/dlx").json()["deleted"]
    assert owner.delete(f"{owner.base}/bar/2026-10-02/dlx").json()["code"] == "bar_not_found"


def test_guardrail_routes_and_preview(owner):
    r = owner.put(f"{owner.base}/guardrails", json={"rows": [
        {"room_type_code": "DLX", "floor": "400", "ceiling": "1500"},
        {"room_type_code": "DLX", "month": 12, "floor": "600", "ceiling": "2500", "max_step_pct": 20}]})
    assert r.status_code == 200 and len(r.json()["rows"]) == 2
    assert owner.put(f"{owner.base}/guardrails", json={"rows": [{"floor": "500", "ceiling": "100"}]}).json()["code"] \
        == "invalid_guardrail"
    owner.put(f"{owner.base}/bar", json={"rows": [{"stay_date": "2026-12-05", "room_type_code": "DLX",
                                                   "bar": "700"}]})
    g = owner.get(f"{owner.base}/guardrails/preview", params={"stay_date": "2026-12-05"}).json()["guardrails"]
    assert (g["source"], g["floor"], g["ceiling"], g["bar"], g["max_step_pct"]) == \
        ("rate_guardrails", "600.00", "2500.00", "700.00", "20")
    rid = r.json()["rows"][0]["id"]
    assert owner.delete(f"{owner.base}/guardrails/{rid}").json()["id"] == rid
    assert owner.delete(f"{owner.base}/guardrails/{rid}").json()["code"] == "guardrail_not_found"


def test_weather_route(owner, cur, hotel_uae):
    from clefs.modules.demand import weather

    weather._memo_clear()
    weather.store(cur, hotel_uae.slug, [{"at_hour": datetime(2026, 9, 29, 6, tzinfo=ZoneInfo("UTC")),
                                         "temp_c": 41.2, "apparent_c": 45.0, "humidity_pct": 40, "precip_mm": 0,
                                         "weather_code": 0, "wind_kmh": 10}], fetched_at=NOW)
    r = owner.get(f"{owner.base}/weather").json()
    assert r["now_c"] == 41.2 and r["days"][0]["max_c"] == 41.2 and r["provider"] == "open-meteo"
    assert r["coordinates"] == {"lat": 25.2048, "lon": 55.2708}
    weather._memo_clear()
