"""Rates-side jobs and the COMP_RATES ingest handler (02 spec 4.2, 4.4 jobs)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs import db as clefs_db
from clefs.core import serpapi
from clefs.modules.demand import comp_rates as cr
from clefs.modules.demand.adapters import csv_rates

NOW = datetime(2026, 9, 29, 4, 5, tzinfo=ZoneInfo("Asia/Dubai"))  # just after the 04:00 local slot
TODAY = date(2026, 9, 29)


@pytest.fixture
def serp(monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.setenv("SERPAPI_KEY", "test-key")
    monkeypatch.delenv("SERPAPI_MONTHLY_BUDGET_USD", raising=False)
    reload_settings()
    fake = serpapi.use_fake(default={"properties": [
        {"type": "hotel", "name": "Comp One", "property_token": "tok1", "rate_per_night": {"extracted_lowest": 500}}]})
    yield fake
    serpapi.set_transport(None)
    monkeypatch.undo()
    reload_settings()


def test_scheduled_fetch_runs_and_continues(cur, hotel_uae, clock, serp, run_jobs, monkeypatch):
    clock.freeze(NOW)
    cr.upsert_comp(cur, hotel_uae.slug, {"name": "Comp One", "property_token": "tok1"})
    # A tiny time budget: the slot fetches what it can and queues the rest.
    monkeypatch.setattr(cr, "FETCH_BUDGET_S", 0.0)
    run_jobs(["dr.comp_rates_fetch"], scheduled=True)
    run = clefs_db.fetch_one("SELECT status, result FROM job_runs WHERE job = 'dr.comp_rates_fetch' "
                             "AND hotel_slug = %s", (hotel_uae.slug,), cur=cur)
    assert run["status"] == "done" and run["result"]["continued"] == 61, run
    q = clefs_db.fetch_one("SELECT payload FROM job_queue WHERE name = 'dr.comp_rates_fetch_more' "
                           "AND hotel_slug = %s", (hotel_uae.slug,), cur=cur)
    assert len(q["payload"]["dates"]) == 61
    monkeypatch.setattr(cr, "FETCH_BUDGET_S", 90.0)
    run_jobs(["dr.comp_rates_fetch_more"])
    n = clefs_db.fetch_one("SELECT count(DISTINCT stay_date) AS n FROM dr_comp_rates WHERE hotel_slug = %s",
                           (hotel_uae.slug,), cur=cur)["n"]
    assert n == 61 and serp.calls == 61


def test_comp_rates_through_core_ingest(cur, hotel_uae, clock):
    from clefs import files
    from clefs.core.ingest import batches, commit_batch, registered_handlers

    clock.freeze(NOW)
    assert registered_handlers()["COMP_RATES"] == "clefs.modules.demand.adapters.csv_rates.commit_comp_rates"
    text = ("hotel,date,rate,channel\nSheraton Dubai Creek,2026-10-01,505,Booking.com\n"
            "Sheraton Dubai Creek,2026-10-01,499,Expedia\nTest Hotel Dubai,2026-10-01,450,Direct\n"
            "Hyatt Regency Creek,2026-10-02,612,Agoda\n")
    ref = files.put_file(cur, hotel_uae.slug, text.encode(), filename="rategain_export.csv", content_type="text/csv",
                         purpose="ingest")
    b = batches.create_batch(cur, hotel_uae.slug, file_id=ref.id, kind="COMP_RATES")
    if b["status"] != "COMMITTED":
        b = commit_batch(cur, hotel_uae.slug, b["id"])
    assert b["status"] == "COMMITTED", b
    rows = clefs_db.fetch_all("SELECT comp_id, stay_date, lowest_minor, source, by_source, import_batch_id "
                              "FROM dr_comp_rates WHERE hotel_slug = %s ORDER BY stay_date", (hotel_uae.slug,), cur=cur)
    assert [(r["comp_id"], r["lowest_minor"], r["source"]) for r in rows] == [
        ("sheraton-dubai-creek", 49900, "CSV_RATEGAIN"), ("hyatt-regency-creek", 61200, "CSV_RATEGAIN")]
    assert {s["source"] for s in rows[0]["by_source"]} == {"Booking.com", "Expedia"}
    assert rows[0]["import_batch_id"] == b["id"]
    # The hotel's own row never became a comp.
    assert [c["origin"] for c in cr.list_comps(cur, hotel_uae.slug)] == ["csv", "csv"]


def test_direct_csv_import_wide_with_sold_out(cur, hotel_uae, clock):
    clock.freeze(NOW)
    wide = "Date,Test Hotel Dubai,Sheraton Dubai Creek,Hyatt Regency Creek\n01/10/2026,450,Sold out,612\n"
    out = csv_rates.import_csv(cur, hotel_uae, wide, source_hint="lighthouse")
    assert out["rates"] == 2 and out["sold_out"] == 1 and out["source"] == "CSV_LIGHTHOUSE"
    rates = cr.rates_for_night(cur, hotel_uae.slug, date(2026, 10, 1))
    assert [(r["competitor"], r["rate"], r["sold_out"]) for r in rates] == [
        ("Hyatt Regency Creek", "612.00", False), ("Sheraton Dubai Creek", None, True)]
    with pytest.raises(Exception) as e:
        csv_rates.import_csv(cur, hotel_uae, wide, currency="USD")
    assert e.value.code == "invalid_currency"
    with pytest.raises(Exception) as e:
        csv_rates.import_csv(cur, hotel_uae, "a,b\n1,2\n")
    assert e.value.code == "unreadable_file"


def test_csv_beats_serpapi_on_the_same_day_and_stale_is_ignored(cur, hotel_uae, clock):
    clock.freeze(NOW)
    c, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "Comp One"})
    d = date(2026, 10, 3)
    cr.record_rates(cur, hotel_uae, [
        cr.CompRate(comp_id=c["comp_id"], stay_date=d, lowest_minor=50000, currency="AED", source="SERPAPI",
                    captured_at=NOW),
        cr.CompRate(comp_id=c["comp_id"], stay_date=d, lowest_minor=52000, currency="AED", source="CSV_LIGHTHOUSE",
                    captured_at=NOW - timedelta(hours=1)),
        cr.CompRate(comp_id=c["comp_id"], stay_date=d, lowest_minor=52000, currency="USD", source="SERPAPI",
                    captured_at=NOW),
    ])
    assert [r["rate"] for r in cr.rates_for_night(cur, hotel_uae.slug, d)] == ["520.00"]
    clock.advance(days=15)
    assert cr.rates_for_night(cur, hotel_uae.slug, d) == []  # older than 14 days: not a signal
    assert len(cr.night_history(cur, hotel_uae.slug, d)) == 2


def test_retention_job(cur, hotel_uae, clock, run_jobs):
    clock.freeze(NOW)
    c, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "Old"})
    cr.record_rates(cur, hotel_uae, [cr.CompRate(comp_id=c["comp_id"], stay_date=TODAY - timedelta(days=401),
                                                 lowest_minor=1000, currency="AED", source="SERPAPI",
                                                 captured_at=NOW)])
    assert cr.purge_old(cur, today=TODAY) == 1


def test_comp_median_metric(cur, hotel_uae, clock, client_as):
    clock.freeze(NOW)
    a, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "A"})
    b, _ = cr.upsert_comp(cur, hotel_uae.slug, {"name": "B"})
    d = date(2026, 10, 1)
    cr.record_rates(cur, hotel_uae, [
        cr.CompRate(comp_id=a["comp_id"], stay_date=d, lowest_minor=40000, currency="AED", source="SERPAPI",
                    captured_at=NOW - timedelta(days=1)),
        cr.CompRate(comp_id=a["comp_id"], stay_date=d, lowest_minor=50000, currency="AED", source="SERPAPI",
                    captured_at=NOW),
        cr.CompRate(comp_id=b["comp_id"], stay_date=d, lowest_minor=70000, currency="AED", source="SERPAPI",
                    captured_at=NOW),
    ])
    r = client_as("owner").post(f"/hotels/{hotel_uae.slug}/metrics/query", json={
        "metric": "dr.comp_median_rate_aed", "start": "2026-10-01", "end": "2026-10-01", "grain": "total"})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 600  # median of the latest captures: 500 and 700
