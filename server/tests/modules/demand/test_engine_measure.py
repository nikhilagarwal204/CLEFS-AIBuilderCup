"""APPLIED verification from BAR evidence, observed outcomes, actionability,
and the weekly top 5 (02 spec 4.3)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.auth.deps import resolve_ctx
from clefs.core import actions as core_actions
from clefs.core import signals as core_signals
from clefs.modules.demand import bar, measure, recommend as rec

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
NIGHT = date(2026, 10, 12)


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    for d in (NIGHT, NIGHT + timedelta(days=1), NIGHT + timedelta(days=2)):
        bar.set_bar(cur, hotel_uae, stay_date=d, room_type_code="DLX", bar_minor=50000, source="MANUAL")
    return hotel_uae


def _night(di, d=NIGHT, events=None):
    return {"stay_date": d.isoformat(), "demand_index": di, "coverage": 0.75,
            "components": {"pace": {"otb": 8, "pace_gap": 0.2, "base": 6.0},
                           "comp": {"comp_move": 0.15, "sold_out": 0, "comps": 5},
                           "events": events or [], "market_holidays": [], "season": None}}


def _ev(eid, score=0.8):
    return {"event_id": eid, "title": f"Show {eid[-1]}", "category": "EXPO", "night_score": score, "score": score,
            "distance_km": 3.0, "confidence": 0.9, "verification": "VERIFIED", "status": "SCHEDULED", "k": 0.2,
            "k_source": "prior", "calibration_fit": 0.3, "event_nights": 3}


def test_applied_from_bar_evidence_verifies_the_action(cur, hotel, clock):
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    ctx = resolve_ctx(core_actions.SYSTEM, hotel.slug, cur=cur)
    out = rec.decide(cur, ctx, r["id"], "approve")
    assert out["status"] == "APPROVED"
    decided = int(out["decided"].replace(".", ""))
    # No new BAR yet: nothing applied.
    assert measure.check_applied(cur, hotel, today=NOW.date())["applied"] == 0
    # A later BAR within 2% of the approved rate.
    clock.advance(hours=3)
    bar.set_bar(cur, hotel, stay_date=NIGHT, room_type_code="DLX", bar_minor=decided - 500, source="CSV")
    assert measure.check_applied(cur, hotel, today=NOW.date())["applied"] == 1
    [r2] = rec.list_recs(cur, hotel.slug)
    assert r2["status"] == "APPLIED" and r2["applied_at"]
    act = core_actions.get_action(cur, hotel.slug, r["action_id"])
    assert act["status"] == "verified" and act["attribution"] == "observed"
    assert not act["counts_toward_verified_profit"]
    sigs = core_signals.list_signals(cur, hotel.slug, modules=["demand"])
    assert any(s["kind"] == "dr.rec.applied_verified" for s in sigs)


def test_bar_outside_tolerance_is_not_applied(cur, hotel, clock):
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    rec.decide(cur, resolve_ctx(core_actions.SYSTEM, hotel.slug, cur=cur), r["id"], "adjust", recommended_minor=55000)
    clock.advance(hours=1)
    bar.set_bar(cur, hotel, stay_date=NIGHT, room_type_code="DLX", bar_minor=52000, source="CSV")  # 5.5% off
    assert measure.check_applied(cur, hotel, today=NOW.date())["applied"] == 0


def test_outcomes_are_observed_not_causal(cur, hotel, clock):
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    rec.decide(cur, resolve_ctx(core_actions.SYSTEM, hotel.slug, cur=cur), r["id"], "approve")
    cur.execute("INSERT INTO otb_snapshots (hotel_slug, snapshot_date, stay_date, rooms_otb, currency) "
                "VALUES (%s, %s, %s, 6, 'AED')", (hotel.slug, NOW.date(), NIGHT))
    cur.execute("INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, rooms_occupied, room_revenue_minor, "
                "currency, source) VALUES (%s, %s, 13, 11, 550000, 'AED', 'MANUAL')", (hotel.slug, NIGHT))
    clock.freeze(NOW + timedelta(days=14))
    assert measure.measure_outcomes(cur, hotel, today=NIGHT + timedelta(days=1))["measured"] == 1
    [r2] = rec.list_recs(cur, hotel.slug)
    o = r2["outcome"]
    assert o["label"] == "observed, not causal" and o["pickup_after_decision"] == 5 and o["adr"] == "500.00"
    assert r2["outcome_label"] == "Observed, not causal"
    rep = measure.outcomes_report(cur, hotel.slug, start=NIGHT, end=NIGHT)
    assert rep["actionability"]["decided_within_48h_pct"] == 100.0 and rep["actionability"]["approved"] == 1
    assert rep["label"] == "Observed, not causal"


def test_top5_ranks_by_impact_times_confidence_two_per_event(cur, hotel):
    a, b = "00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"
    cur.execute("INSERT INTO dr_events (id, canonical_key, title, category, start_date, end_date) VALUES "
                "(%s, 'a', 'Show a', 'EXPO', %s, %s), (%s, 'b', 'Show b', 'EXPO', %s, %s)",
                (a, NIGHT, NIGHT + timedelta(days=2), b, NIGHT, NIGHT + timedelta(days=2)))
    nights = [_night(95, NIGHT + timedelta(days=i), [_ev(a)]) for i in range(3)]
    rec.run(cur, hotel, today=NOW.date(), nights=nights, llm_budget=0)
    cur.execute("UPDATE dr_rate_recommendations SET driver_event_ids = %s::uuid[] WHERE stay_date = %s",
                ([b], NIGHT + timedelta(days=2)))
    top = measure.top5(cur, hotel.slug, today=NOW.date())
    groups = [t["group"] for t in top]
    assert groups.count(a) == 2 and groups.count(b) == 1
    assert measure.raise_top5(cur, hotel, today=NOW.date())["top"] == 3
    kinds = [s["kind"] for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"])]
    assert kinds.count("dr.opportunity") == 3
