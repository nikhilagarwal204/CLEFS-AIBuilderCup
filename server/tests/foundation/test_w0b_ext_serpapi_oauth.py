"""
Extension lane serpapi-oauth: core.serpapi (04 spec F5, Canon K3) and
core.oauth (04 spec F6).

  - SerpApi: the cache key (sha1 of engine + sorted normalised params, minus
    api_key / no_cache), the shared Google Hotels builder, one billed call for
    identical requests from two modules, max_age and no_cache, the global
    budget and the public_check cap (BudgetExhausted), failures that are not
    billed, micros in the ledger, no key -> NotConfigured, the purge.
  - OAuth: start guarded per provider (and never by a magic session), PKCE,
    single-use expiring state that names the hotel, sealed tokens, provider
    quirks (QBO basic auth + realmId, Zoho accounts-server allowlist, Google
    partial consent), get_access_token refresh / revoke / transient failure,
    disconnect with a best-effort revoke, the connections list.

Every provider endpoint is mocked (httpx.MockTransport); nothing leaves the
process.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, parse_qsl, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from clefs import jobs
from clefs.auth import magic, tokens
from clefs.core import oauth, privacy, serpapi
from clefs.core.errors import NotConfigured
from tests.foundation.route_walk import route_paths

T0 = datetime(2026, 9, 28, 8, 0, tzinfo=timezone.utc)
AREA_Q = "hotels in Deira Dubai"


# ─── SerpApi fixtures and helpers ───


@pytest.fixture
def serp(env, clock):
    """A configured SerpApi (fake transport), no budgets, clock frozen."""
    clock.freeze(T0)
    env(SERPAPI_KEY="test-serpapi-key", SERPAPI_MONTHLY_BUDGET_USD=None, PUBLIC_SERPAPI_MONTHLY_CAP=None,
        SERPAPI_COST_PER_SEARCH_USD=None)
    fake = serpapi.use_fake()
    yield fake
    serpapi.set_transport(None)


def _area(hotel, day: int = 2) -> dict:
    d = date(2026, 10, day)
    return serpapi.google_hotels_area_params(hotel, q=AREA_Q, check_in=d, check_out=d + timedelta(days=1))


def _calls(cur) -> list[dict]:
    cur.execute("SELECT * FROM serpapi_calls ORDER BY id")
    return [dict(r) for r in cur.fetchall()]


# ─── SerpApi: keys and params ───


def test_cache_key_is_sha1_of_engine_and_sorted_normalised_params():
    a = serpapi.cache_key("google_hotels", {"q": "hotels in Deira", "adults": 2, "check_in_date": date(2026, 10, 2)})
    b = serpapi.cache_key(
        "google_hotels",
        {"check_in_date": "2026-10-02", "adults": "2", "q": " hotels  in Deira ", "api_key": "secret", "no_cache": True,
         "engine": "google_hotels", "output": "json", "gl": None},
    )
    assert a == b
    assert a == hashlib.sha1(b"google_hotels?adults=2&check_in_date=2026-10-02&q=hotels+in+Deira").hexdigest()
    assert serpapi.cache_key("google_maps", {"q": "hotels in Deira", "adults": 2, "check_in_date": "2026-10-02"}) != a
    assert serpapi.cache_key("google_hotels", {"q": "Hotels in Deira", "adults": 2, "check_in_date": "2026-10-02"}) != a
    assert serpapi.normalize_params({"adults": 2.0, "flag": False, "ids": ["a", "b"], "api_key": "x"}) == {
        "adults": "2", "flag": "false", "ids": "a,b"}


def test_cache_key_refuses_mismatched_engines_and_async_searches():
    with pytest.raises(ValueError):
        serpapi.cache_key("google_hotels", {"engine": "google_maps", "q": "x"})
    with pytest.raises(ValueError):
        serpapi.cache_key("google_hotels", {"q": "x", "async": True})
    with pytest.raises(ValueError):
        serpapi.cache_key("Google Hotels!", {"q": "x"})


def test_google_hotels_area_params_is_the_shared_builder(hotel_uae):
    assert _area(hotel_uae) == {
        "engine": "google_hotels", "q": AREA_Q, "check_in_date": "2026-10-02", "check_out_date": "2026-10-03",
        "currency": "AED", "gl": "ae", "hl": "en", "adults": 2,
    }
    with pytest.raises(ValueError):
        serpapi.google_hotels_area_params(hotel_uae, q=AREA_Q, check_in=date(2026, 10, 2), check_out=date(2026, 10, 2))


# ─── SerpApi: sharing, freshness and the ledger ───


def test_identical_requests_from_two_modules_share_one_billed_call(cur, serp, hotel_uae, legacy_slug):
    serp.push({"properties": [{"name": "Hotel A"}]})
    first = serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    second = serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=legacy_slug, purpose="visibility.google_hotels")
    assert first["properties"] == second["properties"] == [{"name": "Hotel A"}]
    assert serp.calls == 1
    sent = serp.requests[0]
    assert sent["engine"] == "google_hotels" and sent["q"] == AREA_Q and sent["currency"] == "AED"
    assert sent["api_key"] == "***" and sent["output"] == "json"  # the key was sent, the fake masked it

    rows = _calls(cur)
    assert [(r["hotel_slug"], r["purpose"], r["cache_hit"], r["billed"], r["cost_micros"], r["status"]) for r in rows] == [
        (hotel_uae.slug, "demand.comp_rates", False, True, 15_000, "ok"),
        (legacy_slug, "visibility.google_hotels", True, False, 0, "cache_hit"),
    ]
    assert {r["cost_currency"] for r in rows} == {"USD"}
    cur.execute("SELECT params, status, fetched_at, expires_at FROM serpapi_cache")
    cached = cur.fetchone()
    assert "api_key" not in cached["params"] and "engine" not in cached["params"]
    assert cached["status"] == "ok" and cached["fetched_at"] == T0 and cached["expires_at"] == T0 + serpapi.CACHE_RETENTION
    assert serpapi.month_spend_micros(cur) == 15_000
    assert serpapi.month_spend_micros(cur, hotel_slug=legacy_slug) == 0


def test_max_age_and_no_cache_decide_when_to_call_again(cur, serp, clock, hotel_uae):
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    clock.advance(hours=21)
    # Older than the default 20 h, but fresh enough for a caller allowing 48 h.
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="visibility.google_hotels",
                   max_age=timedelta(hours=48))
    assert serp.calls == 1
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert serp.calls == 2
    forced = {**_area(hotel_uae), "no_cache": True}
    serpapi.search("google_hotels", forced, hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert serp.calls == 3 and serp.requests[-1]["no_cache"] == "true"
    assert len({r["cache_key"] for r in _calls(cur)}) == 1  # no_cache never splits the key
    assert [r["billed"] for r in _calls(cur)] == [True, False, True, True]


def test_a_nul_character_in_scraped_text_still_caches(cur, serp, hotel_uae):
    serp.push({"properties": [{"name": "Hotel\x00 A", "description": "x"}]})
    out = serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert out["properties"][0]["name"] == "Hotel A"
    assert _calls(cur)[0]["status"] == "ok"


def test_no_results_is_billed_and_cached_as_empty(cur, serp, hotel_uae):
    serp.push({"error": "Google Hotels hasn't returned any results for this query."})
    out = serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert "error" in out
    cur.execute("SELECT status FROM serpapi_cache")
    assert cur.fetchone()["status"] == "empty"
    assert _calls(cur)[0]["billed"] is True


@pytest.mark.parametrize(
    "answer, fragment",
    [
        ((401, {"error": "Invalid API key. Your API key should be here: https://serpapi.com/manage-api-key"}), "HTTP 401"),
        ({"search_metadata": {"status": "Error"}, "error": "Unsupported parameter"}, "Unsupported parameter"),
        (httpx.ConnectTimeout("timed out"), "timed out"),
        (httpx.ConnectError("boom https://serpapi.com/search.json?api_key=test-serpapi-key"), "network error"),
    ],
)
def test_failed_searches_are_not_billed_or_cached(cur, serp, hotel_uae, answer, fragment):
    serp.push(answer)
    with pytest.raises(serpapi.SerpApiError) as ei:
        serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert ei.value.status == 502 and ei.value.code == "serpapi_error" and fragment in ei.value.detail
    assert "test-serpapi-key" not in ei.value.detail
    (row,) = _calls(cur)
    assert (row["billed"], row["cost_micros"], row["status"]) == (False, 0, "error")
    assert "test-serpapi-key" not in (row["error"] or "")
    cur.execute("SELECT COUNT(*) AS n FROM serpapi_cache")
    assert cur.fetchone()["n"] == 0


def test_no_key_is_not_configured_but_the_cache_still_serves(cur, serp, env, hotel_uae):
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    env(SERPAPI_KEY=None)
    assert serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="reviews.sync")
    with pytest.raises(NotConfigured):
        serpapi.search("google_hotels", _area(hotel_uae, day=9), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert serp.calls == 1


def test_purpose_must_be_a_key():
    for bad in ("", "Demand", "demand comp", "x" * 65, "demand..rates"):
        with pytest.raises(ValueError):
            serpapi.search("google_hotels", {"q": "x"}, hotel_slug=None, purpose=bad)


# ─── SerpApi: budgets ───


def test_the_global_monthly_budget_raises_budget_exhausted(cur, serp, env, clock, hotel_uae):
    env(SERPAPI_MONTHLY_BUDGET_USD="0.02")  # room for one 0.015 search
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    with pytest.raises(serpapi.BudgetExhausted) as ei:
        serpapi.search("google_hotels", _area(hotel_uae, day=3), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert (ei.value.status, ei.value.code, ei.value.scope) == (503, "budget_exhausted", "global")
    assert (ei.value.limit, ei.value.used) == (20_000, 15_000)
    assert serp.calls == 1
    # A cache hit costs nothing, so it is still served.
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="visibility.google_hotels")
    # A new UTC month starts a new budget.
    clock.freeze(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc))
    serpapi.search("google_hotels", _area(hotel_uae, day=3), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert serp.calls == 2


def test_public_checks_have_their_own_monthly_cap(cur, serp, env, hotel_uae):
    assert serpapi.public_monthly_cap() == serpapi.DEFAULT_PUBLIC_MONTHLY_CAP  # bounded even when unset
    env(PUBLIC_SERPAPI_MONTHLY_CAP="1")
    serpapi.search("google_hotels", {"q": "Test Hotel Dubai"}, hotel_slug=None, purpose="public_check")
    with pytest.raises(serpapi.BudgetExhausted) as ei:
        serpapi.search("google_hotels", {"q": "Another Hotel Dubai"}, hotel_slug=None, purpose="public_check")
    assert ei.value.scope == "public_check"
    # Other purposes are not held back by the public cap.
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert serp.calls == 2
    assert _calls(cur)[0]["hotel_slug"] is None


def test_the_price_per_search_is_configurable_in_micros(cur, serp, env, hotel_uae):
    env(SERPAPI_COST_PER_SEARCH_USD="0.01")
    assert serpapi.cost_per_search_micros() == 10_000
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    assert _calls(cur)[0]["cost_micros"] == 10_000
    assert serpapi.usd_to_micros("30") == 30 * 1_000_000


def test_the_ledger_holds_micros_with_a_currency(cur):
    cur.execute(
        "SELECT column_name, data_type, character_maximum_length AS n FROM information_schema.columns "
        "WHERE table_name = 'serpapi_calls' AND column_name IN ('cost_micros', 'cost_currency') ORDER BY column_name"
    )
    assert [(r["column_name"], r["data_type"], r["n"]) for r in cur.fetchall()] == [
        ("cost_currency", "character", 3), ("cost_micros", "bigint", None)]


def test_purge_drops_expired_cache_rows_and_old_ledger_rows(cur, serp, clock, hotel_uae):
    serpapi.search("google_hotels", _area(hotel_uae), hotel_slug=hotel_uae.slug, purpose="demand.comp_rates")
    cur.execute("UPDATE serpapi_calls SET at = %s", (T0 - timedelta(days=401),))
    assert serpapi.purge(cur, now=T0) == {"cache_deleted": 0, "calls_deleted": 1}
    assert serpapi.purge(cur, now=T0 + serpapi.CACHE_RETENTION + timedelta(seconds=1))["cache_deleted"] == 1
    assert "core.serpapi.purge" in {j.name for j in jobs.registered_jobs()}


# ─── OAuth fixtures and helpers ───


class FakeIdP:
    """Every provider endpoint: answers queued per URL (without query), records
    each request's form (or JSON) body and Authorization header."""

    def __init__(self) -> None:
        self.queue: dict[str, list] = {}
        self.requests: list[dict] = []

    def respond(self, url: str, *answers) -> None:
        self.queue.setdefault(url, []).extend(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raw = request.content.decode() if request.content else ""
        ctype = request.headers.get("content-type", "")
        body = json.loads(raw) if "json" in ctype else dict(parse_qsl(raw))
        url = str(request.url).split("?")[0]
        self.requests.append({"url": url, "form": body, "auth": request.headers.get("authorization")})
        answers = self.queue.get(url) or [(404, {"error": "unexpected_url"})]
        item = answers.pop(0)
        if isinstance(item, BaseException):
            raise item
        status, payload = item
        return httpx.Response(status, json=payload, request=request)


GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
XERO_TOKEN = "https://identity.xero.com/connect/token"
QBO_TOKEN = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
API_ORIGIN = "https://api.clefs.test"


@pytest.fixture
def idp(env, clock):
    clock.freeze(T0)
    env(
        PUBLIC_API_ORIGIN=API_ORIGIN, OAUTH_REDIRECT_BASE=None,
        GBP_OAUTH_CLIENT_ID="gbp-client", GBP_OAUTH_CLIENT_SECRET="gbp-secret",
        XERO_CLIENT_ID="xero-client", XERO_CLIENT_SECRET="xero-secret",
        QBO_CLIENT_ID="qbo-client", QBO_CLIENT_SECRET="qbo-secret",
        ZOHO_CLIENT_ID="zoho-client", ZOHO_CLIENT_SECRET="zoho-secret",
    )
    fake = FakeIdP()
    oauth.set_transport(httpx.MockTransport(fake))
    yield fake
    oauth.set_transport(None)


@pytest.fixture
def finance_connect():
    """finance.connect (owner, finance) as the Finance lane will register it
    (04 spec 7.1); the permission registry is restored afterwards."""
    from clefs.auth import permissions

    with permissions._lock:
        saved = (dict(permissions._registry), dict(permissions._owners))
    if permissions.owner_of("finance.connect") is None:
        permissions.register_permissions({"finance.connect": frozenset({"owner", "finance"})}, module="finance")
    yield
    with permissions._lock:
        permissions._registry.clear()
        permissions._registry.update(saved[0])
        permissions._owners.clear()
        permissions._owners.update(saved[1])


def _start(c: TestClient, slug: str, provider: str, **params) -> tuple[dict, str]:
    r = c.get(f"/hotels/{slug}/oauth/{provider}/start", params={"format": "json", **params})
    assert r.status_code == 200, r.text
    url = r.json()["authorize_url"]
    q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    return q, url


def _callback(client: TestClient, provider: str, **params) -> dict:
    r = client.get(f"/public/oauth/{provider}/callback", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    parts = urlsplit(loc)
    return {"location": loc, "path": parts.path, **{k: v[0] for k, v in parse_qs(parts.query).items()}}


def _connection(cur, slug: str, provider: str) -> dict | None:
    cur.execute("SELECT * FROM oauth_connections WHERE hotel_slug = %s AND provider = %s", (slug, provider))
    row = cur.fetchone()
    return dict(row) if row else None


def _seed(cur, slug: str, provider: str, *, access: str = "old-access", refresh: str | None = "old-refresh",
          expires_at: datetime | None = None, meta: dict | None = None) -> int:
    from clefs.db import Json

    cur.execute(
        "INSERT INTO oauth_connections (hotel_slug, provider, scopes, access_token_enc, refresh_token_enc, expires_at, "
        "meta) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (slug, provider, list(oauth.get_provider(provider).scopes), privacy.seal(access),
         privacy.seal(refresh) if refresh else None, expires_at or T0 + timedelta(hours=1), Json(meta or {})),
    )
    return cur.fetchone()["id"]


# ─── OAuth: starting a flow ───


def test_oauth_routes_are_mounted(app):
    paths = route_paths(app.routes)
    assert {"/hotels/{slug}/oauth/{provider}/start", "/public/oauth/{provider}/callback", "/hotels/{slug}/oauth",
            "/hotels/{slug}/oauth/{provider}"} <= paths


def test_start_is_guarded_per_provider(client, client_as, idp, hotel_uae, finance_connect):
    path = f"/hotels/{hotel_uae.slug}/oauth/%s/start"
    assert client.get(path % "google_business", follow_redirects=False).status_code == 401
    assert client_as("front_desk").get(path % "google_business", follow_redirects=False).status_code == 403
    assert client_as("revenue").get(path % "google_business", follow_redirects=False).status_code == 403
    gm = client_as("gm")
    assert gm.get(path % "google_business", follow_redirects=False).status_code == 302
    assert gm.get(path % "xero", follow_redirects=False).status_code == 403  # accounting needs finance.connect
    assert client_as("finance").get(path % "xero", follow_redirects=False).status_code == 302
    assert gm.get(path % "myspace", follow_redirects=False).status_code == 404


def test_a_magic_session_cannot_connect_accounts(cur, app, make_user, idp, hotel_uae):
    owner = make_user("owner", hotel_uae.slug)
    c = TestClient(app)
    token = magic.create_link(cur, user_id=owner["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    assert c.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    r = c.get(f"/hotels/{hotel_uae.slug}/oauth/google_business/start", follow_redirects=False)
    assert r.status_code == 403


def test_google_start_uses_pkce_and_stores_only_a_state_hash(cur, client_as, idp, hotel_uae):
    gm = client_as("gm")
    r = gm.get(f"/hotels/{hotel_uae.slug}/oauth/google_business/start",
               params={"next": f"/app/{hotel_uae.slug}/visibility?tab=gbp"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["cache-control"] == "no-store"
    loc = urlsplit(r.headers["location"])
    assert (loc.scheme, loc.netloc, loc.path) == ("https", "accounts.google.com", "/o/oauth2/v2/auth")
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q["client_id"] == "gbp-client" and q["response_type"] == "code"
    assert q["redirect_uri"] == f"{API_ORIGIN}/public/oauth/google_business/callback"
    assert q["scope"] == oauth.GOOGLE_BUSINESS_SCOPE
    assert (q["access_type"], q["prompt"], q["code_challenge_method"]) == ("offline", "consent", "S256")
    assert "gbp-secret" not in r.headers["location"]

    cur.execute("SELECT * FROM oauth_states")
    (row,) = cur.fetchall()
    assert bytes(row["state_hash"]) == tokens.hash_token(q["state"])
    assert (row["hotel_slug"], row["provider"], row["user_id"]) == (hotel_uae.slug, "google_business", gm.user["id"])
    assert row["next_path"] == f"/app/{hotel_uae.slug}/visibility?tab=gbp"
    assert row["expires_at"] == T0 + oauth.STATE_TTL
    verifier = privacy.unseal(row["code_verifier_enc"])
    assert verifier.encode() not in bytes(row["code_verifier_enc"])
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert q["code_challenge"] == challenge


def test_start_refuses_an_off_site_next_path_and_unconfigured_apps(cur, client_as, env, idp, hotel_uae):
    gm = client_as("gm")
    _start(gm, hotel_uae.slug, "google_business", next="//evil.example/steal")
    cur.execute("SELECT next_path FROM oauth_states")
    assert cur.fetchone()["next_path"] == f"/app/{hotel_uae.slug}/visibility"
    env(GBP_OAUTH_CLIENT_ID=None)
    r = gm.get(f"/hotels/{hotel_uae.slug}/oauth/google_business/start", params={"format": "json"})
    assert r.status_code == 409 and r.json()["code"] == "not_configured"


# ─── OAuth: the callback ───


def test_google_callback_connects_the_hotel_named_by_the_state(cur, client, client_as, idp, hotel_uae, legacy_slug):
    gm = client_as("gm")
    q, _ = _start(gm, hotel_uae.slug, "google_business", next=f"/app/{hotel_uae.slug}/visibility?tab=gbp")
    idp.respond(GOOGLE_TOKEN, (200, {"access_token": "ya29.ACCESS", "refresh_token": "1//REFRESH", "expires_in": 3599,
                                     "scope": oauth.GOOGLE_BUSINESS_SCOPE, "token_type": "Bearer"}))
    # A hotel in the query is ignored: the state decides. The callback is
    # completed by the same signed-in browser that started the flow.
    out = _callback(gm, "google_business", state=q["state"], code="4/code", hotel=legacy_slug, slug=legacy_slug)
    assert out["location"] == (f"http://localhost:3000/app/{hotel_uae.slug}/visibility?tab=gbp"
                               "&oauth=google_business&oauth_status=connected")

    (req,) = idp.requests
    assert req["url"] == GOOGLE_TOKEN
    form = req["form"]
    assert (form["grant_type"], form["code"], form["client_id"], form["client_secret"]) == (
        "authorization_code", "4/code", "gbp-client", "gbp-secret")
    assert form["redirect_uri"] == q["redirect_uri"]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
    assert challenge == q["code_challenge"]

    assert _connection(cur, legacy_slug, "google_business") is None
    row = _connection(cur, hotel_uae.slug, "google_business")
    assert row["status"] == "active" and row["scopes"] == [oauth.GOOGLE_BUSINESS_SCOPE]
    assert row["connected_by"] == gm.user["id"] and row["expires_at"] == T0 + timedelta(seconds=3599)
    assert b"ya29.ACCESS" not in bytes(row["access_token_enc"]) and b"1//REFRESH" not in bytes(row["refresh_token_enc"])
    assert privacy.unseal(row["access_token_enc"]) == "ya29.ACCESS"
    assert privacy.unseal(row["refresh_token_enc"]) == "1//REFRESH"

    cur.execute("SELECT actor_type, actor_id, after::text AS after FROM audit_log WHERE action = 'core.oauth.connect'")
    (a,) = cur.fetchall()
    assert (a["actor_type"], a["actor_id"]) == ("user", str(gm.user["id"]))
    assert "ya29" not in a["after"] and "REFRESH" not in a["after"]

    # The state was single use.
    replay = _callback(gm, "google_business", state=q["state"], code="4/code")
    assert (replay["oauth_status"], replay["oauth_error"], replay["path"]) == ("error", "invalid_state", "/app")
    assert len(idp.requests) == 1
    assert oauth.get_access_token(cur, hotel_uae.slug, "google_business") == "ya29.ACCESS"
    assert len(idp.requests) == 1


def test_expired_denied_and_mismatched_states_never_reach_the_token_endpoint(client, client_as, clock, idp, hotel_uae):
    gm = client_as("gm")
    q1, _ = _start(gm, hotel_uae.slug, "google_business")
    q2, _ = _start(gm, hotel_uae.slug, "google_business")
    q3, _ = _start(gm, hotel_uae.slug, "google_business")
    denied = _callback(gm, "google_business", state=q2["state"], error="access_denied")
    assert (denied["oauth_status"], denied["oauth_error"]) == ("denied", "access_denied")
    mismatch = _callback(gm, "xero", state=q3["state"], code="c")
    assert mismatch["oauth_error"] == "invalid_state"
    assert _callback(gm, "google_business", state=q3["state"], code="c")["oauth_error"] == "invalid_state"
    clock.advance(minutes=16)
    expired = _callback(gm, "google_business", state=q1["state"], code="c")
    assert (expired["oauth_error"], expired["path"]) == ("state_expired", f"/app/{hotel_uae.slug}/visibility")
    assert _callback(gm, "google_business", code="c")["oauth_error"] == "invalid_state"
    assert idp.requests == []


def test_partial_google_consent_and_refused_codes_connect_nothing(cur, client, client_as, idp, hotel_uae):
    gm = client_as("gm")
    q1, _ = _start(gm, hotel_uae.slug, "google_business")
    q2, _ = _start(gm, hotel_uae.slug, "google_business")
    idp.respond(GOOGLE_TOKEN, (200, {"access_token": "a", "refresh_token": "r", "scope": "openid email"}),
                (400, {"error": "invalid_grant"}))
    assert _callback(gm, "google_business", state=q1["state"], code="c")["oauth_error"] == "scope_denied"
    assert _callback(gm, "google_business", state=q2["state"], code="c")["oauth_error"] == "exchange_failed"
    assert _connection(cur, hotel_uae.slug, "google_business") is None


def test_qbo_uses_basic_auth_no_pkce_and_keeps_the_realm(cur, client, client_as, idp, hotel_uae, finance_connect):
    fin = client_as("finance")
    q, _ = _start(fin, hotel_uae.slug, "qbo")
    assert "code_challenge" not in q and q["scope"] == "com.intuit.quickbooks.accounting"
    idp.respond(QBO_TOKEN, (200, {"access_token": "qbo-a", "refresh_token": "qbo-r", "expires_in": 3600,
                                  "x_refresh_token_expires_in": 8726400, "token_type": "bearer"}))
    out = _callback(fin, "qbo", state=q["state"], code="qc", realmId="9130350000000000")
    assert out["oauth_status"] == "connected" and out["path"] == f"/app/{hotel_uae.slug}/finance"
    (req,) = idp.requests
    assert req["auth"] == "Basic " + base64.b64encode(b"qbo-client:qbo-secret").decode()
    assert "client_secret" not in req["form"] and "code_verifier" not in req["form"]
    row = _connection(cur, hotel_uae.slug, "qbo")
    assert row["account_ref"] == "9130350000000000" and row["meta"] == {"env": "sandbox"}


def test_zoho_exchanges_only_with_an_allowlisted_accounts_server(cur, client, client_as, idp, hotel_uae, finance_connect):
    fin = client_as("finance")
    q_bad, _ = _start(fin, hotel_uae.slug, "zoho")
    q_bad2, _ = _start(fin, hotel_uae.slug, "zoho")
    q_ok, _ = _start(fin, hotel_uae.slug, "zoho")
    assert q_ok["scope"] == "ZohoBooks.accountants.READ,ZohoBooks.settings.READ"
    for state, server in ((q_bad["state"], "https://evil.example"), (q_bad2["state"], "https://accounts.zoho.eu.evil.example")):
        out = _callback(fin, "zoho", state=state, code="zc", **{"accounts-server": server})
        assert out["oauth_error"] == "invalid_accounts_server"
    assert idp.requests == []
    idp.respond("https://accounts.zoho.eu/oauth/v2/token",
                (200, {"access_token": "z-a", "refresh_token": "z-r", "expires_in": 3600, "api_domain": "https://www.zohoapis.eu"}))
    out = _callback(fin, "zoho", state=q_ok["state"], code="zc", location="eu",
                    **{"accounts-server": "https://accounts.zoho.eu"})
    assert out["oauth_status"] == "connected"
    assert idp.requests[0]["url"] == "https://accounts.zoho.eu/oauth/v2/token"
    row = _connection(cur, hotel_uae.slug, "zoho")
    assert row["meta"] == {"accounts_server": "https://accounts.zoho.eu", "api_domain": "https://www.zohoapis.eu"}


# ─── OAuth: access tokens ───


def test_get_access_token_refreshes_and_keeps_the_rotated_refresh_token(cur, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "xero", expires_at=T0 + timedelta(seconds=60))  # inside the 2-minute skew
    idp.respond(XERO_TOKEN, (200, {"access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 1800}))
    assert oauth.get_access_token(cur, hotel_uae.slug, "xero") == "new-access"
    (req,) = idp.requests
    assert req["form"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
    assert req["auth"] == "Basic " + base64.b64encode(b"xero-client:xero-secret").decode()
    row = _connection(cur, hotel_uae.slug, "xero")
    assert privacy.unseal(row["access_token_enc"]) == "new-access"
    assert privacy.unseal(row["refresh_token_enc"]) == "new-refresh"
    assert row["expires_at"] == T0 + timedelta(seconds=1800) and row["last_refreshed_at"] == T0
    # Fresh now: no second call.
    assert oauth.get_access_token(cur, hotel_uae.slug, "xero") == "new-access"
    assert len(idp.requests) == 1


def test_a_locked_row_is_refreshed_through_the_callers_transaction(cur, idp, hotel_uae, monkeypatch):
    """When the caller's own transaction (or another refresher) holds the row,
    SKIP LOCKED reads nothing and the refresh goes through the caller's
    cursor instead of deadlocking against it."""
    _seed(cur, hotel_uae.slug, "xero", expires_at=T0 - timedelta(minutes=1))
    real = oauth._select_for_refresh
    seen: list[bool] = []

    def locked(c, slug, provider, *, skip_locked):
        seen.append(skip_locked)
        if skip_locked:
            return None  # what SKIP LOCKED returns for a row another transaction holds
        assert c is cur
        return real(c, slug, provider, skip_locked=False)

    monkeypatch.setattr(oauth, "_select_for_refresh", locked)
    idp.respond(XERO_TOKEN, (200, {"access_token": "via-caller", "refresh_token": "r2", "expires_in": 1800}))
    assert oauth.get_access_token(cur, hotel_uae.slug, "xero") == "via-caller"
    assert seen == [True, False]
    assert privacy.unseal(_connection(cur, hotel_uae.slug, "xero")["refresh_token_enc"]) == "r2"


def test_waiting_on_a_concurrent_refresher_reads_its_token_without_a_second_call(cur, idp, hotel_uae, monkeypatch):
    _seed(cur, hotel_uae.slug, "xero", expires_at=T0 - timedelta(minutes=1))
    real = oauth._select_for_refresh

    def other_worker_won(c, slug, provider, *, skip_locked):
        if skip_locked:
            return None
        # The other worker's refresh committed while we waited for the lock.
        c.execute("UPDATE oauth_connections SET access_token_enc = %s, expires_at = %s WHERE hotel_slug = %s AND provider = %s",
                  (privacy.seal("theirs"), T0 + timedelta(minutes=30), slug, provider))
        return real(c, slug, provider, skip_locked=False)

    monkeypatch.setattr(oauth, "_select_for_refresh", other_worker_won)
    assert oauth.get_access_token(cur, hotel_uae.slug, "xero") == "theirs"
    assert idp.requests == []


def test_the_redirect_uri_follows_oauth_redirect_base(env, idp):
    assert oauth.redirect_uri(oauth.get_provider("xero")) == f"{API_ORIGIN}/public/oauth/xero/callback"
    env(OAUTH_REDIRECT_BASE="https://www.clefs.world/api/clefs/")
    assert oauth.redirect_uri(oauth.get_provider("xero")) == "https://www.clefs.world/api/clefs/public/oauth/xero/callback"


def test_a_refresh_without_a_new_refresh_token_keeps_the_old_one(cur, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "google_business", expires_at=T0 - timedelta(minutes=1))
    idp.respond(GOOGLE_TOKEN, (200, {"access_token": "g-new", "expires_in": 3599}))
    assert oauth.get_access_token(cur, hotel_uae.slug, "google_business") == "g-new"
    assert privacy.unseal(_connection(cur, hotel_uae.slug, "google_business")["refresh_token_enc"]) == "old-refresh"


def test_a_revoked_grant_marks_the_connection_and_raises_not_connected(cur, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "google_business", expires_at=T0 - timedelta(minutes=1))
    idp.respond(GOOGLE_TOKEN, (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(oauth.NotConnected) as ei:
        oauth.get_access_token(cur, hotel_uae.slug, "google_business")
    assert (ei.value.status, ei.value.code, ei.value.provider) == (409, "not_connected", "google_business")
    row = _connection(cur, hotel_uae.slug, "google_business")
    assert row["status"] == "revoked" and row["access_token_enc"] is None and row["refresh_token_enc"] is None
    cur.execute("SELECT actor_type FROM audit_log WHERE action = 'core.oauth.revoked'")
    assert cur.fetchone()["actor_type"] == "system"
    with pytest.raises(oauth.NotConnected):
        oauth.get_access_token(cur, hotel_uae.slug, "google_business")
    assert len(idp.requests) == 1


def test_a_provider_outage_is_transient_and_keeps_the_connection(cur, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "xero", expires_at=T0 - timedelta(minutes=1))
    idp.respond(XERO_TOKEN, (503, {}), httpx.ConnectTimeout("slow"))
    for _ in range(2):
        with pytest.raises(oauth.OAuthError) as ei:
            oauth.get_access_token(cur, hotel_uae.slug, "xero")
        assert ei.value.transient and ei.value.status == 502
    row = _connection(cur, hotel_uae.slug, "xero")
    assert row["status"] == "active" and row["refresh_token_enc"] is not None
    assert row["last_error"] == "refresh failed: network"


def test_missing_or_refreshless_connections_are_not_connected(cur, idp, hotel_uae):
    with pytest.raises(oauth.NotConnected):
        oauth.get_access_token(cur, hotel_uae.slug, "qbo")
    _seed(cur, hotel_uae.slug, "qbo", refresh=None, expires_at=T0 - timedelta(minutes=1))
    with pytest.raises(oauth.NotConnected):
        oauth.get_access_token(cur, hotel_uae.slug, "qbo")
    assert _connection(cur, hotel_uae.slug, "qbo")["status"] == "expired"
    assert idp.requests == []


def test_zoho_refreshes_at_the_accounts_server_it_connected_through(cur, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "zoho", expires_at=T0 - timedelta(minutes=1),
          meta={"accounts_server": "https://accounts.zoho.eu"})
    idp.respond("https://accounts.zoho.eu/oauth/v2/token", (200, {"error": "invalid_code"}))
    with pytest.raises(oauth.NotConnected):
        oauth.get_access_token(cur, hotel_uae.slug, "zoho")
    assert idp.requests[0]["url"] == "https://accounts.zoho.eu/oauth/v2/token"
    assert _connection(cur, hotel_uae.slug, "zoho")["status"] == "revoked"


# ─── OAuth: listing, account refs and disconnecting ───


def test_connections_list_never_shows_tokens(cur, client_as, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "google_business")
    assert oauth.set_account_ref(cur, hotel_uae.slug, "google_business", "accounts/123", meta={"picked": True})
    r = client_as("gm").get(f"/hotels/{hotel_uae.slug}/oauth")
    assert r.status_code == 200
    by = {x["provider"]: x for x in r.json()}
    assert set(by) == {"google_business", "xero", "qbo", "zoho"}
    assert by["google_business"]["status"] == "active" and by["google_business"]["account_ref"] == "accounts/123"
    assert by["xero"]["status"] == "not_connected" and by["xero"]["configured"] is True
    assert "token" not in r.text and "old-access" not in r.text
    assert client_as("front_desk").get(f"/hotels/{hotel_uae.slug}/oauth").status_code == 403


def test_disconnect_forgets_tokens_and_revokes_after_commit(cur, client_as, idp, hotel_uae):
    _seed(cur, hotel_uae.slug, "google_business")
    idp.respond("https://oauth2.googleapis.com/revoke", (200, {}))
    gm = client_as("gm")
    assert gm.delete(f"/hotels/{hotel_uae.slug}/oauth/google_business").status_code == 204
    row = _connection(cur, hotel_uae.slug, "google_business")
    assert row["status"] == "disconnected" and row["access_token_enc"] is None and row["refresh_token_enc"] is None
    (req,) = idp.requests
    assert req["url"] == "https://oauth2.googleapis.com/revoke" and req["form"] == {"token": "old-refresh"}
    cur.execute("SELECT actor_id FROM audit_log WHERE action = 'core.oauth.disconnect'")
    assert cur.fetchone()["actor_id"] == str(gm.user["id"])
    assert gm.delete(f"/hotels/{hotel_uae.slug}/oauth/google_business").status_code == 404
    with pytest.raises(oauth.NotConnected):
        oauth.get_access_token(cur, hotel_uae.slug, "google_business")
