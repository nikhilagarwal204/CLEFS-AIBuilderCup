"""
AI visibility radar: engines, batches and the step executor (04 spec 5.5).

Engines (the registry holds exactly these three; other vendors are excluded by
decision and a contract test pins it):
- gemini_web_search: ai.responses_raw with the web_search tool (Gemini with
  Google Search grounding). The prompt market's main city is recorded as the
  run locale; grounding sources come back in the normalised body.
- perplexity_sonar: POST /chat/completions, search_context_size pinned to
  'low' (it sets the per-request price), only with PERPLEXITY_API_KEY and
  PERPLEXITY_TERMS_APPROVED=true (Canon 20). No localisation, so it runs
  home-market (UAE) prompts only.
- serpapi_ai_mode: core.serpapi.search('google_ai_mode', ...), the only
  SerpApi path (Canon K3). Third-party data: labelled as such, never as an
  API simulation.

Execution: visibility.ai_radar_weekly plans a batch as 'pending' run rows;
visibility.ai_batch_step runs at most 4 of them in one concurrent round with
a 15 s timeout per call, then re-enqueues itself. A timed-out run is retried
once in a later step. A prompt x engine keeps all of its repeats or all are
'discarded', so a cell's sample is never partial.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from clefs import ai
from clefs.auth.deps import HotelCtx, require_perm
from clefs.core import clock
from clefs.modules.visibility import costs, parsers
from clefs.settings import ai_model_for, get_settings

log = logging.getLogger("clefs.visibility.ai")

GEMINI_TASK = "visibility.gemini"  # model from AI_MODEL_VISIBILITY_GEMINI (ai_model_for)
EXTRACT_TASK = "visibility.hotel_extract"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_CONTEXT = "low"
CALL_TIMEOUT_S = 15.0
STEP_MAX_RUNS = 4
CONCURRENCY = 4  # tests set 1: calls then run inline on the test connection
MAX_ATTEMPTS = 2  # a timeout is retried once
ROUND_MARGIN_S = 5.0  # parse + AI fallback time allowed after the call timeout

LABEL_SIMULATION = "API simulation: consumer app answers vary"
LABEL_THIRD_PARTY = "Google AI Mode via SerpApi (third-party); indicative"
METHOD_NOTE = ("Answers are sampled through vendor APIs, 4 repeats per prompt per week; rates carry a Wilson 95% "
               "interval and are hidden below n=30 or when the interval is wider than +/-15 points. See the "
               "arXiv 2609.18729 audit of API vs consumer answers (ai-visibility.md section 2).")

# ─── Engine registry ───


@dataclass(frozen=True)
class Engine:
    key: str
    label: str
    provider: str  # vis_api_costs.provider (AI Mode is ledgered by core.serpapi)
    localised: bool
    call: Callable[[dict, dict, str | None, str], tuple[dict, int | None]]


def label_for(engine: str) -> str:
    return LABEL_THIRD_PARTY if engine == "serpapi_ai_mode" else LABEL_SIMULATION


# Home-market timezone for user_location; source markets omit it.
MARKET_TZ = {"AE": "Asia/Dubai", "SA": "Asia/Riyadh", "GB": "Europe/London", "IN": "Asia/Kolkata",
             "QA": "Asia/Qatar", "KW": "Asia/Kuwait", "BH": "Asia/Bahrain", "OM": "Asia/Muscat"}


def locale_for(engine: str, prompt: dict) -> dict:
    """What the engine is told about the asker (stored in vis_ai_runs.locale)."""
    market = str(prompt.get("market") or "AE").upper()
    if engine == "gemini_web_search":
        loc = {"type": "approximate", "country": market, "city": prompt.get("city")}
        if MARKET_TZ.get(market):
            loc["timezone"] = MARKET_TZ[market]
        return {"user_location": {k: v for k, v in loc.items() if v}}
    if engine == "perplexity_sonar":
        return {"search_context_size": PERPLEXITY_CONTEXT, "model": get_settings().PERPLEXITY_MODEL}
    return {"location": prompt.get("city"), "gl": market.lower(), "hl": prompt.get("language") or "en",
            "device": "desktop"}


# ─── Engine calls (no cursor: they run in worker threads) ───


def _call_gemini(prompt: dict, locale: dict, hotel_slug: str | None, purpose: str) -> tuple[dict, int | None]:
    tool = {"type": "web_search", "user_location": locale.get("user_location") or {"type": "approximate"}}
    body = ai.responses_raw(GEMINI_TASK, input=prompt["text"], tools=[tool], hotel_slug=hotel_slug,
                            timeout_s=CALL_TIMEOUT_S)
    return body, (body.get("clefs") or {}).get("cost_micros")


def _perplexity_cost(body: dict) -> int:
    """The price Perplexity reports (usage.cost.total_cost, USD), else the
    list price: $5 per 1k requests at 'low' context plus $1 per 1M tokens."""
    usage = body.get("usage") or {}
    total = (usage.get("cost") or {}).get("total_cost") if isinstance(usage.get("cost"), dict) else None
    if isinstance(total, (int, float)):
        return int(round(total * 1_000_000))
    tokens = int(usage.get("prompt_tokens") or 0) + int(usage.get("completion_tokens") or 0)
    return 5_000 + tokens


def _call_perplexity(prompt: dict, locale: dict, hotel_slug: str | None, purpose: str) -> tuple[dict, int | None]:
    from clefs.modules.visibility import http as vhttp

    s = get_settings()
    payload = {"model": s.PERPLEXITY_MODEL, "messages": [{"role": "user", "content": prompt["text"]}],
               "web_search_options": {"search_context_size": PERPLEXITY_CONTEXT}}
    try:
        with vhttp.client() as c:
            resp = c.post(PERPLEXITY_URL, json=payload, timeout=CALL_TIMEOUT_S,
                          headers={"Authorization": f"Bearer {s.PERPLEXITY_API_KEY}"})
    except httpx.TimeoutException as exc:
        raise TimeoutError("perplexity timeout") from exc
    except httpx.HTTPError as exc:
        raise vhttp.VendorError("perplexity", None, type(exc).__name__) from exc
    if resp.status_code >= 400:
        raise vhttp.VendorError("perplexity", resp.status_code, f"HTTP {resp.status_code}")
    body = resp.json()
    return body, _perplexity_cost(body)


def _call_ai_mode(prompt: dict, locale: dict, hotel_slug: str | None, purpose: str) -> tuple[dict, int | None]:
    from datetime import timedelta

    from clefs.core import serpapi

    params = {"q": prompt["text"], "location": locale.get("location"), "gl": locale.get("gl"),
              "hl": locale.get("hl"), "device": "desktop"}
    # max_age=0: each repeat is a fresh answer (a cache hit would copy the
    # first repeat and fake a sample of 4). core.serpapi ledgers the cost.
    body = serpapi.search("google_ai_mode", {k: v for k, v in params.items() if v}, hotel_slug=hotel_slug,
                          purpose=purpose, max_age=timedelta(0))
    return body, serpapi.cost_per_search_micros()


ENGINES: dict[str, Engine] = {
    "gemini_web_search": Engine("gemini_web_search", "Gemini (Google Search grounding)", "gemini", True, _call_gemini),
    "perplexity_sonar": Engine("perplexity_sonar", "Perplexity Sonar (API)", "perplexity", False, _call_perplexity),
    "serpapi_ai_mode": Engine("serpapi_ai_mode", "Google AI Mode (SerpApi)", "serpapi", True, _call_ai_mode),
}


def engine_status(profile: dict | None, *, snapshot: bool = False) -> dict[str, str]:
    """{engine: 'ok' | reason} for the settings checklist ("not connected"
    states, never errors). A snapshot has no profile: every available engine."""
    s = get_settings()
    wanted = set(ENGINES) if profile is None else set(profile.get("engines_enabled") or [])
    out: dict[str, str] = {}
    for key in ENGINES:
        if key not in wanted:
            out[key] = "disabled"
        elif not s.VISIBILITY_AI_ENABLED:
            out[key] = "kill_switch"
        elif key == "gemini_web_search":
            out[key] = "ok" if ai.enabled() else "no_gemini_key"
        elif key == "perplexity_sonar":
            out[key] = ("ok" if costs.perplexity_available()
                        else "no_perplexity_key" if not s.PERPLEXITY_API_KEY else "terms_not_approved")
        else:
            out[key] = ("serpapi_disabled" if profile is not None and not profile.get("serpapi_enabled", True)
                        else "ok" if costs.serpapi_available() else "no_serpapi_key")
    return out


def enabled_engines(profile: dict | None, *, snapshot: bool = False) -> list[str]:
    return [k for k, v in engine_status(profile, snapshot=snapshot).items() if v == "ok"]


# ─── Model versions and baseline epochs ───


def version_key(engine: str, model_id: str | None) -> str:
    """What must stay the same for two weeks to be comparable: the model the
    engine answered with, the model we asked for (Gemini) and the parser."""
    asked = ai_model_for(GEMINI_TASK) if engine == "gemini_web_search" else ""
    return f"{model_id or '-'}|{asked}|parser:{parsers.PARSER_VERSION}"


def current_epoch(cur: Any, hotel_slug: str, engine: str) -> int:
    cur.execute("SELECT COALESCE(MAX(baseline_epoch), 1) AS e FROM vis_engine_versions "
                "WHERE hotel_slug = %s AND engine = %s AND status = 'active'", (hotel_slug, engine))
    return int(cur.fetchone()["e"])


def epoch_for(cur: Any, hotel_slug: str, engine: str, model_id: str | None) -> int:
    """The baseline epoch for an answer. A version never seen before starts a
    new epoch (the first one is 1); trends never cross epochs and the UI marks
    the break ("Model changed 12 Oct; baseline reset")."""
    key = version_key(engine, model_id)
    cur.execute("SELECT baseline_epoch, status FROM vis_engine_versions WHERE hotel_slug = %s AND engine = %s "
                "AND version_key = %s", (hotel_slug, engine, key))
    row = cur.fetchone()
    if row and row["status"] == "active":
        # An accepted manual reset (AI Mode source shift) moves the floor for
        # the unchanged model key too: answers after it start the new epoch.
        cur.execute("SELECT MAX(baseline_epoch) AS e FROM vis_engine_versions WHERE hotel_slug = %s AND engine = %s "
                    "AND status = 'active' AND version_key LIKE 'source_shift:%%'", (hotel_slug, engine))
        floor = (cur.fetchone() or {}).get("e") or 0
        return max(int(row["baseline_epoch"]), int(floor))
    cur.execute("SELECT MAX(baseline_epoch) AS e, COUNT(*) AS n FROM vis_engine_versions "
                "WHERE hotel_slug = %s AND engine = %s AND status = 'active'", (hotel_slug, engine))
    agg = cur.fetchone()
    epoch = int(agg["e"] or 0) + 1
    reason = "first_seen" if not agg["n"] else _change_reason(cur, hotel_slug, engine, key)
    cur.execute(
        """
        INSERT INTO vis_engine_versions (hotel_slug, engine, version_key, baseline_epoch, first_seen_at, reason, status)
        VALUES (%s, %s, %s, %s, %s, %s, 'active')
        ON CONFLICT (hotel_slug, engine, version_key) DO UPDATE SET status = 'active',
            baseline_epoch = EXCLUDED.baseline_epoch, first_seen_at = EXCLUDED.first_seen_at, reason = EXCLUDED.reason
        """,
        (hotel_slug, engine, key, epoch, clock.now(), reason),
    )
    return epoch


def _change_reason(cur: Any, hotel_slug: str, engine: str, key: str) -> str:
    cur.execute("SELECT version_key FROM vis_engine_versions WHERE hotel_slug = %s AND engine = %s "
                "AND status = 'active' ORDER BY baseline_epoch DESC LIMIT 1", (hotel_slug, engine))
    prev = (cur.fetchone() or {}).get("version_key") or ""
    old, new = prev.split("|"), key.split("|")
    if len(old) == 3 and old[2] != new[2]:
        return "parser_changed"
    if len(old) == 3 and old[1] != new[1]:
        return "model_setting_changed"
    return "model_changed"


# ─── Batches ───


def _insert_batch(cur: Any, *, hotel_slug: str | None, kind: str, week: Any = None, subject: dict | None = None,
                  requester_ref: str | None = None, plan: dict | None = None, expires_at: Any = None) -> int:
    from clefs.db import Json

    cur.execute(
        """
        INSERT INTO vis_ai_batches (hotel_slug, kind, week, subject, requester_ref, plan, planned, expires_at,
                                    created_at)
        VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s) RETURNING id
        """,
        (hotel_slug, kind, week, Json(subject) if subject else None, requester_ref, Json(plan or {}), expires_at,
         clock.now()),
    )
    return int(cur.fetchone()["id"])


def _plan_runs(cur: Any, batch_id: int, *, kind: str, hotel_slug: str | None, units: list[tuple[dict, str]],
               repeats: int) -> int:
    """One 'pending' row per repeat. run_at is the planning time until the run
    executes (then it is the answer time)."""
    from clefs.db import Json

    now = clock.now()
    n = 0
    for prompt, engine in units:
        loc = locale_for(engine, prompt)
        for r in range(1, repeats + 1):
            cur.execute(
                """
                INSERT INTO vis_ai_runs (batch_id, batch_kind, hotel_slug, prompt_id, prompt_key, prompt_intent,
                                         prompt_text, market, language, engine, locale, repeat_idx, run_at, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                """,
                (batch_id, kind, hotel_slug, prompt.get("id"), prompt["prompt_key"], prompt["intent"], prompt["text"],
                 str(prompt.get("market") or "AE").upper()[:2], prompt.get("language") or "en", engine, Json(loc), r,
                 now),
            )
            n += 1
    cur.execute("UPDATE vis_ai_batches SET planned = %s WHERE id = %s", (n, batch_id))
    return n


def create_weekly_batch(cur: Any, hotel: Any) -> dict:
    """Plan this week's batch (visibility.ai_radar_weekly, Monday 01:00)."""
    from datetime import timedelta

    from clefs import jobs
    from clefs.core.time import local_date
    from clefs.modules.visibility import prompts, store

    if not get_settings().VISIBILITY_AI_ENABLED:
        return {"skipped": "kill_switch"}
    profile = store.get_profile(cur, hotel.slug)
    engines = enabled_engines(profile)
    if not engines:
        return {"skipped": "not_connected", "engines": engine_status(profile)}
    if "serpapi_ai_mode" in engines and not costs.serpapi_budget_ok(cur, searches=20):
        engines.remove("serpapi_ai_mode")  # core's global SerpApi budget is spent: no run would bill
        if not engines:
            return {"skipped": "budget_exhausted", "engines": engine_status(profile)}
    today = local_date(hotel)
    week = today - timedelta(days=today.weekday())
    cur.execute("SELECT id FROM vis_ai_batches WHERE hotel_slug = %s AND kind = 'weekly' AND week = %s",
                (hotel.slug, week))
    if (row := cur.fetchone()) is not None:
        return {"batch_id": row["id"], "existing": True}
    rows = prompts.active_prompts(cur, hotel.slug)
    if not rows:
        prompts.refresh(cur, hotel, profile)
        rows = prompts.active_prompts(cur, hotel.slug)
    budget = store.budget_state(cur, hotel.slug, profile=profile)
    plan = costs.plan_weekly(rows, engines, allowance_micros=costs.weekly_allowance(budget["remaining_micros"]),
                             home_market=hotel.country or "AE")
    if plan.skipped:
        return {"skipped": plan.skipped, "plan": plan.as_dict()}
    batch_id = _insert_batch(cur, hotel_slug=hotel.slug, kind="weekly", week=week, plan=plan.as_dict())
    n = _plan_runs(cur, batch_id, kind="weekly", hotel_slug=hotel.slug, units=plan.units(), repeats=plan.repeats)
    jobs.enqueue("visibility.ai_batch_step", {"batch_id": batch_id}, hotel_slug=hotel.slug,
                 dedupe_key=f"visibility.ai_batch_step:{batch_id}:0", cur=cur)
    return {"batch_id": batch_id, "planned": n, "plan": plan.as_dict()}


def start_snapshot(cur: Any, *, subject: dict, requester_ref: str) -> int | None:
    """5 prompts x the available engines x 3 runs as a kind='snapshot' batch
    (5.9): 45 runs with Perplexity, 30 without. None when the module is off,
    no engine is available, the daily cap is reached or the global monthly
    snapshot budget cannot cover the estimate."""
    from datetime import timedelta

    from clefs import jobs
    from clefs.modules.visibility import prompts

    if not get_settings().VISIBILITY_AI_ENABLED:
        return None
    engines = [e for e in enabled_engines(None, snapshot=True)
               if e != "serpapi_ai_mode" or costs.serpapi_budget_ok(cur, searches=15)]
    if not engines:
        return None
    specs = prompts.snapshot_prompts(hotel_name=subject["hotel_name"], city=subject["city"],
                                     area=subject.get("area"), star=subject.get("star"))
    units = [({"prompt_key": s.prompt_key, "intent": s.intent, "text": s.text, "market": s.market,
               "language": s.language, "city": s.city}, e) for s in specs for e in engines]
    est = sum(costs.run_estimate_micros(e) * costs.SNAPSHOT_REPEATS for _, e in units)
    if costs.snapshot_refusal(cur, est_micros=est):
        return None
    batch_id = _insert_batch(cur, hotel_slug=None, kind="snapshot", subject=subject,
                             requester_ref=str(requester_ref)[:200],
                             plan={"engines": engines, "repeats": costs.SNAPSHOT_REPEATS, "est_micros": est},
                             expires_at=clock.now() + timedelta(days=30))
    _plan_runs(cur, batch_id, kind="snapshot", hotel_slug=None, units=units, repeats=costs.SNAPSHOT_REPEATS)
    jobs.enqueue("visibility.ai_batch_step", {"batch_id": batch_id},
                 dedupe_key=f"visibility.ai_batch_step:{batch_id}:0", cur=cur)
    return batch_id


# ─── The step executor (visibility.ai_batch_step) ───


@dataclass
class Outcome:
    status: str  # ok | empty | error | timeout
    body: dict | None = None
    cost_micros: int | None = None
    parsed: parsers.Parsed | None = None
    entities: list[parsers.Entity] | None = None
    error: str | None = None


def known_entities(cur: Any, batch: dict) -> tuple[list[parsers.Known], str | None]:
    """The hotels an answer may name, and the hotel's own website host."""
    if batch["kind"] == "snapshot":
        subj = batch.get("subject") or {}
        host = parsers.domain_of(subj.get("website")) or None
        return [parsers.Known("self", [subj.get("hotel_name") or ""], host=host)], host
    from clefs.core.hotels import get_hotel
    from clefs.modules.visibility import store

    hotel = get_hotel(cur, batch["hotel_slug"])
    profile = store.get_profile(cur, hotel.slug)
    host = parsers.domain_of(profile.get("website_url")) or None
    known = [parsers.Known("self", [hotel.name, *(profile.get("brand_aliases") or [])], host=host,
                           place_id=profile.get("place_id"))]
    for c in store.competitors(cur, hotel.slug):
        names = [n for n in [c.get("name"), *(c.get("aliases") or [])] if n]
        if names:
            known.append(parsers.Known(str(c["comp_id"]), names, place_id=c.get("place_id")))
    return known, host


class HotelNames(BaseModel):
    hotels: list[str]


EXTRACT_SYSTEM = ("List the names of the hotels this travel answer recommends or names, in the order they appear. "
                  "Only hotel or resort names, exactly as written; an empty list when it names none.")


def extract_hotel_names(answer: str, hotel_slug: str | None) -> list[str]:
    """Step 3 of parsing: the AI fallback when no alias resolved. store=False,
    short timeout; any failure means "no names" (the run stays valid)."""
    try:
        out = ai.structured(EXTRACT_TASK, system=EXTRACT_SYSTEM, input=answer[:6000], schema=HotelNames,
                            hotel_slug=hotel_slug, store=False, timeout_s=8.0, max_output_tokens=400)
    except Exception:  # noqa: BLE001  (AIDisabled / AIError: no fallback names)
        return []
    return [n.strip() for n in out.hotels if n and n.strip()][:15]


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException, FutureTimeout)):
        return True
    detail = str(getattr(exc, "detail", "") or exc)
    return "timeout" in detail.lower()


def _work(row: dict, known: list[parsers.Known], hotel_slug: str | None, serp_purpose: str) -> Outcome:
    """One vendor call, parsed and resolved. No database cursor here (it runs
    in a worker thread); the ai / serpapi layers keep their own ledgers."""
    eng = ENGINES[row["engine"]]
    prompt = {"text": row["prompt_text"], "market": row["market"], "language": row["language"]}
    try:
        body, cost = eng.call(prompt, row.get("locale") or {}, hotel_slug, serp_purpose)
    except Exception as exc:  # noqa: BLE001  (a vendor failure is a run outcome, never a crashed step)
        if _is_timeout(exc):
            return Outcome("timeout", error="timeout")
        return Outcome("error", error=str(getattr(exc, "detail", "") or type(exc).__name__)[:300])
    parsed = parsers.parse(row["engine"], body)
    if parsed.empty:
        return Outcome("empty", body=body, cost_micros=cost, parsed=parsed, entities=[])
    ents = parsers.resolve(parsed, known)
    if not ents:
        names = extract_hotel_names(parsed.answer_text, hotel_slug)
        if names:
            ents = parsers.resolve(parsed, known, extracted_names=names)
    return Outcome("ok", body=body, cost_micros=cost, parsed=parsed, entities=ents)


def _run_round(rows: list[dict], known: list[parsers.Known], hotel_slug: str | None,
               serp_purpose: str) -> list[Outcome]:
    """At most STEP_MAX_RUNS calls in one concurrent round. A call still
    running at CALL_TIMEOUT_S (+ a parse margin) is a 'timeout' for this step;
    its thread is abandoned, never waited for, so the step fits the tick."""
    if CONCURRENCY <= 1 or len(rows) <= 1:
        return [_work(r, known, hotel_slug, serp_purpose) for r in rows]
    pool = ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(rows)), thread_name_prefix="vis-ai")
    try:
        futures = [pool.submit(_work, r, known, hotel_slug, serp_purpose) for r in rows]
        deadline = time.monotonic() + CALL_TIMEOUT_S + ROUND_MARGIN_S
        out: list[Outcome] = []
        for f in futures:
            try:
                out.append(f.result(timeout=max(0.1, deadline - time.monotonic())))
            except FutureTimeout:
                out.append(Outcome("timeout", error="timeout"))
            except Exception as exc:  # noqa: BLE001
                out.append(Outcome("error", error=type(exc).__name__))
        return out
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _store(cur: Any, batch: dict, row: dict, out: Outcome, known: list[parsers.Known], host: str | None) -> None:
    from clefs.db import Json
    from clefs.modules.visibility import store

    if out.status in ("timeout", "error"):
        cur.execute("UPDATE vis_ai_runs SET status = %s, raw = %s, run_at = %s WHERE id = %s",
                    (out.status, Json({"error": out.error}), clock.now(), row["id"]))
        return
    parsed = out.parsed or parsers.Parsed(answer_text="")
    hotel_slug = batch.get("hotel_slug")
    epoch = epoch_for(cur, hotel_slug, row["engine"], parsed.model_id) if hotel_slug else 1
    official = {host} if host else set()
    cites = [dict(d, cls=parsers.classify_domain(d["domain"], official_hosts=official))
             for d in (c.as_dict() for c in parsed.citations)]
    ents = [e.as_dict() for e in out.entities or []]
    self_ent = next((e for e in ents if e["key"] == "self"), None)
    raw = {k: v for k, v in (out.body or {}).items() if k != "clefs"}
    cur.execute(
        """
        UPDATE vis_ai_runs SET status = %s, run_at = %s, model_id = %s, baseline_epoch = %s, searched = %s,
               answer_text = %s, raw = %s, citations = %s, cited_domains = %s, entities = %s, self_mentioned = %s,
               self_position = %s, self_cited = %s, cost_micros = %s, parser_version = %s
        WHERE id = %s
        """,
        (out.status, clock.now(), parsed.model_id, epoch, parsed.searched, parsed.answer_text, Json(raw),
         Json(cites), sorted({c["domain"] for c in cites if c["domain"]}), Json(ents), self_ent is not None,
         self_ent["position"] if self_ent else None, parsers.self_cited(parsed, host), out.cost_micros,
         parsers.PARSER_VERSION, row["id"]),
    )
    eng = ENGINES[row["engine"]]
    if out.cost_micros and eng.provider != "serpapi":  # SerpApi spend lives in core's serpapi_calls
        store.record_cost(cur, hotel_slug, provider=eng.provider,
                          purpose="snapshot" if batch["kind"] == "snapshot" else "ai_radar",
                          cost_micros=int(out.cost_micros))


def _enforce_integrity(cur: Any, batch_id: int) -> int:
    """All repeats of a prompt x engine, or none: one failed repeat (an error,
    or a second timeout) discards the whole unit, pending repeats included, so
    no money is spent finishing a sample that will not count."""
    cur.execute(
        """
        UPDATE vis_ai_runs r SET status = 'discarded'
        WHERE r.batch_id = %(b)s AND r.status <> 'discarded' AND (r.prompt_key, r.engine) IN (
            SELECT prompt_key, engine FROM vis_ai_runs
            WHERE batch_id = %(b)s AND (status = 'error' OR (status = 'timeout' AND attempts >= %(m)s)))
        """,
        {"b": batch_id, "m": MAX_ATTEMPTS},
    )
    return cur.rowcount


def _tally(cur: Any, batch_id: int) -> dict:
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status IN ('ok', 'empty', 'discarded')) AS done,
               COUNT(*) FILTER (WHERE status IN ('pending', 'timeout') AND attempts < %s) AS left,
               COUNT(*) FILTER (WHERE status IN ('ok', 'empty')) AS good,
               COALESCE(SUM(cost_micros), 0) AS cost, COALESCE(SUM(attempts), 0) AS tries
        FROM vis_ai_runs WHERE batch_id = %s
        """,
        (MAX_ATTEMPTS, batch_id),
    )
    return dict(cur.fetchone())


def get_batch(cur: Any, batch_id: int) -> dict | None:
    cur.execute("SELECT * FROM vis_ai_batches WHERE id = %s", (batch_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def step(cur: Any, batch_id: int) -> dict:
    """One round: pick <= STEP_MAX_RUNS due runs, call them concurrently,
    store, enforce integrity, then re-enqueue or finish the batch."""
    from datetime import timedelta

    from clefs import jobs

    batch = get_batch(cur, batch_id)
    if batch is None or batch["status"] != "running":
        return {"skipped": "not_running"}
    if not get_settings().VISIBILITY_AI_ENABLED:
        return _finish(cur, batch, killed=True)
    cur.execute(
        """
        SELECT * FROM vis_ai_runs WHERE batch_id = %s AND status IN ('pending', 'timeout') AND attempts < %s
        ORDER BY (status = 'timeout'), id LIMIT %s FOR UPDATE SKIP LOCKED
        """,
        (batch_id, MAX_ATTEMPTS, STEP_MAX_RUNS),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not rows:
        return _finish(cur, batch)
    cur.execute("UPDATE vis_ai_runs SET attempts = attempts + 1 WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    known, host = known_entities(cur, batch)
    serp_purpose = costs.SNAPSHOT_SERP_PURPOSE if batch["kind"] == "snapshot" else "visibility.ai_radar"
    outcomes = _run_round(rows, known, batch.get("hotel_slug"), serp_purpose)
    for row, out in zip(rows, outcomes, strict=True):
        row["attempts"] = int(row["attempts"]) + 1
        _store(cur, batch, row, out, known, host)
    discarded = _enforce_integrity(cur, batch_id)
    t = _tally(cur, batch_id)
    cur.execute("UPDATE vis_ai_batches SET done = %s, cost_micros = %s WHERE id = %s", (t["done"], t["cost"], batch_id))
    result = {"batch_id": batch_id, "ran": len(rows), "statuses": [o.status for o in outcomes],
              "discarded": discarded, "left": t["left"]}
    if t["left"] == 0:
        return {**result, **_finish(cur, batch)}
    only_retries = all(o.status == "timeout" for o in outcomes)
    jobs.enqueue("visibility.ai_batch_step", {"batch_id": batch_id}, hotel_slug=batch.get("hotel_slug"),
                 dedupe_key=f"visibility.ai_batch_step:{batch_id}:{t['tries']}", cur=cur,
                 run_at=clock.now() + timedelta(seconds=30) if only_retries else None)
    return result


def _finish(cur: Any, batch: dict, *, killed: bool = False) -> dict:
    from clefs import jobs

    t = _tally(cur, batch["id"])
    if killed:
        cur.execute("UPDATE vis_ai_runs SET status = 'discarded' WHERE batch_id = %s "
                    "AND status IN ('pending', 'timeout')", (batch["id"],))
    status = "ready" if t["good"] > 0 and not killed else "failed"
    cur.execute("UPDATE vis_ai_batches SET status = %s, finished_at = %s, done = %s, cost_micros = %s WHERE id = %s",
                (status, clock.now(), t["done"], t["cost"], batch["id"]))
    if batch["kind"] == "weekly" and status == "ready":
        jobs.enqueue("visibility.ai_weekly_rollup", {"batch_id": batch["id"]}, hotel_slug=batch["hotel_slug"],
                     dedupe_key=f"visibility.ai_weekly_rollup:{batch['id']}", cur=cur)
    return {"finished": status, "good": t["good"]}


# ─── Console routes (/hotels/{slug}/visibility/ai...) ───
# Mounted by module.py next to the Google router. Reads need
# visibility.view; creating a fix or dismissing a fact needs visibility.act;
# accepting a suggested baseline reset needs visibility.manage.

router = APIRouter(prefix="/hotels/{slug}/visibility/ai", tags=["visibility"])
_VIEW = require_perm("visibility.view")
_ACT = require_perm("visibility.act")
_MANAGE = require_perm("visibility.manage")


def _run_view(r: dict) -> dict:
    cites = [{"url": c.get("url"), "title": c.get("title") or c.get("domain"), "domain": c.get("domain"),
              "class": c.get("cls"), "kind": c.get("kind"), "start_index": c.get("start_index"),
              "end_index": c.get("end_index")} for c in (r.get("citations") or []) if c.get("url")]
    return {
        "id": r["id"], "batch_id": r["batch_id"], "engine": r["engine"], "label": label_for(r["engine"]),
        "prompt_key": r["prompt_key"], "prompt": r["prompt_text"], "intent": r["prompt_intent"],
        "market": (r["market"] or "").strip(), "language": r["language"], "model_id": r["model_id"],
        "baseline_epoch": r["baseline_epoch"], "repeat_idx": r["repeat_idx"],
        "run_at": r["run_at"].isoformat() if r.get("run_at") else None, "status": r["status"],
        "searched": r["searched"], "answer_text": r["answer_text"], "citations": cites,
        "entities": r.get("entities") or [], "self_mentioned": r["self_mentioned"],
        "self_position": r["self_position"], "self_cited": r["self_cited"], "locale": r.get("locale") or {},
        "parser_version": r["parser_version"],
    }


@router.get("")
def ai_tab(ctx: HotelCtx = Depends(_VIEW)) -> dict:
    from clefs import db
    from clefs.core.hotels import get_hotel
    from clefs.modules.visibility import stats

    with db.db(readonly=True) as cur:
        return stats.overview(cur, get_hotel(cur, ctx.slug))


@router.get("/runs")
def ai_runs(prompt_key: str | None = Query(None, max_length=80), engine: str | None = Query(None, max_length=40),
            batch_id: int | None = Query(None), limit: int = Query(50, ge=1, le=200),
            ctx: HotelCtx = Depends(_VIEW)) -> dict:
    """Recent answers (newest first) for the evidence Drawer's list."""
    from clefs import db

    where, params = ["hotel_slug = %s", "status IN ('ok', 'empty')"], [ctx.slug]
    for col, val in (("prompt_key", prompt_key), ("engine", engine), ("batch_id", batch_id)):
        if val is not None:
            where.append(f"{col} = %s")
            params.append(val)
    with db.db(readonly=True) as cur:
        cur.execute(f"SELECT * FROM vis_ai_runs WHERE {' AND '.join(where)} ORDER BY run_at DESC, id DESC LIMIT %s",
                    (*params, limit))
        return {"runs": [_run_view(dict(r)) for r in cur.fetchall()]}


@router.get("/runs/{run_id}")
def ai_run(run_id: int, ctx: HotelCtx = Depends(_VIEW)) -> dict:
    from clefs import db
    from clefs.core.errors import not_found

    with db.db(readonly=True) as cur:
        cur.execute("SELECT * FROM vis_ai_runs WHERE id = %s AND hotel_slug = %s", (run_id, ctx.slug))
        row = cur.fetchone()
        if row is None:
            raise not_found("AI run not found", "run_not_found")
        return _run_view(dict(row))


@router.get("/prompts")
def ai_prompts(ctx: HotelCtx = Depends(_VIEW)) -> dict:
    from clefs import db
    from clefs.modules.visibility import prompts

    with db.db(readonly=True) as cur:
        return {"prompts": prompts.active_prompts(cur, ctx.slug)}


@router.get("/facts")
def ai_facts(status: str | None = Query(None, pattern="^(open|recurred|fixing|fixed|dismissed)$"),
             ctx: HotelCtx = Depends(_VIEW)) -> dict:
    from clefs import db
    from clefs.modules.visibility import stats

    with db.db(readonly=True) as cur:
        statuses = (status,) if status else ("open", "recurred", "fixing")
        return {"facts": stats.list_facts(cur, ctx.slug, statuses=statuses), "label": LABEL_SIMULATION}


def _fact(cur: Any, slug: str, fact_id: int) -> dict:
    from clefs.core.errors import not_found

    cur.execute("SELECT * FROM vis_ai_facts WHERE id = %s AND hotel_slug = %s FOR UPDATE", (fact_id, slug))
    row = cur.fetchone()
    if row is None:
        raise not_found("Fact error not found", "fact_not_found")
    return dict(row)


@router.post("/facts/{fact_id}/fix", status_code=201)
def ai_fact_fix(fact_id: int, ctx: HotelCtx = Depends(_ACT)) -> dict:
    """"Create fix": proposes the action the cited domain calls for (OTA
    listing, website, Google lodging record, or a KB answer). The autonomy
    dial decides its level; the fact moves to 'fixing'."""
    from clefs import db
    from clefs.audit import audit
    from clefs.core import actions
    from clefs.core.errors import conflict
    from clefs.modules.visibility import signals as vsig

    with db.db() as cur:
        f = _fact(cur, ctx.slug, fact_id)
        if f["status"] in ("fixed", "dismissed"):
            raise conflict(f"This fact error is {f['status']}", "fact_closed")
        kind, params = vsig.action_for(cur, ctx.slug, f)
        act = actions.create_action(cur, ctx.slug, type=kind, title=vsig.fact_title(f), params=params,
                                    owner_role="gm", actor=ctx, via="console",
                                    dedupe_key=f"visibility.ai_fact:{f['fingerprint']}")
        cur.execute("UPDATE vis_ai_facts SET status = 'fixing', action_id = %s WHERE id = %s", (act.get("id"), fact_id))
        audit(cur, ctx, "visibility.ai_fact.fix", "vis_ai_facts", fact_id,
              after={"action_id": act.get("id"), "type": kind})
        return {"fact_id": fact_id, "action": act}


@router.post("/facts/{fact_id}/dismiss")
def ai_fact_dismiss(fact_id: int, ctx: HotelCtx = Depends(_ACT)) -> dict:
    """Not an error (the owner knows better): stays dismissed if seen again."""
    from clefs import db
    from clefs.audit import audit
    from clefs.core import signals as core_signals

    with db.db() as cur:
        f = _fact(cur, ctx.slug, fact_id)
        cur.execute("UPDATE vis_ai_facts SET status = 'dismissed' WHERE id = %s", (fact_id,))
        core_signals.resolve_signal(cur, ctx.slug, f"vis.ai_fact_error:{f['fingerprint']}", resolution="dismissed")
        audit(cur, ctx, "visibility.ai_fact.dismiss", "vis_ai_facts", fact_id, before={"status": f["status"]})
        return {"fact_id": fact_id, "status": "dismissed"}


class BaselineDecision(BaseModel):
    version_key: str
    accept: bool = True


@router.post("/engines/{engine}/baseline")
def ai_baseline(engine: str, body: BaselineDecision, ctx: HotelCtx = Depends(_MANAGE)) -> dict:
    """Accept (new epoch from now) or dismiss a suggested baseline reset (an
    AI Mode source shift)."""
    from clefs import db
    from clefs.audit import audit
    from clefs.core.errors import not_found

    with db.db() as cur:
        cur.execute("SELECT * FROM vis_engine_versions WHERE hotel_slug = %s AND engine = %s AND version_key = %s "
                    "AND status = 'suggested'", (ctx.slug, engine, body.version_key))
        if cur.fetchone() is None:
            raise not_found("No suggested reset with that key", "suggestion_not_found")
        if body.accept:
            cur.execute(
                """
                UPDATE vis_engine_versions SET status = 'active', first_seen_at = %s,
                       baseline_epoch = (SELECT COALESCE(MAX(baseline_epoch), 0) + 1 FROM vis_engine_versions
                                         WHERE hotel_slug = %s AND engine = %s AND status = 'active')
                WHERE hotel_slug = %s AND engine = %s AND version_key = %s
                """,
                (clock.now(), ctx.slug, engine, ctx.slug, engine, body.version_key),
            )
        else:
            cur.execute("UPDATE vis_engine_versions SET status = 'dismissed' WHERE hotel_slug = %s AND engine = %s "
                        "AND version_key = %s", (ctx.slug, engine, body.version_key))
        audit(cur, ctx, "visibility.ai_baseline." + ("accept" if body.accept else "dismiss"), "vis_engine_versions",
              f"{engine}:{body.version_key}")
        return {"engine": engine, "version_key": body.version_key, "status": "active" if body.accept else "dismissed",
                "epoch": current_epoch(cur, ctx.slug, engine)}
