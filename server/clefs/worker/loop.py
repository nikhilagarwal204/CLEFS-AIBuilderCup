"""
The always-on worker: python -m clefs.worker [--once] [--no-services] [--budget S]

  1. Imports every registrant (jobs.load_all) and checks migrations (applies
     them when MIGRATE_ON_STARTUP, otherwise warns about pending ones).
  2. Every SCHEDULE_EVERY_S runs jobs.tick(). Only the process holding the
     session advisory lock 'clefs_scheduler' schedules; every worker drains
     the queue (SKIP LOCKED keeps them apart).
  3. LISTEN clefs_jobs: enqueue() notifies on commit, so a new task (an SLA
     timer due now, an outbox delivery) runs within a second, not at the
     next 15 s tick.
  4. Runs each module's worker_services (asyncio coroutines, e.g. the SIP
     voice sideband), restarting a crashed service with backoff.
  5. Writes a worker_heartbeats row every loop (/internal/status shows it).

The job loop runs in a thread (psycopg2 is blocking); services run on the
asyncio loop in the main thread. SIGTERM/SIGINT stop both cleanly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import select
import signal
import threading
import time
from typing import Any, Awaitable, Callable

from clefs import db, jobs, logsafe
from clefs.settings import get_settings

log = logging.getLogger("clefs.worker")

SCHEDULE_EVERY_S = 15.0
QUEUE_BUDGET_S = 10.0
SERVICE_BACKOFF_MAX_S = 60.0


class SchedulerLock:
    """Session-level advisory lock on a dedicated unpooled connection: held
    for the life of the process, released automatically if it dies."""

    def __init__(self) -> None:
        self.conn: Any = None
        self.held = False

    def try_acquire(self) -> bool:
        if self.held:
            try:
                with self.conn.cursor() as c:
                    c.execute("SELECT 1")
                return True
            except Exception:
                self.release()
        try:
            if self.conn is None or self.conn.closed:
                self.conn = db.raw_connection(unpooled=True)
                self.conn.autocommit = True
            with self.conn.cursor() as c:
                c.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (jobs.SCHEDULER_LOCK,))
                self.held = bool(c.fetchone()[0])
        except Exception:
            log.exception("could not take the scheduler lock")
            self.release()
        return self.held

    def release(self) -> None:
        self.held = False
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        self.conn = None


class JobLoop:
    def __init__(self, worker_id: str, *, budget_s: float = 25.0) -> None:
        self.worker_id = worker_id
        self.budget_s = budget_s
        self.stop = threading.Event()
        self.lock = SchedulerLock()
        self.listen_conn: Any = None
        self.ticks = 0

    def _listen(self) -> None:
        try:
            if self.listen_conn is None or self.listen_conn.closed:
                self.listen_conn = db.raw_connection(unpooled=True)
                self.listen_conn.autocommit = True
                with self.listen_conn.cursor() as c:
                    c.execute(f"LISTEN {jobs.QUEUE_CHANNEL}")
        except Exception:
            log.exception("LISTEN %s failed; polling only", jobs.QUEUE_CHANNEL)
            self.listen_conn = None

    def _wait(self, timeout: float) -> bool:
        """Sleep up to `timeout`; True when a clefs_jobs notification arrived.
        Waits in slices of at most 1 s so SIGTERM is honoured promptly."""
        if self.listen_conn is None:
            self.stop.wait(timeout)
            return False
        deadline = time.monotonic() + timeout
        try:
            while not self.stop.is_set():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                if select.select([self.listen_conn], [], [], min(1.0, left)) == ([], [], []):
                    continue
                self.listen_conn.poll()
                woke = bool(self.listen_conn.notifies)
                self.listen_conn.notifies.clear()
                if woke:
                    return True
            return False
        except Exception:
            log.exception("LISTEN connection lost")
            try:
                self.listen_conn.close()
            except Exception:
                pass
            self.listen_conn = None
            return False

    def run_once(self) -> jobs.TickReport:
        scheduler = self.lock.try_acquire()
        report = jobs.tick(budget_s=self.budget_s, scheduled=scheduler, queue=True, worker_id=self.worker_id)
        self.ticks += 1
        try:
            jobs.record_heartbeat(
                self.worker_id, kind="worker", is_scheduler=scheduler, ticked=scheduler,
                info={"ticks": self.ticks, "last": report.as_dict()},
            )
        except Exception:
            log.exception("heartbeat failed")
        if report.errors:
            log.warning("tick errors: %s", ", ".join(report.errors[:10]))
        if report.out_of_budget and scheduler:
            # A backlog (fresh database, back from an outage) drains over the
            # next ticks: bounded catch-up plus round-robin keeps each tick
            # moving, so this is progress to watch, not an error.
            log.info(
                "scheduler backlog: ran %d of %d due slots (%d missed slots skipped); rest next tick",
                report.scheduled_run + report.scheduled_failed, report.scheduled_due, report.scheduled_skipped,
            )
        return report

    def run_forever(self) -> None:
        self._listen()
        next_full = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            try:
                if now >= next_full:
                    self.run_once()
                    next_full = time.monotonic() + SCHEDULE_EVERY_S
                else:
                    jobs.tick(budget_s=QUEUE_BUDGET_S, scheduled=False, queue=True, worker_id=self.worker_id)
            except Exception:
                log.exception("worker tick failed")
            if self.listen_conn is None:
                self._listen()
            self._wait(max(0.5, next_full - time.monotonic()))
        self.lock.release()
        if self.listen_conn is not None:
            self.listen_conn.close()


async def _supervise(name: str, service: Callable[[], Awaitable[None]], stop: asyncio.Event) -> None:
    backoff = 1.0
    while not stop.is_set():
        try:
            await service()
            backoff = 1.0
            if stop.is_set():
                return
            log.warning("worker service %s returned; restarting", name)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker service %s crashed; restarting in %.0fs", name, backoff)
        try:
            await asyncio.wait_for(stop.wait(), timeout=backoff)
        except asyncio.TimeoutError:
            pass
        backoff = min(backoff * 2, SERVICE_BACKOFF_MAX_S)


async def _main_async(loop_runner: JobLoop, services: list[tuple[str, Callable[[], Awaitable[None]]]]) -> None:
    stop = asyncio.Event()
    aio = asyncio.get_running_loop()

    def _stop(*_: Any) -> None:
        stop.set()
        loop_runner.stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            aio.add_signal_handler(sig, _stop)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - non-POSIX
            signal.signal(sig, _stop)
    thread = threading.Thread(target=loop_runner.run_forever, name="clefs-jobs", daemon=True)
    thread.start()
    tasks = [asyncio.create_task(_supervise(n, s, stop)) for n, s in services]
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await asyncio.to_thread(thread.join, 30)


def _check_migrations(modules: list[str]) -> None:
    from clefs import migrate

    if get_settings().MIGRATE_ON_STARTUP:
        migrate.up(modules)
        return
    try:
        pending = [r for r in migrate.status(modules) if r["state"] in ("pending", "drift")]
    except Exception:
        log.exception("could not check migrations")
        return
    if pending:
        log.warning("%d migrations are pending or drifted; run python -m clefs.migrate up", len(pending))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m clefs.worker")
    parser.add_argument("--once", action="store_true", help="run one tick and exit (prints the report)")
    parser.add_argument("--no-services", action="store_true", help="do not start module worker_services")
    parser.add_argument("--budget", type=float, default=25.0, help="seconds per tick")
    args = parser.parse_args(argv)
    # Same logging setup as create_app, including the httpx query-secret
    # guards: the worker runs the billed SerpApi jobs (clefs.logsafe).
    logsafe.configure(getattr(logging, get_settings().LOG_LEVEL.upper(), logging.INFO))
    # The worker mints the same credentials as the API (button refs, magic
    # links), so it refuses the same misconfiguration, and names the same
    # production gaps create_app logs (jobs seal PII and hash contacts too).
    settings = get_settings()
    for w in settings.validate_for_startup() + settings.production_gaps():
        log.warning(w)

    modules = jobs.load_all()
    _check_migrations(modules)
    worker_id = jobs.default_worker_id()
    runner = JobLoop(worker_id, budget_s=args.budget)
    if args.once:
        report = runner.run_once()
        runner.lock.release()
        print(json.dumps(report.as_dict(), indent=2, default=str))
        return 1 if report.scheduled_failed or report.tasks_failed else 0

    services: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    if not args.no_services:
        from clefs import registry

        for m in modules:
            for svc in registry.load_module(m).worker_services:
                services.append((f"{m}.{getattr(svc, '__name__', 'service')}", svc))
    log.info("clefs worker %s: %d jobs, %d tasks, %d services", worker_id, len(jobs.registered_jobs()),
             len(jobs.registered_tasks()), len(services))
    asyncio.run(_main_async(runner, services))
    return 0
