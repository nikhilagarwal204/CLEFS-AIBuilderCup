"""
Core helpers: settings precedence (the production-URL safety net), clock and
hotel-local time, the hash-chained append-only audit log, DB rate limits, and
per-module settings.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import psycopg2
import pytest
from pydantic import BaseModel

from clefs import db, ratelimit
from clefs.audit import audit
from clefs.core import clock, hotels
from clefs.core import time as htime
from clefs.settings import DEV_SECRET_KEY, load_settings


# ─── Settings ───


def test_database_url_prefers_database_url_over_legacy_psql(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/local_one")
    monkeypatch.setenv("PSQL_DATABASE_URL", "postgresql://prod.example/neondb")
    assert load_settings().database_url == "postgresql://localhost/local_one"
    monkeypatch.delenv("DATABASE_URL")
    assert load_settings().database_url == "postgresql://prod.example/neondb"
    monkeypatch.delenv("PSQL_DATABASE_URL")
    monkeypatch.setenv("DB_HOST", "db")
    monkeypatch.setenv("DB_NAME", "aria")
    monkeypatch.setenv("DB_USER", "u")
    monkeypatch.setenv("DB_PASSWORD", "p@ss")
    assert load_settings().database_url == "postgresql://u:p%40ss@db:5432/aria"


def test_unpooled_url_defaults_to_the_pooled_one(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://pooler/x")
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    assert load_settings().database_url_unpooled == "postgresql://pooler/x"
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://direct/x")
    assert load_settings().database_url_unpooled == "postgresql://direct/x"


def test_unpooled_url_is_used_only_when_it_pairs_with_the_effective_url(monkeypatch):
    # Neon's pooled/direct pair: different hosts, same database.
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@ep-x-pooler.me-central-1.aws.neon.tech/neondb")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://u:p@ep-x.me-central-1.aws.neon.tech/neondb")
    assert load_settings().database_url_unpooled.startswith("postgresql://u:p@ep-x.me-central-1")
    # A local DATABASE_URL never borrows a remote direct URL (migrations, LISTEN and
    # the scheduler lock would go to production while queries go local).
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/clefs_dev")
    s = load_settings()
    assert s.database_url_unpooled == "postgresql://localhost/clefs_dev"
    assert any("DATABASE_URL_UNPOOLED ignored" in w for w in s.validate_for_startup())
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://localhost/other_db")
    assert load_settings().database_url_unpooled == "postgresql://localhost/clefs_dev"


def test_an_exported_database_url_drops_the_unpooled_url_that_came_from_dotenv(monkeypatch):
    from clefs import settings as settings_mod

    monkeypatch.setenv("DATABASE_URL", "postgresql://staging.example/neondb")
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://prod.example/neondb")  # names pair, provenance does not
    monkeypatch.setattr(settings_mod, "_DOTENV_KEYS", {"DATABASE_URL_UNPOOLED", "PSQL_DATABASE_URL"})
    s = load_settings()
    assert s.DATABASE_URL_UNPOOLED is None and s.database_url_unpooled == "postgresql://staging.example/neondb"
    monkeypatch.setattr(settings_mod, "_DOTENV_KEYS", {"DATABASE_URL", "DATABASE_URL_UNPOOLED"})  # both from .env: a pair
    # A remote URL that came from server/.env is refused without the opt-in
    # (test_dotenv_remote_db_guard.py pins why); with it, the pair still pairs.
    monkeypatch.delenv("CLEFS_ALLOW_DOTENV_REMOTE_DB", raising=False)
    with pytest.raises(settings_mod.RemoteDatabaseRefused):
        load_settings().database_url_unpooled
    monkeypatch.setenv("CLEFS_ALLOW_DOTENV_REMOTE_DB", "1")
    assert load_settings().database_url_unpooled == "postgresql://prod.example/neondb"


def test_security_settings_fail_closed(monkeypatch):
    from clefs.settings import DEFAULT_PROD_WEB_ORIGINS

    for k in ("ENV", "SECRET_KEY", "COOKIE_SECURE", "WEB_ORIGINS", "VERCEL_ENV", "TRUST_FORWARDED_FOR", "DATABASE_URL_UNPOOLED"):
        monkeypatch.delenv(k, raising=False)
    # A Vercel deploy that never set ENV is production: prod origins for CSRF,
    # Secure cookies, and no public development key.
    monkeypatch.setenv("VERCEL", "1")
    s = load_settings()
    assert (s.ENV, s.is_production, s.COOKIE_SECURE) == ("production", True, True)
    assert s.WEB_ORIGINS == DEFAULT_PROD_WEB_ORIGINS.split(",") and s.TRUST_FORWARDED_FOR
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s.validate_for_startup()
    monkeypatch.setenv("VERCEL_ENV", "preview")
    s = load_settings()
    assert s.ENV == "preview" and not s.is_production and not s.is_dev
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        s.validate_for_startup()

    # Off Vercel, an unconfigured process on a remote database is not a dev process either.
    monkeypatch.delenv("VERCEL")
    monkeypatch.delenv("VERCEL_ENV")
    monkeypatch.setenv("DATABASE_URL", "postgresql://db.internal/clefs")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        load_settings().validate_for_startup()
    monkeypatch.setenv("ENV", "development")  # explicitly a dev process: allowed, with a warning
    assert any("development key" in w for w in load_settings().validate_for_startup())
    monkeypatch.delenv("ENV")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/clefs_dev")  # unset ENV on a local database: dev
    assert load_settings().is_dev and load_settings().SECRET_KEY == DEV_SECRET_KEY

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "k" * 40)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    with pytest.raises(RuntimeError, match="COOKIE_SECURE"):
        load_settings().validate_for_startup()
    monkeypatch.delenv("COOKIE_SECURE")
    assert load_settings().validate_for_startup() == []


def test_env_example_parses_to_blank_values_not_comments(monkeypatch):
    """python-dotenv reads `KEY=   # note` as the value "# note": a blank
    secret would become a public string (and a module list would fail to
    load), so no value in the template may start with '#'."""
    from pathlib import Path

    from dotenv import dotenv_values

    path = Path(__file__).resolve().parents[2] / ".env.example"
    values = dotenv_values(path)
    assert {k: v for k, v in values.items() if v and v.lstrip().startswith("#")} == {}
    for k, v in values.items():
        monkeypatch.setenv(k, v or "")
    s = load_settings()
    assert (s.SECRET_KEY, s.CRON_SECRET, s.WA_APP_SECRET, s.CLEFS_DISABLED_MODULES) == (DEV_SECRET_KEY, None, None, [])


def test_env_example_lists_every_setting():
    import re
    from pathlib import Path

    from clefs.settings import Settings

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text()
    declared = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.M))
    assert [f for f in Settings.model_fields if f not in declared] == []


def test_vercel_defaults_and_production_secret_guard(monkeypatch):
    for k in ("MIGRATE_ON_STARTUP", "EVENTS_BACKEND", "DB_POOL_MAX"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("VERCEL", "1")
    s = load_settings()
    assert (s.MIGRATE_ON_STARTUP, s.EVENTS_BACKEND, s.DB_POOL_MAX) == (True, "local", 3)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.delenv("SECRET_KEY")
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        load_settings().validate_for_startup()


# ─── Clock and hotel time ───


def test_clock_freeze_and_advance():
    t0 = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    with clock.frozen(t0):
        assert clock.now() == t0
        clock.advance(timedelta(minutes=3))
        assert clock.now_ms() == htime.to_ms(t0 + timedelta(minutes=3))
    assert clock.now() != t0
    with pytest.raises(ValueError):
        clock.freeze(datetime(2026, 1, 1))


def test_an_empty_weekend_is_kept(cur, client_as, hotel_uae):
    owner = client_as("owner")
    r = owner.patch(f"/hotels/{hotel_uae.slug}/settings", json={"weekend_days": []})
    assert r.status_code == 200 and r.json()["weekend_days"] == []
    hotels.clear_cache()
    h = hotels.get_hotel(cur, hotel_uae.slug)
    assert h.weekend_days == [] and not htime.is_weekend(h, date(2026, 10, 3))
    assert owner.get(f"/hotels/{hotel_uae.slug}/settings").json()["weekend_days"] == []
    assert htime.is_weekend(object(), date(2026, 10, 3))  # no field at all: the UAE default


def test_dubai_day_bounds_and_weekend():
    h = hotels.Hotel(id=1, slug="d", name="d", timezone="Asia/Dubai", weekend_days=[6, 7])
    start, end = htime.local_day_bounds(h, date(2026, 9, 28))
    assert start == datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
    assert end - start == timedelta(hours=24)
    assert htime.is_weekend(h, date(2026, 10, 3)) and htime.is_weekend(h, date(2026, 10, 4))  # Sat, Sun
    assert not htime.is_weekend(h, date(2026, 10, 2))  # Friday is a working day since 2022
    s, e = htime.local_range(h, date(2026, 9, 1), date(2026, 9, 30))
    assert e - s == timedelta(days=30)


def test_dst_zone_days_are_not_24_hours():
    h = hotels.Hotel(id=1, slug="l", name="l", timezone="Europe/London")
    start, end = htime.local_day_bounds(h, date(2026, 3, 29))  # clocks go forward
    assert end - start == timedelta(hours=23)


def test_business_day_cutoff_and_ms_roundtrip():
    h = hotels.Hotel(id=1, slug="d", name="d", timezone="Asia/Dubai", business_day_cutoff=time(4, 0))
    ts = datetime(2026, 9, 28, 23, 0, tzinfo=timezone.utc)  # 03:00 Dubai on the 29th
    assert htime.business_date(h, ts) == date(2026, 9, 28)
    assert htime.from_ms(htime.to_ms(ts)) == ts
    bs, be = htime.business_day_bounds(h, date(2026, 9, 28))
    assert bs == datetime(2026, 9, 28, 0, 0, tzinfo=timezone.utc)  # 04:00 Dubai


# ─── Audit log ───


def test_audit_rows_are_hash_chained_per_hotel(cur, hotel_uae):
    with db.db() as c:
        for i in range(3):
            audit(c, None, "test.event", "thing", i, after={"i": i, "password": "hunter2"}, hotel_slug=hotel_uae.slug)
        audit(c, None, "test.platform", "thing", 99)
    cur.execute(
        "SELECT id, prev_hash, row_hash, after, audit_row_hash(prev_hash, a) = row_hash AS ok FROM audit_log a "
        "WHERE hotel_slug = %s AND action = 'test.event' ORDER BY id",
        (hotel_uae.slug,),
    )
    rows = cur.fetchall()
    assert len(rows) == 3 and all(r["ok"] for r in rows)
    assert rows[1]["prev_hash"] == rows[0]["row_hash"] and rows[2]["prev_hash"] == rows[1]["row_hash"]
    assert rows[0]["after"]["password"] == "***"  # credentials never land in the log
    cur.execute("SELECT prev_hash FROM audit_log WHERE hotel_slug IS NULL AND action = 'test.platform'")
    assert cur.fetchone() is not None


def test_audit_log_is_append_only(cur, hotel_uae):
    with db.db() as c:
        audit(c, None, "test.event", "thing", 1, hotel_slug=hotel_uae.slug)
    for sql in ("UPDATE audit_log SET action = 'x'", "DELETE FROM audit_log", "TRUNCATE audit_log"):
        cur.execute("SAVEPOINT t")
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(sql)
        cur.execute("ROLLBACK TO SAVEPOINT t")
    cur.execute("SET LOCAL clefs.audit_maintenance = 'on'")
    cur.execute("DELETE FROM audit_log WHERE action = 'test.event'")
    assert cur.rowcount == 1


def test_tampering_breaks_the_chain(cur, hotel_uae):
    with db.db() as c:
        for i in range(2):
            audit(c, None, "test.event", "thing", i, hotel_slug=hotel_uae.slug)
    cur.execute("SET LOCAL clefs.audit_maintenance = 'on'")
    cur.execute("UPDATE audit_log SET after = '{\"forged\": true}' WHERE hotel_slug = %s AND entity_id = '0'", (hotel_uae.slug,))
    cur.execute(
        "SELECT bool_and(audit_row_hash(prev_hash, a) = row_hash) AS ok FROM audit_log a WHERE hotel_slug = %s",
        (hotel_uae.slug,),
    )
    assert cur.fetchone()["ok"] is False


# ─── Rate limits ───


def test_sliding_window_rate_limit(cur, clock):
    t0 = clock.freeze(datetime(2026, 9, 28, 10, 0, tzinfo=timezone.utc))
    results = [ratelimit.hit(cur, "k:1", limit=3, window_s=60) for _ in range(4)]
    assert results == [True, True, True, False]
    assert ratelimit.remaining(cur, "k:1", limit=3, window_s=60) == 0
    clock.freeze(t0 + timedelta(seconds=61))
    assert ratelimit.hit(cur, "k:1", limit=3, window_s=60) is True
    assert ratelimit.hit(cur, "k:2", limit=1, window_s=60) is True


# ─── Hotels and module settings ───


class _DemoSettings(BaseModel):
    comp_set: list[str] = []
    min_rate: int = 100


def test_module_settings_defaults_validation_and_enable(cur, hotel_uae):
    s = hotels.get_module_settings(cur, hotel_uae.slug, "demand", _DemoSettings)
    assert s == _DemoSettings()
    hotels.set_module_settings(cur, hotel_uae.slug, "demand", _DemoSettings(min_rate=250), actor=None)
    assert hotels.get_module_settings(cur, hotel_uae.slug, "demand", _DemoSettings).min_rate == 250
    cur.execute(
        "UPDATE hotel_module_settings SET settings = '{\"min_rate\": \"lots\"}' WHERE hotel_slug = %s AND module = 'demand'",
        (hotel_uae.slug,),
    )
    assert hotels.get_module_settings(cur, hotel_uae.slug, "demand", _DemoSettings) == _DemoSettings()
    assert hotels.module_enabled(cur, hotel_uae.slug, "demand") is True
    hotels.set_module_settings(cur, hotel_uae.slug, "demand", {}, actor=None, enabled=False)
    assert hotels.module_enabled(cur, hotel_uae.slug, "demand") is False
    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'core.module_settings.update' AND hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 2


def test_get_hotel_cache_is_invalidated_on_update(cur, hotel_uae):
    assert hotels.get_hotel(cur, hotel_uae.slug).city == "Dubai"
    hotels.update_hotel(cur, hotel_uae.slug, {"city": "Abu Dhabi"})
    assert hotels.get_hotel(cur, hotel_uae.slug).city == "Abu Dhabi"
    with pytest.raises(Exception):
        hotels.update_hotel(cur, hotel_uae.slug, {"id": 5})


def test_create_hotel_defaults_to_uae_and_locked(cur):
    h = hotels.create_hotel(cur, slug="brand-new", name="Brand New")
    assert (h.country, h.currency, h.timezone, h.weekend_days, h.legacy_open_access) == ("AE", "AED", "Asia/Dubai", [6, 7], False)
    with pytest.raises(Exception):
        hotels.create_hotel(cur, slug="Bad Slug!", name="x")
