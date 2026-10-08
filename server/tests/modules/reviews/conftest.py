"""Reviews test fixtures: a Dubai hotel with sources, a frozen clock and fakes
for SerpApi, the Google/Tripadvisor HTTP APIs and the AI backend."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

DXB = ZoneInfo("Asia/Dubai")
# Tuesday 2026-09-29 10:00 Dubai.
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)


@pytest.fixture
def now(clock):
    clock.freeze(NOW)
    return clock


@pytest.fixture
def slug(hotel_uae):
    return hotel_uae.slug


@pytest.fixture
def set_settings(cur, hotel_uae):
    def _set(**kw):
        from clefs.core.hotels import set_module_settings

        set_module_settings(cur, hotel_uae.slug, "reviews", kw, actor=None)

    return _set


@pytest.fixture
def env(monkeypatch):
    """env(NAME=value | None): set variables and reload settings; undone after."""
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
def serpapi_fake(env):
    """core.serpapi with a key and a queue of answers (never the network)."""
    from clefs.core import serpapi

    env(SERPAPI_KEY="test-key", SERPAPI_MONTHLY_BUDGET_USD=None)
    fake = serpapi.use_fake()
    yield fake
    serpapi.set_transport(None)


class HttpFake:
    """Routes the module's own httpx calls (GBP, Tripadvisor, Places):
    `answers` maps (method, url-substring) to a JSON body, a (status, body)
    tuple or a callable(request) returning one of those."""

    def __init__(self) -> None:
        self.answers: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []

    def on(self, method: str, fragment: str, answer: object) -> "HttpFake":
        self.answers.append((method.upper(), fragment, answer))
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
def http_fake():
    from clefs.modules.reviews import http

    fake = HttpFake()
    http.set_transport(httpx.MockTransport(fake.handle))
    yield fake
    http.set_transport(None)


@pytest.fixture
def ctx_for(app, cur, make_user, hotel_uae):
    """ctx_for(role) -> (HotelCtx, user) at the test hotel (app: module permissions registered)."""
    from clefs.auth.deps import Principal, resolve_ctx

    def _ctx(role: str, **kw):
        user = make_user(role, hotel_uae.slug, **kw)
        principal = Principal(kind="user", user_id=user["id"], label=f"Test {role}", session_kind="web")
        return resolve_ctx(principal, hotel_uae.slug, cur=cur), user

    return _ctx


@pytest.fixture
def owner_ctx(ctx_for):
    return ctx_for("owner")[0]


GBP_LOCATION = "accounts/111/locations/222"


@pytest.fixture
def gbp_source(cur, slug, monkeypatch, http_fake):
    """A connected Google Business Profile: an active oauth_connections row,
    a token that never refreshes, and the location pinned on the source."""
    from clefs.core import oauth
    from clefs.modules.reviews import store

    cur.execute("INSERT INTO oauth_connections (hotel_slug, provider, account_ref, status) "
                "VALUES (%s, 'google_business', 'accounts/111', 'active')", (slug,))
    monkeypatch.setattr(oauth, "get_access_token", lambda cur, hotel_slug, provider: "tok")
    src = store.create_source(cur, slug, "gbp", config={"location_name": GBP_LOCATION})
    http_fake.on("PUT", "/reply", lambda req: {"comment": "ok", "updateTime": "2026-09-29T06:00:00Z"})
    return src
