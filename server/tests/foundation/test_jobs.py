"""
Jobs (01 spec E4, 04 spec F3): schedule slots in hotel-local time with
same-day catch-up, idempotent claims, retries of failed slots, the queue
(run_at, dedupe, backoff, dead, Retry), module enablement, SKIP LOCKED on real
connections, and the /internal routes.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from clefs import db, jobs
from clefs.core import clock as clock_mod

DUBAI = ZoneInfo("Asia/Dubai")
UTC = timezone.utc


def _hotel(tz: str = "Asia/Dubai", **kw):
    return SimpleNamespace(timezone=tz, slug="h", **kw)


# ─── Slots ───


def test_every_slots_are_interval_starts():
    s = jobs.every(minutes=5)
    now = datetime(2026, 10, 1, 10, 7, 31, tzinfo=UTC)
    assert jobs.due_slot(s, now=now) == datetime(2026, 10, 1, 10, 5, tzinfo=UTC)
    with pytest.raises(ValueError):
        jobs.every()


def test_daily_slots_are_hotel_local_and_only_catch_up_the_same_day():
    s = jobs.daily_at("07:00")
    dubai = _hotel()
    at = lambda h, m, d=1: datetime(2026, 10, d, h, m, tzinfo=DUBAI)  # noqa: E731
    assert jobs.due_slot(s, now=at(6, 59), hotel=dubai) is None
    assert jobs.due_slot(s, now=at(7, 0), hotel=dubai) == at(7, 0).astimezone(UTC)
    assert jobs.due_slot(s, now=at(23, 59), hotel=dubai) == at(7, 0).astimezone(UTC)  # a missed tick catches up
    assert jobs.due_slot(s, now=at(0, 30, d=2), hotel=dubai) is None  # but never the day after
    kolkata = _hotel("Asia/Kolkata")
    # 06:00 Dubai is 07:30 in Kolkata: the Indian hotel is due, the Dubai one is not.
    assert jobs.due_slot(s, now=at(6, 0), hotel=kolkata) == datetime(2026, 10, 1, 7, 0, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)
    utc_job = jobs.daily_at("03:00", tz="utc")
    assert jobs.due_slot(utc_job, now=datetime(2026, 10, 1, 3, 1, tzinfo=UTC), hotel=dubai) == datetime(2026, 10, 1, 3, 0, tzinfo=UTC)


def test_setting_driven_daily_time():
    s = jobs.daily_at("07:00", setting="brief_time_local")
    h = _hotel(brief_time_local=time(6, 30))
    assert jobs.due_slot(s, now=datetime(2026, 10, 1, 6, 45, tzinfo=DUBAI), hotel=h) == datetime(2026, 10, 1, 6, 30, tzinfo=DUBAI).astimezone(UTC)


def test_weekly_monthly_and_nth_weekday():
    h = _hotel()
    weekly = jobs.weekly_at(1, "08:00")  # Mondays
    assert jobs.due_slot(weekly, now=datetime(2026, 10, 5, 9, 0, tzinfo=DUBAI), hotel=h) is not None  # Mon 5 Oct
    assert jobs.due_slot(weekly, now=datetime(2026, 10, 6, 9, 0, tzinfo=DUBAI), hotel=h) is None
    monthly = jobs.monthly_at(31, "06:00")  # clamps to the month's last day
    assert jobs.due_slot(monthly, now=datetime(2026, 9, 30, 7, 0, tzinfo=DUBAI), hotel=h) is not None
    assert jobs.due_slot(monthly, now=datetime(2026, 9, 29, 7, 0, tzinfo=DUBAI), hotel=h) is None
    last_friday = jobs.nth_weekday_at(-1, 5, "10:00")
    assert jobs.due_slot(last_friday, now=datetime(2026, 10, 30, 11, 0, tzinfo=DUBAI), hotel=h) is not None
    assert jobs.due_slot(last_friday, now=datetime(2026, 10, 23, 11, 0, tzinfo=DUBAI), hotel=h) is None
    second_monday = jobs.nth_weekday_at(2, 1, "10:00")
    assert jobs.due_slot(second_monday, now=datetime(2026, 10, 12, 11, 0, tzinfo=DUBAI), hotel=h) is not None
    for bad in (lambda: jobs.daily_at("7am"), lambda: jobs.weekly_at(8, "07:00"), lambda: jobs.nth_weekday_at(0, 1, "07:00")):
        with pytest.raises(ValueError):
            bad()


def test_names_must_be_namespaced():
    with pytest.raises(ValueError):
        jobs.task("nodots")
    with pytest.raises(ValueError):
        jobs.enqueue("Bad Name", {})


# ─── Scheduled runs ───


@pytest.fixture
def daily_job():
    calls: list[tuple[str | None, datetime]] = []

    @jobs.scheduled("core.test_daily", jobs.daily_at("07:00"))
    def _job(ctx: jobs.JobCtx) -> dict:
        calls.append((ctx.hotel_slug, ctx.scheduled_for))
        assert ctx.hotel.slug == ctx.hotel_slug
        return {"ok": True}

    yield calls
    jobs.unregister("core.test_daily")


def test_hotel_local_daily_job_runs_once_and_catches_up(cur, hotel_uae, daily_job, clock):
    def dubai_calls():
        return [c for c in daily_job if c[0] == hotel_uae.slug]

    clock.freeze(datetime(2026, 10, 1, 6, 59, tzinfo=DUBAI))
    jobs.tick(only=["core.test_daily"])
    assert dubai_calls() == []
    assert ("hotel-nandan", datetime(2026, 10, 1, 7, 0, tzinfo=ZoneInfo("Asia/Kolkata")).astimezone(UTC)) in daily_job
    clock.freeze(datetime(2026, 10, 1, 7, 20, tzinfo=DUBAI))  # the 07:00 tick was missed
    report = jobs.tick(only=["core.test_daily"])
    assert dubai_calls() == [(hotel_uae.slug, datetime(2026, 10, 1, 3, 0, tzinfo=UTC))]
    assert report.scheduled_run == 1
    clock.freeze(datetime(2026, 10, 1, 7, 40, tzinfo=DUBAI))
    jobs.tick(only=["core.test_daily"])
    assert len(dubai_calls()) == 1  # the slot is claimed once
    clock.freeze(datetime(2026, 10, 2, 6, 0, tzinfo=DUBAI))
    jobs.tick(only=["core.test_daily"])
    assert len(dubai_calls()) == 1
    clock.freeze(datetime(2026, 10, 2, 7, 5, tzinfo=DUBAI))
    jobs.tick(only=["core.test_daily"])
    assert len(dubai_calls()) == 2
    cur.execute(
        "SELECT status, attempts, result FROM job_runs WHERE job = 'core.test_daily' AND hotel_slug = %s ORDER BY scheduled_for",
        (hotel_uae.slug,),
    )
    assert [(r["status"], r["attempts"], r["result"]) for r in cur.fetchall()] == [("done", 1, {"ok": True})] * 2


def test_a_failed_slot_is_retried(cur, hotel_uae, clock):
    state = {"n": 0}

    @jobs.scheduled("core.test_flaky", jobs.every(minutes=60), per_hotel=False)
    def _flaky(ctx):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("first run fails")
        return {"n": state["n"]}

    try:
        clock.freeze(datetime(2026, 10, 1, 10, 0, 5, tzinfo=UTC))
        r1 = jobs.tick(only=["core.test_flaky"])
        assert r1.scheduled_failed == 1 and r1.errors == ["core.test_flaky@-"]
        jobs.tick(only=["core.test_flaky"])
        assert state["n"] == 1  # retried only after RETRY_FAILED_AFTER
        clock.advance(seconds=61)
        r2 = jobs.tick(only=["core.test_flaky"])
        assert r2.scheduled_run == 1 and state["n"] == 2
        cur.execute("SELECT status, attempts, error FROM job_runs WHERE job = 'core.test_flaky'")
        assert dict(cur.fetchone()) == {"status": "done", "attempts": 2, "error": None}
    finally:
        jobs.unregister("core.test_flaky")


def test_per_hotel_jobs_skip_hotels_where_the_module_is_off(cur, hotel_uae, clock):
    calls: list[str] = []

    def fn(ctx):
        calls.append(ctx.hotel_slug)

    # Upsell, not an owner module: migration 1900 writes enabled = FALSE rows
    # for the owner modules (demand, finance, ...) on every legacy hotel, so
    # hotel-nandan (legacy) would be off too and the positive control below
    # could never run. Legacy hotels keep upsell on (no row).
    fn.__module__ = "clefs.modules.upsell.jobs"  # owned by the upsell module
    jobs.scheduled("upsell.test_scan", jobs.every(minutes=10))(fn)
    try:
        cur.execute(
            "INSERT INTO hotel_module_settings (hotel_slug, module, enabled) VALUES (%s, 'upsell', FALSE)", (hotel_uae.slug,)
        )
        clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
        jobs.tick(only=["upsell.test_scan"])
        assert hotel_uae.slug not in calls and "hotel-nandan" in calls
        assert jobs.registered_jobs()[[j.name for j in jobs.registered_jobs()].index("upsell.test_scan")].module == "upsell"
    finally:
        jobs.unregister("upsell.test_scan")


def test_budget_stops_the_tick(hotel_uae, daily_job, clock):
    clock.freeze(datetime(2026, 10, 1, 8, 0, tzinfo=DUBAI))
    report = jobs.tick(only=["core.test_daily"], budget_s=0)
    assert report.out_of_budget and report.scheduled_run == 0


# ─── Queue ───


@pytest.fixture
def task_log():
    seen: list[dict] = []
    behaviour = {"fail": 0, "retry_until": None}

    @jobs.task("core.test_task", max_attempts=3, backoff_s=10)
    def _t(ctx: jobs.JobCtx, payload: dict) -> dict:
        if behaviour["retry_until"] is not None:
            until = behaviour["retry_until"]
            behaviour["retry_until"] = None
            raise jobs.Retry(until, "not yet")
        if behaviour["fail"] > 0:
            behaviour["fail"] -= 1
            raise RuntimeError("boom")
        seen.append({"payload": payload, "attempt": ctx.attempt, "hotel": ctx.hotel_slug})
        return {"done": payload.get("n")}

    yield SimpleNamespace(seen=seen, behaviour=behaviour)
    jobs.unregister("core.test_task")


def _job(cur, job_id):
    cur.execute("SELECT status, attempts, run_at, last_error, result FROM job_queue WHERE id = %s", (job_id,))
    return dict(cur.fetchone())


def test_run_at_and_dedupe(cur, hotel_uae, task_log, clock):
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    later = jobs.enqueue("core.test_task", {"n": 1}, hotel_slug=hotel_uae.slug, run_at=now + timedelta(minutes=3), dedupe_key="sla:9:l1")
    assert later is not None
    assert jobs.enqueue("core.test_task", {"n": 2}, run_at=now, dedupe_key="sla:9:l1") is None
    assert jobs.run_pending(only=["core.test_task"]).tasks_run == 0
    clock.advance(minutes=3)
    assert jobs.run_pending(only=["core.test_task"]).tasks_run == 1
    assert task_log.seen == [{"payload": {"n": 1}, "attempt": 1, "hotel": hotel_uae.slug}]
    assert _job(cur, later)["result"] == {"done": 1}


def test_the_shared_run_jobs_fixture_drains_the_queue(hotel_uae, task_log, clock, run_jobs):
    """run_jobs lives in the root conftest so module lanes (tests/modules/<m>/)
    get it, with outbox and fake_ai, without copying the patterns."""
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jobs.enqueue("core.test_task", {"n": 7}, run_at=now + timedelta(minutes=1))
    assert run_jobs("core.test_task").tasks_run == 0
    clock.advance(minutes=1)
    assert run_jobs(["core.test_task"]).tasks_run == 1 and task_log.seen[0]["payload"] == {"n": 7}
    assert run_jobs(scheduled=True, only=["core.test_task"]).tasks_run == 0


def test_enqueue_joins_the_callers_transaction(cur, task_log):
    with pytest.raises(RuntimeError):
        with db.db() as c:
            jobs.enqueue("core.test_task", {"n": 1}, cur=c, dedupe_key="rolled-back")
            raise RuntimeError("abort")
    cur.execute("SELECT COUNT(*) AS n FROM job_queue WHERE dedupe_key = 'rolled-back'")
    assert cur.fetchone()["n"] == 0


def test_cancel_a_timer(cur, task_log, clock):
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jid = jobs.enqueue("core.test_task", {"n": 1}, run_at=now + timedelta(minutes=5), dedupe_key="sla:1")
    assert jobs.cancel(cur, dedupe_key="sla:1") == 1
    clock.advance(minutes=10)
    jobs.run_pending(only=["core.test_task"])
    assert _job(cur, jid)["status"] == "cancelled" and task_log.seen == []


def test_failures_back_off_then_go_dead(cur, task_log, clock):
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    task_log.behaviour["fail"] = 5
    jid = jobs.enqueue("core.test_task", {"n": 1})
    r = jobs.run_pending(only=["core.test_task"])
    assert r.tasks_failed == 1
    j = _job(cur, jid)
    assert (j["status"], j["attempts"], j["run_at"]) == ("queued", 1, now + timedelta(seconds=10))
    assert "boom" in j["last_error"]
    clock.advance(seconds=10)
    jobs.run_pending(only=["core.test_task"])
    assert _job(cur, jid)["run_at"] == now + timedelta(seconds=10) + timedelta(seconds=20)
    clock.advance(seconds=20)
    jobs.run_pending(only=["core.test_task"])
    j = _job(cur, jid)
    assert (j["status"], j["attempts"]) == ("dead", 3)
    clock.advance(hours=1)
    assert jobs.run_pending(only=["core.test_task"]).tasks_run == 0


def test_retry_does_not_count_as_a_failure(cur, task_log, clock):
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    task_log.behaviour["retry_until"] = now + timedelta(seconds=6)
    jid = jobs.enqueue("core.test_task", {"n": 7})
    r = jobs.run_pending(only=["core.test_task"])
    assert r.tasks_retried == 1
    j = _job(cur, jid)
    assert (j["status"], j["attempts"], j["run_at"]) == ("queued", 0, now + timedelta(seconds=6))
    clock.advance(seconds=6)
    jobs.run_pending(only=["core.test_task"])
    assert _job(cur, jid)["status"] == "done" and task_log.seen[0]["attempt"] == 1


def test_stale_running_rows_are_taken_over(cur, task_log, clock):
    now = clock.freeze(datetime(2026, 10, 1, 10, 0, tzinfo=UTC))
    jid = jobs.enqueue("core.test_task", {"n": 3})
    cur.execute("UPDATE job_queue SET status = 'running', attempts = 1, locked_until = %s WHERE id = %s", (now - timedelta(seconds=1), jid))
    jobs.run_pending(only=["core.test_task"])
    assert _job(cur, jid)["status"] == "done"


@pytest.mark.commits
def test_queue_claims_skip_rows_locked_by_another_consumer():
    @jobs.task("core.test_skip_locked")
    def _t(ctx, payload):
        return None

    try:
        first = jobs.enqueue("core.test_skip_locked", {"n": 1})
        second = jobs.enqueue("core.test_skip_locked", {"n": 2})
        other = db.raw_connection()
        try:
            with other.cursor() as c:
                c.execute("SELECT id FROM job_queue WHERE id = %s FOR UPDATE", (first,))
                claimed = jobs._claim_task(["core.test_skip_locked"], "worker-b")
                assert claimed is not None and claimed["id"] == second
                assert jobs._claim_task(["core.test_skip_locked"], "worker-c") is None
            other.rollback()
        finally:
            other.close()
        again = jobs._claim_task(["core.test_skip_locked"], "worker-b")
        assert again is not None and again["id"] == first
    finally:
        jobs.unregister("core.test_skip_locked")
        clock_mod.unfreeze()


# ─── Internal routes ───


def test_cron_tick_route_needs_the_cron_secret(client, cur):
    assert client.post("/internal/cron/tick").status_code == 401
    assert client.post("/internal/cron/tick", headers={"Authorization": "Bearer wrong"}).status_code == 401
    r = client.get("/internal/cron/tick?budget_s=5", headers={"Authorization": "Bearer test-cron-secret"})  # Vercel Cron uses GET
    assert r.status_code == 200, r.text
    assert set(r.json()) >= {"scheduled_run", "tasks_run", "errors", "elapsed_ms"}
    assert client.post("/internal/cron/tick", headers={"Authorization": "Bearer test-worker-token"}).status_code == 200
    cur.execute("SELECT kind, is_scheduler, last_tick_at FROM worker_heartbeats WHERE worker_id = 'cron'")
    hb = cur.fetchone()
    assert hb["kind"] == "cron" and hb["is_scheduler"] and hb["last_tick_at"] is not None


def test_internal_status(client, cur, task_log):
    jobs.enqueue("core.test_task", {"n": 1})
    assert client.get("/internal/status").status_code == 401
    r = client.get("/internal/status", headers={"Authorization": "Bearer test-cron-secret"})
    assert r.status_code == 200
    body = r.json()
    assert body["queue"]["due"] >= 1 and "failed_24h" in body["outbox"] and body["notify_mode"] == "capture"
    assert body["registered"]["tasks"] >= 3 and body["events_backend"] == "local"


def test_core_jobs_are_registered():
    jobs.load_all()
    names = {j.name for j in jobs.registered_jobs()} | {t.name for t in jobs.registered_tasks()}
    assert {
        "core.actions.execute", "core.actions.verify", "core.actions.wake", "core.autonomy_review",
        "core.notify.deliver", "core.notify.sweep", "core.files.purge", "core.jobs.cleanup",
    } <= names
