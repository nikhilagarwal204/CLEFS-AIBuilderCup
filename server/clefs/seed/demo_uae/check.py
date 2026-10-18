"""
`python -m clefs.cli demo-check --hotel demo-deira-crest`: the end-to-end
proof that a seeded demo hotel shows what 02 spec 9 promises.

    python -m clefs.cli demo-check --hotel demo-deira-crest [--manifest path] [--anchor 2026-09-27]
                                   [--scale 1.0] [--no-jobs] [--commit]

What it does, in one transaction:
  1. Freezes the clock at the anchor evening (the moment the seed ran "at")
     and runs every per-hotel scheduled job once for the demo hotel, in
     pipeline order (stays, detectors, signals, then the brief last), each in
     its own savepoint so one failing job never hides the others. This is what
     a worker catching up on the anchor would do; plants must survive it.
  2. Checks every planted case of the manifest (expected_plants.json) through
     the part that planted it (money.fingerprints_of, ops.surfaced,
     growth.surfaced): plants found / expected, decoys fired, per module.
  3. Prints one table per module plus the core property checks, open signals
     by brief section, the anchor's brief top 5 and the verified value.
  4. Rolls back (default), so the check is repeatable; --commit keeps what
     the jobs wrote. A few jobs also write on a connection of their own
     (trust.retention_purge commits its batches one by one, and enqueue()
     without cur commits at once): those writes are not undone, exactly as
     when the worker runs the job; tasks queued that way are cancelled after
     the rollback, and a write that waits on the check's transaction is
     cancelled by the unblocker (see there) and reported.
Exit status is non-zero on any miss: a plant not found, a decoy that fired
(or could not be evaluated), a failed core, money or brief check, a module
with no planted case at all, or a job that raised for a reason other than
being offline or blocked by the check's own transaction. Only per-hotel jobs
run; platform-wide ones (cross-hotel purges and syncs) are counted, not run.
A hotel whose status is not 'demo' is refused before any job runs.

Measured on local clefs_demo (anchor 2026-09-27, full scale): 91 jobs in
about 20 s, 135/135 money plants (216/216 fingerprints), every ops and
growth plant, 0 decoys fired.

Offline by construction. While jobs run, clefs.ai answers every task with
AIDisabled (each module's documented fallback runs) and Python-level sockets
refuse to connect (vendor adapters, WhatsApp, e-mail), so a demo-check run on
a laptop whose .env holds real keys never spends money or messages anyone.
The database driver (libpq) does not use Python sockets and is unaffected.
Queued tasks the jobs enqueue are never drained here.

Test mode. The end-to-end test seeds at --scale 0.1 to stay under two
minutes; a thinned history cannot land in the spec's occupancy, ADR and
channel-mix ranges, so with --scale below 1 those volume checks are reported
as skipped instead of failed. Every plant and decoy is still checked.

This module defines no PART_ORDER, seed_part or CHECKS: discover_parts and
_check.part_checks import it and find nothing to run.
"""

from __future__ import annotations

import json
import logging
import socket
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from clefs.core import clock

# ─── Constants ───

# Job order: the pipeline a night runs in. Stays and ingest first (occupancy,
# OTB), then the detectors that read them, then the modules that read
# signals, and the brief last so its top 5 sees everything above. A module
# not listed runs just before the brief.
JOB_ORDER = ("core", "profit_guard", "demand", "amenities", "service", "reviews", "guests", "inquiries",
             "upsell", "visibility", "finance", "trust", "concierge", "voice", "brief")

# Per-module tables, in the order they print. Money plants carry no module
# key (the money part predates it): they are Profit Guard's.
SECTIONS = ("profit_guard", "service", "amenities", "reviews", "guests", "inquiries", "upsell", "demand",
            "visibility", "finance", "brief")
OPS = ("service", "amenities", "reviews", "guests", "inquiries", "upsell")
GROWTH = ("demand", "visibility", "finance", "brief")

# Core checks that measure history volume: a thinned test seed (--scale < 1)
# cannot reach the spec's ranges, so they are skipped there, not failed.
VOLUME_CHECKS = ("history occupancy", "history ADR", "channel mix")

# Money acceptance (02 spec 9): precision over claims, and RECOVERABLE at
# least 3 x an AED 1,500 monthly subscription.
MIN_PRECISION = 0.9
MIN_RECOVERABLE_MINOR = 3 * 150_000


@dataclass
class Row:
    """One line of a module table: a check (or plant group) and its verdict.
    ok=None means skipped (shown, never a failure)."""

    name: str
    ok: bool | None
    detail: str = ""


@dataclass
class Section:
    key: str
    title: str
    expected: int = 0
    found: int = 0
    decoys: int = 0
    fired: int = 0
    missing: list[str] = field(default_factory=list)
    fired_keys: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # context lines, never a verdict

    @property
    def ok(self) -> bool:
        return not self.missing and not self.fired_keys and all(r.ok is not False for r in self.rows)


@dataclass
class JobRun:
    name: str
    module: str
    status: str  # ok | offline (a vendor it could not reach) | blocked (see unblocker) | error
    ms: int = 0
    detail: str = ""


# ─── Manifest ───


def load_manifest(path: str | None, slug: str) -> tuple[dict | None, str]:
    """The planted-case manifest for `slug` and where it came from. A
    manifest for another hotel (a test seed, or --manifest elsewhere) does
    not count: its plants name another hotel's subjects."""
    from clefs.seed.demo_uae import MANIFEST_PATH

    target = path or MANIFEST_PATH
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, f"{target} not readable ({type(exc).__name__})"
    if data.get("hotel") != slug:
        return None, f"{target} describes {data.get('hotel')!r}, not {slug!r} (re-run seed-demo, or pass --manifest)"
    return data, target


def plant_module(p: dict) -> str:
    return p.get("module") or "profit_guard"

# ─── Offline guard ───


class Offline(ConnectionRefusedError):
    """Raised by the socket guard. A ConnectionRefusedError (an OSError), so
    vendor adapters take the same path as a vendor that is down."""


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


class OfflineAI:
    """clefs.ai backend for the check: every task is refused as if AI were
    switched off, so each module's documented fallback runs and no model is
    ever called (or billed)."""

    name = "demo-check-offline"

    def respond(self, req: Any) -> Any:
        from clefs import ai

        raise ai.AIDisabled(f"demo-check runs offline ({req.task})")


@contextmanager
def offline() -> Iterator[list[str]]:
    """Block outbound network and AI while jobs run; yields the list of
    refused destinations (host:port) for the report."""
    from clefs import ai

    refused: list[str] = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def blocked(address: Any) -> bool:
        host = address[0] if isinstance(address, tuple) and address else None
        return host is not None and str(host) not in _LOOPBACK

    def connect(self: socket.socket, address: Any) -> Any:
        if blocked(address):
            refused.append(f"{address[0]}:{address[1] if len(address) > 1 else ''}")
            raise Offline(f"demo-check is offline: no connection to {address[0]}")
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        if blocked(address):
            refused.append(f"{address[0]}:{address[1] if len(address) > 1 else ''}")
            return 111  # ECONNREFUSED
        return real_connect_ex(self, address)

    prev = getattr(ai, "_backend_override", None)
    ai_log = logging.getLogger("clefs.ai")
    level = ai_log.level
    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]
    ai.set_backend(OfflineAI())
    # clefs.ai logs each refused call on its own connection; on a database
    # the check will roll back that is noise, not news.
    ai_log.setLevel(logging.CRITICAL)
    try:
        yield refused
    finally:
        socket.socket.connect = real_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = real_connect_ex  # type: ignore[method-assign]
        ai.set_backend(prev)
        ai_log.setLevel(level)


@contextmanager
def unblocker(cur: Any, *, after_s: float = 2.0) -> Iterator[list[str]]:
    """Cancel statements that wait on the check's own transaction.

    Why: every job runs in the check's one transaction, but a few write on a
    connection of their own (trust.retention_purge verifies the audit chain
    in a separate db() block). Such a write can wait on a row an earlier job
    wrote in the check's still-open transaction, while that transaction waits
    on the job: a deadlock Postgres cannot see, because one side is this
    Python thread. The worker never hits it (one transaction per job, each
    committed). A watcher on its own connection cancels any statement blocked
    by the check for more than `after_s`; the job then fails and is reported
    as "blocked", not as a module bug. Under the test harness every db() is
    a savepoint of one connection, so nothing can block and nothing runs."""
    import threading

    from clefs import db

    cancelled: list[str] = []
    if db.in_test_override():
        yield cancelled
        return
    me = cur.connection.get_backend_pid()
    stop = threading.Event()

    def watch() -> None:
        while not stop.wait(0.5):
            try:
                with db.db() as c:
                    c.execute(
                        """SELECT pid, left(regexp_replace(query, '\\s+', ' ', 'g'), 100) AS q FROM pg_stat_activity
                           WHERE %s = ANY(pg_blocking_pids(pid)) AND now() - query_start > make_interval(secs => %s)""",
                        (me, after_s))
                    for r in c.fetchall():
                        c.execute("SELECT pg_cancel_backend(%s)", (r["pid"],))
                        cancelled.append(r["q"])
            except Exception:  # the watcher must never take the check down
                logging.getLogger("clefs.seed.demo_check").exception("unblocker poll failed")

    t = threading.Thread(target=watch, name="demo-check-unblocker", daemon=True)
    t.start()
    try:
        yield cancelled
    finally:
        stop.set()
        t.join(timeout=5)


def _is_offline(exc: BaseException) -> bool:
    """True when the failure traces back to the guard (or to AI being off):
    a vendor that cannot be reached, which the check expects."""
    from clefs import ai

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (Offline, ai.AIDisabled)) or "demo-check is offline" in str(cur):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


# ─── Jobs ───


def _job_rank(job: Any) -> tuple[float, str]:
    brief = JOB_ORDER.index("brief")
    return (JOB_ORDER.index(job.module) if job.module in JOB_ORDER else brief - 0.5, job.name)


def hotel_jobs(cur: Any, slug: str) -> tuple[list[Any], int]:
    """The per-hotel scheduled jobs the worker would run for `slug`, in
    pipeline order, and how many platform-wide jobs were left out (purges and
    syncs across every hotel are not this check's business)."""
    from clefs import jobs
    from clefs.core.hotels import module_enabled
    from clefs.registry import enabled_modules

    jobs.load_all()
    loaded = set(enabled_modules())
    out, platform = [], 0
    for job in jobs.registered_jobs():
        if not job.per_hotel:
            platform += 1
            continue
        if job.module != "core" and job.name not in jobs.MODULE_OFF_EXEMPT:
            if job.module not in loaded or not module_enabled(cur, slug, job.module):
                continue
        out.append(job)
    return sorted(out, key=_job_rank), platform


def run_jobs(cur: Any, slug: str, now: datetime) -> list[JobRun]:
    """Every per-hotel job body once, at `now`, in the caller's transaction.
    Each job runs in a savepoint: one that raises is rolled back alone and
    reported, the rest carry on (the worker isolates them the same way, with
    a transaction per job)."""
    import time as _time

    from clefs import jobs
    from clefs.core.hotels import get_hotel

    hotel = get_hotel(cur, slug)
    todo, _platform = hotel_jobs(cur, slug)
    out: list[JobRun] = []
    # Each job gets its own statement_timeout (as the worker sets it); the
    # caller's is restored afterwards, since SET LOCAL outlives a savepoint.
    cur.execute("SHOW statement_timeout")
    prev_timeout = cur.fetchone()["statement_timeout"]
    with unblocker(cur) as cancelled:
        for job in todo:
            slot = jobs.due_slot(job.schedule, now=now, hotel=hotel) or now
            t0, seen = _time.monotonic(), len(cancelled)
            cur.execute("SAVEPOINT demo_check_job")
            try:
                cur.execute("SET LOCAL statement_timeout = %s", (int(job.timeout_s) * 1000,))
                result = job.fn(jobs.JobCtx(cur=cur, job=job.name, hotel_slug=slug, scheduled_for=slot,
                                            _hotel=hotel))
                cur.execute("RELEASE SAVEPOINT demo_check_job")
                status, detail = "ok", _brief_result(result)
            except Exception as exc:  # a job's failure is the finding, not a crash of the check
                cur.execute("ROLLBACK TO SAVEPOINT demo_check_job")
                cur.execute("RELEASE SAVEPOINT demo_check_job")
                status = "offline" if _is_offline(exc) else "error"
                detail = f"{type(exc).__name__}: {exc}"[:300]
            if len(cancelled) > seen:
                # Its own-connection write waited on the check's transaction
                # (see unblocker): an artefact of the check, not of the job.
                status = "blocked" if status == "error" else status
                detail = f"own-connection write cancelled ({'; '.join(cancelled[seen:])[:160]}) {detail}"
            out.append(JobRun(job.name, job.module, status, int((_time.monotonic() - t0) * 1000), detail))
    cur.execute("SET LOCAL statement_timeout = %s", (prev_timeout,))
    return out


def _brief_result(result: Any) -> str:
    """A job's return value, shortened for the table (counts, not rows)."""
    if not isinstance(result, dict):
        return "" if result is None else str(result)[:80]
    parts = [f"{k}={v}" for k, v in result.items() if isinstance(v, (int, float, str, bool)) and len(str(v)) < 40]
    return ", ".join(parts[:6])

# ─── Plants ───

TITLES = {"core": "Property and stays", "profit_guard": "Profit Guard (OTA leakage, tourism dirham)",
          "service": "Service Control", "amenities": "Amenities", "reviews": "Reviews",
          "guests": "Guests and journeys", "inquiries": "Inquiry Guard", "upsell": "Upsell",
          "demand": "Demand Radar", "visibility": "Visibility", "finance": "Finance", "brief": "Brief"}


@contextmanager
def _savepoint(cur: Any, name: str = "demo_check_eval") -> Iterator[None]:
    """A SQL error inside a check must not abort the whole transaction (and
    with it every later check): roll back to here and let the caller report."""
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except Exception:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        raise
    cur.execute(f"RELEASE SAVEPOINT {name}")


def _surfaced(cur: Any, fn: Any, slug: str, p: dict) -> tuple[bool, str]:
    try:
        with _savepoint(cur):
            return bool(fn(cur, slug, p)), ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"[:160]


def plant_section(cur: Any, slug: str, key: str, plants: list[dict], fn: Any) -> Section:
    """Ops and growth plants: each part's surfaced() says whether the case
    produced what it should (for a DECOY, True means it fired)."""
    s = Section(key, TITLES.get(key, key))
    for p in plants:
        ok, err = _surfaced(cur, fn, slug, p)
        label = f"{p['detector']}:{p['key']}" + (f" ({err})" if err else "")
        if p.get("klass") == "DECOY":
            s.decoys += 1
            # A decoy the check could not evaluate is not a quiet decoy.
            if ok or err:
                s.fired += ok
                s.fired_keys.append(label)
        else:
            s.expected += 1
            s.found += ok
            if not ok:
                s.missing.append(label)
    return s


def money_section(cur: Any, slug: str, plants: list[dict]) -> Section:
    """Profit Guard plants by fingerprint (money.fingerprints_of resolves the
    ones named by pms_res_id), decoys by their decoy fingerprints, then the
    spec's precision and RECOVERABLE bars over every detector finding."""
    from clefs.seed.demo_uae import money

    s = Section("profit_guard", TITLES["profit_guard"])
    cur.execute("SELECT to_regclass('public.pfg_findings') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        s.rows.append(Row("findings table", False, "pfg_findings missing (profit_guard not migrated)"))
        return s
    cur.execute("SELECT fingerprint, class, amount_minor, currency, internal_only FROM pfg_findings "
                "WHERE hotel_slug = %s AND source = 'detector'", (slug,))
    rows = {r["fingerprint"]: dict(r) for r in cur.fetchall()}
    want: set[str] = set()
    for p in plants:
        for fp in p.get("decoy_fingerprints") or []:
            s.decoys += 1
            if fp in rows:
                s.fired += 1
                s.fired_keys.append(f"{p['detector']}:{p['key']} decoy {fp[:12]}")
        if p.get("klass") == "DECOY":
            continue
        fps = money.fingerprints_of(cur, slug, p)
        want.update(fps)
        s.expected += 1
        hit = bool(fps) and all(fp in rows for fp in fps)
        s.found += hit
        if not hit:
            s.missing.append(f"{p['detector']}:{p['key']} ({sum(fp in rows for fp in fps)}/{len(fps)} findings)")
    claims = [fp for fp, r in rows.items() if r["class"] != "STRUCTURAL"]
    precision = sum(fp in want for fp in claims) / len(claims) if claims else 0.0
    recoverable = sum(int(r["amount_minor"] or 0) for r in rows.values()
                      if r["class"] == "RECOVERABLE" and (r["currency"] or "").strip() == money.CCY
                      and not r["internal_only"])
    # Plants are counted per manifest entry; one entry may expect many
    # findings (L1 is 25 lines), so the fingerprint count is shown alongside.
    s.notes.append(f"findings present: {len(want & set(rows))}/{len(want)} planted fingerprints, "
                   f"{len(rows)} detector findings in all")
    s.rows += [
        Row(f"precision >= {MIN_PRECISION}", precision >= MIN_PRECISION, f"{precision:.3f} over {len(claims)} claims"),
        Row("RECOVERABLE >= 3 x AED 1,500", recoverable >= MIN_RECOVERABLE_MINOR, f"AED {money.major(recoverable)}"),
    ]
    return s

# ─── Signals, brief, verified value ───


def signals_by_section(cur: Any, slug: str) -> list[dict]:
    """Signals per brief section: open (open or surfaced, what the owner
    sees) and all-time, plus how many modules feed each section."""
    cur.execute(
        """SELECT COALESCE(section, '-') AS section,
                  count(*) FILTER (WHERE status IN ('open', 'surfaced')) AS open,
                  count(*) AS total, count(DISTINCT module) AS modules
           FROM signals WHERE hotel_slug = %s GROUP BY 1 ORDER BY 1""",
        (slug,),
    )
    return [dict(r) for r in cur.fetchall()]


def brief_top5(cur: Any, slug: str, anchor: date) -> dict:
    """The anchor's daily brief: headline, its five ranked items and the
    Verified results line (02 spec 6.1). {} when no daily brief exists."""
    cur.execute("SELECT to_regclass('public.brf_runs') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return {}
    cur.execute(
        """SELECT id, headline, currency, at_stake_minor, payload FROM brf_runs
           WHERE hotel_slug = %s AND cadence = 'daily' AND brief_date = %s
           ORDER BY (trigger = 'schedule') DESC, id DESC LIMIT 1""",
        (slug, anchor),
    )
    run = cur.fetchone()
    if run is None:
        return {}
    cur.execute(
        """SELECT item_no, section, detector, title, impact_minor, currency, score FROM brf_items
           WHERE run_id = %s AND kind = 'ranked' ORDER BY item_no LIMIT 5""",
        (run["id"],),
    )
    payload = run["payload"] if isinstance(run["payload"], dict) else {}
    verified = payload.get("verified")
    # brief.verified_line stores {text, last_24h_minor, mtd_minor, ...}.
    line = verified.get("text") if isinstance(verified, dict) else verified
    return {"headline": run["headline"], "currency": (run["currency"] or "").strip(),
            "at_stake_minor": int(run["at_stake_minor"] or 0), "items": [dict(r) for r in cur.fetchall()],
            "verified_line": line}


def verified_value(cur: Any, slug: str, start: date, end: date) -> dict | None:
    """Clefs-verified profit over the history window, through finance's api
    (None when finance is off for the hotel)."""
    import importlib

    from clefs.core.hotels import module_enabled
    from clefs.registry import enabled_modules

    if "finance" not in enabled_modules() or not module_enabled(cur, slug, "finance"):
        return None
    api = importlib.import_module("clefs.modules.finance.api")
    with _savepoint(cur):
        return api.verified_profit(cur, slug, start=start, end=end)


def brief_rows(top: dict) -> list[Row]:
    """The brief's acceptance: five ranked items in the hotel's currency with
    money at stake, ordered by score, and the Verified results line."""
    items = top.get("items") or []
    ccys = {(i["currency"] or top.get("currency") or "").strip() for i in items}
    scores = [float(i["score"]) for i in items if i["score"] is not None]
    impact = sum(int(i["impact_minor"] or 0) for i in items)
    line = str(top.get("verified_line") or "")
    return [
        Row("daily brief for the anchor", bool(top), (top.get("headline") or "none")[:90]),
        Row("top 5 ranked", len(items) == 5, f"{len(items)} item(s)"),
        Row("top 5 in AED with money at stake", ccys <= {"AED"} and impact > 0, f"{sorted(ccys)} {impact / 100:,.2f}"),
        Row("ranked by score", scores == sorted(scores, reverse=True), ", ".join(f"{x:.3f}" for x in scores)),
        Row("Verified results line", line.startswith("Verified results"), line[:90]),
    ]

# ─── Run ───


@dataclass
class Report:
    hotel: str
    anchor: date | None
    scale: float
    manifest: str
    jobs: list[JobRun] = field(default_factory=list)
    platform_jobs: int = 0
    refused: list[str] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)  # core first, then SECTIONS order
    signals: list[dict] = field(default_factory=list)
    brief: dict = field(default_factory=dict)
    verified: dict | None = None
    committed: bool = False
    side_tasks: int = 0  # tasks queued on another connection, cancelled after the rollback

    @property
    def job_errors(self) -> list[JobRun]:
        return [j for j in self.jobs if j.status == "error"]

    @property
    def ok(self) -> bool:
        return all(s.ok for s in self.sections) and not self.job_errors

    @property
    def misses(self) -> int:
        """Plants not found + decoys fired + failed checks + failed jobs."""
        return (sum(len(s.missing) + len(s.fired_keys) + sum(r.ok is False for r in s.rows) for s in self.sections)
                + len(self.job_errors))


def anchor_evening(anchor: date) -> datetime:
    """The instant the check's jobs run at: 21:00 Dubai on the anchor, or,
    when the real clock is on the anchor day before that evening (a live
    seed, DemoCtx.as_of), the real clock. WHY: a check run on the live
    demo at 13:42 froze at 21:00 and wrote tomorrow-looking briefs and
    alerts into the presenter's console (QA OWN-08)."""
    from clefs.seed.demo_uae import DemoCtx, live_anchor

    live_day, as_of = live_anchor()
    return DemoCtx(cur=None, slug="", anchor=anchor, as_of=as_of if live_day == anchor else None).now


def _derived_anchor(cur: Any, slug: str) -> date | None:
    cur.execute("SELECT max(date) AS d FROM occupancy_daily WHERE hotel_slug = %s AND source = 'DERIVED'", (slug,))
    row = cur.fetchone()
    return row["d"] if row else None


def core_section(cur: Any, slug: str, scale: float) -> Section:
    """The seed-core property checks (_check.core_checks). Below scale 1 the
    volume checks are shown as skipped: a thinned history is too small for
    the spec's ranges by design (see the module docstring)."""
    from clefs.seed.demo_uae import _check

    s = Section("core", TITLES["core"])
    try:
        with _savepoint(cur):
            results = _check.core_checks(cur, slug)
    except Exception as exc:  # a broken check is a failed check, not a crash
        s.rows.append(Row("core checks", False, f"{type(exc).__name__}: {exc}"[:200]))
        return s
    for name, ok, detail in results:
        if scale < 1 and name.startswith(VOLUME_CHECKS):
            s.rows.append(Row(name, None, f"skipped at scale {scale:g} ({detail})"))
        else:
            s.rows.append(Row(name, ok, detail))
    return s


def module_sections(cur: Any, slug: str, plants: list[dict], anchor: date, scale: float) -> list[Section]:
    """One Section per module in SECTIONS order. A module with no planted case
    at all fails: every module has plants in the spec, so an empty one means
    its seed part did not run (module off, or an old manifest)."""
    from clefs.seed.demo_uae import growth, ops

    by_mod: dict[str, list[dict]] = {}
    for p in plants:
        by_mod.setdefault(plant_module(p), []).append(p)
    if not any(by_mod.get(m) for m in GROWTH):
        # A manifest written before the growth part existed: its plants are
        # constants of the seed, so they can be rebuilt for this anchor.
        for p in growth.expected_plants(anchor, scale=scale):
            by_mod.setdefault(plant_module(p), []).append(p)
    out: list[Section] = []
    for key in SECTIONS:
        mine = by_mod.get(key) or []
        if key == "profit_guard":
            s = money_section(cur, slug, mine)
        else:
            s = plant_section(cur, slug, key, mine, ops.surfaced if key in OPS else growth.surfaced)
        if not mine:
            s.rows.append(Row("planted cases", False, "none in the manifest (its seed part did not run)"))
        out.append(s)
    return out


def run(cur: Any, slug: str, *, manifest_path: str | None = None, anchor: date | None = None,
        scale: float = 1.0, jobs: bool = True) -> Report:
    """The whole check in the caller's transaction (see the module docstring).
    The caller decides whether to keep what the jobs wrote."""
    from clefs.seed.demo_uae import HISTORY_DAYS

    manifest, source = load_manifest(manifest_path, slug)
    anchor = anchor or (date.fromisoformat(manifest["anchor"]) if manifest else None) or _derived_anchor(cur, slug)
    rep = Report(hotel=slug, anchor=anchor, scale=scale, manifest=source)
    cur.execute("SELECT status FROM hotels WHERE slug = %s", (slug,))
    row = cur.fetchone()
    # Jobs run only on a demo hotel: the check is rolled back, but a real
    # hotel's jobs have no business running at a synthetic anchor at all.
    refuse = ("no such hotel" if row is None else None) or (
        None if row["status"] == "demo" else f"not a demo hotel (status={row['status']}); nothing was run")
    if refuse or anchor is None:
        why = refuse or "no manifest and no derived occupancy, pass --anchor"
        rep.sections.append(Section("core", TITLES["core"], rows=[Row("demo hotel and anchor", False, why)]))
        return rep
    now = anchor_evening(anchor)
    prev = clock.now() if clock.is_frozen() else None
    clock.freeze(now)  # jobs read clock.now(); freeze() stores it as UTC
    try:
        if jobs:
            with offline() as refused:
                rep.jobs = run_jobs(cur, slug, now)
            rep.refused = sorted(set(refused))
            rep.platform_jobs = hotel_jobs(cur, slug)[1]
        rep.sections.append(core_section(cur, slug, scale))
        if manifest is None:
            rep.sections[0].rows.append(Row("planted-case manifest", False, source))
        else:
            rep.sections += module_sections(cur, slug, manifest.get("plants") or [], anchor, scale)
            rep.brief = brief_top5(cur, slug, anchor)
            brief = next(s for s in rep.sections if s.key == "brief")
            brief.rows += brief_rows(rep.brief)
        rep.signals = signals_by_section(cur, slug)
        try:
            rep.verified = verified_value(cur, slug, anchor - timedelta(days=HISTORY_DAYS - 1), anchor)
        except Exception as exc:  # shown as unavailable; the brief's line is the check
            rep.verified = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    finally:
        if prev is not None:
            clock.freeze(prev)
        else:
            clock.unfreeze()
    return rep


class _Rollback(Exception):
    """Raised inside db() to roll the check's transaction back on purpose."""


def check_hotel(slug: str, *, commit: bool = False, **kw: Any) -> Report:
    """run() in its own transaction, rolled back unless commit=True. Under
    the test harness db() is a savepoint, so the seeded rows stay."""
    from clefs import db

    with db.db() as cur:
        cur.execute("SELECT COALESCE(max(id), 0) AS n FROM job_queue")
        before = int(cur.fetchone()["n"])
    box: dict[str, Report] = {}
    try:
        with db.db(statement_timeout_ms=0) as cur:
            box["report"] = run(cur, slug, **kw)
            if not commit:
                raise _Rollback
    except _Rollback:
        pass
    rep = box["report"]
    rep.committed = commit
    if not commit:
        rep.side_tasks = _cancel_side_tasks(slug, before)
    return rep


def _cancel_side_tasks(slug: str, before: int) -> int:
    """A few module helpers enqueue on their own connection (enqueue without
    cur), which commits even though the check rolls back. Tasks queued for
    this hotel during the check would then run on the next worker tick, at
    the real clock, off rows that no longer exist (or send what the check
    never meant to send): cancel them. Under the test harness every db() is
    a savepoint of one transaction, so there is nothing to find."""
    from clefs import db, jobs

    with db.db() as cur:
        cur.execute("SELECT id FROM job_queue WHERE hotel_slug = %s AND id > %s AND status = 'queued'",
                    (slug, before))
        return sum(jobs.cancel(cur, job_id=int(r["id"])) for r in cur.fetchall())

# ─── Render ───

MAX_LISTED = 12  # misses listed per module before "... and N more"


def _mark(ok: bool | None) -> str:
    return "[skip]" if ok is None else ("[ok]" if ok else "[FAIL]")


def _aed(minor: int | None) -> str:
    return "-" if minor is None else f"AED {minor / 100:,.2f}"


def render_jobs(rep: Report) -> list[str]:
    if not rep.jobs:
        return ["Jobs: not run (--no-jobs)"]
    by = {k: sum(j.status == k for j in rep.jobs) for k in ("ok", "offline", "blocked", "error")}
    secs = sum(j.ms for j in rep.jobs) / 1000
    out = [f"Jobs: {len(rep.jobs)} per-hotel jobs run once at the anchor evening in {secs:.1f} s "
           f"({by['ok']} ok, {by['offline']} offline, {by['blocked']} blocked, {by['error']} failed); "
           f"{rep.platform_jobs} platform-wide jobs left out"]
    if rep.refused:
        out.append(f"  offline: refused {len(rep.refused)} destination(s): {', '.join(rep.refused[:6])}")
    for j in rep.jobs:
        if j.status != "ok" or j.detail.startswith("own-connection"):
            out.append(f"  [{'FAIL' if j.status == 'error' else j.status}] {j.name}: {j.detail}")
    return out


def render_summary(rep: Report) -> list[str]:
    head = f"  {'Module':<44} {'Plants found':>13} {'Decoys fired':>13}  Result"
    out = ["Summary", head, "  " + "-" * (len(head) - 2)]
    for s in rep.sections:
        plants = f"{s.found}/{s.expected}" if s.expected else "-"
        decoys = f"{s.fired}/{s.decoys}" if s.decoys else "-"
        failed = sum(r.ok is False for r in s.rows)
        verdict = "ok" if s.ok else f"FAIL ({len(s.missing)} missing, {len(s.fired_keys)} fired, {failed} checks)"
        out.append(f"  {s.title:<44} {plants:>13} {decoys:>13}  {verdict}")
    return out


def render_section(s: Section) -> list[str]:
    head = f"== {s.title} =="
    if s.expected or s.decoys:
        head += f"  plants {s.found}/{s.expected}, decoys fired {s.fired}/{s.decoys}"
    out = [head + f"  {'OK' if s.ok else 'FAIL'}"]
    out += [f"  - {n}" for n in s.notes]
    out += [f"  {_mark(r.ok)} {r.name}: {r.detail}" for r in s.rows]
    for tag, keys in (("[MISS]", s.missing), ("[FIRED]", s.fired_keys)):
        out += [f"  {tag} {k}" for k in keys[:MAX_LISTED]]
        if len(keys) > MAX_LISTED:
            out.append(f"  {tag} ... and {len(keys) - MAX_LISTED} more")
    return out


def render_extras(rep: Report) -> list[str]:
    out = ["Signals by section", f"  {'section':<18} {'open':>6} {'total':>7} {'modules':>8}"]
    out += [f"  {r['section']:<18} {r['open']:>6} {r['total']:>7} {r['modules']:>8}" for r in rep.signals]
    if not rep.signals:
        out.append("  (none)")
    top = rep.brief
    out.append(f"Brief top 5 ({rep.anchor}): {top.get('headline') or 'no daily brief'}")
    for i in top.get("items") or []:
        score = f"{float(i['score']):.3f}" if i["score"] is not None else "-"
        out.append(f"  {i['item_no']}. [{i['section']}] {str(i['title'])[:70]}  {_aed(i['impact_minor'])}  "
                   f"score {score}")
    if top.get("verified_line"):
        out.append(f"  {top['verified_line']}")
    v = rep.verified
    if v is None:
        out.append("Verified value: finance is off for this hotel")
    elif "error" in v:
        out.append(f"Verified value: unavailable ({v['error']})")
    else:
        mods = ", ".join(f"{m} {_aed(n)}" for m, n in (v.get("by_module") or {}).items()) or "none yet"
        out.append(f"Verified value {v['start']}..{v['end']}: {_aed(v['verified_minor'])} over "
                   f"{v['actions']} verified action(s) ({mods})")
    return out


def render(rep: Report) -> str:
    lines = [f"demo-check {rep.hotel}  anchor {rep.anchor} 21:00 Asia/Dubai  scale {rep.scale:g}",
             f"manifest: {rep.manifest}", ""]
    lines += render_jobs(rep) + [""] + render_summary(rep) + [""]
    for s in rep.sections:
        lines += render_section(s) + [""]
    lines += render_extras(rep) + [""]
    if rep.side_tasks:
        lines.append(f"cancelled {rep.side_tasks} task(s) the jobs queued outside the check's transaction")
    lines.append(f"{'PASS' if rep.ok else 'FAIL'}: {rep.misses} miss(es) for {rep.hotel}"
                 + ("" if rep.committed else " (rolled back; only writes a job made on its own connection stay)"))
    return "\n".join(lines)
