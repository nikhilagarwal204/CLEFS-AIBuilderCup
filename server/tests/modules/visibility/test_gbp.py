"""Google Business Profile (04 spec 5.4, 5.12): connection states, daily
metrics and 30-day raw expiry, derived KPIs, keyword thresholds, the UTM
patch and its verifier, the location picker."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 10, 3, 6, 0, tzinfo=ZoneInfo("Asia/Dubai"))
LOC = "locations/222"


class GoogleFake:
    """(method, url fragment) -> JSON body, (status, body) or callable(request)."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def on(self, method: str, fragment: str, answer: object) -> GoogleFake:
        self.answers.insert(0, (method, fragment, answer))
        return self

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        for method, fragment, answer in self.answers:
            if request.method == method and fragment in str(request.url):
                if callable(answer):
                    answer = answer(request)
                status, body = answer if isinstance(answer, tuple) else (200, answer)
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


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
def google(monkeypatch):
    from clefs.core import oauth
    from clefs.modules.visibility import http

    fake = GoogleFake()
    http.set_transport(httpx.MockTransport(fake.handle))
    monkeypatch.setattr(oauth, "get_access_token", lambda cur, slug, provider: "tok-123")
    yield fake
    http.set_transport(None)


@pytest.fixture
def hotel(cur, hotel_uae, clock, env):
    clock.freeze(NOW)
    env(GBP_PROJECT_QPM="300")
    return hotel_uae


@pytest.fixture
def connected(cur, hotel):
    from clefs.modules.visibility import store

    cur.execute("INSERT INTO oauth_connections (hotel_slug, provider, status) VALUES (%s, 'google_business', 'active')",
                (hotel.slug,))
    store.update_profile(cur, hotel.slug, {"gbp_location": LOC}, user_id=None)
    return hotel


# ─── Connection state ───


def test_connection_states_in_order(cur, hotel, env):
    from clefs.modules.visibility import api, gbp, store

    env(GBP_PROJECT_QPM="0")
    assert gbp.connection_state(cur, hotel.slug)["message"] == "not connected (project not approved, 0 QPM)"
    env(GBP_PROJECT_QPM="300")
    assert gbp.connection_state(cur, hotel.slug)["status"] == "not_connected"
    cur.execute("INSERT INTO oauth_connections (hotel_slug, provider) VALUES (%s, 'google_business')", (hotel.slug,))
    assert gbp.connection_state(cur, hotel.slug)["status"] == "pick_location"
    assert api.gbp_location(cur, hotel.slug) is None
    store.update_profile(cur, hotel.slug, {"gbp_location": LOC}, user_id=None)
    assert gbp.connection_state(cur, hotel.slug)["status"] == "connected"
    assert api.gbp_location(cur, hotel.slug) == LOC


def test_jobs_skip_until_connected(cur, hotel):
    from clefs.modules.visibility import gbp, lodging

    assert gbp.run_daily(cur, hotel) == {"skipped": "not_connected"}
    assert lodging.run_check(cur, hotel) == {"skipped": "not_connected"}


def test_location_picker(cur, connected, google):
    from clefs.modules.visibility import gbp

    google.on("GET", "/v1/accounts", {"accounts": [{"name": "accounts/1", "accountName": "Owner"}]})
    google.on("GET", "accounts/1/locations", {"locations": [
        {"name": LOC, "title": "Test Hotel Dubai", "websiteUri": "https://hotel.example/",
         "storefrontAddress": {"addressLines": ["1 Road"], "locality": "Dubai"}}]})
    locs = gbp.list_locations(cur, connected.slug)
    assert locs == [{"location": LOC, "title": "Test Hotel Dubai", "address": "1 Road, Dubai",
                     "website_uri": "https://hotel.example/", "account": "Owner"}]
    assert google.requests[0].headers["authorization"] == "Bearer tok-123"


# ─── Daily metrics, KPIs, raw expiry ───


def _series(values_by_metric: dict[str, dict[date, int]]) -> dict:
    return {"multiDailyMetricTimeSeries": [{"dailyMetricTimeSeries": [
        {"dailyMetric": m, "timeSeries": {"datedValues": [
            {"date": {"year": d.year, "month": d.month, "day": d.day}, **({"value": str(v)} if v else {})}
            for d, v in days.items()]}}
        for m, days in values_by_metric.items()]}]}


def test_daily_refetches_10_days_and_raw_rows_expire_at_30d(cur, connected, google, clock, run_jobs):
    from clefs.modules.visibility import gbp

    days = {date(2026, 10, 2) - timedelta(days=i): 5 for i in range(10)}
    google.on("GET", ":fetchMultiDailyMetricsTimeSeries", _series({
        "BUSINESS_IMPRESSIONS_MOBILE_MAPS": days, "WEBSITE_CLICKS": {date(2026, 10, 2): 0},
        "BUSINESS_BOOKINGS": days}))
    out = gbp.run_daily(cur, connected)
    assert out == {"rows": 11, "start": "2026-09-23", "end": "2026-10-02"}
    q = parse_qs(urlsplit(str(google.requests[-1].url)).query)
    assert set(q["dailyMetrics"]) == set(gbp.DAILY_METRICS) and "BUSINESS_BOOKINGS" not in q["dailyMetrics"]
    cur.execute("SELECT value FROM vis_gbp_daily WHERE metric = 'WEBSITE_CLICKS'")
    assert cur.fetchone()["value"] == 0                      # an omitted value is a zero day
    cur.execute("INSERT INTO vis_gbp_kpis_monthly (hotel_slug, month, reach_user_days, computed_at) "
                "VALUES (%s, '2026-09-01', 150, now())", (connected.slug,))

    assert gbp.expire_raw(cur, now=NOW + timedelta(days=29))["vis_gbp_daily"] == 0
    assert gbp.expire_raw(cur, now=NOW + timedelta(days=30))["vis_gbp_daily"] == 11
    cur.execute("SELECT count(*) AS n FROM vis_gbp_kpis_monthly WHERE hotel_slug = %s", (connected.slug,))
    assert cur.fetchone()["n"] == 1                          # derived KPIs remain


def test_kpis_reach_prior_yoy_and_action_rate(cur, connected, google):
    from clefs.modules.visibility import gbp

    def answer(request):
        q = parse_qs(urlsplit(str(request.url)).query)
        start = date(int(q["dailyRange.startDate.year"][0]), int(q["dailyRange.startDate.month"][0]),
                     int(q["dailyRange.startDate.day"][0]))
        if start.year == 2025:                               # the live year-ago re-fetch
            return _series({"BUSINESS_IMPRESSIONS_DESKTOP_SEARCH": {date(2025, 9, 20): 70}})
        return _series({
            "BUSINESS_IMPRESSIONS_MOBILE_SEARCH": {date(2026, 9, 30): 800, date(2026, 9, 10): 200,
                                                   date(2026, 8, 20): 500},
            "CALL_CLICKS": {date(2026, 9, 30): 10}, "WEBSITE_CLICKS": {date(2026, 9, 2): 40, date(2026, 9, 30): 30},
            "BUSINESS_DIRECTION_REQUESTS": {date(2026, 9, 15): 20}})

    google.on("GET", ":fetchMultiDailyMetricsTimeSeries", answer)
    out = gbp.compute_kpis(cur, connected, date(2026, 9, 1))
    cur.execute("SELECT * FROM vis_gbp_kpis_monthly WHERE hotel_slug = %s", (connected.slug,))
    k = cur.fetchone()
    # Window 2026-09-03..09-30: 800 + 200 impressions; actions 10 + 30 + 20.
    assert (k["reach_user_days"], k["reach_prior_user_days"], k["reach_yoy_user_days"]) == (1000, 500, 70)
    assert k["actions"] == 60 and k["action_rate"] == Decimal("0.0600")
    assert k["website_clicks"] == 70 and out["month"] == "2026-09-01"


def test_keywords_thresholds_are_never_summed(cur, connected, google):
    from clefs.modules.visibility import gbp

    pages = [
        {"searchKeywordsCounts": [
            {"searchKeyword": "test hotel dubai", "insightsValue": {"value": "120"}},
            {"searchKeyword": "hotel deira", "insightsValue": {"value": "45"}},
            {"searchKeyword": "hotels near gold souk", "insightsValue": {"value": "30"}}],
         "nextPageToken": "p2"},
        {"searchKeywordsCounts": [{"searchKeyword": f"kw{i}", "insightsValue": {"threshold": "15"}}
                                  for i in range(5)]},
    ]
    google.on("GET", "searchkeywords/impressions/monthly", lambda r: pages.pop(0))
    out = gbp.run_keywords(cur, connected, date(2026, 9, 1))
    assert out["display"] == "195 + 5 under threshold" and out["under_threshold"] == 5
    assert out["branded_share"] == "0.6154"                  # 120 / 195: thresholds not in the sum
    assert "pageToken=p2" in str(google.requests[-1].url)
    cur.execute("SELECT count(*) AS n FROM vis_gbp_keywords WHERE value IS NULL AND threshold = 15")
    assert cur.fetchone()["n"] == 5
    ov = gbp.overview(cur, connected)
    assert [r["display"] for r in ov["keywords"]["rows"]][-1] == "< 15"
    cur.execute("SELECT branded_share, keywords_under_threshold FROM vis_gbp_kpis_monthly WHERE hotel_slug = %s",
                (connected.slug,))
    assert cur.fetchone()["keywords_under_threshold"] == 5


# ─── UTM patch ───


def test_utm_merge_keeps_existing_params():
    from clefs.modules.visibility.gbp import has_utm, merge_utm

    out = merge_utm("https://hotel.example/en/book?lang=en&utm_source=tripadvisor&promo=X#top")
    assert out == ("https://hotel.example/en/book?lang=en&promo=X&utm_source=google&utm_medium=organic"
                   "&utm_campaign=gbp_listing#top")
    assert has_utm(out) and not has_utm("https://hotel.example/")
    assert merge_utm(out) == out                              # idempotent


def test_utm_action_executes_and_verifies(cur, connected, google):
    from clefs.core import actions
    from clefs.modules.visibility import action_types  # noqa: F401  (registers the types)

    state = {"uri": "https://hotel.example/?lang=en"}

    def patch(request):
        import json

        assert parse_qs(urlsplit(str(request.url)).query)["updateMask"] == ["websiteUri"]
        state["uri"] = json.loads(request.content)["websiteUri"]
        return {"name": LOC, "websiteUri": state["uri"]}

    google.on("GET", f"/v1/{LOC}?readMask=websiteUri", lambda r: {"websiteUri": state["uri"]})
    google.on("PATCH", f"/v1/{LOC}", patch)
    act = actions.create_action(cur, connected.slug, type="visibility.gbp_utm_patch", title="Tag",
                                params={"location": LOC})
    assert act["status"] == "proposed"
    t = actions.get_action_type("visibility.gbp_utm_patch")
    ctx = actions.ActionCtx(cur=cur, hotel=connected, action=act, action_type=t, actor=None)
    res = t.executor(ctx)
    assert res.ok and res.reversal_handle == {"kind": "gbp_patch", "field": "websiteUri", "location": LOC,
                                              "previous": "https://hotel.example/?lang=en"}
    assert state["uri"].endswith("lang=en&utm_source=google&utm_medium=organic&utm_campaign=gbp_listing")
    done = {**act, "execution_result": res.data}
    v = t.verifier(actions.ActionCtx(cur=cur, hotel=connected, action=done, action_type=t, actor=None))
    assert v.status == "verified"
    state["uri"] = "https://hotel.example/"                   # someone reverted it
    assert t.verifier(actions.ActionCtx(cur=cur, hotel=connected, action=done, action_type=t, actor=None)
                      ).status == "not_verified"


# ─── Draft replies (04 spec 5.4, review finding spec-vis-draft-replies-missing) ───


@pytest.fixture
def reviews_seen(cur, hotel, monkeypatch):
    """A temp rev_reviews (it shadows the real table for this transaction, so
    the test runs with or without the Reviews module) plus a recording
    reviews.api.draft_reply."""
    from clefs.modules.reviews import api as reviews_api

    cur.execute("CREATE TEMP TABLE rev_reviews (id BIGINT, hotel_slug TEXT, platform TEXT, "
                "has_owner_response BOOLEAN, posted_at TIMESTAMPTZ) ON COMMIT DROP")
    rows = [(1, "google", False, 1), (2, "google", True, 2), (3, "booking", False, 3), (4, "google", False, 200)]
    rows += [(10 + i, "google", False, 10 + i) for i in range(6)]
    for rid, platform, answered, age_days in rows:
        cur.execute("INSERT INTO rev_reviews VALUES (%s, %s, %s, %s, %s)",
                    (rid, hotel.slug, platform, answered, NOW - timedelta(days=age_days)))
    calls: list[int] = []

    def draft(c, slug, review_id):
        assert slug == hotel.slug
        calls.append(review_id)
        return {"review_id": review_id, "reply_id": 100 + review_id, "text": "Thank you", "language": "en",
                "needs_approval": True, "status": "draft"}

    monkeypatch.setattr(reviews_api, "draft_reply", draft)
    monkeypatch.setattr(reviews_api, "reply_stats", lambda c, slug, **kw: {"unanswered": 7})
    return calls


def test_draft_replies_delegates_to_reviews_draft_reply(client_as, hotel, reviews_seen, google):
    r = client_as("gm").post(f"/hotels/{hotel.slug}/visibility/gbp/draft-replies")
    assert r.status_code == 200, r.text
    out = r.json()
    # Newest unanswered Google reviews in the 90-day window, capped at 5:
    # the answered one (2), the Booking one (3) and the 200-day-old one (4) are skipped.
    assert reviews_seen == [1, 10, 11, 12, 13]
    assert out["drafted"] == 5 and out["considered"] == 5 and out["unanswered"] == 7
    assert out["reviews_tab"] == "replies"
    assert out["drafts"][0] == {"review_id": 1, "reply_id": 101, "status": "draft", "needs_approval": True,
                                "language": "en"}
    assert google.requests == []  # drafts only: Visibility never calls Google to post a reply


def test_draft_replies_needs_visibility_act(client_as, hotel, reviews_seen):
    r = client_as("finance").post(f"/hotels/{hotel.slug}/visibility/gbp/draft-replies")
    assert r.status_code == 403 and reviews_seen == []


def test_draft_replies_degrade_when_reviews_is_absent(cur, hotel, monkeypatch):
    """No rev_reviews table (Reviews off or not migrated) or a failing
    draft_reply: nothing drafted, no error, the transaction stays usable."""
    from clefs.modules.reviews import api as reviews_api
    from clefs.modules.visibility import gbp, xmod

    # Real lookup: [] with no rev_reviews table (a visibility-only run) and
    # [] with Reviews migrated but no reviews for this hotel.
    assert xmod.unanswered_google_review_ids(cur, hotel.slug, days=90, limit=5) == []
    monkeypatch.setattr(xmod, "unanswered_google_review_ids", lambda c, slug, **kw: [5, 6])

    def boom(c, slug, review_id):
        if review_id == 5:
            raise RuntimeError("reviews mid-build")
        return {"review_id": review_id, "reply_id": 9, "status": "draft", "needs_approval": False, "language": "en"}

    monkeypatch.setattr(reviews_api, "draft_reply", boom)
    monkeypatch.setattr(reviews_api, "reply_stats", lambda c, slug, **kw: None)
    out = gbp.draft_replies(cur, hotel.slug)
    assert out["drafted"] == 1 and out["drafts"][0]["review_id"] == 6 and out["unanswered"] is None
    cur.execute("SELECT 1 AS ok")
    assert cur.fetchone()["ok"] == 1
