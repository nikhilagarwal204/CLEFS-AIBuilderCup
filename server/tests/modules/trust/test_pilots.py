"""Pilot scorecards, checkpoints and the ROI report (03 spec 5.5)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from clefs.core import actions, signals
from clefs.core.actions import SYSTEM, ActionType
from clefs.modules.trust.pilots import checkpoints, store

KEY = "trust.test_win"
DUBAI = timezone(timedelta(hours=4))


@pytest.fixture
def win_type():
    t = ActionType(key=KEY, module="trust", label="Test win", counts_toward_verified_profit=True)
    actions.register_action_type(t)
    yield t
    actions.unregister_action_type(KEY)


def verified_win(cur, slug, minor, at, clock):
    """A verified ledger outcome of `minor` fils at hotel-local datetime `at`."""
    clock.freeze(at)
    a = actions.create_action(cur, slug, type=KEY, title="Recovered commission", actor=SYSTEM)
    actions.decide(cur, a["id"], "approve", actor=SYSTEM)
    actions.mark_done(cur, a["id"], actor=SYSTEM)
    return actions.record_verification(cur, a["id"], verified=True, outcome_minor=minor, currency="AED",
                                       counts_toward_verified_profit=True)


def _pilot(client, slug, **kw):
    body = {"name": "Profit Guard pilot", "modules": ["profit_guard"], "start_date": "2026-09-01",
            "end_date": "2026-09-30", "baseline_start": "2026-08-01", "baseline_end": "2026-08-31",
            "conversion_price": "1500", "terms": {"billing_period": "month"},
            "success_criteria": [{"metric_key": "core.verified_value", "comparator": "gte", "target": "5000",
                                  "weight": 2, "label": "Verified value"},
                                 {"metric_key": "core.verified_value", "comparator": "improve_pct", "target": "50",
                                  "label": "Better than August"}]}
    body.update(kw)
    return client.post(f"/hotels/{slug}/trust/pilots", json=body)


# ─── Criteria ───


def test_judge_criterion_comparators():
    c = {"metric_key": "m", "comparator": "gte", "target": "10", "weight": 1}
    assert store.judge_criterion(c, Decimal(10), None)["status"] == "met"
    assert store.judge_criterion({**c, "comparator": "lt"}, Decimal(10), None)["status"] == "not_met"
    up = store.judge_criterion({**c, "comparator": "improve_pct", "target": "20"}, Decimal(130), Decimal(100))
    assert up["met"] and up["change_pct"] == "30.0"
    down = store.judge_criterion({**c, "comparator": "reduce_pct", "target": "20"}, Decimal(70), Decimal(100))
    assert down["met"]
    assert store.judge_criterion({**c, "comparator": "improve_pct"}, Decimal(5), None)["status"] == "no_data"
    assert store.judge_criterion(c, None, None)["status"] == "no_data"
    assert store.score([{"weight": "2", "met": True, "status": "met"}, {"weight": "1", "met": False, "status": "not_met"},
                        {"weight": "5", "met": False, "status": "no_data"}]) == Decimal("0.6667")


# ─── Pilots ───


def test_create_captures_baseline_and_validates(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    verified_win(cur, slug, 200000, datetime(2026, 8, 10, 12, tzinfo=DUBAI), clock)
    clock.freeze(datetime(2026, 9, 2, 9, tzinfo=DUBAI))
    gm = client_as("gm")
    r = _pilot(gm, slug)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["status"] == "draft" and p["conversion_price_minor"] == 150000 and p["conversion_price"] == "1500.00"
    assert Decimal(p["baseline_metrics"]["core.verified_value"]) == Decimal("2000")
    bad = _pilot(gm, slug, success_criteria=[{"metric_key": "nope.metric", "comparator": "gte", "target": 1}])
    assert bad.status_code == 400 and bad.json()["code"] == "unknown_metric"
    assert _pilot(gm, slug, end_date="2026-08-01").json()["code"] == "invalid_dates"
    assert _pilot(gm, slug, baseline_end="2026-09-05").json()["code"] == "invalid_baseline"
    assert client_as("front_desk").get(f"/hotels/{slug}/trust/pilots").status_code == 403
    assert client_as("finance").get(f"/hotels/{slug}/trust/pilots").status_code == 200
    assert client_as("finance").post(f"/hotels/{slug}/trust/pilots/{p['id']}/status", json={"status": "active"}).status_code == 403
    cur.execute("SELECT 1 FROM audit_log WHERE hotel_slug = %s AND action = 'trust.pilot.create'", (slug,))
    assert cur.fetchone()


def test_status_machine_and_frozen_outcome(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    clock.freeze(datetime(2026, 9, 2, 9, tzinfo=DUBAI))
    gm = client_as("gm")
    pid = _pilot(gm, slug).json()["id"]
    assert gm.post(f"/hotels/{slug}/trust/pilots/{pid}/status", json={"status": "converted"}).json()["code"] == "illegal_transition"
    assert gm.post(f"/hotels/{slug}/trust/pilots/{pid}/status", json={"status": "active"}).json()["status"] == "active"
    verified_win(cur, slug, 600000, datetime(2026, 9, 10, 12, tzinfo=DUBAI), clock)
    clock.freeze(datetime(2026, 10, 1, 9, tzinfo=DUBAI))
    done = gm.post(f"/hotels/{slug}/trust/pilots/{pid}/status", json={"status": "completed"}).json()
    assert done["status"] == "completed" and done["outcome"]["criteria"][0]["value"] == "6000.00"
    assert gm.patch(f"/hotels/{slug}/trust/pilots/{pid}", json={"name": "x"}).json()["code"] == "pilot_frozen"
    # Later outcomes do not move a completed pilot's figures.
    verified_win(cur, slug, 900000, datetime(2026, 9, 29, 12, tzinfo=DUBAI), clock)
    rep = gm.get(f"/hotels/{slug}/trust/pilots/{pid}/report").json()
    assert rep["final"] is True and rep["criteria"][0]["value"] == "6000.00"


# ─── Checkpoints ───


def _active(cur, client, slug, clock, **kw):
    clock.freeze(datetime(2026, 9, 1, 9, tzinfo=DUBAI))
    pid = _pilot(client, slug, **kw).json()["id"]
    client.post(f"/hotels/{slug}/trust/pilots/{pid}/status", json={"status": "active"})
    return store.get_pilot(cur, slug, pid)


def _sigs(cur, slug, kind):
    return [s for s in signals.list_signals(cur, slug) if s["kind"] == kind]


def test_checkpoint_at_risk_after_two_bad_days_past_the_grace_week(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    p = _active(cur, client_as("gm"), slug, clock)
    out = checkpoints.checkpoint(cur, slug, p, day=date(2026, 9, 3))
    assert out["on_track"] is False and out["signal"] is None  # inside the first week
    checkpoints.checkpoint(cur, slug, p, day=date(2026, 9, 9))
    assert not _sigs(cur, slug, checkpoints.SIGNAL_AT_RISK)  # only one bad day past the grace week
    out = checkpoints.checkpoint(cur, slug, p, day=date(2026, 9, 10))
    assert out["signal"] == checkpoints.SIGNAL_AT_RISK
    sig = _sigs(cur, slug, checkpoints.SIGNAL_AT_RISK)[0]
    assert sig["section"] == "verified" and "Verified value" in sig["summary"]
    cur.execute('SELECT date, on_track FROM trs_pilot_checkpoints WHERE pilot_id = %s ORDER BY date', (p["id"],))
    assert [r["on_track"] for r in cur.fetchall()] == [False, False, False]


def test_checkpoint_criteria_met(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    verified_win(cur, slug, 100000, datetime(2026, 8, 10, 12, tzinfo=DUBAI), clock)
    p = _active(cur, client_as("gm"), slug, clock)
    verified_win(cur, slug, 600000, datetime(2026, 9, 5, 12, tzinfo=DUBAI), clock)
    out = checkpoints.checkpoint(cur, slug, p, day=date(2026, 9, 6))
    assert out["all_met"] and out["on_track"] and out["signal"] == checkpoints.SIGNAL_MET
    assert _sigs(cur, slug, checkpoints.SIGNAL_MET)[0]["dedupe_key"] == f"{checkpoints.SIGNAL_MET}:{p['id']}"


def test_checkpoint_job_runs_at_seven_and_completes_ended_pilots(cur, hotel_uae, client_as, clock, win_type, run_jobs):
    slug = hotel_uae.slug
    p = _active(cur, client_as("gm"), slug, clock)
    clock.freeze(datetime(2026, 9, 15, 7, 5, tzinfo=DUBAI))
    run_jobs(only="trust.pilot_checkpoint", scheduled=True)
    cur.execute("SELECT date FROM trs_pilot_checkpoints WHERE pilot_id = %s", (p["id"],))
    assert [r["date"] for r in cur.fetchall()] == [date(2026, 9, 14)]
    clock.freeze(datetime(2026, 10, 1, 7, 5, tzinfo=DUBAI))
    run_jobs(only="trust.pilot_checkpoint", scheduled=True)
    assert store.get_pilot(cur, slug, p["id"])["status"] == "completed"


# ─── Report ───


def test_report_verified_profit_per_available_room(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    p = _active(cur, client_as("owner"), slug, clock)
    verified_win(cur, slug, 3000000, datetime(2026, 9, 5, 12, tzinfo=DUBAI), clock)
    verified_win(cur, slug, 999999, datetime(2026, 8, 20, 12, tzinfo=DUBAI), clock)  # before the pilot: excluded
    cur.execute("INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, currency) "
                "SELECT %s, d::date, 10, 'AED' FROM generate_series('2026-09-01'::date, '2026-09-10'::date, '1 day') d",
                (slug,))
    clock.freeze(datetime(2026, 9, 30, 20, tzinfo=DUBAI))
    rep = client_as("finance").get(f"/hotels/{slug}/trust/pilots/{p['id']}/report").json()
    v = rep["verified"]
    rooms = hotel_uae.rooms_count
    assert v["label"] == "Clefs-verified profit" and v["minor"] == 3000000 and v["value"] == "30000.00"
    assert v["available_room_nights"] == 10 * 10 + 20 * rooms
    assert v["per_available_room_night_minor"] == round(3000000 / v["available_room_nights"])
    assert v["per_room_minor"] == round(3000000 / rooms)
    assert v["query"] == {"metric": "core.verified_value", "start": "2026-09-01", "end": "2026-09-30", "grain": "total"}
    roi = rep["roi"]
    assert roi["annual_price_minor"] == 150000 * 12 and roi["annualised_verified_minor"] == round(3000000 / 30 * 365)
    assert Decimal(roi["roi_multiple"]) > 1 and roi["payback_days"] == round(1800000 / (3000000 / 30))
    assert any("Occupancy data covers 10 of 30 days" in c for c in rep["caveats"])
    assert rep["criteria"][0]["status"] == "met" and rep["hotel"]["currency"] == "AED"
    assert all(r["query"]["metric"].startswith("upsell.") for r in rep["revenue_through_clefs"])


def test_scorecard_patch_and_manual_checkpoint_routes(cur, hotel_uae, client_as, clock, win_type):
    slug = hotel_uae.slug
    gm = client_as("gm")
    p = _active(cur, gm, slug, clock)
    clock.freeze(datetime(2026, 9, 12, 10, tzinfo=DUBAI))
    r = gm.patch(f"/hotels/{slug}/trust/pilots/{p['id']}", json={"conversion_price": "2000", "name": "PG pilot"})
    assert r.json()["conversion_price_minor"] == 200000 and r.json()["name"] == "PG pilot"
    cp = gm.post(f"/hotels/{slug}/trust/pilots/{p['id']}/checkpoint").json()
    assert cp["date"] == "2026-09-12" and cp["on_track"] is False
    card = client_as("finance").get(f"/hotels/{slug}/trust/pilots/{p['id']}").json()
    assert card["scorecard"]["period_end"] == "2026-09-12" and len(card["checkpoints"]) == 1
    assert card["scorecard"]["criteria"][1]["status"] == "no_data"  # no August outcome: no baseline to improve on
    assert client_as("gm").get(f"/hotels/{slug}/trust/pilots/99999").json()["code"] == "pilot_not_found"
