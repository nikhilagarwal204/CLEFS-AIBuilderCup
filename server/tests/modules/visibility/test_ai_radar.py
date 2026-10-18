"""AI radar: engines, prompts, batches, the step executor, epochs (04 spec 5.5, 5.10, 5.12)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

NOW = datetime(2026, 9, 28, 1, 30, tzinfo=ZoneInfo("Asia/Dubai"))  # Monday
MODULE_DIR = Path(__file__).resolve().parents[3] / "clefs" / "modules" / "visibility"


def gemini_body(names: list[str], *, model: str = "gemini-2.5-flash", cite: str | None = None) -> dict:
    text = "Top picks:\n" + "".join(f"{i}. **{n}** - a good choice.\n" for i, n in enumerate(names, 1))
    ann = [{"type": "url_citation", "url": cite, "title": "src", "start_index": 0, "end_index": 5}] if cite else []
    return {"model": model, "output": [
        {"type": "web_search_call", "status": "completed", "action": {"type": "search", "sources": []}},
        {"type": "message", "content": [{"type": "output_text", "text": text, "annotations": ann}]}]}


def ai_mode_body(names: list[str]) -> dict:
    return {"text_blocks": [{"type": "list", "list": [{"title": n, "snippet": "Nice.", "reference_indexes": [0]}
                                                       for n in names]}],
            "references": [{"index": 0, "title": "Booking", "link": "https://www.booking.com/x"}]}


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def radar(env, fake_ai, monkeypatch, cur, hotel_uae, clock):
    """Gemini fake + SerpApi fake (AI Mode answers) + Perplexity mock, inline calls."""
    from clefs.core import serpapi
    from clefs.modules.visibility import ai_radar, http as vhttp, store

    clock.freeze(NOW)
    env(SERPAPI_KEY="k", SERPAPI_MONTHLY_BUDGET_USD=None, PERPLEXITY_API_KEY=None, PERPLEXITY_TERMS_APPROVED=None,
        VISIBILITY_AI_ENABLED="true", AI_MODEL_VISIBILITY_GEMINI=None)
    monkeypatch.setattr(ai_radar, "CONCURRENCY", 1)
    serp = serpapi.use_fake(default=ai_mode_body(["Palm Vista Hotel", "Test Hotel Dubai"]))
    pplx: list[dict] = []

    def pplx_handler(request: httpx.Request) -> httpx.Response:
        pplx.append({"url": str(request.url), "body": request.read().decode()})
        return httpx.Response(200, json={"model": "sonar", "choices": [{"message": {
            "content": "- **Test Hotel Dubai**: fine [1]."}}], "search_results": [
            {"title": "t", "url": "https://www.expedia.com/x"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20}})

    vhttp.set_transport(httpx.MockTransport(pplx_handler))
    store.update_profile(cur, hotel_uae.slug, {"area": "Downtown", "landmarks": ["Dubai Mall"],
                                               "website_url": "https://testhoteldubai.example",
                                               "monthly_budget_minor": 100_000}, user_id=None)
    fake_ai.push(*[lambda req: gemini_body(["Test Hotel Dubai", "Palm Vista Hotel"]) if req.kind == "raw"
                   else {"hotels": [], "facts": []} for _ in range(400)])
    yield type("R", (), {"ai": fake_ai, "serp": serp, "pplx": pplx, "hotel": hotel_uae, "cur": cur})
    serpapi.set_transport(None)
    vhttp.set_transport(None)


# ─── Engines: exactly three, no scraping ───


def test_engines_are_exactly_three_and_nothing_scrapes():
    from clefs.modules.visibility import ai_radar

    assert set(ai_radar.ENGINES) == {"gemini_web_search", "perplexity_sonar", "serpapi_ai_mode"}
    banned = re.compile(r"perplexity\.ai/search|playwright|selenium", re.I)
    files = [p for p in MODULE_DIR.rglob("*") if p.suffix in (".py", ".sql", ".md", ".json")]
    assert len(files) > 20
    hits = [f"{p.name}:{m.group(0)}" for p in files for m in banned.finditer(p.read_text())]
    assert hits == []


def test_engine_status_not_connected_without_keys(env, cur, hotel_uae):
    from clefs import ai
    from clefs.modules.visibility import ai_radar, store

    env(SERPAPI_KEY=None, PERPLEXITY_API_KEY="p", PERPLEXITY_TERMS_APPROVED="false", GEMINI_API_KEY=None)
    ai.reset_backend()
    profile = store.get_profile(cur, hotel_uae.slug)
    st = ai_radar.engine_status({**profile, "engines_enabled": list(ai_radar.ENGINES)})
    assert st["serpapi_ai_mode"] == "no_serpapi_key"
    assert st["perplexity_sonar"] == "terms_not_approved"
    out = ai_radar.create_weekly_batch(cur, hotel_uae) if st["gemini_web_search"] != "ok" else {"skipped": "x"}
    assert "skipped" in out


# ─── Prompts ───


def test_weekly_prompts_are_24_and_version_on_input_change(radar):
    from clefs.modules.visibility import prompts, store

    cur, hotel = radar.cur, radar.hotel
    store.update_profile(cur, hotel.slug, {"ai_markets": [{"market": "GB"}, {"market": "SA", "city": "Jeddah"}]},
                         user_id=None)
    first = prompts.refresh(cur, hotel)
    assert first == {"prompts": 24, "created": 24, "kept": 0, "retired": 0}
    rows = prompts.active_prompts(cur, hotel.slug)
    intents = [r["intent"] for r in rows]
    assert intents.count("fact_check") == 4 and intents.count("brand") == 1
    assert sum(1 for r in rows if r["language"] == "ar") == 5
    assert {r["city"] for r in rows if r["market"].strip() == "SA"} == {"Jeddah"}
    assert prompts.refresh(cur, hotel)["kept"] == 24
    store.update_profile(cur, hotel.slug, {"area": "Business Bay"}, user_id=None)
    again = prompts.refresh(cur, hotel)
    assert again["created"] > 0 and again["kept"] < 24
    cur.execute("SELECT version, active FROM vis_ai_prompts WHERE hotel_slug = %s AND prompt_key = 'ae.en.generic.1' "
                "ORDER BY version", (hotel.slug,))
    assert [(r["version"], r["active"]) for r in cur.fetchall()] == [(1, False), (2, True)]


# ─── Batches and the step executor ───


def _statuses(cur, batch_id):
    cur.execute("SELECT status, COUNT(*) AS n FROM vis_ai_runs WHERE batch_id = %s GROUP BY status", (batch_id,))
    return {r["status"]: r["n"] for r in cur.fetchall()}


def test_weekly_batch_plans_all_repeats(radar):
    from clefs.modules.visibility import ai_radar

    out = ai_radar.create_weekly_batch(radar.cur, radar.hotel)
    # 20 home-market prompts (no source markets without reservations) x 2 engines x 4 repeats
    assert out["planned"] == 160 and out["plan"]["steps"] == []
    assert ai_radar.create_weekly_batch(radar.cur, radar.hotel)["existing"] is True


def test_serpapi_disabled_drops_ai_mode(radar):
    from clefs.modules.visibility import ai_radar, store

    store.update_profile(radar.cur, radar.hotel.slug, {"serpapi_enabled": False}, user_id=None)
    out = ai_radar.create_weekly_batch(radar.cur, radar.hotel)
    assert out["plan"]["engines"] == ["gemini_web_search"] and out["planned"] == 80


def test_perplexity_needs_the_legal_gate_and_runs_home_market_only(radar, env):
    from clefs.modules.visibility import ai_radar, store

    all3 = ["gemini_web_search", "perplexity_sonar", "serpapi_ai_mode"]
    store.update_profile(radar.cur, radar.hotel.slug, {"engines_enabled": all3}, user_id=None)
    assert "perplexity_sonar" not in ai_radar.enabled_engines(store.get_profile(radar.cur, radar.hotel.slug))
    env(PERPLEXITY_API_KEY="pk", PERPLEXITY_TERMS_APPROVED="true")
    assert "perplexity_sonar" in ai_radar.enabled_engines(store.get_profile(radar.cur, radar.hotel.slug))


def test_step_runs_at_most_4_with_15s_timeout(radar):
    from clefs.modules.visibility import ai_radar

    batch = ai_radar.create_weekly_batch(radar.cur, radar.hotel)["batch_id"]
    out = ai_radar.step(radar.cur, batch)
    assert out["ran"] == 4 and out["statuses"] == ["ok"] * 4
    raw_calls = [c for c in radar.ai.calls if c.kind == "raw"]
    assert len(raw_calls) == 4 and all(c.timeout_s == 15.0 for c in raw_calls)
    assert raw_calls[0].include == []  # Gemini grounding returns its sources unasked
    tool = raw_calls[0].tools[0]
    assert tool["type"] == "web_search" and tool["user_location"]["city"] == "Dubai"
    cur = radar.cur
    cur.execute("SELECT * FROM vis_ai_runs WHERE batch_id = %s AND status = 'ok' ORDER BY id LIMIT 1", (batch,))
    run = cur.fetchone()
    assert run["self_mentioned"] and run["self_position"] == 1 and run["model_id"] == "gemini-2.5-flash"
    assert run["baseline_epoch"] == 1 and run["parser_version"]
    cur.execute("SELECT COUNT(*) AS n FROM vis_api_costs WHERE hotel_slug = %s AND provider = 'gemini'",
                (radar.hotel.slug,))
    assert cur.fetchone()["n"] == 4


def test_ai_mode_call_is_never_a_cache_hit(radar):
    from clefs.modules.visibility import ai_radar, store

    store.update_profile(radar.cur, radar.hotel.slug, {"engines_enabled": ["serpapi_ai_mode"]}, user_id=None)
    batch = ai_radar.create_weekly_batch(radar.cur, radar.hotel)["batch_id"]
    ai_radar.step(radar.cur, batch)
    ai_radar.step(radar.cur, batch)
    assert radar.serp.calls == 8  # 4 repeats of one prompt are 4 billed answers, not 1 + 3 cache hits
    req = radar.serp.requests[0]
    assert req["engine"] == "google_ai_mode" and req["gl"] == "ae" and req["device"] == "desktop"


def test_error_discards_the_whole_prompt_engine_unit(radar):
    from clefs import ai
    from clefs.modules.visibility import ai_radar, store

    store.update_profile(radar.cur, radar.hotel.slug, {"engines_enabled": ["gemini_web_search"]}, user_id=None)
    batch = ai_radar.create_weekly_batch(radar.cur, radar.hotel)["batch_id"]
    radar.ai._queue.clear()
    radar.ai.push(gemini_body(["Test Hotel Dubai"]), ai.AIError("Gemini call failed: BadRequestError"),
                  gemini_body(["Test Hotel Dubai"]), gemini_body(["Test Hotel Dubai"]))
    out = ai_radar.step(radar.cur, batch)
    assert out["statuses"] == ["ok", "error", "ok", "ok"]
    cur = radar.cur
    cur.execute("SELECT DISTINCT status FROM vis_ai_runs WHERE batch_id = %s AND prompt_key = %s",
                (batch, "ae.ar.generic.1"))
    assert {r["status"] for r in cur.fetchall()} == {"discarded"}


def test_timeout_is_retried_once_then_discarded(radar):
    from clefs import ai
    from clefs.modules.visibility import ai_radar, store

    store.update_profile(radar.cur, radar.hotel.slug, {"engines_enabled": ["gemini_web_search"]}, user_id=None)
    batch = ai_radar.create_weekly_batch(radar.cur, radar.hotel)["batch_id"]
    radar.ai._queue.clear()
    timeout = ai.AIError("Gemini call failed: APITimeoutError")
    radar.ai.push(timeout, *[gemini_body(["X Hotel"])] * 3)
    ai_radar.step(radar.cur, batch)
    cur = radar.cur
    cur.execute("SELECT status, attempts FROM vis_ai_runs WHERE batch_id = %s ORDER BY id LIMIT 1", (batch,))
    assert dict(cur.fetchone()) == {"status": "timeout", "attempts": 1}
    # later steps take pending runs first; the timed-out run comes back once
    radar.ai.push(*[timeout] * 1 + [gemini_body(["X Hotel"])] * 200)
    cur.execute("UPDATE vis_ai_runs SET status = 'ok' WHERE batch_id = %s AND status = 'pending'", (batch,))
    out = ai_radar.step(radar.cur, batch)
    assert out["ran"] == 1 and out["statuses"] == ["timeout"]
    cur.execute("SELECT DISTINCT status FROM vis_ai_runs WHERE batch_id = %s AND prompt_key = 'ae.ar.generic.1'",
                (batch,))
    assert {r["status"] for r in cur.fetchall()} == {"discarded"}
    assert out["finished"] == "ready"


# ─── Model versions and epochs ───


def test_epoch_resets_on_a_model_change(radar, env):
    from clefs.modules.visibility import ai_radar, stats, store

    cur, slug = radar.cur, radar.hotel.slug
    store.update_profile(cur, slug, {"engines_enabled": ["gemini_web_search"]}, user_id=None)
    batch = ai_radar.create_weekly_batch(cur, radar.hotel)["batch_id"]
    radar.ai._queue.clear()
    radar.ai.push(*[gemini_body(["Test Hotel Dubai"])] * 4,
                  *[gemini_body(["Test Hotel Dubai"], model="gemini-2.5-flash-preview-09-2025")] * 4)
    ai_radar.step(cur, batch)
    ai_radar.step(cur, batch)
    cur.execute("SELECT DISTINCT model_id, baseline_epoch FROM vis_ai_runs WHERE batch_id = %s AND status = 'ok' "
                "ORDER BY baseline_epoch", (batch,))
    assert [(r["model_id"], r["baseline_epoch"]) for r in cur.fetchall()] == [
        ("gemini-2.5-flash", 1), ("gemini-2.5-flash-preview-09-2025", 2)]
    epochs = stats.overview(cur, radar.hotel)["epochs"]
    assert epochs[-1]["reason"] == "model_changed" and epochs[-1]["note"].endswith("; baseline reset")
    assert epochs[-1]["note"].startswith("Model changed 28 Sep")
    # a change of the configured model is a reset too, even with the same answering model id
    env(AI_MODEL_VISIBILITY_GEMINI="gemini-2.5-pro")
    assert ai_radar.epoch_for(cur, slug, "gemini_web_search", "gemini-2.5-flash-preview-09-2025") == 3
    cur.execute("SELECT reason FROM vis_engine_versions WHERE hotel_slug = %s AND baseline_epoch = 3", (slug,))
    assert cur.fetchone()["reason"] == "model_setting_changed"


def test_ai_mode_source_shift_is_only_suggested(radar):
    from datetime import date

    from clefs.modules.visibility import ai_radar, stats

    cur, slug = radar.cur, radar.hotel.slug
    ai_radar.epoch_for(cur, slug, "serpapi_ai_mode", "google_ai_mode")
    for week, dom in ((date(2026, 9, 21), "booking.com"), (date(2026, 9, 28), "reddit.com")):
        cur.execute("INSERT INTO vis_ai_batches (hotel_slug, kind, week, status) VALUES (%s, 'weekly', %s, 'ready') "
                    "RETURNING id", (slug, week))
        b = cur.fetchone()["id"]
        cur.execute("INSERT INTO vis_ai_runs (batch_id, batch_kind, hotel_slug, engine, run_at, status, cited_domains) "
                    "VALUES (%s, 'weekly', %s, 'serpapi_ai_mode', NOW(), 'ok', %s)", (b, slug, [dom]))
    shift = stats.source_shift(cur, slug, date(2026, 9, 28))
    assert shift and shift["jsd"] == 1.0
    assert ai_radar.current_epoch(cur, slug, "serpapi_ai_mode") == 1  # nothing resets on its own
    assert stats.overview(cur, radar.hotel)["suggested_resets"] == ["source_shift:2026-09-28"]


# ─── Budget (5.10) ───


def test_degradation_order():
    from clefs.modules.visibility import costs

    prompts = ([{"market": "AE", "language": "en"}] * 15 + [{"market": "AE", "language": "ar"}] * 5
               + [{"market": "GB", "language": "en"}] * 4)
    engines = ["gemini_web_search", "serpapi_ai_mode", "perplexity_sonar"]
    full = costs.plan_weekly(prompts, engines, allowance_micros=None)
    steps_seen = []
    for allowance in range(full.est_micros, 0, -50_000):
        p = costs.plan_weekly(prompts, engines, allowance_micros=allowance)
        assert p.skipped or p.est_micros <= allowance
        if p.steps and p.steps not in steps_seen:
            steps_seen.append(p.steps)
    assert steps_seen[0] == ["drop_source_markets"]
    assert steps_seen[1] == ["drop_source_markets", "repeats_3"]
    assert steps_seen[2] == ["drop_source_markets", "repeats_3", "drop_ar"]
    dearest = max(engines, key=lambda e: (costs.run_estimate_micros(e), e))
    assert steps_seen[3][-1] == f"drop_engine:{dearest}"  # the dearest per run
    # integrity: every planned unit keeps the same number of repeats
    p = costs.plan_weekly(prompts, engines, allowance_micros=1_000_000)
    assert p.repeats == 3 and all(u[1] != dearest for u in p.units())


def test_budget_compares_micros_with_minor_times_10000(radar):
    from clefs.modules.visibility import ai_radar, store

    cur, slug = radar.cur, radar.hotel.slug
    store.update_profile(cur, slug, {"monthly_budget_minor": 100}, user_id=None)  # USD 1.00
    assert store.budget_micros(cur, store.get_profile(cur, slug)) == 1_000_000
    store.record_cost(cur, slug, provider="gemini", purpose="ai_radar", cost_micros=999_999)
    out = ai_radar.create_weekly_batch(cur, radar.hotel)
    assert out["skipped"] == "budget_exhausted"
    assert out["plan"]["steps"][:3] == ["drop_source_markets", "repeats_3", "drop_ar"]


def test_kill_switch(radar, env):
    from clefs.modules.visibility import ai_radar

    env(VISIBILITY_AI_ENABLED="false")
    assert ai_radar.create_weekly_batch(radar.cur, radar.hotel) == {"skipped": "kill_switch"}


# ─── End to end through the worker ───


def test_weekly_batch_through_the_worker(radar, run_jobs):
    from clefs.modules.visibility import ai_radar, module, stats, store  # noqa: F401  (module registers the tasks)

    cur, slug = radar.cur, radar.hotel.slug
    store.update_profile(cur, slug, {"engines_enabled": ["gemini_web_search"]}, user_id=None)
    batch = ai_radar.create_weekly_batch(cur, radar.hotel)["batch_id"]
    rep = run_jobs(["visibility.ai_batch_step", "visibility.ai_weekly_rollup"])
    assert not rep.errors
    assert ai_radar.get_batch(cur, batch)["status"] == "ready"
    cur.execute("SELECT runs, mentions, sufficient FROM vis_ai_weekly WHERE hotel_slug = %s AND window_weeks = 1 "
                "AND market = 'AE' AND language = 'en' AND entity = 'self'", (slug,))
    row = cur.fetchone()
    assert (row["runs"], row["mentions"]) == (40, 40)  # 10 EN discovery prompts x 4 repeats
    cur.execute("SELECT COUNT(*) AS n FROM vis_ai_runs WHERE batch_id = %s AND facts_checked_at IS NULL", (batch,))
    assert cur.fetchone()["n"] == 0
    tab = stats.overview(cur, radar.hotel)
    assert tab["latest_batch"]["status"] == "ready" and tab["cells"]


def test_concurrent_round_abandons_a_slow_call_as_timeout(monkeypatch):
    import time

    from clefs.modules.visibility import ai_radar

    def fake_work(row, known, hotel_slug, purpose):
        if row["id"] == 2:
            time.sleep(1.0)
        return ai_radar.Outcome("ok")

    monkeypatch.setattr(ai_radar, "_work", fake_work)
    monkeypatch.setattr(ai_radar, "CALL_TIMEOUT_S", 0.1)
    monkeypatch.setattr(ai_radar, "ROUND_MARGIN_S", 0.1)
    t0 = time.monotonic()
    out = ai_radar._run_round([{"id": i} for i in range(1, 5)], [], None, "visibility.ai_radar")
    assert time.monotonic() - t0 < 0.9  # never waits for the slow thread
    assert [o.status for o in out] == ["ok", "timeout", "ok", "ok"]


def test_event_prompts_come_from_demand_and_stay_stable(radar, monkeypatch):
    from clefs.modules.demand import api as demand_api
    from clefs.modules.visibility import prompts

    events = [{"event_id": "gitex-2026", "name": "GITEX Global", "start": "2026-10-12", "score": 0.9},
              {"event_id": "3f0e4c7a-6f0c-4a55-9d7e-2f1f6c1f0a11", "name": "Dubai Airshow", "score": 0.5}]
    monkeypatch.setattr(demand_api, "upcoming_events", lambda cur, slug, **kw: events)
    first = prompts.refresh(radar.cur, radar.hotel)
    rows = {r["prompt_key"]: r for r in prompts.active_prompts(radar.cur, radar.hotel.slug)}
    assert rows["ae.en.event.1"]["text"] == "Where should I stay in Dubai during GITEX Global?"
    assert rows["ae.en.event.2"]["intent"] == "event" and first["prompts"] == 20
    assert prompts.refresh(radar.cur, radar.hotel)["created"] == 0
