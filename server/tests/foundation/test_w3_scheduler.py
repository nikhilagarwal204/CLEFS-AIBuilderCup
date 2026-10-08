"""
Scheduler hardening before the demo (Wave 3 ops lane):

  1. Bounded catch-up: per (job, hotel) only the most recent missed slot runs;
     the older ones are recorded 'skipped' (attempts 0), never executed.
     Ticks interleave hotels round-robin and always make progress, and the
     queue still drains when the scheduled backlog outlasts the budget.
  2. Per-hotel jobs run only where core.hotels.module_enabled says the owning
     module is on, and never for archived hotels.
  3. Migration 1900: legacy hotels (legacy_open_access) get explicit
     enabled = FALSE rows for the owner modules; new hotels keep every module.
"""

from __future__ import annotations

import time as _time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from clefs import jobs
from clefs.core import hotels

UTC = timezone.utc
DUBAI = ZoneInfo("Asia/Dubai")

LEGACY_OFF = ("profit_guard", "demand", "amenities", "brief", "ask", "inquiries", "visibility", "finance", "reviews", "trust")
LEGACY_ON = ("service", "concierge", "guests", "upsell", "voice")
MIGRATION = Path(jobs.__file__).parent / "migrations" / "1900_integrator_legacy_module_defaults.sql"


def _register(name: str, schedule: jobs.Schedule, fn, *, owner: str = "core", per_hotel: bool = True) -> None:
    """Register a test job as if `owner`'s module had declared it."""
    fn.__module__ = "clefs.core.test_w3" if owner == "core" else f"clefs.modules.{owner}.jobs"
    jobs.scheduled(name, schedule, per_hotel=per_hotel)(fn)


@pytest.fixture
def cleanup():
    names: list[str] = []
    yield names
    for n in names:
        jobs.unregister(n)


def _make_hotels(cur, n: int, prefix: str = "w3-h") -> list[str]:
    slugs = [f"{prefix}{i:03d}" for i in range(n)]
    for s in slugs:
        hotels.create_hotel(cur, slug=s, name=s.upper())
    return slugs


def _runs(cur, job: str) -> list[dict]:
    cur.execute(
        "SELECT hotel_slug, scheduled_for, status, attempts, result FROM job_runs WHERE job = %s ORDER BY scheduled_for, hotel_slug",
        (job,),
    )
    return [dict(r) for r in cur.fetchall()]


# ─── 1. Bounded catch-up ───


def test_missed_slots_helper_is_bounded_and_newest_first():
    e = jobs.every(minutes=5)
    slot = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    assert jobs.missed_slots(e, slot=slot, after=slot - timedelta(minutes=16)) == [
        slot - timedelta(minutes=5), slot - timedelta(minutes=10), slot - timedelta(minutes=15)
    ]
    assert jobs.missed_slots(e, slot=slot, after=slot - timedelta(minutes=5)) == []  # consecutive: no gap
    long_outage = jobs.missed_slots(e, slot=slot, after=slot - timedelta(days=3))
    assert len(long_outage) == jobs.MISSED_RECORD_LIMIT and long_outage[0] == slot - timedelta(minutes=5)
    h = hotels.Hotel.model_construct(slug="h", timezone="Asia/Dubai")
    weekly = jobs.weekly_at(1, "08:00")  # Mondays
    monday = datetime(2026, 10, 19, 8, 0, tzinfo=DUBAI).astimezone(UTC)
    assert jobs.missed_slots(weekly, slot=monday, after=monday - timedelta(days=21), hotel=h) == [
        monday - timedelta(days=7), monday - timedelta(days=14)
    ]


def test_only_the_latest_missed_slot_runs_and_older_ones_are_skipped(cur, hotel_uae, clock, cleanup):
    calls: list[datetime] = []

    def fn(ctx):
        calls.append(ctx.scheduled_for)

    _register("core.test_w3_every", jobs.every(minutes=10), fn, per_hotel=False)
    cleanup.append("core.test_w3_every")
    clock.freeze(datetime(2026, 10, 1, 10, 0, 5, tzinfo=UTC))
    jobs.tick(only=["core.test_w3_every"])
    clock.freeze(datetime(2026, 10, 1, 10, 45, tzinfo=UTC))  # the worker was down for 10:10-10:30
    report = jobs.tick(only=["core.test_w3_every"])
    assert calls == [datetime(2026, 10, 1, 10, 0, tzinfo=UTC), datetime(2026, 10, 1, 10, 40, tzinfo=UTC)]
    assert report.scheduled_run == 1 and report.scheduled_skipped == 3
    rows = _runs(cur, "core.test_w3_every")
    assert [(r["scheduled_for"].astimezone(UTC).strftime("%H:%M"), r["status"], r["attempts"]) for r in rows] == [
        ("10:00", "done", 1), ("10:10", "skipped", 0), ("10:20", "skipped", 0), ("10:30", "skipped", 0), ("10:40", "done", 1)
    ]
    assert rows[1]["result"] == {"skipped": "missed", "superseded_by": "2026-10-01T10:40:00+00:00"}
    again = jobs.tick(only=["core.test_w3_every"])  # nothing new: no second run, no duplicate skips
    assert again.scheduled_run == 0 and again.scheduled_skipped == 0 and len(calls) == 2


def test_hotel_local_daily_catch_up_runs_today_and_skips_the_missed_days(cur, hotel_uae, clock, cleanup):
    calls: list[tuple[str, datetime]] = []

    def fn(ctx):
        if ctx.hotel_slug == hotel_uae.slug:
            calls.append((ctx.hotel_slug, ctx.scheduled_for))

    _register("core.test_w3_daily", jobs.daily_at("07:00"), fn)
    cleanup.append("core.test_w3_daily")
    clock.freeze(datetime(2026, 10, 1, 7, 5, tzinfo=DUBAI))
    jobs.tick(only=["core.test_w3_daily"])
    clock.freeze(datetime(2026, 10, 4, 9, 0, tzinfo=DUBAI))  # 2 and 3 Oct were missed
    jobs.tick(only=["core.test_w3_daily"])
    at7 = lambda d: datetime(2026, 10, d, 7, 0, tzinfo=DUBAI).astimezone(UTC)  # noqa: E731
    assert calls == [(hotel_uae.slug, at7(1)), (hotel_uae.slug, at7(4))]
    mine = [(r["scheduled_for"], r["status"]) for r in _runs(cur, "core.test_w3_daily") if r["hotel_slug"] == hotel_uae.slug]
    assert mine == [(at7(1), "done"), (at7(2), "skipped"), (at7(3), "skipped"), (at7(4), "done")]


def test_a_fresh_database_records_no_skips(cur, hotel_uae, clock, cleanup):
    """No job_runs history means no known gap: the due slot runs, nothing is
    back-filled as skipped (a new job or hotel has no past to report)."""
    _register("core.test_w3_fresh", jobs.daily_at("07:00"), lambda ctx: None)
    cleanup.append("core.test_w3_fresh")
    clock.freeze(datetime(2026, 10, 4, 9, 0, tzinfo=DUBAI))
    report = jobs.tick(only=["core.test_w3_fresh"])
    assert report.scheduled_skipped == 0 and report.scheduled_run >= 1
    assert {r["status"] for r in _runs(cur, "core.test_w3_fresh")} == {"done"}


# ─── 1b. Fair ticks that always make progress ───


@pytest.fixture
def slow_backlog(cur, clock, cleanup):
    """40 new hotels x 3 slow per-hotel jobs, all due: a backlog far larger
    than one tick's budget (the fresh-database starvation case)."""
    slugs = _make_hotels(cur, 40)
    ran: list[tuple[str, str]] = []
    names = [f"core.test_w3_slow_{c}" for c in "abc"]
    for name in names:

        def fn(ctx, _name=name):
            _time.sleep(0.01)
            if ctx.hotel_slug in slugs:
                ran.append((_name, ctx.hotel_slug))

        _register(name, jobs.every(minutes=30), fn)
        cleanup.append(name)
    clock.freeze(datetime(2026, 10, 1, 10, 1, tzinfo=UTC))
    return slugs, names, ran


def test_one_tick_makes_progress_on_a_huge_backlog(slow_backlog):
    slugs, names, ran = slow_backlog
    report = jobs.tick(only=names, budget_s=0.001)  # the plan alone outlasts this budget
    assert report.scheduled_run == 1 and report.out_of_budget
    assert report.scheduled_due >= len(slugs) * len(names)


def test_ticks_interleave_hotels_and_finish_the_backlog(slow_backlog):
    slugs, names, ran = slow_backlog
    first = jobs.tick(only=names, budget_s=0.25)
    assert 0 < first.scheduled_run < len(slugs) * len(names) and first.out_of_budget
    # Round-robin: the first round gives every hotel one slot before any
    # hotel gets a second, so the first tick's runs are all distinct hotels.
    ours = [h for _, h in ran]
    assert len(ours) == len(set(ours))
    for _ in range(200):
        if len(ran) >= len(slugs) * len(names):
            break
        jobs.tick(only=names, budget_s=0.25)
    assert sorted(ran) == sorted((n, s) for n in names for s in slugs)  # every slot ran exactly once


def test_the_queue_still_drains_behind_a_scheduled_backlog(cur, slow_backlog):
    slugs, names, ran = slow_backlog
    seen: list[dict] = []

    @jobs.task("core.test_w3_task")
    def _t(ctx, payload):
        seen.append(payload)

    try:
        jobs.enqueue("core.test_w3_task", {"n": 1}, cur=cur)
        report = jobs.tick(only=[*names, "core.test_w3_task"], budget_s=0.2)
        assert report.scheduled_run >= 1 and report.scheduled_run < len(slugs) * len(names)
        assert report.tasks_run == 1 and seen == [{"n": 1}]
    finally:
        jobs.unregister("core.test_w3_task")


# ─── 2. Module switch and archived hotels ───


@pytest.fixture
def demand_loaded(monkeypatch):
    """Pretend the demand module is loaded even under CLEFS_ONLY_MODULES, so
    the test does not depend on which lanes a scoped run enabled."""
    from clefs import registry

    real = registry.enabled_modules
    monkeypatch.setattr(registry, "enabled_modules", lambda *a, **k: sorted({*real(*a, **k), "demand"}))


def test_disabled_module_jobs_are_not_scheduled(cur, hotel_uae, clock, cleanup, demand_loaded, legacy_slug):
    calls: list[str] = []
    _register("demand.test_w3_scan", jobs.every(minutes=10), lambda ctx: calls.append(ctx.hotel_slug), owner="demand")
    cleanup.append("demand.test_w3_scan")
    on, off, gone = _make_hotels(cur, 3, prefix="w3-mod-")
    hotels.set_module_settings(cur, off, "demand", {}, actor=None, enabled=False)
    hotels.update_hotel(cur, gone, {"status": "archived"})
    clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jobs.tick(only=["demand.test_w3_scan"])
    assert on in calls and hotel_uae.slug in calls
    assert off not in calls and gone not in calls
    assert legacy_slug not in calls  # migration 1900 switched demand off for legacy hotels
    # No claim row either: a switched-off module leaves no trace in job_runs.
    assert {r["hotel_slug"] for r in _runs(cur, "demand.test_w3_scan")} == set(calls)
    # The scheduler's bulk rule is exactly module_enabled, hotel by hotel.
    everyone = hotels.list_hotels(cur, include_archived=True)
    assert set(calls) == {h.slug for h in everyone if hotels.module_enabled(cur, h.slug, "demand")}
    # Switching it back on schedules the hotel from the next slot.
    hotels.set_module_settings(cur, off, "demand", {}, actor=None, enabled=True)
    clock.advance(minutes=10)
    calls.clear()
    jobs.tick(only=["demand.test_w3_scan"])
    assert off in calls and gone not in calls


def test_jobs_of_an_unloaded_module_never_run(cur, hotel_uae, clock, cleanup, monkeypatch):
    from clefs import registry

    monkeypatch.setattr(registry, "enabled_modules", lambda *a, **k: [])
    calls: list[str] = []
    _register("finance.test_w3_off", jobs.every(minutes=10), lambda ctx: calls.append(ctx.hotel_slug), owner="finance")
    cleanup.append("finance.test_w3_off")
    clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jobs.tick(only=["finance.test_w3_off"])
    assert calls == [] and not hotels.module_enabled(cur, hotel_uae.slug, "finance")


def test_module_enabled_is_off_for_archived_hotels_and_core_is_always_on(cur, hotel_uae):
    assert hotels.module_enabled(cur, hotel_uae.slug, "core")
    hotels.update_hotel(cur, hotel_uae.slug, {"status": "archived"})
    assert not hotels.module_enabled(cur, hotel_uae.slug, "core")
    assert not any(hotels.module_enabled(cur, hotel_uae.slug, m) for m in LEGACY_ON)


def test_core_jobs_skip_archived_hotels(cur, hotel_uae, clock, cleanup):
    calls: list[str] = []
    _register("core.test_w3_core", jobs.every(minutes=10), lambda ctx: calls.append(ctx.hotel_slug))
    cleanup.append("core.test_w3_core")
    hotels.update_hotel(cur, hotel_uae.slug, {"status": "archived"})
    clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jobs.tick(only=["core.test_w3_core"])
    assert calls and hotel_uae.slug not in calls


# ─── 3. Legacy module defaults (migration 1900) ───


def _switches(cur, slugs: list[str]) -> dict[tuple[str, str], bool]:
    cur.execute("SELECT hotel_slug, module, enabled FROM hotel_module_settings WHERE hotel_slug = ANY(%s)", (slugs,))
    return {(r["hotel_slug"], r["module"]): r["enabled"] for r in cur.fetchall()}


def test_legacy_hotels_have_the_owner_modules_off(cur, legacy_slug):
    cur.execute("SELECT slug FROM hotels WHERE legacy_open_access ORDER BY slug")
    legacy = [r["slug"] for r in cur.fetchall()]
    assert legacy_slug in legacy
    rows = _switches(cur, legacy)
    for slug in legacy:
        for m in LEGACY_OFF:
            assert rows.get((slug, m)) is False, (slug, m)
        for m in LEGACY_ON:
            assert rows.get((slug, m), True) is True, (slug, m)  # no row (or an explicit on) means enabled


def test_legacy_defaults_drive_module_enabled(cur, legacy_slug, monkeypatch):
    from clefs import registry

    monkeypatch.setattr(registry, "enabled_modules", lambda *a, **k: [*LEGACY_OFF, *LEGACY_ON])
    assert not any(hotels.module_enabled(cur, legacy_slug, m) for m in LEGACY_OFF)
    assert all(hotels.module_enabled(cur, legacy_slug, m) for m in LEGACY_ON)


def test_new_hotels_keep_every_module_on(cur, hotel_uae, monkeypatch):
    from clefs import registry

    monkeypatch.setattr(registry, "enabled_modules", lambda *a, **k: [*LEGACY_OFF, *LEGACY_ON])
    cur.execute(MIGRATION.read_text())  # re-running it never touches a non-legacy hotel
    assert _switches(cur, [hotel_uae.slug]) == {}
    assert all(hotels.module_enabled(cur, hotel_uae.slug, m) for m in (*LEGACY_OFF, *LEGACY_ON))


def test_the_migration_keeps_an_owner_choice_and_is_idempotent(cur, legacy_slug):
    hotels.set_module_settings(cur, legacy_slug, "demand", {}, actor=None, enabled=True)
    cur.execute("DELETE FROM hotel_module_settings WHERE hotel_slug = %s AND module = 'brief'", (legacy_slug,))
    before = _switches(cur, [legacy_slug])
    cur.execute(MIGRATION.read_text())
    cur.execute(MIGRATION.read_text())
    after = _switches(cur, [legacy_slug])
    assert after[(legacy_slug, "demand")] is True  # an owner who switched it on keeps it
    assert after[(legacy_slug, "brief")] is False  # a missing row is filled with off
    assert {k: v for k, v in after.items() if k[1] != "brief"} == {k: v for k, v in before.items() if k[1] != "brief"}


def test_the_migration_is_in_the_integrator_range():
    from clefs import migrate

    found = [m for m in migrate.discover([]) if m.version == 1900]
    assert [(m.module, m.kind) for m in found] == [("integrator", "sql")]
