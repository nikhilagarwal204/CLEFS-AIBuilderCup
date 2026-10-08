"""
Test harness shared by every lane. FROZEN after the foundation wave.

Database: one throwaway database per pytest process, clefs_t_<pid>, created
from TEST_DATABASE_URL (default postgresql://localhost/postgres), migrated
once, dropped at exit. Parallel agents in separate worktrees never collide.

Safety: the URL variables are forced to that local database BEFORE clefs is
imported, and a non-local TEST_DATABASE_URL aborts the run. server/.env (which
may hold the production URL) is never loaded (CLEFS_DOTENV=0).

Isolation: each test runs inside one transaction on a shared connection;
db._test_override makes every `with db()` a SAVEPOINT on it, and the test is
rolled back afterwards. Tests marked @pytest.mark.commits run on real
transactions instead, and the database is recreated from a template after.

Fixtures: cur, app, client, hotel_uae, make_user, login, client_as,
device_client, clock, legacy_slug, and for module lanes (01 spec H):
  outbox     the NOTIFY_MODE=capture transport, emptied before and after
  fake_ai    a clefs.ai.FakeBackend installed for the test (works with AI off)
  run_jobs   run_jobs(only=None, *, scheduled=False) drains due queued tasks
             (and, with scheduled=True, due scheduled slots) -> TickReport
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse

import pytest

# ─── Environment (must run before any clefs import) ───

_BASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/postgres")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
if (urlparse(_BASE_URL).hostname or "") not in _LOCAL_HOSTS:
    raise pytest.UsageError(f"TEST_DATABASE_URL must point at a local server, got {urlparse(_BASE_URL).hostname!r}")

TEST_DB = f"clefs_t_{os.getpid()}"
TEMPLATE_DB = f"{TEST_DB}_tpl"
TEST_URL = urlunparse(urlparse(_BASE_URL)._replace(path="/" + TEST_DB))

os.environ.update(
    {
        "CLEFS_DOTENV": "0",
        "DATABASE_URL": TEST_URL,
        "DATABASE_URL_UNPOOLED": TEST_URL,
        "PSQL_DATABASE_URL": TEST_URL,
        "ENV": "test",
        "SECRET_KEY": "test-secret-key-for-the-clefs-test-suite-0123456789",
        "EVENTS_BACKEND": "local",
        "NOTIFY_MODE": "capture",
        "SMS_ENABLED": "false",
        "AI_ENABLED": "false",
        "MIGRATE_ON_STARTUP": "false",
        "COOKIE_SECURE": "false",
        "WEB_ORIGINS": "http://localhost:3000,http://testserver",
        "CRON_SECRET": "test-cron-secret",
        "WORKER_TOKEN": "test-worker-token",
        "DB_POOL_MAX": "4",
    }
)
for _k in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "VERCEL"):
    os.environ.pop(_k, None)

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

from clefs import db as clefs_db  # noqa: E402
from clefs import migrate  # noqa: E402
from clefs.registry import enabled_modules  # noqa: E402
from clefs.settings import reload_settings  # noqa: E402

reload_settings()

TEST_PASSWORD = "correct-horse-battery"


# ─── Database lifecycle ───


def _admin() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(_BASE_URL)
    conn.autocommit = True
    return conn


def _drop(cur: psycopg2.extensions.cursor, name: str) -> None:
    cur.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (name,),
    )
    cur.execute(f'DROP DATABASE IF EXISTS "{name}"')


class _State:
    conn: psycopg2.extensions.connection | None = None


_state = _State()


def pytest_sessionstart(session: pytest.Session) -> None:
    admin = _admin()
    with admin.cursor() as c:
        _drop(c, TEST_DB)
        _drop(c, TEMPLATE_DB)
        c.execute(f'CREATE DATABASE "{TEST_DB}"')
    admin.close()
    migrate.up(enabled_modules(), database_url=TEST_URL, verbose=False)
    admin = _admin()
    with admin.cursor() as c:
        # Template for resetting after @pytest.mark.commits tests.
        c.execute(f'CREATE DATABASE "{TEMPLATE_DB}" TEMPLATE "{TEST_DB}"')
    admin.close()
    _state.conn = psycopg2.connect(TEST_URL)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    clefs_db._test_clear()
    clefs_db.close_pool()
    if _state.conn is not None:
        _state.conn.close()
    if os.environ.get("CLEFS_KEEP_TEST_DB"):
        return
    admin = _admin()
    with admin.cursor() as c:
        _drop(c, TEST_DB)
        _drop(c, TEMPLATE_DB)
    admin.close()


def _reset_from_template() -> None:
    clefs_db.close_pool()
    if _state.conn is not None:
        _state.conn.close()
    admin = _admin()
    with admin.cursor() as c:
        _drop(c, TEST_DB)
        c.execute(f'CREATE DATABASE "{TEST_DB}" TEMPLATE "{TEMPLATE_DB}"')
    admin.close()
    _state.conn = psycopg2.connect(TEST_URL)


def _clear_caches() -> None:
    from clefs.auth import deps, devices, sessions
    from clefs.core import clock, hotels

    sessions.clear_cache()
    devices.clear_cache()
    hotels.clear_cache()
    deps._admin_seen.clear()
    clock.unfreeze()


@pytest.fixture(autouse=True)
def _isolation(request: pytest.FixtureRequest):
    _clear_caches()
    if request.node.get_closest_marker("commits"):
        clefs_db._test_clear()
        try:
            yield
        finally:
            _reset_from_template()
            _clear_caches()
        return
    conn = _state.conn
    assert conn is not None
    if conn.closed:
        conn = _state.conn = psycopg2.connect(TEST_URL)
    clefs_db._test_override(conn)
    try:
        yield
    finally:
        clefs_db._test_clear()
        conn.rollback()
        _clear_caches()


# ─── Core fixtures ───


@pytest.fixture
def cur():
    """RealDictCursor inside the test's transaction (same connection as db())."""
    assert _state.conn is not None
    c = _state.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield c
    finally:
        c.close()


@pytest.fixture(scope="session")
def app():
    from clefs.app import create_app

    return create_app()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def legacy_slug() -> str:
    """A seeded Indian hotel with legacy_open_access (from migrations 0002/0003)."""
    return "hotel-nandan"


@pytest.fixture
def hotel_uae(cur):
    """A locked-down UAE hotel: AED, Asia/Dubai, Sat-Sun weekend, 3 floors of rooms."""
    from clefs.core import hotels, rooms

    h = hotels.create_hotel(
        cur,
        slug="test-dubai",
        name="Test Hotel Dubai",
        city="Dubai",
        country="AE",
        emirate="dubai",
        timezone="Asia/Dubai",
        currency="AED",
        locale="en-AE",
        guest_languages=["en", "ar"],
        star_rating=4,
        rooms_count=13,
        status="pilot",
    )
    cur.execute(
        "INSERT INTO room_types (hotel_slug, code, name, bedrooms, max_occupancy) VALUES (%s, 'DLX', 'Deluxe King', 1, 2)",
        (h.slug,),
    )
    rooms.upsert_rooms(
        cur,
        h.slug,
        [{"room_number": f"{f}{r:02d}", "floor": str(f), "room_type_code": "DLX"} for f in (1, 2) for r in range(1, 6)]
        + [{"room_number": f"5{r:02d}", "floor": "5"} for r in range(1, 4)],
    )
    return h


@pytest.fixture
def make_user(cur):
    """make_user(role, hotel=None, *, email=None, password=TEST_PASSWORD,
    departments=(), floors=(), admin=False) -> dict(id, email, password, membership_id)."""
    from clefs.auth import passwords

    counter = {"n": 0}
    pw_cache: dict[str, str] = {}

    def _make(
        role: str | None = "owner",
        hotel: str | None = None,
        *,
        email: str | None = None,
        password: str = TEST_PASSWORD,
        departments: tuple[str, ...] | list[str] = (),
        floors: tuple[str, ...] | list[str] = (),
        admin: bool = False,
        name: str | None = None,
    ) -> dict:
        counter["n"] += 1
        email = (email or f"{role or 'user'}{counter['n']}@example.test").lower()
        if password not in pw_cache:
            pw_cache[password] = passwords.hash_password(password)
        cur.execute(
            "INSERT INTO users (email, name, password_hash, is_clefs_admin) VALUES (%s, %s, %s, %s) RETURNING id",
            (email, name or f"Test {role or 'user'} {counter['n']}", pw_cache[password], admin),
        )
        uid = cur.fetchone()["id"]
        membership_id = None
        if hotel and role:
            cur.execute(
                "INSERT INTO hotel_memberships (user_id, hotel_slug, role, departments, floors) VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (uid, hotel, role, list(departments), list(floors)),
            )
            membership_id = cur.fetchone()["id"]
        return {"id": uid, "email": email, "password": password, "membership_id": membership_id}

    return _make


@pytest.fixture
def login():
    def _login(client, user: dict):
        r = client.post("/auth/login", json={"email": user["email"], "password": user["password"]})
        assert r.status_code == 200, r.text
        return r

    return _login


@pytest.fixture
def client_as(app, make_user, login, hotel_uae):
    """client_as(role, hotel=hotel_uae.slug, **make_user_kw) -> logged-in TestClient
    (its own cookie jar). The user dict is on client.user."""
    from fastapi.testclient import TestClient

    def _as(role: str, hotel: str | None = None, **kw):
        user = make_user(role, hotel or hotel_uae.slug, **kw)
        c = TestClient(app)
        login(c, user)
        c.user = user  # type: ignore[attr-defined]
        return c

    return _as


@pytest.fixture
def device_client(app, cur, hotel_uae):
    """device_client(floors=(), departments=(), hotel=hotel_uae.slug, kind="floor")
    -> TestClient holding a clefs_device cookie. Device dict on client.device."""
    from fastapi.testclient import TestClient

    from clefs.auth import devices

    def _device(floors=(), departments=(), hotel: str | None = None, kind: str = "floor", name: str = "Test tablet"):
        slug = hotel or hotel_uae.slug
        out = devices.create_pairing_code(
            cur, slug, name=name, kind=kind, floors=list(floors), departments=list(departments), actor=None
        )
        c = TestClient(app)
        r = c.post("/auth/devices/pair", json={"code": out["code"]})
        assert r.status_code == 200, r.text
        c.device = r.json()["device"]  # type: ignore[attr-defined]
        return c

    return _device


class _Outbox:
    """notify.CAPTURE with the helper this fixture documents. CaptureTransport
    itself keeps `.sent` and has no captured(); everything else (fail_next,
    unconfigured, sent, reset) is the transport's own attribute."""

    def __init__(self, transport) -> None:
        object.__setattr__(self, "_transport", transport)

    def captured(self, channel: str | None = None) -> list[dict]:
        from clefs import notify

        return notify.captured(channel)

    def __getattr__(self, name: str):
        return getattr(self._transport, name)

    def __setattr__(self, name: str, value) -> None:
        setattr(self._transport, name, value)


@pytest.fixture
def outbox():
    """The capture transport (NOTIFY_MODE=capture): outbox.captured(channel=None)
    lists what would have been sent; outbox.fail_next(131056) scripts a Meta
    error; outbox.unconfigured.add("sms") switches a channel off."""
    from clefs import notify
    from clefs.notify.transport import set_transport

    set_transport(None)
    notify.clear_captured()
    yield _Outbox(notify.CAPTURE)
    notify.clear_captured()
    set_transport(None)


@pytest.fixture
def fake_ai():
    """A FakeBackend for clefs.ai: queue answers with fake_ai.push(...) and
    read fake_ai.calls afterwards."""
    from clefs import ai

    backend = ai.set_backend(ai.FakeBackend())
    yield backend
    ai.reset_backend()


@pytest.fixture
def run_jobs():
    """run_jobs(only=None, *, scheduled=False) -> TickReport. Drains due queued
    tasks (jobs.run_pending); scheduled=True also runs due scheduled slots
    (jobs.tick). Combine with the clock fixture to move time forward."""
    from clefs import jobs

    def _run(only: list[str] | tuple[str, ...] | str | None = None, *, scheduled: bool = False):
        names = [only] if isinstance(only, str) else only
        if scheduled:
            return jobs.tick(only=names, budget_s=60.0)
        return jobs.run_pending(only=names)

    return _run


class _Clock:
    def freeze(self, at):
        from clefs.core import clock

        clock.freeze(at)
        return at

    def advance(self, **kw):
        from datetime import timedelta

        from clefs.core import clock

        return clock.advance(timedelta(**kw))

    def now(self):
        from clefs.core import clock

        return clock.now()


@pytest.fixture
def clock():
    """clock.freeze(aware_dt); clock.advance(minutes=5); unfrozen after the test."""
    from clefs.core import clock as clock_mod

    c = _Clock()
    try:
        yield c
    finally:
        clock_mod.unfreeze()
