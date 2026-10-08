"""Demo seed, growth part: Demand Radar. The planted calibration expo, comp
rates (+60% GITEX, +15% CABSAT, one comp sold out for GITEX), the BAR
calendar and guardrails, and the engine's derived rows (02 spec 9 "Demand
Radar" row). One seed at scale 0.1 with only the demand section."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import growth

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-growth-dr"
PARTS = ["core", "core_catalog", "core_stays", "growth"]


@pytest.fixture
def seeded(cur, monkeypatch):
    monkeypatch.setattr(growth, "SECTIONS", ("demand",))
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    return next(iter(cur.fetchone().values()))


def test_demand_plants_surface(cur, seeded):
    plants = [p for p in seeded["plants"] if p["module"] == "demand"]
    assert {p["detector"] for p in plants} == {"dr.event", "dr.calibration", "dr.comp_sold_out", "dr.comp_uplift",
                                               "dr.recommendation"}
    missing = [(p["detector"], p["key"]) for p in plants if not growth.surfaced(cur, SLUG, p)]
    assert missing == []


def test_events_come_from_every_source_without_duplicates(cur, seeded):
    out = seeded["parts"]["growth"]["demand"]["events"]
    # PredictHQ's three real events merge into the curated calendar's rows.
    assert out["phq_events"]["ingested"] == 3 and out["phq_events"]["created"] == 0
    assert out["phq_features"]["days"] == 15
    # The synthetic expo is the hotel's own row, never a global one.
    assert _one(cur, "SELECT count(*) FROM dr_events WHERE hotel_slug IS NULL AND title LIKE '%%(DEMO)%%'") == 0
    gitex = _one(cur, """SELECT count(DISTINCT s.source) FROM dr_events e JOIN dr_event_sources s ON s.event_id = e.id
                         WHERE e.title ILIKE 'GITEX Global%%' AND e.start_date = '2026-12-07'""")
    assert gitex >= 2


def test_rates_bar_and_guardrails(cur, seeded):
    rates = seeded["parts"]["growth"]["demand"]["rates"]
    assert rates["shop_anchor"]["sold_out"] == 5 and rates["shop_anchor"]["unmatched"] == []
    assert rates["bar"]["room_types"] == ["FAM_2BR", "STD_KING", "STD_TWIN"] and rates["guardrails"] == 3
    # Every recommendation stays inside the guardrails.
    assert _one(cur, "SELECT count(*) FROM dr_rate_recommendations WHERE hotel_slug = %s AND "
                     "(recommended_minor < floor_minor OR recommended_minor > ceiling_minor)", (SLUG,)) == 0
    assert _one(cur, "SELECT count(*) FROM dr_demand_days WHERE hotel_slug = %s", (SLUG,)) >= 120


def test_no_vendor_or_model_call(cur, seeded):
    assert _one(cur, "SELECT count(*) FROM serpapi_calls WHERE hotel_slug = %s AND billed", (SLUG,)) == 0
    assert _one(cur, "SELECT count(*) FROM dr_rate_recommendations WHERE hotel_slug = %s AND why_source = 'llm'",
                (SLUG,)) == 0
