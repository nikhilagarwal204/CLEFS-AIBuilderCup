"""Map rank grid (04 spec 5.3, 5.12): Places IDs-only mask, ARP/ATRP/SoLV
goldens, calibration drift and the serpapi_enabled switch."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 10, 5, 4, 0, tzinfo=ZoneInfo("Asia/Dubai"))  # Monday
SELF = "place-self"


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
def hotel(cur, hotel_uae, clock, env):
    from clefs.modules.visibility import store

    clock.freeze(NOW)
    env(GOOGLE_MAPS_API_KEY="maps-key", SERPAPI_KEY="serp-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    cur.execute("UPDATE hotels SET lat = 25.2697, lng = 55.3095 WHERE slug = %s", (hotel_uae.slug,))
    from clefs.core import hotels

    hotels.clear_cache()
    store.update_profile(cur, hotel_uae.slug, {"place_id": SELF, "area": "Deira", "landmarks": ["Gold Souk"]},
                         user_id=None)
    cur.execute("INSERT INTO vis_competitors (hotel_slug, comp_id, name, place_id) VALUES (%s, 'c1', 'Comp', 'p-c1')",
                (hotel_uae.slug,))
    return hotels.get_hotel(cur, hotel_uae.slug)


@pytest.fixture
def places(hotel):
    """Answers Places with an ordering chosen per call; records requests."""
    from clefs.modules.visibility import http

    state = {"requests": [], "order": lambda body: [SELF, "p-c1", "x1"]}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        state["requests"].append({"headers": dict(request.headers), "body": body, "url": str(request.url)})
        return httpx.Response(200, json={"places": [{"id": i} for i in state["order"](body)]})

    http.set_transport(httpx.MockTransport(handle))
    yield state
    http.set_transport(None)


# ─── Contract and pure parts ───


def test_places_field_mask_is_exactly_ids_only():
    from clefs.modules.visibility import rank_grid

    headers, body = rank_grid.places_request("hotel Deira", 25.0, 55.0)
    assert rank_grid.FIELD_MASK == "places.id" and headers["X-Goog-FieldMask"] == "places.id"
    assert body == {
        "textQuery": "hotel Deira",
        "locationBias": {"circle": {"center": {"latitude": 25.0, "longitude": 55.0}, "radius": 1000.0}},
        "rankPreference": "RELEVANCE", "includedType": "lodging", "strictTypeFiltering": True, "pageSize": 20,
        "regionCode": "AE", "languageCode": "en",
    }
    assert rank_grid.places_request("فندق في ديرة", 25.0, 55.0)[1]["languageCode"] == "ar"


def test_metrics_golden():
    from clefs.modules.visibility.rank_grid import metrics

    # 30 found points summing to 150 (9 of them in the top 3), 19 not found.
    found = [1, 1, 1, 2, 2, 2, 3, 3, 3] + [6] * 20 + [12]
    assert len(found) == 30 and sum(found) == 150
    m = metrics(found + [None] * 19)
    assert m["points"] == 49 and m["found"] == 30
    assert m["arp"] == Decimal("5.00") and m["atrp"] == Decimal("11.20") and m["solv"] == Decimal("0.1837")


def test_keywords_from_templates_skip_missing_inputs(hotel):
    from clefs.modules.visibility.rank_grid import keywords

    kws = keywords({"area": "Deira", "landmarks": ["Gold Souk"]}, hotel)
    assert kws == ["hotel Deira", "hotels near Gold Souk", "4 star hotel Deira", "فندق في Deira"]
    assert keywords({"area": None, "landmarks": []}, hotel) == []
    assert keywords({"rank_keywords": ["a", "b"]}, hotel) == ["a", "b"]


def test_grid_is_7x7_centred():
    from clefs.modules.visibility.rank_grid import grid_points, subsample

    pts = grid_points(25.0, 55.0, 7, 800)
    assert len(pts) == 49 and pts[24] == (25.0, 55.0)
    assert pts[0][0] > 25.0 and pts[0][1] < 55.0      # north-west first
    assert subsample(7) == [0, 3, 6, 21, 24, 27, 42, 45, 48]


def test_spearman_and_top3():
    from clefs.modules.visibility.rank_grid import spearman, top3_agreement

    assert spearman(["a", "b", "c", "d"], ["a", "b", "c", "d"]) == 1
    assert spearman(["a", "b", "c"], ["c", "b", "a"]) == -1
    assert spearman(["a", "b"], ["a", "b"]) is None
    assert top3_agreement(["a", "b", "c"], ["a", "x", "c"]) == Decimal(2) / 3


# ─── Runs ───


def test_run_keyword_stores_run_points_and_comp_metrics(cur, hotel, places):
    from clefs.modules.visibility import rank_grid, store

    profile = store.get_profile(cur, hotel.slug)
    out = rank_grid.run_keyword(cur, hotel, profile, "hotel Deira")
    assert out["calls"] == 49 and out["found"] == 49 and out["solv"] == "1.0000"
    assert all(r["headers"]["x-goog-fieldmask"] == "places.id" for r in places["requests"])
    assert all(r["headers"]["x-goog-api-key"] == "maps-key" for r in places["requests"])
    cur.execute("SELECT count(*) AS n FROM vis_rank_points WHERE run_id = %s", (out["run_id"],))
    assert cur.fetchone()["n"] == 49
    cur.execute("SELECT comp_metrics FROM vis_rank_runs WHERE id = %s", (out["run_id"],))
    assert cur.fetchone()["comp_metrics"]["c1"]["arp"] == "2.00"


def test_run_keyword_not_found_ranks_21(cur, hotel, places):
    from clefs.modules.visibility import rank_grid, store

    places["order"] = lambda body: ["x1", "x2"]
    out = rank_grid.run_keyword(cur, hotel, store.get_profile(cur, hotel.slug), "hotel Deira")
    assert out["found"] == 0 and out["atrp"] == "21.00" and out["arp"] is None


def test_rank_drop_needs_two_weeks(cur, hotel, clock):
    from clefs.core import signals
    from clefs.modules.visibility import rank_grid

    def run(solv, days):
        cur.execute("INSERT INTO vis_rank_runs (hotel_slug, run_at, keyword, source, solv) "
                    "VALUES (%s, now() - make_interval(days => %s), 'k', 'places_ids', %s)", (hotel.slug, days, solv))

    run("0.50", 21)
    run("0.30", 14)
    run("0.45", 7)                                     # recovered one week: no drop
    assert rank_grid.check_drop(cur, hotel, "k") is False
    run("0.20", 0)
    cur.execute("DELETE FROM vis_rank_runs WHERE solv = 0.45")
    assert rank_grid.check_drop(cur, hotel, "k") is True
    keys = [s["dedupe_key"] for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])]
    assert "vis.rank_drop:k" in keys


def test_calibration_flags_drift_and_serpapi_off_is_uncalibrated(cur, hotel, places):
    from clefs.core import serpapi
    from clefs.modules.visibility import rank_grid, store

    store.update_profile(cur, hotel.slug, {"rank_keywords": ["hotel Deira"]}, user_id=None)
    places["order"] = lambda body: ["a", "b", "c", "d", "e"]
    fake = serpapi.use_fake(default={"local_results": [{"place_id": x} for x in ["e", "d", "c", "b", "a"]]})
    try:
        out = rank_grid.calibrate(cur, hotel)
    finally:
        serpapi.set_transport(None)
    assert out == {"hotel Deira": "drift"} and fake.calls == 9
    assert all(r["engine"] == "google_maps" and r["type"] == "search" and r["gl"] == "ae" for r in fake.requests)
    assert fake.requests[0]["ll"].startswith("@") and fake.requests[0]["ll"].endswith(",14z")
    st = rank_grid.calibration_status(cur, hotel.slug, "hotel Deira", serpapi_enabled=True)
    assert st["status"] == "drift" and st["note"] == "ordering may differ from Maps"

    store.update_profile(cur, hotel.slug, {"serpapi_enabled": False}, user_id=None)
    assert rank_grid.calibrate(cur, hotel) == {"skipped": "serpapi_disabled"}
    ov = rank_grid.overview(cur, hotel)
    assert ov["keywords"][0]["calibration"]["status"] == "uncalibrated"
