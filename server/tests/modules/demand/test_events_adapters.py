"""Demand event adapters: PredictHQ Events and Features, Ticketmaster and AI
discovery parse their fixtures, and every source merges into one event
(02 spec 4.4 tests: "GITEX arriving from PredictHQ, curated and AI sources
merges into one event with 3 sources")."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from clefs import db as clefs_db
from clefs.ai import AIResponse, Citation
from clefs.modules.demand import curated, events
from clefs.modules.demand import adapters
from clefs.modules.demand.adapters import ai_web, phq_events, phq_features, ticketmaster

FIX = Path(events.__file__).with_name("fixtures")
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
DEIRA = (25.2697, 55.3095)


def fixture(name: str) -> dict:
    return json.loads((FIX / name).read_text())


@pytest.fixture
def hotel(cur, clock, hotel_uae):
    from clefs.core.hotels import get_hotel, update_hotel

    clock.freeze(NOW)
    update_hotel(cur, hotel_uae.slug, {"lat": DEIRA[0], "lng": DEIRA[1]})
    return get_hotel(cur, hotel_uae.slug, fresh=True)


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            monkeypatch.setenv(k, str(v)) if v is not None else monkeypatch.delenv(k, raising=False)
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


def _by_source(cur, source: str, ext: str) -> dict | None:
    row = clefs_db.fetch_one("SELECT event_id FROM dr_event_sources WHERE source = %s AND external_id = %s",
                             (source, ext), cur=cur)
    return row and clefs_db.fetch_one("SELECT * FROM dr_events WHERE id = %s", (row["event_id"],), cur=cur)


# ─── PredictHQ Events ───


def test_phq_parse_fixture():
    rows = {e["id"]: phq_events.parse_event(e) for e in fixture("phq_events_dubai.json")["results"]}
    g = rows["phqGitex2026"]
    assert g["category"] == "EXPO" and (g["start"], g["end"]) == (date(2026, 12, 7), date(2026, 12, 11))
    assert g["attendance"] == 180000 and g["spend_minor"] == 4500000050 and g["verification"] == "VERIFIED"
    assert (g["lat"], g["lon"]) == (24.964, 55.148)
    assert rows["phqDup"] is None  # deleted as a duplicate
    assert rows["phqDesignWeek"]["verification"] == "UNVERIFIED"  # predicted
    eid = rows["phqEidFitr2027"]
    assert eid["moon_dependent"] and eid["uncertainty_days"] == 1 and eid["start"] == eid["end"] == date(2027, 3, 10)
    # An end at local midnight is exclusive.
    assert rows["phqColdplay"]["end"] == date(2026, 11, 14)


def test_phq_cancellation_arrives_as_deleted_reason():
    ev = dict(fixture("phq_events_dubai.json")["results"][1], state="deleted", deleted_reason="cancelled")
    assert phq_events.parse_event(ev)["status"] == "CANCELLED"


def test_phq_sync_merges_with_curated(cur, hotel):
    curated.sync(cur, force=True)
    out = phq_events.sync_hotel(cur, hotel, results=fixture("phq_events_dubai.json")["results"])
    assert out["ingested"] == 5 and out["skipped_rows"] == 1
    gitex = _by_source(cur, "PREDICTHQ", "phqGitex2026")
    assert gitex["id"] == _by_source(cur, "CURATED", "gitex-global-2026")["id"]
    # Curated dates win; attendance is the largest credible figure.
    assert gitex["date_source"] == "CURATED" and gitex["attendance_est"] == 200000
    assert gitex["attendance_source"] == "CURATED" and gitex["phq_rank"] == 88
    assert gitex["predicted_accom_spend_minor"] == 4500000050
    assert _by_source(cur, "PREDICTHQ", "phqBig52026")["id"] == _by_source(cur, "CURATED", "big5-global-2026")["id"]
    eid = _by_source(cur, "PREDICTHQ", "phqEidFitr2027")
    assert eid["id"] == _by_source(cur, "CURATED", "ae-eid-al-fitr-2027")["id"]
    assert (eid["start_date"], eid["end_date"]) == (date(2027, 3, 10), date(2027, 3, 12))
    # GITEX Global (DEC) never merges with GITEX Global Summit (DWTC).
    summit = _by_source(cur, "CURATED", "gitex-global-summit-2026")
    assert summit["id"] != gitex["id"]
    coldplay = _by_source(cur, "PREDICTHQ", "phqColdplay")
    assert coldplay["venue_codes"] == ["COCA_COLA_ARENA"] and coldplay["hotel_slug"] is None
    state = clefs_db.fetch_one("SELECT cursor FROM dr_sync_state WHERE source = 'PREDICTHQ'", cur=cur)
    assert state["cursor"] == "2026-09-21T12:00:00Z"
    # Idempotent: a second pass changes nothing.
    again = phq_events.sync_hotel(cur, hotel, results=fixture("phq_events_dubai.json")["results"])
    assert again["created"] == 0 and again["changed"] == 0


def test_phq_without_token_is_skipped(cur, hotel, env):
    env(PREDICTHQ_TOKEN=None)
    assert phq_events.sync_hotel(cur, hotel) == {"skipped": "no_token"}
    assert phq_features.sync_hotel(cur, hotel) == {"skipped": "no_token"}


def test_phq_fetch_sends_radius_and_bearer(cur, hotel, env):
    env(PREDICTHQ_TOKEN="phq-test-token")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json=fixture("phq_events_dubai.json"))

    adapters.set_transport(httpx.MockTransport(handler))
    try:
        out = phq_events.sync_hotel(cur, hotel)
        # Same block, same day: the second hotel run is served by the first.
        assert phq_events.sync_hotel(cur, hotel) == {"skipped": "fresh", "scope": "25.27,55.31"}
    finally:
        adapters.set_transport(None)
    assert out["ingested"] == 5 and len(seen) == 1
    q = dict(seen[0].url.params)
    assert q["within"] == "60km@25.26970,55.30950" and q["rank.gte"] == "40" and q["limit"] == "500"
    assert q["active.gte"] == "2026-09-29" and q["active.lte"] == "2027-03-28"
    assert seen[0].headers["authorization"] == "Bearer phq-test-token"


# ─── PredictHQ Features ───


def test_features_parse_and_store(cur, hotel):
    payload = fixture("phq_features_dubai.json")
    rows = phq_features.parse_rows(payload)
    assert (date(2026, 12, 7), "phq_attendance_expos", 180000.0) in rows
    assert (date(2026, 12, 2), "phq_rank_public_holidays", 4.0) in rows
    assert (date(2026, 12, 7), "phq_rank_public_holidays", 0.0) in rows
    out = phq_features.sync_hotel(cur, hotel, payload=payload)
    assert out == {"days": 2, "values": 12}
    days = phq_features.features_for(cur, hotel.slug, date(2026, 12, 1), date(2026, 12, 31))
    assert days["2026-12-07"]["phq_attendance_conferences"] == 12000.0
    body = phq_features.request_body(25.27, 55.31, date(2026, 9, 29), date(2027, 3, 28))
    assert body["location"]["geo"]["radius"] == "10km" and body["phq_rank_public_holidays"] is True
    assert body["phq_attendance_sports"] == {"stats": ["sum", "count"]}


# ─── Ticketmaster ───


def test_ticketmaster_parse_and_merge(cur, hotel):
    phq_events.sync_hotel(cur, hotel, results=fixture("phq_events_dubai.json")["results"])
    page = fixture("ticketmaster_ae.json")
    parsed = ticketmaster.parse_page(page)
    assert [p[0] for p in parsed] == ["tmColdplay1", "tmDisneyIce"]  # film skipped
    out = ticketmaster.sync(cur, pages=[page])
    assert out["ingested"] == 2 and out["created"] == 1
    coldplay = _by_source(cur, "TICKETMASTER", "tmColdplay1")
    assert coldplay["id"] == _by_source(cur, "PREDICTHQ", "phqColdplay")["id"]
    # PredictHQ outranks Ticketmaster on dates and title; the ticket URL is kept.
    assert coldplay["date_source"] == "PREDICTHQ"
    assert "https://www.ticketmaster.ae/event/tmColdplay1" in coldplay["source_urls"]
    disney = _by_source(cur, "TICKETMASTER", "tmDisneyIce")
    assert disney["status"] == "POSTPONED" and disney["category"] == "COMMUNITY"
    assert float(disney["confidence"]) == 0.7


def test_ticketmaster_without_key_is_skipped(cur, env):
    env(TICKETMASTER_API_KEY=None)
    assert ticketmaster.sync(cur) == {"skipped": "no_key"}


# ─── AI web discovery ───


def _ai_answer(events_: list[dict], citations: list[str]) -> AIResponse:
    return AIResponse(output_text=json.dumps({"events": events_}), citations=[Citation(url=u) for u in citations],
                      tokens_in=10, tokens_out=10)


GITEX_AI = {"title": "GITEX GLOBAL 2026", "venue": "Expo City Dubai", "start": "2026-12-07", "end": "2026-12-11",
            "url": "https://www.gitex.com/", "category": "expo", "confidence": 0.95}
NEW_AI = {"title": "Dubai Coffee Festival", "venue": "Alserkal Avenue", "start": "2026-11-20", "end": "2026-11-22",
          "url": "https://example-coffee.ae/festival", "category": "festival", "confidence": 0.9}


def test_gitex_from_three_sources_is_one_event(cur, hotel, fake_ai):
    curated.sync(cur, force=True)
    phq_events.sync_hotel(cur, hotel, results=fixture("phq_events_dubai.json")["results"])
    fake_ai.push(_ai_answer([GITEX_AI, NEW_AI], ["https://www.gitex.com/visit", "https://timeoutdubai.com/x"]))
    out = ai_web.discover(cur, hotel)
    assert out["kept"] == 2 and out["corroborated"] == 1 and out["created"] == 1
    gitex_id = str(_by_source(cur, "CURATED", "gitex-global-2026")["id"])
    ev = events.get_event(cur, hotel.slug, gitex_id)
    assert sorted(s["source"] for s in ev["sources"]) == ["AI_WEB", "CURATED", "PREDICTHQ"]
    assert ev["verification"] == "VERIFIED" and ev["scope"] == "global" and ev["actionable"]
    # The AI row corroborates; it never edits the global event.
    assert ev["date_source"] == "CURATED"


def test_ai_only_event_is_unverified_capped_and_hotel_scoped(cur, hotel, fake_ai, make_user):
    fake_ai.push(_ai_answer([NEW_AI], ["https://example-coffee.ae/news"]))
    ai_web.discover(cur, hotel)
    row = clefs_db.fetch_one("SELECT e.* FROM dr_event_sources s JOIN dr_events e ON e.id = s.event_id "
                             "WHERE s.source = 'AI_WEB'", cur=cur)
    assert row["hotel_slug"] == hotel.slug and row["verification"] == "UNVERIFIED"
    assert float(row["confidence"]) == 0.6
    ev = events.get_event(cur, hotel.slug, str(row["id"]))
    assert ev["corroborated"] is False and ev["actionable"] is False
    # Another hotel never sees it.
    from clefs.core.hotels import create_hotel

    create_hotel(cur, slug="other-hotel", name="Other")
    other = events.list_events(cur, "other-hotel", start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert "Dubai Coffee Festival" not in {e["title"] for e in other}
    with pytest.raises(Exception, match="Event not found"):
        events.get_event(cur, "other-hotel", str(row["id"]))
    user = make_user("owner")
    confirmed = events.confirm(cur, hotel.slug, str(row["id"]), user_id=user["id"])
    assert confirmed["actionable"] and confirmed["verification"] == "VERIFIED"


def test_ai_uncited_url_and_no_citations(cur, hotel, fake_ai):
    fake_ai.push(_ai_answer([NEW_AI], ["https://some-news.ae/article"]))
    ai_web.discover(cur, hotel)
    row = clefs_db.fetch_one("SELECT confidence FROM dr_event_sources WHERE source = 'AI_WEB'", cur=cur)
    assert float(row["confidence"]) == 0.3  # its URL's domain is not among the citations
    fake_ai.push(_ai_answer([GITEX_AI], []))
    assert ai_web.discover(cur, hotel)["skipped"] == "no_citations"
    past = dict(NEW_AI, start="2026-01-01", end="2026-01-02", title="Old fair")
    fake_ai.push(_ai_answer([past], ["https://example-coffee.ae/"]))
    assert ai_web.discover(cur, hotel)["kept"] == 0


def test_ai_disabled_is_a_skip(cur, hotel):
    assert ai_web.discover(cur, hotel) == {"skipped": "ai_disabled"}


def test_phq_pages_follow_only_predicthq_links(env):
    env(PREDICTHQ_TOKEN="t")
    results = fixture("phq_events_dubai.json")["results"]
    hosts = []

    def handler(req: httpx.Request) -> httpx.Response:
        hosts.append(req.url.host)
        if "offset" not in req.url.params:
            return httpx.Response(200, json={"results": results[:2], "next": phq_events.BASE + "?offset=2"})
        return httpx.Response(200, json={"results": results[2:3], "next": "https://evil.example/steal?offset=3"})

    adapters.set_transport(httpx.MockTransport(handler))
    try:
        got = phq_events.fetch(25.27, 55.31, date(2026, 9, 29))
    finally:
        adapters.set_transport(None)
    assert len(got) == 3 and hosts == ["api.predicthq.com", "api.predicthq.com"]


def test_vendor_errors_are_classified(env):
    env(PREDICTHQ_TOKEN="t")
    adapters.set_transport(httpx.MockTransport(lambda req: httpx.Response(401, json={"error": "bad key t"})))
    try:
        with pytest.raises(adapters.VendorError) as exc:
            phq_events.fetch(25.27, 55.31, date(2026, 9, 29))
    finally:
        adapters.set_transport(None)
    assert exc.value.status == 401 and not exc.value.transient and "bad key" not in exc.value.detail
