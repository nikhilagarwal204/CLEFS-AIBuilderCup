"""Visibility console routes, Google side (04 spec 5.11): permissions,
settings, setup states and the action proposals."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))


@pytest.fixture
def base(hotel_uae, clock):
    clock.freeze(NOW)
    return f"/hotels/{hotel_uae.slug}/visibility"


@pytest.mark.parametrize("path", ["/google", "/rank", "/gbp", "/site-health", "/attribution", "/settings"])
def test_tabs_read_for_view_roles(client_as, base, path):
    r = client_as("revenue").get(base + path)
    assert r.status_code == 200, r.text
    assert client_as("front_desk").get(base + path).status_code == 403


def test_google_tab_labels_and_setup(client_as, base):
    body = client_as("gm").get(base + "/google").json()
    assert body["labels"]["disclosure"].startswith("Google Hotels data comes from SerpApi")
    assert body["labels"]["parity"] == "indicative, third-party data"
    assert "6064419" in body["labels"]["price_accuracy"]
    assert "property_token" in body["setup_missing"] and body["parity"] == []


def test_settings_put_is_manage_only_and_partial(client_as, base):
    r = client_as("revenue").put(base + "/settings", json={"area": "Deira"})
    assert r.status_code == 403
    gm = client_as("gm")
    r = gm.put(base + "/settings", json={"area": "Deira", "landmarks": ["Gold Souk"], "serpapi_enabled": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["area"] == "Deira" and body["serpapi_enabled"] is False and body["grid_size"] == 7
    assert body["gbp"]["status"] == "project_not_approved" and body["budget"]["budget_micros"] == 30_000_000
    r = gm.put(base + "/settings", json={"place_id": "ChIJ1"})
    assert r.json()["area"] == "Deira" and r.json()["place_id"] == "ChIJ1"
    assert gm.put(base + "/settings", json={"gbp_location": "accounts/1"}).status_code == 422
    assert gm.put(base + "/settings", json={"rank_keywords": ["a", "b", "c", "d", "e", "f"]}).status_code == 422
    rank = gm.get(base + "/rank").json()
    assert "place_id" not in rank["setup_missing"] and "area" not in rank["setup_missing"]


def test_gbp_tab_and_picker_before_connect(client_as, base):
    owner = client_as("owner")
    gbp = owner.get(base + "/gbp").json()
    assert gbp["state"]["message"] == "not connected (project not approved, 0 QPM)"
    assert gbp["kpis"] == [] and gbp["utm"]["status"] is None
    r = owner.get(base + "/gbp/locations")
    assert r.status_code == 409 and r.json()["code"] == "project_not_approved"
    assert client_as("revenue").get(base + "/gbp/locations").status_code == 403


def test_actions_need_act_and_a_connection(client_as, base):
    assert client_as("front_desk").post(base + "/actions/utm-patch").status_code == 403
    r = client_as("revenue").post(base + "/actions/utm-patch")
    assert r.status_code == 409 and r.json()["code"] == "gbp_project_not_approved"
    r = client_as("gm").post(base + "/actions/lodging-sync")
    assert r.status_code == 409


def test_attribution_month_query(client_as, base, cur, hotel_uae):
    cur.execute("INSERT INTO vis_attribution_monthly (hotel_slug, month, gbp_website_clicks, direct_bookings, "
                "currency) VALUES (%s, '2026-09-01', 10, 2, 'AED')", (hotel_uae.slug,))
    body = client_as("gm").get(base + "/attribution?month=2026-09-15").json()
    assert body["months"][0]["line"] == "Google sent 10 website clicks → 2 direct bookings"
