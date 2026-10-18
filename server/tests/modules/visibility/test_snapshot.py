"""Public AI snapshot for Profit Guard's audit (04 spec 5.9, 5.10, 5.12)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from clefs.modules.visibility import api

NOW = datetime(2026, 9, 28, 11, 0, tzinfo=ZoneInfo("Asia/Dubai"))
SUBJECT = api.SnapshotSubject(hotel_name="Test Hotel Dubai", city="Dubai", area="Downtown",
                              website="https://testhoteldubai.example", star=4)


def _gemini(req):
    if req.kind != "raw":
        return {"hotels": []}
    text = "1. **Test Hotel Dubai** - near the mall.\n2. **Rove Downtown** - budget."
    return {"model": "gemini-2.5-flash", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": text, "annotations": [
            {"type": "url_citation", "url": "https://www.booking.com/h", "title": "b", "start_index": 3,
             "end_index": 9}]}]}]}


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
def snap(env, fake_ai, monkeypatch, clock):
    from clefs.core import serpapi
    from clefs.modules.visibility import ai_radar, http as vhttp

    clock.freeze(NOW)
    env(SERPAPI_KEY="k", SERPAPI_MONTHLY_BUDGET_USD=None, PERPLEXITY_API_KEY="pk", PERPLEXITY_TERMS_APPROVED="true",
        VISIBILITY_AI_ENABLED="true", SNAPSHOT_DAILY_CAP=None, SNAPSHOT_MONTHLY_BUDGET_USD=None)
    monkeypatch.setattr(ai_radar, "CONCURRENCY", 1)
    fake_ai.push(*[_gemini] * 100)
    serp = serpapi.use_fake(default={"text_blocks": [{"type": "paragraph", "snippet": "Rove Downtown is popular."}],
                                     "references": []})
    vhttp.set_transport(httpx.MockTransport(lambda r: httpx.Response(200, json={
        "model": "sonar", "choices": [{"message": {"content": "Test Hotel Dubai checks in at 15:00 [1]."}}],
        "search_results": [{"title": "t", "url": "https://testhoteldubai.example/faq"}]})))
    yield serp
    serpapi.set_transport(None)
    vhttp.set_transport(None)


def _count(cur, batch_id):
    cur.execute("SELECT COUNT(*) AS n FROM vis_ai_runs WHERE batch_id = %s", (batch_id,))
    return cur.fetchone()["n"]


def test_snapshot_is_45_runs_with_perplexity(cur, snap):
    bid = api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1")
    assert bid and _count(cur, bid) == 45


def test_snapshot_is_30_runs_without_the_legal_gate(cur, snap, env):
    env(PERPLEXITY_TERMS_APPROVED="false")
    bid = api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1")
    assert _count(cur, bid) == 30


def test_ai_mode_dropped_without_serpapi(cur, snap, env):
    env(SERPAPI_KEY=None, PERPLEXITY_TERMS_APPROVED="false")
    bid = api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1")
    cur.execute("SELECT DISTINCT engine FROM vis_ai_runs WHERE batch_id = %s", (bid,))
    assert [r["engine"] for r in cur.fetchall()] == ["gemini_web_search"]


@pytest.mark.parametrize("setting", [{"SNAPSHOT_MONTHLY_BUDGET_USD": "0.10"}, {"SNAPSHOT_DAILY_CAP": "0"},
                                     {"VISIBILITY_AI_ENABLED": "false"}])
def test_snapshot_refused(cur, snap, env, setting):
    env(**setting)
    assert api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1") is None


def test_budget_counts_earlier_snapshot_spend(cur, snap, env):
    from clefs.modules.visibility import store

    env(SNAPSHOT_MONTHLY_BUDGET_USD="1")
    store.record_cost(cur, None, provider="gemini", purpose="snapshot", cost_micros=990_000)
    assert api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1") is None


def test_snapshot_end_to_end(cur, snap, run_jobs):
    bid = api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1")
    assert api.get_ai_snapshot(cur, bid)["status"] == "running"
    from clefs.modules.visibility import module  # noqa: F401  (registers the step task)

    rep = run_jobs("visibility.ai_batch_step")
    assert rep.tasks_run == 12 and not rep.errors  # 45 runs at <= 4 per step: 12 steps
    out = api.get_ai_snapshot(cur, bid)
    assert out["status"] == "ready"
    per = {e["engine"]: e for e in out["per_engine"]}
    assert per["gemini_web_search"]["mentioned"] == 9 and per["gemini_web_search"]["runs"] == 9
    assert per["gemini_web_search"]["display"] == "mentioned in 9 of 9 answers"
    assert per["serpapi_ai_mode"]["mentioned"] == 0
    assert per["perplexity_sonar"]["mentioned"] == 9
    assert "booking.com" in out["top_cited_domains"]
    assert {a["prompt"][:5] for a in out["answers_about_you"]} == {"Is Te", "What "}
    assert "API simulation" in out["label"] and "SerpApi (third-party)" in out["label"]
    cur.execute("SELECT COUNT(*) AS n FROM vis_api_costs WHERE hotel_slug IS NULL AND purpose = 'snapshot'")
    assert cur.fetchone()["n"] == 30  # Gemini + Perplexity; AI Mode is in core's ledger
    cur.execute("SELECT COUNT(*) AS n FROM serpapi_calls WHERE purpose = 'visibility.snapshot' AND billed")
    assert cur.fetchone()["n"] == 15
    latest = api.ai_snapshot(cur, None, hotel_name="test hotel dubai", city="Dubai")
    assert latest["sample_size"] == 27 and len(latest["queries"]) == 3 and latest["mentioned"] == 18
    assert api.purge_ai_snapshot(cur, bid) == 46
    assert api.get_ai_snapshot(cur, bid) is None and api.purge_ai_snapshot(cur, bid) == 0


def test_ai_mode_dropped_when_the_serpapi_budget_is_spent(cur, snap, env):
    env(SERPAPI_MONTHLY_BUDGET_USD="0.01", PERPLEXITY_TERMS_APPROVED="false")
    bid = api.start_ai_snapshot(cur, subject=SUBJECT, requester_ref="audit-1")
    cur.execute("SELECT DISTINCT engine FROM vis_ai_runs WHERE batch_id = %s", (bid,))
    assert [r["engine"] for r in cur.fetchall()] == ["gemini_web_search"]
