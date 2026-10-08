"""Visibility's Google-side jobs run through the real scheduler and degrade to
"not set up" results on a hotel with nothing configured (04 spec 5.10:
missing keys show a "not connected" state with no errors)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

# Monday 5 October 2026, 23:00 Dubai: the first Monday of the month and day 5,
# so every daily, weekly (Mon), nth-weekday and day-5 schedule is due today.
LATE_MONDAY = datetime(2026, 10, 5, 23, 0, tzinfo=ZoneInfo("Asia/Dubai"))

JOBS = [
    "visibility.google_hotels_daily", "visibility.rank_grid_weekly", "visibility.rank_calibrate_monthly",
    "visibility.gbp_daily", "visibility.gbp_keywords_monthly", "visibility.lodging_check",
    "visibility.competitors_sync", "visibility.gbp_raw_expire",
]


@pytest.fixture
def ready(app, cur, hotel_uae, clock, monkeypatch):
    from clefs.settings import reload_settings

    for k in ("SERPAPI_KEY", "GOOGLE_MAPS_API_KEY", "GOOGLE_API_KEY", "GBP_PROJECT_QPM"):
        monkeypatch.delenv(k, raising=False)
    reload_settings()
    clock.freeze(LATE_MONDAY)
    yield hotel_uae
    monkeypatch.undo()
    reload_settings()


def test_every_google_job_is_registered():
    from clefs import jobs
    from clefs.modules.visibility import module  # noqa: F401

    names = set(jobs._scheduled) | set(jobs._tasks)
    assert set(JOBS) | {"visibility.site_health_weekly", "visibility.attribution_monthly",
                        "visibility.gbp_kpis_monthly", "visibility.rank_grid_keyword",
                        "visibility.rank_calibrate_keyword"} <= names


def test_jobs_run_clean_on_an_unconfigured_hotel(cur, ready, run_jobs):
    run_jobs(only=JOBS, scheduled=True)
    cur.execute("SELECT job, status, error, result FROM job_runs WHERE job LIKE 'visibility.%%' "
                "AND (hotel_slug = %s OR hotel_slug IS NULL)", (ready.slug,))
    rows = {r["job"]: r for r in cur.fetchall()}
    assert set(JOBS) <= set(rows), sorted(rows)
    assert all(r["status"] == "done" for r in rows.values()), {k: (r["status"], r["error"]) for k, r in rows.items()}
    assert rows["visibility.google_hotels_daily"]["result"] == {"skipped": "not_configured", "observations": 0,
                                                                 "errors": 0, "breaches": 0}
    assert rows["visibility.gbp_daily"]["result"] == {"skipped": "project_not_approved"}
    assert rows["visibility.rank_grid_weekly"]["result"] == {"keywords": 0}
