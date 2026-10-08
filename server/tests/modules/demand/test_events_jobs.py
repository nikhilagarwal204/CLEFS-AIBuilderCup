"""Scheduled event jobs run through the real scheduler (jobs.tick)."""

from __future__ import annotations

from datetime import date, datetime, timezone

from clefs import db as clefs_db
from clefs.modules.demand import curated


def test_curated_sync_job_runs_once_per_day(cur, clock, run_jobs, hotel_uae):
    from clefs import jobs
    from clefs.modules.demand import events_jobs  # noqa: F401  (registers the jobs)

    names = {j.name for j in jobs.registered_jobs()}
    assert {"dr.curated_sync", "dr.phq_events_sync", "dr.phq_features_sync", "dr.ticketmaster_sync",
            "dr.ai_discovery", "dr.curated_verify_reminder"} <= names
    clock.freeze(datetime(2026, 9, 30, 0, 31, tzinfo=timezone.utc))
    report = run_jobs(["dr.curated_sync", "dr.phq_events_sync", "dr.phq_features_sync"], scheduled=True)
    assert report.scheduled_failed == 0, report.errors
    assert clefs_db.fetch_one("SELECT count(*) AS n FROM dr_events", cur=cur)["n"] > 20
    state = clefs_db.fetch_one("SELECT last_result FROM dr_sync_state WHERE source = 'CURATED'", cur=cur)
    assert state["last_result"]["events"] >= 18


def test_verify_reminder_lists_stale_rows():
    seed, _ = curated.load_seed()
    assert curated.stale_rows(seed, date(2026, 10, 1)) == []
    stale = curated.stale_rows(seed, date(2027, 1, 1))
    keys = {r["key"] for r in stale}
    # Future rows verified in Sep 2026 are due a re-check; past events are not.
    assert "gitex-global-2026" not in keys and "gulfood-2027" in keys and "DWTC" in keys


def test_vendor_guard_skips_permanent_errors_and_retries_transient():
    import pytest

    from clefs.modules.demand import events_jobs
    from clefs.modules.demand.adapters import VendorError

    def fail(status):
        def _f():
            raise VendorError("predicthq", status, f"HTTP {status}")
        return _f

    assert events_jobs._vendor_guard(fail(403)) == {"skipped": "vendor_error", "status": 403, "vendor": "predicthq"}
    with pytest.raises(VendorError):
        events_jobs._vendor_guard(fail(503))
