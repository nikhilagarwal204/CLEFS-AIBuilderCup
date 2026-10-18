"""Demo seed, growth part: Visibility. Google Hotels with and without an
official price (the parity plant 450 vs 412 and a 0.78% decoy), GBP
Performance series, and the AI radar with mentions, competitor wins and two
planted fact errors (04 spec 7.4). One seed per test, visibility only."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-growth-vis"
PARTS = ["core", "core_catalog", "core_stays", "growth"]


@pytest.fixture
def seeded(cur, monkeypatch):
    monkeypatch.setattr(growth, "SECTIONS", ("visibility",))
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return next(iter(cur.fetchone().values()))


def test_every_plant_surfaces_and_the_decoy_stays_quiet(cur, seeded):
    plants = [p for p in seeded["plants"] if p["module"] == "visibility"]
    missing = [(p["detector"], p["key"]) for p in plants if p["klass"] != "DECOY" and not growth.surfaced(cur, SLUG, p)]
    fired = [(p["detector"], p["key"]) for p in plants if p["klass"] == "DECOY" and growth.surfaced(cur, SLUG, p)]
    assert missing == [] and fired == []
    assert sum(p["detector"] == "vis.ai_fact_error" for p in plants) == 2


def test_parity_gap_and_official_price(cur, seeded):
    cur.execute("""SELECT check_in, official_present, official_price_minor, min_other_price_minor, min_other_channel,
                          parity_gap_minor, parity_gap_pct FROM vis_google_hotels_obs
                   WHERE hotel_slug = %s AND observed_at = (SELECT max(observed_at) FROM vis_google_hotels_obs
                                                            WHERE hotel_slug = %s) ORDER BY check_in""", (SLUG, SLUG))
    rows = {r["check_in"].isoformat(): dict(r) for r in cur.fetchall()}
    plant = rows["2026-10-02"]
    assert (plant["official_price_minor"], plant["min_other_price_minor"], plant["min_other_channel"]) == \
        (45000, 41200, "BOOKING")
    assert plant["parity_gap_minor"] == 3800 and float(plant["parity_gap_pct"]) == pytest.approx(8.444, abs=0.001)
    assert rows["2026-10-27"]["official_present"] is False
    # The decoy was observed the day before and stayed inside the tolerance.
    assert float(_one(cur, "SELECT parity_gap_pct FROM vis_google_hotels_obs WHERE hotel_slug = %s "
                           "AND check_in = '2026-10-26' LIMIT 1", (SLUG,))) == pytest.approx(0.781, abs=0.001)
    # The exposure is the gap times Booking rooms on the books that night.
    cur.execute("SELECT impact_minor FROM signals WHERE hotel_slug = %s AND dedupe_key = %s",
                (SLUG, "vis.parity_gap_google:2026-10-02"))
    impact = cur.fetchone()["impact_minor"]
    assert impact is not None and impact % 3800 == 0


def test_gbp_series_kpis_and_keywords(cur, seeded):
    cur.execute("SELECT month FROM vis_gbp_kpis_monthly WHERE hotel_slug = %s ORDER BY month", (SLUG,))
    assert [r["month"].isoformat() for r in cur.fetchall()] == ["2026-05-01", "2026-06-01", "2026-07-01", "2026-08-01"]
    # Raw daily rows only for the last 30 days, none expiring on day one.
    cur.execute("SELECT min(date) AS lo, max(date) AS hi, min(expires_at) AS exp FROM vis_gbp_daily WHERE hotel_slug = %s",
                (SLUG,))
    r = cur.fetchone()
    assert (ANCHOR - r["lo"]).days <= 30 and r["hi"].isoformat() == "2026-09-26"
    assert r["exp"].date() > ANCHOR
    assert _one(cur, "SELECT count(*) FROM vis_gbp_keywords WHERE hotel_slug = %s", (SLUG,)) == 5
    assert _one(cur, "SELECT count(*) FROM signals WHERE hotel_slug = %s AND kind = 'vis.gbp_setup' "
                     "AND status = 'open'", (SLUG,)) == 0


def test_ai_radar_batches_facts_and_no_vendor_call(cur, seeded):
    batches = seeded["parts"]["growth"]["visibility"]["ai"]["batches"]
    assert len(batches) == 3 and all(b["planned"] == 192 for b in batches)
    assert _one(cur, "SELECT count(*) FROM vis_ai_batches WHERE hotel_slug = %s AND status = 'ready'", (SLUG,)) == 3
    # Only the two planted claims contradict the KB.
    cur.execute("SELECT fact_key, claimed_value FROM vis_ai_facts WHERE hotel_slug = %s ORDER BY 1", (SLUG,))
    assert [tuple(r.values()) for r in cur.fetchall()] == [("checkin_time", "12:00"), ("parking", "no")]
    # The queue holds nothing a worker would replay after the seed.
    assert _one(cur, "SELECT count(*) FROM job_queue WHERE hotel_slug = %s AND status = 'queued' "
                     "AND name LIKE 'visibility.%%'", (SLUG,)) == 0
    assert _one(cur, "SELECT count(*) FROM serpapi_calls WHERE hotel_slug = %s AND billed", (SLUG,)) == 0
