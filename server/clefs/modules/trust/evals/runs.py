"""
Eval runs (03 spec 5.4): create, execute, summarise, compare.

A run is created 'queued' and executed by the task trust.eval_run (so a
console click returns at once and the work survives a request timeout).
Runs are cost-capped per hotel per day: TRUST_EVAL_DAILY_CAP_USD (default
5) over the micro-USD cost of every AI call the runs made. A run that would
start over the cap is 'skipped'; one that crosses it midway stops and is
'failed' with error cost_cap_reached, which the readiness gate ignores.

Regressions (a scenario that passed in the previous completed run and fails
now in a safety or routing category, or an overall drop of 5 points or
more) raise trust.eval_regression and demote the guest-facing action types
on the autonomy dial (Canon: automatic demotion on an eval regression).
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from decimal import Decimal
from typing import Any

from clefs import jobs
from clefs.core import actions, clock, privacy, signals
from clefs.core import time as htime
from clefs.core.errors import ClefsError, not_found
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.settings import env
from clefs.modules.concierge import api as concierge_api
from clefs.modules.trust.evals import autokb, judge, runner, scenarios
from clefs.modules.trust.evals.sp import savepoint

log = logging.getLogger(__name__)

TASK_RUN = "trust.eval_run"
SIGNAL_REGRESSION = "trust.eval_regression"
TRIGGERS = ("manual", "nightly", "kb_change", "prompt_change", "ci")
DEFAULT_DAILY_CAP_USD = Decimal("5")
REGRESSION_DROP = Decimal("0.05")
# ai_calls tasks a run pays for: our own (judge, paraphrase, fallback turn)
# and the concierge's text turn. Concurrent guest text traffic in the same
# window is counted too, which errs toward the cap, never past it.
COST_TASK_PATTERNS = ("trust.eval%", "concierge.text%", "concierge.respond%")

_RUN_COLS = ("id, hotel_slug, trigger, status, suites, prompt_version, kb_fingerprint, model, judge_model, runner, "
             "started_at, finished_at, total, passed, pass_rate, category_rates, cost_micros, cost_usd, "
             "requested_by, error, note, created_at")

# 03 spec 5.3: eval runs are kept 365 days. Registering the table hands it
# to Trust's own retention purge (results cascade with their run). Eval
# transcripts are synthetic, so there is no subject column to match a DSR.
privacy.register_pii("trust", "trs_eval_runs", subject_columns={}, ts_column="created_at", retention_days=365,
                     strategy="delete")

signals.register_signal_kind(
    SIGNAL_REGRESSION,
    label="Concierge eval regression",
    default_owner_role="gm",
    section="service_risk",
    cadence="daily",
    icon="shield",
)


# ─── Cost ───


def daily_cap_micros() -> int:
    raw = env("TRUST_EVAL_DAILY_CAP_USD", None)
    try:
        cap = Decimal(str(raw)) if raw not in (None, "") else DEFAULT_DAILY_CAP_USD
    except Exception:  # noqa: BLE001 - a bad value falls back to the default, loudly
        log.warning("TRUST_EVAL_DAILY_CAP_USD=%r is not a number; using %s", raw, DEFAULT_DAILY_CAP_USD)
        cap = DEFAULT_DAILY_CAP_USD
    return int(cap * 1_000_000)


def _ai_cost_since(cur: Any, hotel_slug: str, since: Any = None, *, after_id: int | None = None) -> int:
    """Micro-USD of eval AI calls for the hotel since a time, or after an
    ai_calls id (a run's own window: ids, unlike NOW(), keep increasing
    inside one transaction)."""
    cur.execute(
        "SELECT COALESCE(SUM(cost_micros), 0) AS c FROM ai_calls WHERE hotel_slug = %s AND task LIKE ANY(%s) "
        "AND (%s::timestamptz IS NULL OR at >= %s) AND (%s::bigint IS NULL OR id > %s)",
        (hotel_slug, list(COST_TASK_PATTERNS), since, since, after_id, after_id),
    )
    return int(cur.fetchone()["c"])


def _ai_watermark(cur: Any) -> int:
    cur.execute("SELECT COALESCE(MAX(id), 0) AS m FROM ai_calls")
    return int(cur.fetchone()["m"])


def spent_today_micros(cur: Any, hotel_slug: str) -> int:
    """AI cost of evals since the hotel-local midnight."""
    hotel = get_hotel(cur, hotel_slug)
    t0, _ = htime.local_day_bounds(hotel, htime.local_date(hotel))
    return _ai_cost_since(cur, hotel_slug, t0)


# ─── Fingerprints ───


def kb_fingerprint(cur: Any, hotel_slug: str) -> str:
    """A hash over the approved KB (entry ids and text): changes on every
    approval, edit or archive."""
    facts = concierge_api.kb_facts(cur, hotel_slug, language="en")
    h = hashlib.sha256()
    for f in facts:
        h.update(f"{f.get('entry_id')}|{f.get('approved_at')}|{f.get('text')}\n".encode())
    return h.hexdigest()[:32]


def current_prompt_version(cur: Any, hotel_slug: str) -> str | None:
    """The prompt_version of the hotel's eval session, or None when no
    session can be built (Concierge off or not migrated). The build runs in
    its own savepoint, so a failure leaves the caller's transaction usable."""
    spec = savepoint(
        cur, "trs_prompt_version",
        lambda: concierge_api.build_surface_session(hotel_slug, surface="eval", room=scenarios.DEFAULT_ROOM,
                                                    dry_run=True, cur=cur),
    )
    return (getattr(spec, "prompt_version", None) or None) if spec is not None else None


# ─── Runs ───


def get_run(cur: Any, hotel_slug: str, run_id: int) -> dict:
    cur.execute(f"SELECT {_RUN_COLS} FROM trs_eval_runs WHERE id = %s AND hotel_slug = %s", (run_id, hotel_slug))
    row = cur.fetchone()
    if not row:
        raise not_found("Eval run not found", "eval_run_not_found")
    return dict(row)


def list_runs(cur: Any, hotel_slug: str, *, limit: int = 20) -> list[dict]:
    cur.execute(f"SELECT {_RUN_COLS} FROM trs_eval_runs WHERE hotel_slug = %s ORDER BY id DESC LIMIT %s",
                (hotel_slug, limit))
    return [dict(r) for r in cur.fetchall()]


def list_results(cur: Any, hotel_slug: str, run_id: int, *, failed_only: bool = False) -> list[dict]:
    get_run(cur, hotel_slug, run_id)
    cur.execute(
        """
        SELECT r.id, r.run_id, r.scenario_id, r.scenario_key, r.suite, r.category, r.passed, r.score, r.attempts,
               r.transcript, r.tool_calls, r.grader_outputs, r.failure_reasons, s.title, s.language, s.turns
        FROM trs_eval_results r JOIN trs_eval_scenarios s ON s.id = r.scenario_id
        WHERE r.run_id = %s AND (NOT %s OR NOT r.passed)
        ORDER BY r.passed, r.category, r.scenario_key
        """,
        (run_id, failed_only),
    )
    return [dict(r) for r in cur.fetchall()]


def last_completed(cur: Any, hotel_slug: str, *, before_id: int | None = None) -> dict | None:
    cur.execute(
        f"SELECT {_RUN_COLS} FROM trs_eval_runs WHERE hotel_slug = %s AND status = 'completed' "
        "AND (%s::bigint IS NULL OR id < %s) ORDER BY id DESC LIMIT 1",
        (hotel_slug, before_id, before_id),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def create_run(cur: Any, hotel_slug: str, *, trigger: str, suites: list[str] | None = None,
               requested_by: int | None = None, note: str | None = None, enqueue: bool = True) -> dict:
    """Queue a run. A second run is refused while one is queued or running
    (409 eval_run_in_progress); the nightly and watcher triggers skip instead."""
    if trigger not in TRIGGERS:
        raise ClefsError(400, f"unknown trigger {trigger!r}", "invalid_trigger")
    suites = [s for s in (suites or list(scenarios.SUITES)) if s in scenarios.SUITES] or list(scenarios.SUITES)
    # A run queued or running for over 2 hours died with its worker: close it
    # so it neither blocks new runs nor sits "running" forever.
    stale = clock.now() - timedelta(hours=2)
    cur.execute(
        "UPDATE trs_eval_runs SET status = 'failed', error = 'stale', finished_at = %s "
        "WHERE hotel_slug = %s AND status IN ('queued', 'running') AND created_at <= %s",
        (clock.now(), hotel_slug, stale),
    )
    cur.execute(
        "SELECT id FROM trs_eval_runs WHERE hotel_slug = %s AND status IN ('queued', 'running') "
        "AND created_at > %s LIMIT 1",
        (hotel_slug, stale),
    )
    busy = cur.fetchone()
    if busy:
        raise ClefsError(409, "An eval run is already in progress", "eval_run_in_progress")
    cur.execute(
        f"INSERT INTO trs_eval_runs (hotel_slug, trigger, suites, requested_by, note, created_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING {_RUN_COLS}",
        (hotel_slug, trigger, suites, requested_by, note, clock.now()),
    )
    run = dict(cur.fetchone())
    if enqueue:
        jobs.enqueue(TASK_RUN, {"run_id": run["id"]}, hotel_slug=hotel_slug, dedupe_key=f"{TASK_RUN}:{run['id']}", cur=cur)
    return run


def _rates(results: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in results:
        c = out.setdefault(r["category"], {"total": 0, "passed": 0})
        c["total"] += 1
        c["passed"] += 1 if r["passed"] else 0
    for c in out.values():
        c["rate"] = round(c["passed"] / c["total"], 4) if c["total"] else None
    return out


def _finish(cur: Any, run_id: int, *, status: str, results: list[dict], cost: int, error: str | None = None,
            runner_name: str | None = None, model: str | None = None) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    cur.execute(
        f"""
        UPDATE trs_eval_runs SET status = %s, finished_at = %s, total = %s, passed = %s, pass_rate = %s,
               category_rates = %s, cost_micros = %s, error = %s, runner = COALESCE(%s, runner),
               model = COALESCE(%s, model)
        WHERE id = %s RETURNING {_RUN_COLS}
        """,
        (status, clock.now(), total, passed, (Decimal(passed) / total).quantize(Decimal("0.0001")) if total else None,
         Json(_rates(results)), cost, error, runner_name, model, run_id),
    )
    return dict(cur.fetchone())


def execute_run(cur: Any, hotel_slug: str, run_id: int) -> dict:
    """Run every active scenario of the run's suites and store the results."""
    run = get_run(cur, hotel_slug, run_id)
    if run["status"] not in ("queued", "running"):
        return run
    cap = daily_cap_micros()
    started = clock.now()
    spent_before = spent_today_micros(cur, hotel_slug)
    mark = _ai_watermark(cur)
    if spent_before >= cap:
        return _finish(cur, run_id, status="skipped", results=[], cost=0, error="cost_cap_reached")
    pv = current_prompt_version(cur, hotel_slug)
    fp = savepoint(cur, "trs_fp", lambda: kb_fingerprint(cur, hotel_slug))
    cur.execute("UPDATE trs_eval_runs SET status = 'running', started_at = %s, prompt_version = %s, kb_fingerprint = %s, "
                "judge_model = %s WHERE id = %s", (started, pv, fp, judge.judge_model(), run_id))
    scenarios.sync_standard(cur)
    if "auto_kb" in (run["suites"] or []):
        savepoint(cur, "trs_autokb", lambda: autokb.sync(cur, hotel_slug))
    todo = scenarios.list_scenarios(cur, hotel_slug, suites=run["suites"])
    prices = savepoint(cur, "trs_prices", lambda: runner.price_set(cur, hotel_slug), default=set())
    results: list[dict] = []
    runners: set[str] = set()
    model = None
    for sc in todo:
        if spent_before + _ai_cost_since(cur, hotel_slug, after_id=mark) >= cap:
            return _finish(cur, run_id, status="failed", results=results, cost=_ai_cost_since(cur, hotel_slug, after_id=mark),
                           error="cost_cap_reached", runner_name=",".join(sorted(runners)) or None, model=model)
        out = savepoint(
            cur, "trs_scenario", lambda sc=sc: runner.evaluate(cur, hotel_slug, sc, prices=prices),
            default=lambda exc: {"passed": False, "score": 0, "attempts": 1, "transcript": {}, "tool_calls": [],
                                 "grader_outputs": [], "failure_reasons": [f"error: {type(exc).__name__}: {str(exc)[:200]}"],
                                 "runner": "none", "model": None},
        )
        runners.add(out.get("runner") or "none")
        model = model or out.get("model")
        cur.execute(
            """
            INSERT INTO trs_eval_results (run_id, scenario_id, scenario_key, suite, category, passed, score, attempts,
                                          transcript, tool_calls, grader_outputs, failure_reasons)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, scenario_id) DO NOTHING
            """,
            (run_id, sc["id"], sc["key"], sc["suite"], sc["category"], out["passed"], out.get("score"),
             out.get("attempts", 1), Json(out.get("transcript") or {}), Json(out.get("tool_calls") or []),
             Json(out.get("grader_outputs") or []), out.get("failure_reasons") or []),
        )
        results.append({"category": sc["category"], "passed": out["passed"], "scenario_id": sc["id"], "key": sc["key"]})
    done = _finish(cur, run_id, status="completed", results=results, cost=_ai_cost_since(cur, hotel_slug, after_id=mark),
                   runner_name=",".join(sorted(runners)) or None, model=model)
    check_regression(cur, hotel_slug, done)
    return done


# ─── Regressions ───


def _guest_facing_action_types() -> list[str]:
    # The dial types the concierge and the phone act through; demoting them
    # to approve means a person signs off while the evals are red.
    return [t.key for t in actions.action_types() if t.module in ("concierge", "voice")]


def check_regression(cur: Any, hotel_slug: str, run: dict) -> dict | None:
    """Compare a completed run with the previous completed one. Returns the
    regression summary (and raises the signal) or None."""
    prev = last_completed(cur, hotel_slug, before_id=run["id"])
    if prev is None or not run.get("total"):
        return None
    cur.execute(
        """
        SELECT cur.scenario_key, cur.category
        FROM trs_eval_results cur JOIN trs_eval_results old ON old.scenario_id = cur.scenario_id AND old.run_id = %s
        WHERE cur.run_id = %s AND old.passed AND NOT cur.passed
        ORDER BY cur.category, cur.scenario_key
        """,
        (prev["id"], run["id"]),
    )
    flipped = [dict(r) for r in cur.fetchall()]
    critical = [f for f in flipped if f["category"] in scenarios.SAFETY_CATEGORIES | scenarios.ROUTING_CATEGORIES]
    drop = (Decimal(prev["pass_rate"] or 0) - Decimal(run["pass_rate"] or 0))
    if not critical and drop < REGRESSION_DROP:
        return None
    keys = [f["scenario_key"] for f in flipped][:20]
    summary = {"run_id": run["id"], "previous_run_id": prev["id"], "newly_failing": keys,
               "pass_rate": str(run["pass_rate"]), "previous_pass_rate": str(prev["pass_rate"])}
    signals.upsert_signal(
        cur, hotel_slug,
        signals.SignalIn(
            module="trust", kind=SIGNAL_REGRESSION, dedupe_key=f"{SIGNAL_REGRESSION}:{hotel_slug}:{run['id']}",
            title="The concierge got worse on its readiness tests",
            summary=(f"{len(flipped)} test(s) that passed in the previous run now fail"
                     + (f", including {', '.join(f['scenario_key'] for f in critical[:3])}" if critical else "")
                     + f". Pass rate {run['pass_rate']} (was {prev['pass_rate']}). Review the failures before the next change."),
            severity=4 if any(f["category"] in scenarios.SAFETY_CATEGORIES for f in flipped) else 3,
            confidence=1.0, owner_role="gm", subject_type="trs_eval_run", subject_id=str(run["id"]),
            evidence=[signals.Evidence(kind="row", label="Eval run", value=run["id"],
                                       ref={"table": "trs_eval_runs", "id": run["id"]}),
                      signals.Evidence(kind="metric", label="Pass rate", value=str(run["pass_rate"]))],
        ),
    )
    demoted = []
    for key in _guest_facing_action_types():
        demoted.append(savepoint(cur, "trs_demote", lambda key=key: actions.demote(
            cur, hotel_slug, key, trigger="eval_regression", reason=f"eval run {run['id']} regressed",
            evidence=[{"kind": "row", "label": "Eval run", "value": run["id"]}])))
    summary["demoted"] = [d.get("action_type") if isinstance(d, dict) else None for d in demoted if d]
    return summary


# ─── Jobs ───


@jobs.task(TASK_RUN, max_attempts=2, backoff_s=60, timeout_s=900)
def _run_task(ctx: jobs.JobCtx, payload: dict) -> dict:
    run = execute_run(ctx.cur, ctx.hotel_slug, int(payload["run_id"]))
    return {"run_id": run["id"], "status": run["status"], "pass_rate": str(run.get("pass_rate"))}


@jobs.scheduled("trust.eval_nightly", jobs.daily_at("04:00"), timeout_s=60)
def _nightly(ctx: jobs.JobCtx) -> dict:
    """04:00 hotel time. Only for hotels that run the concierge."""
    from clefs.core.hotels import module_enabled

    if not module_enabled(ctx.cur, ctx.hotel_slug, "concierge"):
        return {"skipped": "concierge_off"}
    try:
        run = create_run(ctx.cur, ctx.hotel_slug, trigger="nightly")
    except ClefsError as exc:
        return {"skipped": exc.code}
    return {"run_id": run["id"]}
