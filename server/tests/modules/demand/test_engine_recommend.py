"""Rate recommendations (02 spec 4.3): delta clip, step cap, round to 5,
guardrail clamp, the minimum move, levers, why-this-matters lint,
supersede / expire, and owner decisions through the core action."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.demand import bar, guardrails, recommend as rec

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
NIGHT = date(2026, 10, 12)  # Monday, lead 13 days


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    bar.set_bar(cur, hotel_uae, stay_date=NIGHT, room_type_code="DLX", bar_minor=50000, source="MANUAL")
    return hotel_uae


def _night(di: int, *, pace_gap: float = 0.0, comp_move: float = 0.0, otb: int = 10, events=None, d=NIGHT):
    return {"stay_date": d.isoformat(), "demand_index": di, "coverage": 0.75,
            "components": {"pace": {"otb": otb, "pace_gap": pace_gap, "base": 8.0},
                           "comp": {"comp_move": comp_move, "sold_out": 0, "comps": 5},
                           "events": events or [], "market_holidays": [], "season": None}}


def test_delta_clip_and_step_cap():
    d = rec.delta(100, 0.5, 0.5, max_step_pct=15)
    assert d["comp_move"] == 0.2 and d["raw"] == pytest.approx(0.1 + 0.2 + 0.1)
    assert d["clipped"] == 0.3 and d["delta"] == 0.15 and d["step_capped"]
    low = rec.delta(0, -0.5, -0.5, max_step_pct=15)
    assert low["clipped"] == -0.1 and low["delta"] == -0.1


def test_price_rounds_to_5_and_clamps():
    assert rec.price(50000, 0.0873, floor=None, ceiling=None, max_step_pct=15, currency="AED") == 54500
    # the ceiling wins over the delta, the floor over a cut
    assert rec.price(50000, 0.15, floor=40000, ceiling=52000, max_step_pct=15, currency="AED") == 52000
    assert rec.price(50000, -0.10, floor=48000, ceiling=None, max_step_pct=15, currency="AED") == 48000
    # rounding never carries the move past the step cap
    assert rec.price(50100, 0.05, floor=None, ceiling=None, max_step_pct=5, currency="AED") <= 50100 * 1.05
    assert rec.price(50100, 0.05, floor=None, ceiling=None, max_step_pct=5, currency="AED") % 500 == 0


def test_minimum_move_and_levers():
    assert not rec.worth_emitting(50000, 51000, "AED")      # AED 10 < 15
    assert rec.worth_emitting(50000, 51500, "AED")
    assert not rec.worth_emitting(80000, 82000, "AED")      # 2.5% < 3%
    hi = rec.levers(85, lead_days=10, otb_share=0.75, event_nights=3, capacity=40)
    assert [x["code"] for x in hi] == ["close_discounts", "min_stay_2", "reduce_ota_allotment", "direct_offer"]
    assert hi[2]["rooms"] == 4
    assert [x["code"] for x in rec.levers(85, lead_days=30, otb_share=0.9, event_nights=3, capacity=40)] == []
    assert [x["code"] for x in rec.levers(30, lead_days=5, otb_share=0.2, event_nights=0, capacity=40)] == \
        ["pre_arrival_offer", "open_closed_plans"]


def test_confidence_and_impact():
    assert rec.confidence(1.0, 1.0, 1.0, prior=False) == 1.0
    assert rec.confidence(1.0, 1.0, 1.0, prior=True) == 0.6
    assert rec.impact(10, 8, 5.0, 55000, 50000, 0.2) == 10 * 5000 * 0.8


def test_why_lint_and_template(fake_ai):
    assert rec.lint_why("Prices will increase, guaranteed") == ["will increase", "guaranteed", "guarantee"]
    f = rec.facts_for(_night(80, pace_gap=0.1, comp_move=0.12), currency="AED", bar_minor=50000, rec_minor=55000)
    text, src = rec.why_this_matters(f, hotel_slug="x", use_llm=False)
    assert src == "template" and not rec.lint_why(text) and "indicative comp prices are 12% above" in text.lower()
    fake_ai.push("Demand will increase so rates are guaranteed to rise.")
    text, src = rec.why_this_matters(f, hotel_slug="x")
    assert src == "template"
    fake_ai.push("GITEX runs 3 km away and indicative comp prices sit 12% above comparable nights.")
    assert rec.why_this_matters(f, hotel_slug="x")[1] == "llm"


def test_run_creates_supersedes_expires_and_clamps(cur, hotel, clock):
    guardrails.upsert_rows(cur, hotel, [{"room_type_code": "DLX", "floor": "450", "ceiling": "560"}], user_id=None)
    out = rec.run(cur, hotel, today=NOW.date(), nights=[_night(95, pace_gap=0.3, comp_move=0.3)], llm_budget=0)
    assert out["created"] == 1
    [r] = rec.list_recs(cur, hotel.slug)
    assert r["status"] == "PROPOSED" and r["recommended"] == "560.00" and r["action_id"]  # ceiling clamp
    assert r["impact_label"] == "Estimated opportunity" and not rec.lint_why(r["why"])
    # A small DI move keeps it; a move of more than 15 points supersedes it.
    assert rec.run(cur, hotel, today=NOW.date(), nights=[_night(85, pace_gap=0.3, comp_move=0.3)])["kept"] == 1
    out = rec.run(cur, hotel, today=NOW.date(), nights=[_night(60, pace_gap=0.1, comp_move=0.1)], llm_budget=0)
    assert out["superseded"] == 1
    rows = rec.list_recs(cur, hotel.slug)
    assert [x["status"] for x in rows] == ["PROPOSED", "SUPERSEDED"] and rows[0]["demand_index"] == 60
    from clefs.core import actions as core_actions

    assert core_actions.get_action(cur, hotel.slug, rows[1]["action_id"])["status"] == "cancelled"
    # The stay date passes undecided: EXPIRED.
    clock.freeze(NOW + timedelta(days=14))
    assert rec.run(cur, hotel, today=NIGHT + timedelta(days=1), nights=[])["expired"] == 1
    assert rec.list_recs(cur, hotel.slug, statuses=["EXPIRED"])[0]["id"] == rows[0]["id"]


def test_decide_approve_adjust_reject(cur, hotel, client_as):
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95, pace_gap=0.3, comp_move=0.3)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    from clefs.auth.deps import resolve_ctx
    from clefs.core import actions as core_actions

    owner = client_as("owner", hotel.slug)  # noqa: F841  (creates the membership)
    ctx = resolve_ctx(core_actions.SYSTEM, hotel.slug, cur=cur)
    with pytest.raises(Exception) as e:
        rec.decide(cur, ctx, r["id"], "adjust", recommended_minor=99999)
    assert e.value.code == "rate_out_of_bounds"
    out = rec.decide(cur, ctx, r["id"], "adjust", recommended_minor=53000)
    assert out["status"] == "ADJUSTED" and out["decided"] == "530.00"
    act = core_actions.get_action(cur, hotel.slug, r["action_id"])
    assert act["status"] == "approved" and act["params"]["recommended_minor"] == 53000
    with pytest.raises(Exception) as e:
        rec.decide(cur, ctx, r["id"], "reject")
    assert e.value.code == "recommendation_closed"


def test_expected_adr_prefers_the_decided_rate(cur, hotel):
    from clefs.modules.demand import api as demand_api

    bar.set_bar(cur, hotel, stay_date=NIGHT + timedelta(days=1), room_type_code="DLX", bar_minor=60000, source="MANUAL")
    out = demand_api.expected_adr(cur, hotel.slug, check_in=NIGHT, check_out=NIGHT + timedelta(days=2))
    assert out == {"amount": "550.00", "currency": "AED", "room_type_code": "DLX", "source": "bar", "confidence": 0.7}
    assert demand_api.expected_adr(cur, hotel.slug, check_in=NIGHT, check_out=NIGHT + timedelta(days=3)) is None
    rec.run(cur, hotel, today=NOW.date(), nights=[_night(95, pace_gap=0.3, comp_move=0.3)], llm_budget=0)
    [r] = rec.list_recs(cur, hotel.slug)
    from clefs.auth.deps import resolve_ctx
    from clefs.core import actions as core_actions

    rec.decide(cur, resolve_ctx(core_actions.SYSTEM, hotel.slug, cur=cur), r["id"], "adjust", recommended_minor=56000)
    out = demand_api.expected_adr(cur, hotel.slug, check_in=NIGHT, check_out=NIGHT + timedelta(days=2))
    assert out["amount"] == "580.00" and out["source"] == "mixed"
