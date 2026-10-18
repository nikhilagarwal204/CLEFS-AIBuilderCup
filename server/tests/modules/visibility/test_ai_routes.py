"""AI radar console routes (04 spec 5.11): the tab, runs with clickable citations, facts and fixes."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))
BASE = "/hotels/test-dubai/visibility/ai"


@pytest.fixture
def seeded(cur, hotel_uae, clock):
    from clefs.db import Json
    from clefs.modules.visibility import facts

    clock.freeze(NOW)
    cur.execute("INSERT INTO vis_ai_batches (hotel_slug, kind, week, status) VALUES (%s, 'weekly', '2026-09-28', "
                "'ready') RETURNING id", (hotel_uae.slug,))
    batch = cur.fetchone()["id"]
    cites = [{"url": "https://www.booking.com/h", "title": "Booking", "domain": "booking.com", "cls": "ota",
              "kind": "citation", "start_index": 0, "end_index": 4},
             {"url": "https://testhoteldubai.example/", "title": None, "domain": "testhoteldubai.example",
              "cls": "official", "kind": "source"}]
    cur.execute(
        """
        INSERT INTO vis_ai_runs (batch_id, batch_kind, hotel_slug, prompt_key, prompt_intent, prompt_text, market,
            language, engine, model_id, run_at, status, answer_text, citations, entities, self_mentioned, self_position)
        VALUES (%s, 'weekly', %s, 'ae.en.generic.1', 'generic', 'Best hotels?', 'AE', 'en', 'serpapi_ai_mode',
                'google_ai_mode', NOW(), 'ok', 'Test Hotel Dubai is great.', %s, %s, TRUE, 1) RETURNING id
        """,
        (batch, hotel_uae.slug, Json(cites), Json([{"key": "self", "position": 1}])),
    )
    run_id = cur.fetchone()["id"]
    fact = facts.record_fact(cur, hotel_uae.slug, fact_key="checkin_time", claimed="12:00", quote="Check-in is noon",
                             truth_value="14:00", error_type="wrong_value", engine="serpapi_ai_mode", run_id=run_id,
                             cited_domains=["booking.com"])
    return {"run": run_id, "fact": fact}


def test_tab_shows_setup_checklist_without_keys(client_as, seeded, monkeypatch):
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    from clefs.settings import reload_settings

    reload_settings()
    r = client_as("owner").get(BASE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["enabled"] is False and body["setup_missing"]
    assert {e["engine"] for e in body["engines"]} == {"gemini_web_search", "perplexity_sonar", "serpapi_ai_mode"}
    assert body["facts"][0]["fact_key"] == "checkin_time"
    assert body["labels"]["third_party"].startswith("Google AI Mode via SerpApi")


def test_run_detail_renders_every_citation_as_a_link(client_as, seeded):
    r = client_as("gm").get(f"{BASE}/runs/{seeded['run']}")
    assert r.status_code == 200, r.text
    run = r.json()
    assert run["label"] == "Google AI Mode via SerpApi (third-party); indicative"
    assert run["model_id"] == "google_ai_mode" and run["run_at"]
    assert len(run["citations"]) == 2 and all(c["url"].startswith("https://") for c in run["citations"])
    assert client_as("gm").get(f"{BASE}/runs/999999").status_code == 404


def test_view_permission_is_required(client_as, seeded):
    assert client_as("finance").get(BASE).status_code == 403


def test_create_fix_routes_by_cited_domain_and_marks_fixing(client_as, seeded, cur):
    c = client_as("revenue")
    r = c.post(f"{BASE}/facts/{seeded['fact']['id']}/fix")
    assert r.status_code == 201, r.text
    act = r.json()["action"]
    assert act["type"] == "visibility.ota_listing_fix" and act["params"]["fact_key"] == "checkin_time"
    listed = c.get(f"{BASE}/facts").json()["facts"]
    assert listed[0]["status"] == "fixing" and listed[0]["action_id"] == act["id"]


def test_dismiss(client_as, seeded):
    c = client_as("owner")
    assert c.post(f"{BASE}/facts/{seeded['fact']['id']}/dismiss").json()["status"] == "dismissed"
    assert c.get(f"{BASE}/facts").json()["facts"] == []
    assert c.post(f"{BASE}/facts/{seeded['fact']['id']}/fix").status_code == 409


def test_accept_a_suggested_baseline_reset(client_as, seeded, cur):
    from clefs.core import clock
    from clefs.modules.visibility import ai_radar

    ai_radar.epoch_for(cur, "test-dubai", "serpapi_ai_mode", "google_ai_mode")
    cur.execute("INSERT INTO vis_engine_versions (hotel_slug, engine, version_key, baseline_epoch, first_seen_at, "
                "reason, status) VALUES ('test-dubai', 'serpapi_ai_mode', 'source_shift:2026-09-28', 2, %s, 'x', "
                "'suggested')", (clock.now(),))
    c = client_as("gm")
    r = c.post(f"{BASE}/engines/serpapi_ai_mode/baseline", json={"version_key": "source_shift:2026-09-28"})
    assert r.status_code == 200, r.text
    assert r.json()["epoch"] == 2
    assert ai_radar.epoch_for(cur, "test-dubai", "serpapi_ai_mode", "google_ai_mode") == 2  # later answers
    assert c.post(f"{BASE}/engines/serpapi_ai_mode/baseline", json={"version_key": "nope"}).status_code == 404
    assert client_as("revenue").post(f"{BASE}/engines/serpapi_ai_mode/baseline",
                                     json={"version_key": "x"}).status_code == 403
