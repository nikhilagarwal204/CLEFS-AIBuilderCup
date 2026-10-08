"""Engine jobs through the real scheduler, the event-change edge case (a
cancelled show after a proposal), and dr.pace.behind."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from clefs import db as clefs_db
from clefs.core import signals as core_signals
from clefs.modules.demand import bar, events, recommend as rec

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))


def _setup(cur, hotel_uae):
    from clefs.core import hotels
    from clefs.modules.demand import curated

    curated.sync(cur, force=True)
    cur.execute("UPDATE hotels SET lat = 25.2697, lng = 55.3095 WHERE slug = %s", (hotel_uae.slug,))
    hotel = hotels.get_hotel(cur, hotel_uae.slug, fresh=True)
    for i in range(10):
        bar.set_bar(cur, hotel, stay_date=date(2026, 10, 10) + timedelta(days=i), room_type_code="DLX",
                    bar_minor=40000, source="MANUAL")
    eid = events.ingest(cur, {"title": "Zephyr Widget Summit", "category": "CONFERENCE", "start": date(2026, 10, 13),
                              "end": date(2026, 10, 15), "venue_codes": ["DWTC"], "attendance": 150000},
                        source="CURATED", external_id="zw:j")["event_id"]
    return hotel, eid


def test_engine_jobs_are_registered_and_run(cur, clock, run_jobs, hotel_uae):
    from clefs import jobs
    from clefs.modules.demand import jobs as engine_jobs  # noqa: F401

    names = {j.name for j in jobs.registered_jobs()}
    assert {"dr.relevance", "dr.calibrate", "dr.demand_index", "dr.recommend", "dr.graph_actions",
            "dr.measure_outcomes", "dr.weekly_top5", "dr.ghc_deadline", "dr.leads_purge"} <= names
    clock.freeze(NOW)
    _setup(cur, hotel_uae)
    # Tuesday 29 Sep, Dubai time: each slot at its own minute, in the nightly order.
    for hhmm, job in (("00:06", "dr.relevance"), ("00:16", "dr.demand_index"), ("00:31", "dr.recommend"),
                      ("00:46", "dr.graph_actions"), ("07:01", "dr.ghc_deadline")):
        h, m = map(int, hhmm.split(":"))
        clock.freeze(datetime(2026, 9, 29, h, m, tzinfo=ZoneInfo("Asia/Dubai")))
        report = run_jobs([job], scheduled=True)
        assert report.scheduled_failed == 0, (job, report.errors)
    clock.freeze(datetime(2026, 9, 30, 3, 36, tzinfo=timezone.utc))
    assert run_jobs(["dr.leads_purge"], scheduled=True).scheduled_failed == 0
    n = clefs_db.fetch_one("SELECT count(*) AS n FROM dr_demand_days WHERE hotel_slug = %s", (hotel_uae.slug,), cur=cur)
    assert n["n"] == 120
    assert clefs_db.fetch_one("SELECT count(*) AS n FROM dr_rate_recommendations", cur=cur)["n"] >= 1


def test_cancelled_event_supersedes_open_proposals(cur, clock, hotel_uae):
    from clefs.modules.demand import demand_index as di
    from clefs.modules.demand import relevance as rel

    clock.freeze(NOW)
    hotel, eid = _setup(cur, hotel_uae)
    rel.compute(cur, hotel, today=NOW.date())
    nights = di.compute(cur, hotel, start=NOW.date(), days=30)
    rec.run(cur, hotel, today=NOW.date(), nights=nights, llm_budget=0)
    open_ = [r for r in rec.list_recs(cur, hotel.slug, statuses=["PROPOSED"]) if eid in r["driver_event_ids"]]
    assert open_
    events.ingest(cur, {"title": "Zephyr Widget Summit", "category": "CONFERENCE", "start": date(2026, 10, 13),
                        "end": date(2026, 10, 15), "venue_codes": ["DWTC"], "attendance": 150000,
                        "status": "CANCELLED"}, source="CURATED", external_id="zw:j")
    after = {r["id"]: r for r in rec.list_recs(cur, hotel.slug)}
    assert all(after[r["id"]]["status"] == "SUPERSEDED" and after[r["id"]]["status_reason"] == "event_changed"
               for r in open_)
    kinds = [s["kind"] for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"])]
    assert "dr.event.changed" in kinds


def test_pace_behind_signal(cur, clock, hotel_uae):
    clock.freeze(NOW)
    hotel, _ = _setup(cur, hotel_uae)
    night = {"stay_date": "2026-10-12", "demand_index": 40, "coverage": 0.5,
             "components": {"pace_z": -2.0, "pace": {"otb": 2, "base": 8.0, "lead": 13, "pace_gap": -0.46}}}
    out = rec.raise_pace_signals(cur, hotel, [night], today=NOW.date())
    assert out["behind"] == 1
    [s] = [s for s in core_signals.list_signals(cur, hotel.slug, modules=["demand"]) if s["kind"] == "dr.pace.behind"]
    assert s["section"] == "money_available" and s["impact_minor"] == int(6 * 40000 * 0.88)
    # Back on pace: resolved on the next run.
    assert rec.raise_pace_signals(cur, hotel, [], today=NOW.date())["resolved"] == 1
