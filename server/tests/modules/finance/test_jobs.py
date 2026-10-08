"""Finance jobs: weekly rollup, the LLM mapping task, memory rebuild (04 spec 6.8)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from clefs import jobs
from clefs.core import signals
from clefs.modules.finance import jobs as fin_jobs
from clefs.modules.finance import pulse
from tests.modules.finance.conftest import dist_row, statement_csv


def test_weekly_rollup_stores_prior_week_and_signals(cur, hotel_uae, clock, occupancy, neighbours):
    clock.freeze(datetime(2026, 9, 21, 2, 0, tzinfo=timezone.utc))     # Monday 06:00 in Dubai
    wk = date(2026, 9, 14)
    occupancy(hotel_uae.slug, wk, 7, 80, 50, 1_400_000)
    neighbours.dist = [dist_row(wk, "BOOKING", True, 1_050_000, 84_000), dist_row(wk, "DIRECT", False, 0, 126_000)]
    out = fin_jobs.weekly_rollup(jobs.JobCtx(cur=cur, job="finance.weekly_rollup", hotel_slug=hotel_uae.slug))
    assert out == {"week_start": "2026-09-14", "nrevpar_minor": 15_250}
    assert pulse.stored_week(cur, hotel_uae.slug, wk)["nrevpar_minor"] == 15_250
    sig = signals.list_signals(cur, hotel_uae.slug, modules=["finance"])
    assert [s["kind"] for s in sig] == ["fin.nrevpar_weekly"] and sig[0]["cadence"] == "weekly"
    assert sig[0]["section"] == "money_available" and "152.50" in sig[0]["title"]


def test_map_propose_task_uses_llm_and_refreshes(cur, hotel_uae, upload_statement, neighbours, fake_ai, run_jobs):
    upload_statement(hotel_uae.slug, statement_csv(), date(2026, 8, 1))
    cur.execute("SELECT payload FROM job_queue WHERE name = 'finance.map_propose'")
    payload = cur.fetchone()["payload"]
    ids = payload["account_ids"]
    assert len(ids) == 19       # 24 accounts, 5 answered by the rules
    fake_ai.push({"proposals": [{"account_ref": str(i), "usali_line": "ag.other", "is_pass_through_tax": False,
                                 "pass_through_kind": None, "confidence": 0.9, "rationale": "fixture",
                                 "alternatives": []} for i in ids]})
    run_jobs(only=["finance.map_propose"])
    cur.execute("""SELECT COUNT(*) AS n FROM fin_usali_map WHERE hotel_slug = %s AND learned_from = 'llm'
                   AND superseded_at IS NULL""", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 19
    cur.execute("SELECT status FROM fin_import_batches WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["status"] == "mapping_required"      # proposals still need a person


def test_memory_rebuild_job_runs(cur):
    out = fin_jobs.memory_rebuild(jobs.JobCtx(cur=cur, job="finance.memory_rebuild", hotel_slug=None))
    assert "rows" in out
