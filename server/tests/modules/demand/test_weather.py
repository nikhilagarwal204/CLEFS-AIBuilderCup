"""Open-Meteo weather cache and demand.api.outdoor_temp_c (Canon 12)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from clefs.modules.demand import adapters, weather
from clefs.modules.demand import api as demand_api

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))  # 06:00 UTC
T0 = int(datetime(2026, 9, 29, 0, 0, tzinfo=timezone.utc).timestamp())


def _body(hours=48, base=30.0):
    return {"latitude": 25.2, "longitude": 55.27, "hourly_units": {"temperature_2m": "°C"},
            "hourly": {"time": [T0 + 3600 * i for i in range(hours)],
                       "temperature_2m": [base + (i % 24) * 0.5 for i in range(hours)],
                       "apparent_temperature": [base + 3 for _ in range(hours)],
                       "relative_humidity_2m": [55] * hours, "precipitation": [0.0] * hours,
                       "weather_code": [0] * hours, "wind_speed_10m": [12.4] * hours}}


@pytest.fixture
def meteo(clock):
    clock.freeze(NOW)
    weather._memo_clear()
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=_body())

    adapters.set_transport(httpx.MockTransport(handler))
    yield seen
    adapters.set_transport(None)
    weather._memo_clear()


def test_parse_hourly_drops_gaps():
    body = _body(3)
    body["hourly"]["temperature_2m"][1] = None
    rows = weather.parse_hourly(body)
    assert [r["at_hour"].hour for r in rows] == [0, 2] and str(rows[1]["temp_c"]) == "31.0"
    assert rows[0]["humidity_pct"] == 55 and str(rows[0]["wind_kmh"]) == "12.4"
    assert weather.parse_hourly({"error": True}) == [] and weather.parse_hourly({"hourly": {}}) == []


def test_sync_and_outdoor_temp(cur, hotel_uae, meteo, monkeypatch):
    monkeypatch.delenv("OPEN_METEO_API_KEY", raising=False)
    out = weather.sync_hotel(cur, hotel_uae)
    # No coordinates on the hotel: the Dubai centroid.
    assert out == {"hours": 48, "lat": 25.2048, "lon": 55.2708}
    q = meteo[0].url.params
    assert meteo[0].url.host == "api.open-meteo.com" and "apikey" not in q
    assert q["timeformat"] == "unixtime" and q["timezone"] == "GMT" and q["forecast_days"] == "7"
    # 06:00 UTC = hour 6 = 33.0 C; 06:40 rounds to the nearest cached hour (07:00, 33.5).
    assert demand_api.outdoor_temp_c(hotel_uae, NOW) == 33.0
    assert demand_api.outdoor_temp_c(hotel_uae.slug, NOW + timedelta(minutes=40)) == 33.5
    assert demand_api.outdoor_temp_c(hotel_uae, datetime(2026, 9, 29, 6, 0)) == 33.0  # naive = UTC
    # Outside the cached range: None (Service falls back to its heuristic).
    assert demand_api.outdoor_temp_c(hotel_uae, NOW + timedelta(days=5)) is None
    assert demand_api.outdoor_temp_c("no-such-hotel", NOW) is None
    assert demand_api.outdoor_temp_c(None, NOW) is None


def test_outdoor_temp_is_memoised_and_refreshed_on_store(cur, hotel_uae, meteo):
    weather.sync_hotel(cur, hotel_uae)
    assert demand_api.outdoor_temp_c(hotel_uae, NOW) == 33.0
    cur.execute("UPDATE dr_weather SET temp_c = 45.5 WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert demand_api.outdoor_temp_c(hotel_uae, NOW) == 33.0  # memo (10 minutes)
    weather.store(cur, hotel_uae.slug, weather.parse_hourly(_body(48, base=40.0)), fetched_at=NOW)
    assert demand_api.outdoor_temp_c(hotel_uae, NOW) == 43.0  # a store clears the hotel's memo


def test_daily_summary_and_commercial_key(cur, hotel_uae, meteo, monkeypatch):
    monkeypatch.setenv("OPEN_METEO_API_KEY", "k-123")
    weather.sync_hotel(cur, hotel_uae)
    assert meteo[0].url.host == "customer-api.open-meteo.com" and meteo[0].url.params["apikey"] == "k-123"
    days = weather.daily(cur, hotel_uae, date(2026, 9, 29), date(2026, 9, 30))
    # Hotel-local days: 29 Sep Dubai = 28 Sep 20:00 UTC to 29 Sep 20:00 UTC (hours 0-19 cached).
    assert days[0]["date"] == "2026-09-29" and days[0]["hours"] == 20 and days[0]["max_c"] == 39.5
    assert days[1]["hours"] == 24 and days[1]["min_c"] == 30.0
    s = weather.status(cur, hotel_uae)
    assert s["hours_cached"] == 48 and s["commercial_key"] and not s["from_hotel"]


def test_weather_job_and_retention(cur, hotel_uae, meteo, run_jobs, clock):
    from clefs import jobs
    from clefs.core import clock as core_clock

    job = next(j for j in jobs.registered_jobs() if j.name == "dr.weather_fetch")
    assert job.per_hotel
    weather.sync_hotel(cur, hotel_uae)
    clock.advance(days=16)
    assert weather.purge_old(cur, now=core_clock.now()) == 48


def test_no_coordinates_is_skipped(cur, hotel_uae, meteo):
    class NoWhere:
        slug, lat, lng, emirate = hotel_uae.slug, None, None, "atlantis"

    assert weather.sync_hotel(cur, NoWhere()) == {"skipped": "no_coordinates"}
    assert meteo == []
