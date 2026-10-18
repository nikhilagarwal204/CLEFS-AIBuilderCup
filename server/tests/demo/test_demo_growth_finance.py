"""Demo seed, growth part: Finance. 13 months of EN/AR P&L imported through
core ingest, mapped and published; only the planted labour anomaly
(2026-06) and booked-net month (2026-07) fire (04 spec 7.4)."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-growth-fin"
PARTS = ["core", "core_catalog", "growth"]


@pytest.fixture
def seeded(cur, monkeypatch):
    monkeypatch.setattr(growth, "SECTIONS", ("finance",))
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return next(iter(cur.fetchone().values()))


def test_anomalies_surface_only_in_the_planted_months(cur, seeded):
    plants = [p for p in seeded["plants"] if p["module"] == "finance"]
    missing = [(p["detector"], p["key"]) for p in plants if p["klass"] != "DECOY" and not growth.surfaced(cur, SLUG, p)]
    fired = [(p["detector"], p["key"]) for p in plants if p["klass"] == "DECOY" and growth.surfaced(cur, SLUG, p)]
    assert missing == [] and fired == []
    cur.execute("SELECT DISTINCT to_char(period_month, 'YYYY-MM') AS m FROM fin_anomalies WHERE hotel_slug = %s "
                "ORDER BY 1", (SLUG,))
    assert [r["m"] for r in cur.fetchall()] == ["2026-06", "2026-07"]
    cur.execute("SELECT dedupe_key FROM signals WHERE hotel_slug = %s AND kind = 'fin.booked_net'", (SLUG,))
    assert [r["dedupe_key"] for r in cur.fetchall()] == ["fin.booked_net:2026-07-01"]


def test_labour_anomaly_values(cur, seeded):
    cur.execute("SELECT value, expected FROM fin_anomalies WHERE hotel_slug = %s AND kind = 'labour_ratio'", (SLUG,))
    r = cur.fetchone()
    # 142,000 / 450,000 = 31.56%, at least 4 pp above the trailing mean.
    assert float(r["value"]) == pytest.approx(0.3156, abs=0.0005) or float(r["value"]) == pytest.approx(31.56, abs=0.05)
    value = float(r["value"]) * (100 if float(r["value"]) < 1 else 1)
    expected = float(r["expected"]) * (100 if float(r["expected"]) < 1 else 1)
    assert value - expected >= 4


def test_pl_imported_mapped_and_published(cur, seeded):
    out = seeded["parts"]["growth"]["finance"]
    assert len(out["published_months"]) == 13
    # One multi-month CSV through core ingest, mixed English and Arabic names.
    assert _one(cur, "SELECT count(*) FROM import_batches WHERE hotel_slug = %s AND kind = 'PL_STATEMENT'", (SLUG,)) == 1
    cur.execute("SELECT name_raw AS name FROM fin_coa_accounts WHERE hotel_slug = %s", (SLUG,))
    names = [r["name"] for r in cur.fetchall()]
    assert any("إيرادات الغرف" in n for n in names) and "DEWA Electricity" in names and "رسوم البلدية" in names
    assert _one(cur, "SELECT count(*) FROM job_queue WHERE hotel_slug = %s AND status = 'queued' "
                     "AND name LIKE 'finance.%%'", (SLUG,)) == 0
