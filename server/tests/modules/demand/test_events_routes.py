"""HTTP routes of the events side: /hotels/{slug}/demand/... (README.md)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.demand import curated

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
ICS = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x1@test\r\nSUMMARY:Pharma Leaders Summit\r\n"
       b"DTSTART;VALUE=DATE:20261021\r\nDTEND;VALUE=DATE:20261023\r\nLOCATION:Dubai World Trade Centre\r\n"
       b"END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.fixture
def owner(cur, clock, client_as, hotel_uae, monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.delenv("PREDICTHQ_TOKEN", raising=False)
    reload_settings()
    clock.freeze(NOW)
    curated.sync(cur, force=True)
    c = client_as("owner")
    c.base = f"/hotels/{hotel_uae.slug}/demand"
    return c


def test_reads_need_demand_view(owner, client_as, hotel_uae):
    fd = client_as("front_desk")
    assert fd.get(f"/hotels/{hotel_uae.slug}/demand/events").status_code == 403
    r = owner.get(f"{owner.base}/events", params={"from": "2026-12-01", "to": "2026-12-31", "category": "expo"})
    assert r.status_code == 200
    titles = {e["title"]: e for e in r.json()}
    assert "GITEX Global" in titles and "Dubai Shopping Festival" not in titles
    assert titles["GITEX Global"]["sources"][0]["source"] == "CURATED" and titles["GITEX Global"]["actionable"]
    assert owner.get(f"{owner.base}/events", params={"category": "PARTY"}).json()["code"] == "invalid_category"
    assert owner.get(f"{owner.base}/events", params={"from": "2026-12-01", "to": "2026-11-01"}).status_code == 400
    one = owner.get(f"{owner.base}/events/{titles['GITEX Global']['id']}").json()
    assert one["changes"] == [] and one["venue_codes"] == ["DEC"]
    assert owner.get(f"{owner.base}/events/not-a-uuid").json()["code"] == "event_not_found"
    assert owner.get(f"{owner.base}/events/lookup", params={"q": "gitex week"}).json()["match"]["name"] == "GITEX Global"
    assert owner.get(f"{owner.base}/events/upcoming", params={"days": 30, "min_score": 0}).status_code == 200


def test_manual_event_lifecycle(owner):
    r = owner.post(f"{owner.base}/events", json={"title": "Wedding block (Al Habtoor family)", "category": "COMMUNITY",
                                                 "start": "2026-11-05", "end": "2026-11-07"})
    assert r.status_code == 201, r.text
    eid = r.json()["id"]
    r = owner.patch(f"{owner.base}/events/{eid}", json={"start": "2026-11-12", "end": "2026-11-14",
                                                        "reason": "rescheduled"})
    assert r.json()["status"] == "RESCHEDULED" and r.json()["original_start"] == "2026-11-05"
    assert owner.post(f"{owner.base}/events/{eid}/confirm").json()["owner_confirmed"] is True
    assert owner.delete(f"{owner.base}/events/{eid}").json()["deleted"] is True
    bad = owner.post(f"{owner.base}/events", json={"title": "x", "category": "NOPE", "start": "2026-11-05"})
    assert bad.status_code == 422


def test_ics_import_json_and_multipart(owner):
    r = owner.post(f"{owner.base}/events/import-ics", json={"content": ICS.decode(), "filename": "a.ics"})
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1 and r.json()["created"] == 1
    r = owner.post(f"{owner.base}/events/import-ics", files={"file": ("a.ics", ICS, "text/calendar")})
    assert r.json()["created"] == 0 and r.json()["imported"] == 1
    assert owner.post(f"{owner.base}/events/import-ics", json={"nope": 1}).json()["code"] == "invalid_body"


def test_reference_data_routes(owner):
    venues = owner.get(f"{owner.base}/venues").json()
    assert {"DWTC", "DEC", "ADNEC", "YAS_MARINA"} <= {v["code"] for v in venues}
    hol = owner.get(f"{owner.base}/holidays", params={"from": "2026-11-01", "to": "2026-11-30", "country": "IN"}).json()
    assert [h["name"] for h in hol] == ["Diwali"]
    assert owner.get(f"{owner.base}/source-markets").json()["is_default"] is True
    r = owner.put(f"{owner.base}/source-markets", json={"markets": [{"market_code": "GCC", "share_pct": 40}]})
    assert r.status_code == 200 and r.json()["markets"][0]["share_pct"] == "40.00"
    mh = owner.get(f"{owner.base}/holidays", params={"from": "2026-11-01", "to": "2026-11-30", "markets_only": True})
    assert {h["market_code"] for h in mh.json()} == {"GCC"}
    assert owner.get(f"{owner.base}/features").json()["days"] == {}


def test_sources_status_and_rate_limited_sync(owner):
    st = owner.get(f"{owner.base}/sources/status").json()
    assert st["configured"]["predicthq"] is False
    assert any(r["source"] == "CURATED" for r in st["runs"])
    r = owner.post(f"{owner.base}/sources/sync", json={"source": "curated"})
    assert r.status_code == 200 and r.json()["result"]["skipped"] is True
    r = owner.post(f"{owner.base}/sources/sync", json={"source": "predicthq"})
    assert r.json()["result"] == {"skipped": "no_token"}
    for _ in range(2):
        owner.post(f"{owner.base}/sources/sync", json={"source": "curated"})
    assert owner.post(f"{owner.base}/sources/sync", json={"source": "curated"}).status_code == 429
