"""
Jobs: one registry, one tick() (01 spec E4, 04 spec F3).

    @scheduled("demand.comp_rates_fetch", daily_at("04:00"))          # hotel-local, per hotel
    def fetch(ctx: JobCtx) -> dict | None: ...

    @scheduled("core.notify.sweep", every(minutes=1), per_hotel=False)
    def sweep(ctx): ...

    @task("service.sla_escalate", max_attempts=3)
    def escalate(ctx: JobCtx, payload: dict) -> dict | None: ...

    enqueue("service.sla_escalate", {"request_id": 9}, hotel_slug=slug,
            run_at=now + timedelta(minutes=3), dedupe_key="sla:9:l1", cur=cur)

Who calls tick(): the always-on worker (python -m clefs.worker) every 15 s and
on LISTEN clefs_jobs wake-ups; /internal/cron/tick (Bearer CRON_SECRET) on
Vercel. Both may run at once: every piece of work is claimed in the database,
so each scheduled slot runs once and each queued task is taken by one caller.

Scheduled jobs. A schedule yields at most one "slot" (an instant) that is due
now; the run is claimed by inserting job_runs(job, hotel, slot), which is
UNIQUE. Hotel-local calendar schedules (daily_at, weekly_at, monthly_at,
nth_weekday_at) catch up a missed tick on the same local day: a tick at 07:20
still runs the 07:00 slot once, a tick the next morning does not replay it.
A failed slot is retried by later ticks up to SCHEDULED_MAX_ATTEMPTS times.
Catch-up is bounded: per (job, hotel) only the most recent due slot runs;
older slots missed since its last job_runs row are recorded 'skipped'
(attempts 0), never executed. A tick runs pending slots round-robin across
hotels, always runs at least one, and leaves the queue a share of the budget
(see "Scheduler: bounded catch-up and fair ordering" below).
Per-hotel jobs run where core.hotels.module_enabled holds for the owning
module (loaded, hotel not archived, not switched off), except the
MODULE_OFF_EXEMPT compliance jobs; core jobs run for every live hotel.

Queue. job_queue rows are claimed with FOR UPDATE SKIP LOCKED, so concurrent
consumers never block or double-run. Failures back off exponentially
(backoff_s * 2^(attempt-1)) and go 'dead' after max_attempts. A task can
raise Retry(run_at) to be re-run later without counting as a failure (for
example WhatsApp's per-recipient pacing). SLA timers use enqueue(run_at=...),
never polling.

Names are "<module>.<what>" (core jobs "core."); the contract test checks
that a module registers only its own prefixes.
"""

from __future__ import annotations

import calendar
import importlib
import json
import logging
import os
import re
import socket
import threading
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Iterable, Literal

from clefs import db
from clefs.core import clock
from clefs.core.time import tz_of

log = logging.getLogger("clefs.jobs")

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
QUEUE_CHANNEL = "clefs_jobs"
SCHEDULER_LOCK = "clefs_scheduler"
DEFAULT_TIMEOUT_S = 120
SCHEDULED_MAX_ATTEMPTS = 3
# Share of a tick's budget the scheduled phase may use when the queue also
# drains in that tick; the queue gets the rest (and whatever slots left over).
SCHEDULED_BUDGET_SHARE = 0.6
RETRY_FAILED_AFTER = timedelta(seconds=60)
MAX_BACKOFF = timedelta(hours=6)

TzMode = Literal["hotel", "utc"]


# ─── Schedules ───


@dataclass(frozen=True)
class Schedule:
    kind: Literal["every", "daily", "weekly", "monthly", "nth_weekday"]
    seconds: int = 0
    hhmm: str | None = None
    tz: TzMode = "hotel"
    setting: str | None = None  # a Hotel attribute holding the local time (e.g. "brief_time_local")
    weekday: int | None = None  # ISO 1 (Mon) .. 7 (Sun)
    day: int | None = None  # monthly: 1..31, clamped to the month's last day
    n: int | None = None  # nth_weekday: 1..5, or -1 for the last one

    def describe(self) -> str:
        if self.kind == "every":
            return f"every {self.seconds}s"
        at = f"{self.hhmm} {self.tz}" + (f" (setting {self.setting})" if self.setting else "")
        if self.kind == "daily":
            return f"daily at {at}"
        if self.kind == "weekly":
            return f"weekly on ISO day {self.weekday} at {at}"
        if self.kind == "monthly":
            return f"monthly on day {self.day} at {at}"
        return f"monthly on weekday {self.weekday} #{self.n} at {at}"


def _parse_hhmm(hhmm: str) -> time:
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", hhmm or "")
    if not m:
        raise ValueError(f"time must be HH:MM, got {hhmm!r}")
    return time(int(m.group(1)), int(m.group(2)))


def _check_tz(tz: str) -> TzMode:
    if tz not in ("hotel", "utc"):
        raise ValueError("tz must be 'hotel' or 'utc'")
    return tz  # type: ignore[return-value]


def every(*, hours: int = 0, minutes: int = 0, seconds: int = 0) -> Schedule:
    total = hours * 3600 + minutes * 60 + seconds
    if total < 1:
        raise ValueError("every() needs a positive interval")
    return Schedule(kind="every", seconds=total, tz="utc")


def daily_at(hhmm: str, *, tz: TzMode = "hotel", setting: str | None = None) -> Schedule:
    _parse_hhmm(hhmm)
    return Schedule(kind="daily", hhmm=hhmm, tz=_check_tz(tz), setting=setting)


def weekly_at(iso_weekday: int, hhmm: str, *, tz: TzMode = "hotel") -> Schedule:
    if not 1 <= iso_weekday <= 7:
        raise ValueError("iso_weekday is 1 (Mon) .. 7 (Sun)")
    _parse_hhmm(hhmm)
    return Schedule(kind="weekly", hhmm=hhmm, tz=_check_tz(tz), weekday=iso_weekday)


def monthly_at(day: int, hhmm: str, *, tz: TzMode = "hotel") -> Schedule:
    if not 1 <= day <= 31:
        raise ValueError("day is 1..31 (clamped to the month's last day)")
    _parse_hhmm(hhmm)
    return Schedule(kind="monthly", hhmm=hhmm, tz=_check_tz(tz), day=day)


def nth_weekday_at(n: int, iso_weekday: int, hhmm: str, *, tz: TzMode = "hotel") -> Schedule:
    if n not in (1, 2, 3, 4, 5, -1):
        raise ValueError("n is 1..5, or -1 for the last")
    if not 1 <= iso_weekday <= 7:
        raise ValueError("iso_weekday is 1 (Mon) .. 7 (Sun)")
    _parse_hhmm(hhmm)
    return Schedule(kind="nth_weekday", hhmm=hhmm, tz=_check_tz(tz), weekday=iso_weekday, n=n)


def _day_matches(s: Schedule, d: date) -> bool:
    if s.kind == "daily":
        return True
    if s.kind == "weekly":
        return d.isoweekday() == s.weekday
    if s.kind == "monthly":
        last = calendar.monthrange(d.year, d.month)[1]
        return d.day == min(s.day or 1, last)
    if s.kind == "nth_weekday":
        if d.isoweekday() != s.weekday:
            return False
        if s.n == -1:
            return (d + timedelta(days=7)).month != d.month
        return (d.day - 1) // 7 + 1 == s.n
    return False


def due_slot(s: Schedule, *, now: datetime, hotel: Any = None) -> datetime | None:
    """The slot (aware UTC instant) that is due at `now`, or None.

    every: the start of the current interval (no backfill of older ones).
    Calendar kinds: today's slot in the hotel's (or UTC) calendar once its
    local time has passed; only today's, which is the same-day catch-up rule.
    """
    if s.kind == "every":
        epoch = int(now.timestamp())
        return datetime.fromtimestamp(epoch - epoch % s.seconds, tz=timezone.utc)
    zone = _zone(s, hotel)
    local_now = now.astimezone(zone)
    slot = _slot_on(s, local_now.date(), hotel, zone)
    if slot is None or now < slot:
        return None
    return slot


def _zone(s: Schedule, hotel: Any) -> Any:
    return tz_of(hotel) if (s.tz == "hotel" and hotel is not None) else timezone.utc


def _slot_on(s: Schedule, d: date, hotel: Any, zone: Any) -> datetime | None:
    """A calendar schedule's slot on local date `d` (aware UTC), or None when
    the schedule has no slot that day."""
    if not _day_matches(s, d):
        return None
    at = None
    if s.setting and hotel is not None:
        at = getattr(hotel, s.setting, None)
        if isinstance(at, str):
            at = _parse_hhmm(at[:5])
    if not isinstance(at, time):
        at = _parse_hhmm(s.hhmm or "00:00")
    return datetime.combine(d, at.replace(tzinfo=None), tzinfo=zone).astimezone(timezone.utc)


# How far back missed_slots() looks, and how many it returns. Monthly
# schedules need ~31 days to find the previous slot; the cap keeps a long
# outage of an every-minute job from writing thousands of skipped rows.
MISSED_LOOKBACK_DAYS = 35
MISSED_RECORD_LIMIT = 24


def missed_slots(
    s: Schedule, *, slot: datetime, after: datetime, hotel: Any = None, limit: int = MISSED_RECORD_LIMIT
) -> list[datetime]:
    """Slots strictly between `after` (the last slot this (job, hotel) has a
    job_runs row for) and `slot` (the one due now), newest first, at most
    `limit`. These are the ones a stopped worker missed: the scheduler records
    them as 'skipped' and runs only `slot` (bounded catch-up)."""
    out: list[datetime] = []
    if limit <= 0 or after >= slot:
        return out
    if s.kind == "every":
        step = timedelta(seconds=s.seconds)
        cur = slot - step
        while cur > after and len(out) < limit:
            out.append(cur)
            cur -= step
        return out
    zone = _zone(s, hotel)
    d = slot.astimezone(zone).date()
    floor = max(after, slot - timedelta(days=MISSED_LOOKBACK_DAYS))
    for back in range(1, MISSED_LOOKBACK_DAYS + 1):
        prev = _slot_on(s, d - timedelta(days=back), hotel, zone)
        if prev is None:
            continue
        if prev <= floor or len(out) >= limit:
            break
        out.append(prev)
    return out


# ─── Registry ───


@dataclass
class JobCtx:
    """What a job or task receives. `cur` is its own transaction: it commits
    when the function returns and rolls back if it raises."""

    cur: Any
    job: str
    hotel_slug: str | None
    scheduled_for: datetime | None = None
    attempt: int = 1
    payload: dict = field(default_factory=dict)
    run_id: int | None = None
    _hotel: Any = None

    @property
    def hotel(self) -> Any:
        """The core.hotels.Hotel (None for platform-wide jobs)."""
        if self._hotel is None and self.hotel_slug:
            from clefs.core.hotels import get_hotel

            self._hotel = get_hotel(self.cur, self.hotel_slug)
        return self._hotel

    @property
    def now(self) -> datetime:
        return clock.now()


@dataclass(frozen=True)
class ScheduledJob:
    name: str
    fn: Callable[[JobCtx], Any]
    schedule: Schedule
    per_hotel: bool
    timeout_s: int
    module: str  # owning module ("core" for foundation code)
    registered_from: str


@dataclass(frozen=True)
class TaskDef:
    name: str
    fn: Callable[[JobCtx, dict], Any]
    max_attempts: int
    backoff_s: int
    timeout_s: int
    module: str
    registered_from: str


class Retry(Exception):
    """Raise from a task to run it again at `run_at` without it counting as a
    failed attempt (pacing, a provider's retry-after). Unlike any other
    exception it does not roll back: what the task wrote before raising is
    committed (e.g. the outbox row's attempt count and not_before)."""

    def __init__(self, run_at: datetime, reason: str = ""):
        super().__init__(reason or "retry requested")
        self.run_at = run_at
        self.reason = reason


_scheduled: dict[str, ScheduledJob] = {}
_tasks: dict[str, TaskDef] = {}
_reg_lock = threading.Lock()


def owner_module(py_module: str) -> str:
    """'clefs.modules.demand.jobs' -> 'demand'; any other clefs code -> 'core'."""
    parts = py_module.split(".")
    if len(parts) >= 3 and parts[0] == "clefs" and parts[1] == "modules":
        return parts[2]
    return "core"


def _check_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(f"job name {name!r} must look like '<module>.<what>'")


def scheduled(name: str, schedule: Schedule, *, per_hotel: bool = True, timeout_s: int = DEFAULT_TIMEOUT_S) -> Callable:
    """Register fn(ctx: JobCtx) -> dict | None to run on `schedule` (per hotel
    by default, where the owning module is enabled for that hotel)."""
    _check_name(name)

    def deco(fn: Callable[[JobCtx], Any]) -> Callable[[JobCtx], Any]:
        with _reg_lock:
            existing = _scheduled.get(name)
            if existing is not None and existing.registered_from != fn.__module__:
                raise ValueError(f"job {name!r} already registered by {existing.registered_from}")
            _scheduled[name] = ScheduledJob(
                name=name,
                fn=fn,
                schedule=schedule,
                per_hotel=per_hotel,
                timeout_s=timeout_s,
                module=owner_module(fn.__module__),
                registered_from=fn.__module__,
            )
        return fn

    return deco


def task(name: str, *, max_attempts: int = 5, backoff_s: int = 30, timeout_s: int = DEFAULT_TIMEOUT_S) -> Callable:
    """Register fn(ctx: JobCtx, payload: dict) -> dict | None for enqueue()."""
    _check_name(name)
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    def deco(fn: Callable[[JobCtx, dict], Any]) -> Callable[[JobCtx, dict], Any]:
        with _reg_lock:
            existing = _tasks.get(name)
            if existing is not None and existing.registered_from != fn.__module__:
                raise ValueError(f"task {name!r} already registered by {existing.registered_from}")
            _tasks[name] = TaskDef(
                name=name,
                fn=fn,
                max_attempts=max_attempts,
                backoff_s=backoff_s,
                timeout_s=timeout_s,
                module=owner_module(fn.__module__),
                registered_from=fn.__module__,
            )
        return fn

    return deco


def registered_jobs() -> list[ScheduledJob]:
    with _reg_lock:
        return sorted(_scheduled.values(), key=lambda j: j.name)


def registered_tasks() -> list[TaskDef]:
    with _reg_lock:
        return sorted(_tasks.values(), key=lambda t: t.name)


def unregister(name: str) -> None:
    """Tests only: drop a job or task registered by the test itself."""
    with _reg_lock:
        _scheduled.pop(name, None)
        _tasks.pop(name, None)


# Foundation modules that register jobs, tasks, metrics, action types or signal
# kinds at import. Domain packages (stays/ingest/tax) are optional here because
# another lane builds them.
CORE_REGISTRANTS: tuple[str, ...] = (
    "clefs.core.signals",
    "clefs.core.actions",
    "clefs.core.metrics",
    "clefs.core.consent",
    "clefs.core.privacy",
    "clefs.files",
    "clefs.ai",
    "clefs.notify",
    # core.serpapi.purge must run even on a deploy where no module has
    # imported the SerpApi client yet (04 spec F5).
    "clefs.core.serpapi",
)
OPTIONAL_REGISTRANTS: tuple[str, ...] = ("clefs.core.stays", "clefs.core.ingest", "clefs.core.tax")


def load_all(modules: list[str] | None = None) -> list[str]:
    """Import every registrant: foundation code, then each enabled module's
    manifest (which imports its jobs/metrics/actions). The worker and the cron
    route call this so a tick sees every job. Returns the enabled modules."""
    from clefs import registry

    for name in CORE_REGISTRANTS:
        importlib.import_module(name)
    for name in OPTIONAL_REGISTRANTS:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:
                raise
    enabled = registry.enabled_modules(modules)
    for m in enabled:
        registry.load_module(m)
    return enabled


# ─── Enqueue ───


def _notify_workers(cur: Any, name: str) -> None:
    cur.execute("SELECT pg_notify(%s, %s)", (QUEUE_CHANNEL, name))


def enqueue(
    name: str,
    payload: dict | None = None,
    *,
    hotel_slug: str | None = None,
    run_at: datetime | None = None,
    dedupe_key: str | None = None,
    cur: Any = None,
    max_attempts: int | None = None,
) -> int | None:
    """Queue a task. With `cur` it joins the caller's transaction (so it only
    exists if that commits). Returns the job_queue id, or None when a row with
    the same dedupe_key already exists (enqueue is then a no-op)."""
    _check_name(name)
    body = json.loads(json.dumps(payload or {}, default=str))
    if max_attempts is None:
        with _reg_lock:
            t = _tasks.get(name)
        max_attempts = t.max_attempts if t else 5
    params = (name, hotel_slug, db.Json(body), run_at or clock.now(), dedupe_key, max_attempts, clock.now())
    sql = """
        INSERT INTO job_queue (name, hotel_slug, payload, run_at, dedupe_key, max_attempts, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (dedupe_key) DO NOTHING
        RETURNING id
    """
    if cur is not None:
        cur.execute(sql, params)
        row = cur.fetchone()
        if row:
            _notify_workers(cur, name)
        return row["id"] if row else None
    with db.db() as c:
        c.execute(sql, params)
        row = c.fetchone()
        if row:
            _notify_workers(c, name)
        return row["id"] if row else None


def cancel(cur: Any, *, dedupe_key: str | None = None, job_id: int | None = None) -> int:
    """Cancel a queued (not yet running) task, e.g. an SLA timer once acked."""
    if dedupe_key is None and job_id is None:
        raise ValueError("pass dedupe_key or job_id")
    cur.execute(
        """
        UPDATE job_queue SET status = 'cancelled', finished_at = %s
        WHERE status = 'queued' AND (dedupe_key = %s OR id = %s)
        """,
        (clock.now(), dedupe_key, job_id),
    )
    return cur.rowcount


# ─── Tick ───


@dataclass
class TickReport:
    started_at: datetime
    finished_at: datetime | None = None
    elapsed_ms: int = 0
    scheduled_run: int = 0
    scheduled_failed: int = 0
    scheduled_due: int = 0  # slots this tick found still to run (before the budget cut in)
    scheduled_skipped: int = 0  # older missed slots recorded as 'skipped', never executed
    tasks_run: int = 0
    tasks_failed: int = 0
    tasks_retried: int = 0
    out_of_budget: bool = False
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["started_at"] = self.started_at.isoformat()
        d["finished_at"] = self.finished_at.isoformat() if self.finished_at else None
        return d


def default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _json_result(result: Any) -> Any:
    if result is None:
        return None
    return db.Json(json.loads(json.dumps(result, default=str)))


# Compliance jobs keep running for a hotel that switched their module off:
# PDPL retention and the audit chain check are obligations of the platform,
# not a feature a hotel opts out of (a hotel that disables Trust must still
# have its expired personal data purged and its audit chain verified).
MODULE_OFF_EXEMPT: frozenset[str] = frozenset({"trust.retention_purge", "trust.audit_chain_verify"})

# Scheduled fetches from paid or outside sources (SerpApi, AI answer engines,
# Google Business Profile, review sites, the hotel's own website). A demo
# hotel and its comp set are fictional: these would spend quota on hotels
# that do not exist and bury the planted demo data under empty live results.
# What a presenter uses on the demo hotel (Ask, the concierge) stays live.
DEMO_SKIP: frozenset[str] = frozenset({
    "dr.comp_rates_fetch", "dr.ai_discovery", "reviews.sync",
    "visibility.ai_prompts_refresh", "visibility.ai_radar_weekly", "visibility.google_hotels_daily",
    "visibility.rank_grid_weekly", "visibility.rank_calibrate_monthly", "visibility.site_health_weekly",
    "visibility.gbp_daily", "visibility.gbp_kpis_monthly", "visibility.gbp_keywords_monthly",
    "visibility.lodging_check",
})


def _target_hotels(job: ScheduledJob, hotels: list[Any], disabled: dict[str, set[str]]) -> list[Any]:
    """The hotels a job runs for: the bulk form of core.hotels.module_enabled
    (one query per tick instead of one per job x hotel). `hotels` is already
    list_hotels() (archived excluded) and the caller has dropped jobs whose
    module is not loaded, so what is left is the per-hotel switch: no row or
    enabled = TRUE is on, an enabled = FALSE row is off. Demo hotels skip
    the DEMO_SKIP fetches."""
    if not job.per_hotel:
        return [None]
    if job.name in DEMO_SKIP:
        hotels = [h for h in hotels if getattr(h, "status", "active") != "demo"]
    if job.module == "core" or job.name in MODULE_OFF_EXEMPT:
        return [h for h in hotels if getattr(h, "status", "active") != "archived"]
    return [
        h
        for h in hotels
        if getattr(h, "status", "active") != "archived" and job.module not in disabled.get(h.slug, set())
    ]


def _claim_run(job: ScheduledJob, hotel_slug: str | None, slot: datetime, worker: str) -> tuple[int, int] | None:
    now = clock.now()
    with db.db() as cur:
        cur.execute(
            """
            INSERT INTO job_runs (job, hotel_slug, scheduled_for, status, attempts, worker, started_at)
            VALUES (%s, %s, %s, 'running', 1, %s, %s)
            ON CONFLICT (job, (COALESCE(hotel_slug, '')), scheduled_for) DO NOTHING
            RETURNING id, attempts
            """,
            (job.name, hotel_slug, slot, worker, now),
        )
        row = cur.fetchone()
        if row:
            return row["id"], row["attempts"]
        # Retry a failed slot, or take over one whose runner died mid-run.
        cur.execute(
            """
            UPDATE job_runs SET status = 'running', attempts = attempts + 1, worker = %s, started_at = %s,
                                finished_at = NULL, error = NULL
            WHERE job = %s AND COALESCE(hotel_slug, '') = COALESCE(%s, '') AND scheduled_for = %s
              AND ((status = 'failed' AND attempts < %s AND finished_at < %s)
                   OR (status = 'running' AND started_at < %s))
            RETURNING id, attempts
            """,
            (
                worker,
                now,
                job.name,
                hotel_slug,
                slot,
                SCHEDULED_MAX_ATTEMPTS,
                now - RETRY_FAILED_AFTER,
                now - timedelta(seconds=job.timeout_s * 2),
            ),
        )
        row = cur.fetchone()
        return (row["id"], row["attempts"]) if row else None


def _finish_run(run_id: int, *, ok: bool, t0: float, result: Any = None, error: str | None = None) -> None:
    with db.db() as cur:
        cur.execute(
            """
            UPDATE job_runs SET status = %s, finished_at = %s, duration_ms = %s, result = %s, error = %s
            WHERE id = %s
            """,
            (
                "done" if ok else "failed",
                clock.now(),
                int((_time.monotonic() - t0) * 1000),
                _json_result(result),
                (error or "")[:4000] or None,
                run_id,
            ),
        )


def run_scheduled_job(job: ScheduledJob, hotel: Any, slot: datetime, run_id: int, attempt: int) -> bool:
    t0 = _time.monotonic()
    slug = hotel.slug if hotel is not None else None
    try:
        with db.db(statement_timeout_ms=job.timeout_s * 1000) as cur:
            ctx = JobCtx(cur=cur, job=job.name, hotel_slug=slug, scheduled_for=slot, attempt=attempt, run_id=run_id, _hotel=hotel)
            result = job.fn(ctx)
    except Exception as exc:
        log.exception("scheduled job %s failed for %s", job.name, slug or "platform")
        _finish_run(run_id, ok=False, t0=t0, error=f"{type(exc).__name__}: {exc}")
        return False
    _finish_run(run_id, ok=True, t0=t0, result=result)
    return True


# ─── Scheduler: bounded catch-up and fair ordering ───
#
# Why this shape: on a fresh database 41 hotels x 86 jobs gave 2417 due slots,
# and the old loop (job by job, hotel by hotel, one INSERT round trip per
# pair even when the slot was long done) spent the whole budget before the
# queue ran at all. Now a tick:
#   1. works out every due (job, hotel, slot) in memory,
#   2. reads the newest job_runs row of all of them in ONE query, so a slot
#      that is done (or running, or out of retries) costs no round trip,
#   3. records the older slots a stopped worker missed as 'skipped' (never
#      executed: only the most recent missed slot runs),
#   4. runs what is left round-robin across hotels (platform jobs first, the
#      starting hotel rotating per tick), so no hotel or late-sorting job name
#      starves when the budget is tight, and
#   5. always runs at least one slot when it has any budget, so every tick
#      makes progress; the queue then gets the remaining budget (tick()).


@dataclass
class _Due:
    job: ScheduledJob
    hotel: Any
    slot: datetime

    @property
    def slug(self) -> str | None:
        return self.hotel.slug if self.hotel is not None else None


_rotation = {"n": 0}  # per-process tick counter for the round-robin start


def _latest_runs(due: list[_Due]) -> dict[tuple[str, str], dict]:
    """The newest job_runs row at or before each candidate's slot, keyed
    (job, hotel_slug or ''). One LATERAL lookup per pair on job_runs_slot_idx
    (a backward index scan with LIMIT 1), all in a single statement."""
    if not due:
        return {}
    with db.db() as cur:
        cur.execute(
            """
            SELECT k.job, k.slug, r.scheduled_for, r.status, r.attempts, r.started_at, r.finished_at
            FROM unnest(%s::text[], %s::text[], %s::timestamptz[]) AS k (job, slug, slot)
            CROSS JOIN LATERAL (
                SELECT scheduled_for, status, attempts, started_at, finished_at FROM job_runs
                WHERE job = k.job AND COALESCE(hotel_slug, '') = k.slug AND scheduled_for <= k.slot
                ORDER BY scheduled_for DESC LIMIT 1
            ) r
            """,
            ([d.job.name for d in due], [d.slug or "" for d in due], [d.slot for d in due]),
        )
        return {(r["job"], r["slug"]): dict(r) for r in cur.fetchall()}


def _needs_claim(d: _Due, row: dict | None, now: datetime) -> bool:
    """False when the slot's own row says there is nothing to do yet: done,
    skipped, running within its timeout, or failed and not retryable now.
    _claim_run re-checks the same conditions atomically; this is the cheap
    in-memory pre-filter that keeps finished slots from costing a query."""
    if row is None or row["scheduled_for"] != d.slot:
        return True
    if row["status"] == "failed":
        return row["attempts"] < SCHEDULED_MAX_ATTEMPTS and (row["finished_at"] or now) < now - RETRY_FAILED_AFTER
    if row["status"] == "running":
        return row["started_at"] < now - timedelta(seconds=d.job.timeout_s * 2)
    return False


def _record_missed(pending: list[_Due], latest: dict[tuple[str, str], dict], worker: str) -> int:
    """Write the slots each pending (job, hotel) missed since its last row as
    'skipped' (attempts 0: never executed). Only pairs with an earlier row
    count: a job or hotel that never ran has no gap to report."""
    rows: list[tuple] = []
    now = clock.now()
    for d in pending:
        prev = latest.get((d.job.name, d.slug or ""))
        if prev is None or prev["scheduled_for"] >= d.slot:
            continue
        for s in missed_slots(d.job.schedule, slot=d.slot, after=prev["scheduled_for"], hotel=d.hotel):
            note = {"skipped": "missed", "superseded_by": d.slot.isoformat()}
            rows.append((d.job.name, d.slug, s, worker, now, now, db.Json(note)))
    if not rows:
        return 0
    with db.db() as cur:
        cur.executemany(
            """
            INSERT INTO job_runs (job, hotel_slug, scheduled_for, status, attempts, worker, started_at, finished_at, result)
            VALUES (%s, %s, %s, 'skipped', 0, %s, %s, %s, %s)
            ON CONFLICT (job, (COALESCE(hotel_slug, '')), scheduled_for) DO NOTHING
            """,
            rows,
        )
    return len(rows)


def _interleave(pending: list[_Due], rotate: int) -> list[_Due]:
    """Round-robin by hotel: platform-wide slots first, then one slot per
    hotel per round, the first hotel rotating with `rotate`. Within a hotel
    the oldest slot goes first (then job name, for a stable order)."""
    by_hotel: dict[str, list[_Due]] = {}
    for d in sorted(pending, key=lambda d: (d.slot, d.job.name)):
        by_hotel.setdefault(d.slug or "", []).append(d)
    platform = by_hotel.pop("", [])
    slugs = sorted(by_hotel)
    if slugs:
        k = rotate % len(slugs)
        slugs = slugs[k:] + slugs[:k]
    out = list(platform)
    depth = max((len(v) for v in by_hotel.values()), default=0)
    for i in range(depth):
        out.extend(by_hotel[s][i] for s in slugs if i < len(by_hotel[s]))
    return out


def _run_scheduled(report: TickReport, deadline: float, worker: str, only: set[str] | None, *, has_budget: bool = True) -> None:
    from clefs.core.hotels import list_hotels
    from clefs.registry import enabled_modules

    now = clock.now()
    loaded = set(enabled_modules())
    with db.db() as cur:
        hotels = list_hotels(cur)
        cur.execute("SELECT hotel_slug, module FROM hotel_module_settings WHERE NOT enabled")
        disabled: dict[str, set[str]] = {}
        for r in cur.fetchall():
            disabled.setdefault(r["hotel_slug"], set()).add(r["module"])
    due: list[_Due] = []
    for job in registered_jobs():
        if only is not None and job.name not in only:
            continue
        if job.module != "core" and job.module not in loaded:
            continue
        for hotel in _target_hotels(job, hotels, disabled):
            slot = due_slot(job.schedule, now=now, hotel=hotel)
            if slot is not None:
                due.append(_Due(job, hotel, slot))
    latest = _latest_runs(due)
    pending = [d for d in due if _needs_claim(d, latest.get((d.job.name, d.slug or "")), now)]
    report.scheduled_due = len(pending)
    report.scheduled_skipped = _record_missed(pending, latest, worker)
    _rotation["n"] += 1
    for d in _interleave(pending, _rotation["n"]):
        # Progress guarantee: the first slot runs even if computing the
        # plan used the budget up, unless the tick was given none at all.
        if _time.monotonic() > deadline and (report.scheduled_run + report.scheduled_failed > 0 or not has_budget):
            report.out_of_budget = True
            return
        claim = _claim_run(d.job, d.slug, d.slot, worker)
        if claim is None:
            continue
        if run_scheduled_job(d.job, d.hotel, d.slot, *claim):
            report.scheduled_run += 1
        else:
            report.scheduled_failed += 1
            report.errors.append(f"{d.job.name}@{d.slug or '-'}")


def _claim_task(names: list[str], worker: str) -> dict | None:
    now = clock.now()
    with db.db() as cur:
        cur.execute(
            """
            SELECT id, name FROM job_queue
            WHERE name = ANY(%s)
              AND ((status = 'queued' AND run_at <= %s) OR (status = 'running' AND locked_until < %s))
            ORDER BY run_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """,
            (names, now, now),
        )
        row = cur.fetchone()
        if row is None:
            return None
        with _reg_lock:
            t = _tasks.get(row["name"])
        timeout_s = t.timeout_s if t else DEFAULT_TIMEOUT_S
        cur.execute(
            """
            UPDATE job_queue SET status = 'running', attempts = attempts + 1, locked_by = %s, locked_until = %s
            WHERE id = %s
            RETURNING id, name, hotel_slug, payload, run_at, attempts, max_attempts, dedupe_key
            """,
            (worker, now + timedelta(seconds=timeout_s * 2), row["id"]),
        )
        return dict(cur.fetchone())


def run_task_row(row: dict, report: TickReport | None = None) -> str:
    """Execute one claimed job_queue row. Returns its new status."""
    with _reg_lock:
        t = _tasks.get(row["name"])
    now = clock.now()
    if t is None:
        with db.db() as cur:
            cur.execute(
                "UPDATE job_queue SET status = 'queued', locked_by = NULL, locked_until = NULL, attempts = attempts - 1 WHERE id = %s",
                (row["id"],),
            )
        return "queued"
    retry: Retry | None = None
    try:
        with db.db(statement_timeout_ms=t.timeout_s * 1000) as cur:
            ctx = JobCtx(
                cur=cur,
                job=t.name,
                hotel_slug=row["hotel_slug"],
                scheduled_for=row["run_at"],
                attempt=row["attempts"],
                payload=row["payload"] or {},
                run_id=row["id"],
            )
            try:
                result = t.fn(ctx, row["payload"] or {})
            except Retry as r:
                # Work done before the Retry is kept (it commits with this
                # block): the task recorded why it has to wait.
                retry = r
    except Exception as exc:
        return _task_failed(t, row, now, exc, report)
    if retry is not None:
        r = retry
        with db.db() as cur:
            cur.execute(
                """
                UPDATE job_queue SET status = 'queued', run_at = %s, attempts = attempts - 1,
                                     locked_by = NULL, locked_until = NULL, last_error = %s
                WHERE id = %s
                """,
                (r.run_at, r.reason or None, row["id"]),
            )
        if report is not None:
            report.tasks_retried += 1
        return "queued"
    with db.db() as cur:
        cur.execute(
            """
            UPDATE job_queue SET status = 'done', finished_at = %s, result = %s, locked_by = NULL, locked_until = NULL,
                                 last_error = NULL
            WHERE id = %s
            """,
            (clock.now(), _json_result(result), row["id"]),
        )
    if report is not None:
        report.tasks_run += 1
    return "done"


def _task_failed(t: TaskDef, row: dict, now: datetime, exc: Exception, report: TickReport | None) -> str:
    log.error("task %s (#%s) failed", t.name, row["id"], exc_info=exc)
    err = f"{type(exc).__name__}: {exc}"[:4000]
    dead = row["attempts"] >= row["max_attempts"]
    delay = min(timedelta(seconds=t.backoff_s * 2 ** max(0, row["attempts"] - 1)), MAX_BACKOFF)
    with db.db() as cur:
        cur.execute(
            """
            UPDATE job_queue SET status = %s, run_at = %s, last_error = %s, locked_by = NULL, locked_until = NULL,
                                 finished_at = %s
            WHERE id = %s
            """,
            ("dead" if dead else "queued", now + delay, err, clock.now() if dead else None, row["id"]),
        )
    if report is not None:
        report.tasks_failed += 1
        report.errors.append(f"{t.name}#{row['id']}")
    return "dead" if dead else "queued"


def _drain_queue(
    report: TickReport, deadline: float, worker: str, only: set[str] | None, max_tasks: int | None, *, has_budget: bool = True
) -> None:
    names = [t.name for t in registered_tasks() if only is None or t.name in only]
    if not names:
        return
    done = 0
    # Progress guarantee: one due task per tick even when a slow scheduled
    # slot overran the whole budget (a tick given no budget runs nothing).
    while (_time.monotonic() < deadline or (done == 0 and has_budget)) and (max_tasks is None or done < max_tasks):
        row = _claim_task(names, worker)
        if row is None:
            return
        run_task_row(row, report)
        done += 1
    if _time.monotonic() >= deadline:
        report.out_of_budget = True


def tick(
    *,
    budget_s: float = 25.0,
    scheduled: bool = True,
    queue: bool = True,
    worker_id: str | None = None,
    only: Iterable[str] | None = None,
    max_tasks: int | None = None,
) -> TickReport:
    """Run due scheduled slots, then drain due queued tasks, within budget_s.
    `only` restricts to these job/task names (tests, manual runs)."""
    report = TickReport(started_at=clock.now())
    t0 = _time.monotonic()
    deadline = t0 + budget_s
    worker = worker_id or default_worker_id()
    names = set(only) if only is not None else None
    if scheduled:
        # With the queue also to drain, scheduled slots get a share of the
        # budget, not all of it: a backlog of slots (a fresh database, a
        # worker back from an outage) must not starve SLA timers and the
        # outbox. Unfinished slots are simply picked up by the next tick.
        share = budget_s * (SCHEDULED_BUDGET_SHARE if queue else 1.0)
        _run_scheduled(report, t0 + share, worker, names, has_budget=budget_s > 0)
    if queue:
        _drain_queue(report, deadline, worker, names, max_tasks, has_budget=budget_s > 0)
    report.finished_at = clock.now()
    report.elapsed_ms = int((report.finished_at - report.started_at).total_seconds() * 1000)
    return report


def run_pending(*, only: Iterable[str] | None = None, max_tasks: int = 100) -> TickReport:
    """Tests and scripts: drain due queued tasks only (no scheduled jobs)."""
    return tick(scheduled=False, queue=True, only=only, max_tasks=max_tasks, budget_s=60.0)


# ─── Heartbeats and status ───


def record_heartbeat(
    worker_id: str,
    *,
    kind: Literal["worker", "cron"] = "worker",
    is_scheduler: bool = False,
    ticked: bool = False,
    info: dict | None = None,
) -> None:
    now = clock.now()
    with db.db() as cur:
        cur.execute(
            """
            INSERT INTO worker_heartbeats (worker_id, kind, host, pid, is_scheduler, started_at, last_beat_at, last_tick_at, info)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (worker_id) DO UPDATE SET
                kind = EXCLUDED.kind, is_scheduler = EXCLUDED.is_scheduler, last_beat_at = EXCLUDED.last_beat_at,
                last_tick_at = COALESCE(EXCLUDED.last_tick_at, worker_heartbeats.last_tick_at),
                info = EXCLUDED.info
            """,
            (
                worker_id,
                kind,
                socket.gethostname(),
                os.getpid(),
                is_scheduler,
                now,
                now,
                now if ticked else None,
                db.Json(json.loads(json.dumps(info or {}, default=str))),
            ),
        )


def status_report(cur: Any) -> dict:
    """What /internal/status shows: last tick, queue depth, recent failures,
    outbox health and every worker's heartbeat."""
    now = clock.now()
    day_ago = now - timedelta(hours=24)
    cur.execute("SELECT MAX(last_tick_at) AS last_tick_at FROM worker_heartbeats")
    last_tick = cur.fetchone()["last_tick_at"]
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'queued' AND run_at <= %(now)s) AS due,
               COUNT(*) FILTER (WHERE status = 'queued') AS queued,
               COUNT(*) FILTER (WHERE status = 'running') AS running,
               COUNT(*) FILTER (WHERE status = 'dead' AND finished_at > %(day)s) AS dead_24h,
               MIN(run_at) FILTER (WHERE status = 'queued' AND run_at <= %(now)s) AS oldest_due
        FROM job_queue
        """,
        {"now": now, "day": day_ago},
    )
    q = dict(cur.fetchone())
    oldest = q.pop("oldest_due")
    q["oldest_due_age_s"] = int((now - oldest).total_seconds()) if oldest else 0
    cur.execute(
        "SELECT job, COUNT(*) AS n FROM job_runs WHERE status = 'failed' AND finished_at > %s GROUP BY job ORDER BY job",
        (day_ago,),
    )
    failed_jobs = {r["job"]: r["n"] for r in cur.fetchall()}
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'queued') AS queued,
               COUNT(*) FILTER (WHERE status = 'failed' AND failed_at > %(day)s) AS failed_24h,
               COUNT(*) FILTER (WHERE status IN ('sent', 'delivered', 'read') AND sent_at > %(day)s) AS sent_24h
        FROM outbound_messages
        """,
        {"day": day_ago},
    )
    outbox = dict(cur.fetchone())
    cur.execute(
        "SELECT worker_id, kind, is_scheduler, last_beat_at, last_tick_at FROM worker_heartbeats ORDER BY last_beat_at DESC LIMIT 20"
    )
    workers = [
        {**dict(r), "age_s": int((now - r["last_beat_at"]).total_seconds())} for r in cur.fetchall()
    ]
    return {
        "now": now,
        "last_tick_at": last_tick,
        "last_tick_age_s": int((now - last_tick).total_seconds()) if last_tick else None,
        "queue": q,
        "failed_jobs_24h": failed_jobs,
        "outbox": outbox,
        "workers": workers,
        "registered": {"jobs": len(_scheduled), "tasks": len(_tasks)},
    }


# ─── Housekeeping ───


@scheduled("core.jobs.cleanup", daily_at("03:20", tz="utc"), per_hotel=False)
def _cleanup(ctx: JobCtx) -> dict:
    """Keep the job tables small: finished queue rows after 14 days, run
    history after 60 (failures are kept 60 days too, for /internal/status)."""
    now = ctx.now
    ctx.cur.execute(
        "DELETE FROM job_queue WHERE status IN ('done', 'cancelled') AND finished_at < %s", (now - timedelta(days=14),)
    )
    queue_rows = ctx.cur.rowcount
    ctx.cur.execute("DELETE FROM job_runs WHERE started_at < %s", (now - timedelta(days=60),))
    return {"queue_rows": queue_rows, "run_rows": ctx.cur.rowcount}
