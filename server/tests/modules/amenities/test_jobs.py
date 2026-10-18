"""The nightly job end to end, the rollup task and the registered metrics
(the investigation verifier lives in test_investigation.py)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from clefs.core import signals
from clefs.core.metrics import MetricQuery, compile_metric
from clefs.modules.amenities import catalog, detectors
from clefs.modules.amenities import jobs as am_jobs

from .conftest import DXB, END_DEFAULT, at

START = END_DEFAULT - timedelta(days=62)


def _soap(cur, slug):
    return catalog.create_item(cur, slug, "AED", catalog.ItemIn(
        key="TEST_SOAP", name_en="Test soap", category="BATH", basis="PER_OCCUPIED_ROOM_NIGHT",
        std_qty=Decimal("1"), unit_cost=Decimal("1.00")))


def _plant_spike(cur, slug, occ_range, issue):
    item = _soap(cur, slug)
    occ_range(START, 63, 50)
    for i in range(63):
        issue(item, 50, at(START + timedelta(days=i)))
    issue(item, 200, at(END_DEFAULT))
    return item


def test_nightly_rollup_job_backfills_and_raises_the_spike(cur, hotel_uae, clock, occ_range, issue, run_jobs):
    clock.freeze(datetime(2026, 9, 29, 3, 31, tzinfo=DXB))
    _plant_spike(cur, hotel_uae.slug, occ_range, issue)
    run_jobs("am.variance_rollup", scheduled=True)
    cur.execute("SELECT COUNT(*) AS n, MIN(business_date) AS first FROM amn_daily_totals WHERE hotel_slug = %s",
                (hotel_uae.slug,))
    r = cur.fetchone()
    assert r["n"] == 63 and r["first"] == START                     # first run backfills 9 weeks
    kinds = {s["kind"] for s in signals.list_signals(cur, hotel_uae.slug, modules=["amenities"])}
    assert detectors.SPIKE in kinds


def test_rollup_range_task_rerolls_a_back_dated_line(cur, hotel_uae, frozen, occ, issue, run_jobs):
    item = _soap(cur, hotel_uae.slug)
    d = END_DEFAULT - timedelta(days=3)
    occ(d, 10)
    issue(item, 12, at(d))                                            # queues am.rollup_range for d
    run_jobs("am.rollup_range")
    cur.execute("SELECT issued, expected_total FROM amn_variance_daily WHERE item_id = %s", (item["id"],))
    r = cur.fetchone()
    assert (r["issued"], r["expected_total"]) == (12, 10)


def test_metrics_are_registered_and_compile(hotel_uae):
    keys = ("am.issued_units", "am.expected_units", "am.variance_units", "am.variance_aed", "am.cpor_aed",
            "am.requests_per_100_orn", "am.issued_per_100_orn")
    assert am_jobs.ROLLUP_DAYS == 14
    for k in keys:
        sql, params = compile_metric(hotel_uae, MetricQuery(metric=k, start=date(2026, 9, 1), end=date(2026, 9, 28)))
        assert "amn_" in sql and params
