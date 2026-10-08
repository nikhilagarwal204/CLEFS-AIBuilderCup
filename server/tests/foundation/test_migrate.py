"""
Migration runner: discovery rules, range checks, idempotence, unapplied-in-order,
checksum drift, the advisory lock, and that 0001-0006 are safe on a database
that already has the legacy (production-shaped) schema and data.

Tests that apply migrations use their own scratch database so the shared test
database is never touched.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg2
import psycopg2.extras
import pytest

from clefs import migrate, registry

BASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/postgres")
CORE_MIGRATIONS = Path(migrate.CORE_DIR)


@pytest.fixture
def scratch_db():
    """A brand-new empty database; yields its URL and drops it afterwards."""
    name = f"clefs_t_{os.getpid()}_scratch"
    admin = psycopg2.connect(BASE_URL)
    admin.autocommit = True
    with admin.cursor() as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        c.execute(f'CREATE DATABASE "{name}"')
    url = urlunparse(urlparse(BASE_URL)._replace(path="/" + name))
    try:
        yield url
    finally:
        with admin.cursor() as c:
            c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _q(url: str, sql: str, params=None) -> list[dict]:
    conn = psycopg2.connect(url)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as c:
            c.execute(sql, params)
            rows = [dict(r) for r in c.fetchall()] if c.description else []
        conn.commit()
        return rows
    finally:
        conn.close()


@pytest.fixture
def fake_tree(tmp_path, monkeypatch):
    """Point discovery at a temp tree: real core migrations + fake module dirs."""
    core = tmp_path / "migrations"
    core.mkdir()
    for p in CORE_MIGRATIONS.iterdir():
        if p.suffix in (".sql", ".py") and not p.name.startswith("_"):
            (core / p.name).write_bytes(p.read_bytes())
    mods = tmp_path / "modules"
    for m in registry.INSTALLED_MODULES:
        (mods / m / "migrations").mkdir(parents=True)
    monkeypatch.setattr(migrate, "CORE_DIR", core)
    monkeypatch.setattr(migrate, "MODULES_DIR", mods)
    monkeypatch.setattr(migrate, "CLEFS_DIR", tmp_path)
    return tmp_path


# ─── Discovery ───


def test_core_migrations_are_discovered_in_order_and_in_range():
    migs = migrate.discover(list(registry.INSTALLED_MODULES))
    core = [m for m in migs if m.module == "core"]
    assert [m.version for m in core][:7] == [1, 2, 3, 4, 5, 6, 7]
    assert [m.name for m in core][:7] == [
        "0001_core_legacy_baseline",
        "0002_core_seed_legacy_hotels",
        "0003_core_hotel_settings",
        "0004_core_auth",
        "0005_core_audit_ratelimit",
        "0006_core_rooms_channels",
        "0007_core_team_invites",
    ]
    for m in migs:
        lo, hi = registry.MIGRATION_RANGES[m.module]
        assert lo <= m.version <= hi


def test_foundation_core_lane_owns_exactly_0001_to_0007():
    # F-server-core owns 0001-0009; 0010-0019 is the contracts lane; 0020+ domain.
    mine = sorted(p.name for p in CORE_MIGRATIONS.iterdir() if p.name[:4].isdigit() and int(p.name[:4]) <= 9)
    assert mine == [
        "0001_core_legacy_baseline.sql",
        "0002_core_seed_legacy_hotels.py",
        "0003_core_hotel_settings.sql",
        "0004_core_auth.sql",
        "0005_core_audit_ratelimit.sql",
        "0006_core_rooms_channels.sql",
        "0007_core_team_invites.sql",
    ]


def test_discovery_rejects_bad_names_ranges_and_duplicates(fake_tree):
    svc = fake_tree / "modules" / "service" / "migrations"
    (svc / "0300_service_wrong_range.sql").write_text("SELECT 1;")
    (svc / "0101_demand_wrong_module.sql").write_text("SELECT 1;")
    (svc / "101_service_short.sql").write_text("SELECT 1;")
    (svc / "0102_service_notes.txt").write_text("x")
    with pytest.raises(migrate.MigrationError) as err:
        migrate.discover(["service"])
    text = str(err.value)
    assert "outside service range" in text
    assert "must start NNNN_service_" in text
    assert "name must be NNNN_<module>_<desc>" in text
    assert "only .sql and .py" in text


def test_duplicate_versions_are_rejected(fake_tree):
    (fake_tree / "modules" / "service" / "migrations" / "0100_service_a.sql").write_text("SELECT 1;")
    (fake_tree / "modules" / "service" / "migrations" / "0100_service_b.sql").write_text("SELECT 1;")
    with pytest.raises(migrate.MigrationError, match="duplicate version 0100"):
        migrate.discover(["service"])


def test_disabled_module_migrations_are_not_discovered(fake_tree):
    (fake_tree / "modules" / "demand" / "migrations" / "0400_demand_x.sql").write_text("SELECT 1;")
    assert any(m.module == "demand" for m in migrate.discover(["demand"]))
    assert not any(m.module == "demand" for m in migrate.discover(["service"]))


def test_new_picks_next_number_in_module_range(fake_tree):
    p1 = migrate.new("service", "Add SLA tables!")
    assert p1.name == "0100_service_add_sla_tables.sql"
    p2 = migrate.new("service", "second", py=True)
    assert p2.name == "0101_service_second.py" and "def up(cur)" in p2.read_text()
    with pytest.raises(migrate.MigrationError):
        migrate.new("service", "x", number=250)


# ─── Applying ───


def test_up_on_fresh_database_is_idempotent(scratch_db):
    applied = migrate.up([], database_url=scratch_db, verbose=False)
    assert applied[:7] == [
        "0001_core_legacy_baseline",
        "0002_core_seed_legacy_hotels",
        "0003_core_hotel_settings",
        "0004_core_auth",
        "0005_core_audit_ratelimit",
        "0006_core_rooms_channels",
        "0007_core_team_invites",
    ]
    assert migrate.up([], database_url=scratch_db, verbose=False) == []
    rows = _q(scratch_db, "SELECT COUNT(*) AS n, bool_and(legacy_open_access) AS open FROM hotels")
    assert rows[0]["n"] == 40 and rows[0]["open"] is True
    assert all(r["state"] == "applied" for r in migrate.status([], database_url=scratch_db))


def test_baseline_is_a_noop_on_a_legacy_production_shaped_database(scratch_db):
    # Build what production looks like today: the legacy DDL (run twice, as
    # every cold start did), the hotel upsert, and live rows.
    baseline = (CORE_MIGRATIONS / "0001_core_legacy_baseline.sql").read_text()
    conn = psycopg2.connect(scratch_db)
    with conn.cursor() as c:
        c.execute(baseline)
        c.execute(baseline)
        c.execute("INSERT INTO hotels (slug, name, city, assistant_name) VALUES ('prod-only', 'Prod Only', 'Pune', 'Asha')")
        c.execute(
            "INSERT INTO staff_requests (hotel_slug, room, type, timestamp) VALUES ('prod-only', '12', 'Towel', 1700000000000)"
        )
        c.execute("INSERT INTO staff_members (hotel_slug, name, role, department) VALUES ('prod-only', 'A', 'B', 'Front Desk')")
    conn.commit()
    conn.close()

    migrate.up([], database_url=scratch_db, verbose=False)

    hotel = _q(scratch_db, "SELECT * FROM hotels WHERE slug = 'prod-only'")[0]
    assert hotel["assistant_name"] == "Asha"  # operator-set data survives
    assert hotel["legacy_open_access"] is True
    assert (hotel["timezone"], hotel["currency"].strip(), hotel["country"].strip()) == ("Asia/Kolkata", "INR", "IN")
    assert _q(scratch_db, "SELECT COUNT(*) AS n FROM staff_requests")[0]["n"] == 1
    staff = _q(scratch_db, "SELECT pin_hash, pin_failed_count, floors FROM staff_members")[0]
    assert staff == {"pin_hash": None, "pin_failed_count": 0, "floors": []}
    settings = _q(scratch_db, "SELECT settings FROM hotel_module_settings WHERE hotel_slug = 'prod-only' AND module = 'concierge'")
    assert settings[0]["settings"] == {"legacy_qr_personalization": True}


def test_hotels_created_after_0003_are_locked_by_default(scratch_db):
    migrate.up([], database_url=scratch_db, verbose=False)
    _q(scratch_db, "INSERT INTO hotels (slug, name) VALUES ('new-uae', 'New')")
    assert _q(scratch_db, "SELECT legacy_open_access FROM hotels WHERE slug = 'new-uae'")[0]["legacy_open_access"] is False


def test_unapplied_versions_run_even_below_the_highest_applied(scratch_db, fake_tree):
    svc = fake_tree / "modules" / "service" / "migrations"
    pg = fake_tree / "modules" / "profit_guard" / "migrations"
    (pg / "0305_profit_guard_later.sql").write_text("CREATE TABLE pfg_later (id INT);")
    migrate.up(["service", "profit_guard"], database_url=scratch_db, verbose=False)
    # service's 0103 merges after profit_guard's 0305 was applied
    (svc / "0103_service_merged_late.sql").write_text("CREATE TABLE svc_late (id INT);")
    applied = migrate.up(["service", "profit_guard"], database_url=scratch_db, verbose=False)
    assert applied == ["0103_service_merged_late"]
    assert _q(scratch_db, "SELECT to_regclass('svc_late') IS NOT NULL AS ok")[0]["ok"]


def test_failed_migration_rolls_back_only_itself(scratch_db, fake_tree):
    svc = fake_tree / "modules" / "service" / "migrations"
    (svc / "0100_service_ok.sql").write_text("CREATE TABLE svc_ok (id INT);")
    (svc / "0101_service_broken.sql").write_text("CREATE TABLE svc_broken (id INT); SELECT no_such_function();")
    with pytest.raises(migrate.MigrationError, match="0101_service_broken"):
        migrate.up(["service"], database_url=scratch_db, verbose=False)
    assert _q(scratch_db, "SELECT to_regclass('svc_ok') IS NOT NULL AS a, to_regclass('svc_broken') IS NULL AS b")[0] == {
        "a": True,
        "b": True,
    }
    states = {r["name"]: r["state"] for r in migrate.status(["service"], database_url=scratch_db)}
    assert states["0100_service_ok"] == "applied" and states["0101_service_broken"] == "pending"


def test_python_migrations_run_with_a_cursor(scratch_db, fake_tree):
    (fake_tree / "modules" / "guests" / "migrations" / "0650_guests_py.py").write_text(
        "def up(cur):\n    cur.execute('CREATE TABLE gst_py (id INT)')\n"
    )
    migrate.up(["guests"], database_url=scratch_db, verbose=False)
    assert _q(scratch_db, "SELECT to_regclass('gst_py') IS NOT NULL AS ok")[0]["ok"]


def test_checksum_drift_is_reported_by_status_and_verify(scratch_db, fake_tree):
    f = fake_tree / "modules" / "service" / "migrations" / "0100_service_x.sql"
    f.write_text("CREATE TABLE svc_x (id INT);")
    migrate.up(["service"], database_url=scratch_db, verbose=False)
    assert migrate.verify(["service"], database_url=scratch_db) == []
    f.write_text("CREATE TABLE svc_x (id BIGINT);")
    assert "drift" in {r["state"] for r in migrate.status(["service"], database_url=scratch_db)}
    problems = migrate.verify(["service"], database_url=scratch_db)
    assert any("checksum drift" in p for p in problems)


def test_concurrent_runners_apply_each_migration_once(scratch_db):
    errors: list[BaseException] = []

    def run():
        try:
            migrate.up([], database_url=scratch_db, verbose=False)
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors
    rows = _q(scratch_db, "SELECT version, COUNT(*) AS n FROM schema_migrations GROUP BY version")
    assert rows and all(r["n"] == 1 for r in rows)
