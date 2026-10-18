"""occupancy_daily (derived from stayed nights; manual overrides win),
occupancy_by_floor_daily, OTB snapshots and baseline exclusions (02 spec 2.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import stays
from clefs.core.stays import occupancy as occ
from clefs.core.stays import otb

NOW = datetime(2025, 10, 20, 8, 0, tzinfo=timezone.utc)  # business date 2025-10-20 in Dubai
D = date.fromisoformat


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae.slug


def book(cur, slug, pms_id, ci, co, *, room="101", status="CHECKED_OUT", rev=None, channel="BOOKING", rooms=1):
    data = {"pms_res_id": pms_id, "check_in": ci, "check_out": co, "room_number": room, "status": status,
            "channel_code": channel, "rooms_count": rooms, "room_revenue_minor": rev, "guest_name": pms_id}
    return stays.upsert_reservation(cur, slug, data)["id"]


def test_derived_occupancy_counts_stayed_nights_only(cur, slug):
    book(cur, slug, "O1", "2025-10-15", "2025-10-17", rev=80000)             # floor 1
    book(cur, slug, "O2", "2025-10-16", "2025-10-18", room="201", rev=60000)  # floor 2
    book(cur, slug, "O3", "2025-10-16", "2025-10-17", room="202", status="NO_SHOW", rev=50000)
    book(cur, slug, "O4", "2025-10-16", "2025-10-17", room="203", status="CANCELLED", rev=50000)
    book(cur, slug, "O5", "2025-10-19", "2025-10-23", room="501", status="IN_HOUSE", rev=40000)
    out = occ.derive_occupancy(cur, slug, D("2025-10-15"), D("2025-10-21"))
    assert out["days"] == 7
    rows = {r["date"]: r for r in occ.get_occupancy(cur, slug, D("2025-10-15"), D("2025-10-21"))}
    assert rows[D("2025-10-16")]["rooms_occupied"] == 2 and rows[D("2025-10-16")]["room_revenue_minor"] == 70000
    assert rows[D("2025-10-16")]["arrivals"] == 1 and rows[D("2025-10-17")]["departures"] == 1
    assert rows[D("2025-10-16")]["rooms_available"] == 13 and rows[D("2025-10-16")]["occupancy"] == "0.1538"
    assert rows[D("2025-10-16")]["adr_minor"] == 35000 and rows[D("2025-10-16")]["revpar_minor"] == 5385
    assert rows[D("2025-10-16")]["is_complete"] and not rows[D("2025-10-20")]["is_complete"]
    # IN_HOUSE: nights up to the business date count; tomorrow's does not yet.
    assert rows[D("2025-10-20")]["rooms_occupied"] == 1 and rows[D("2025-10-21")]["rooms_occupied"] == 0
    floors = {(r["date"], r["floor"]): r["rooms_occupied"] for r in occ.occupancy_by_floor(cur, slug, D("2025-10-16"), D("2025-10-16"))}
    assert floors == {(D("2025-10-16"), "1"): 1, (D("2025-10-16"), "2"): 1}


def test_manual_override_wins_over_derivation_and_pms(cur, slug):
    book(cur, slug, "O6", "2025-10-10", "2025-10-11", rev=50000)
    day = D("2025-10-10")
    row = occ.set_occupancy(cur, slug, day, {"rooms_occupied": 9, "room_revenue_minor": 450000})
    assert row["source"] == "MANUAL" and row["rooms_occupied"] == 9 and row["arrivals"] == 1
    occ.derive_occupancy(cur, slug, day, day)
    assert occ.get_occupancy(cur, slug, day, day)[0]["rooms_occupied"] == 9
    assert occ.set_occupancy(cur, slug, day, {"rooms_occupied": 3}, source="PMS") is None
    cur.execute("SELECT action FROM audit_log WHERE entity_type = 'occupancy_daily' ORDER BY id")
    assert [r["action"] for r in cur.fetchall()] == ["stays.occupancy_override"]
    back = occ.clear_override(cur, slug, day)
    assert back["source"] == "DERIVED" and back["rooms_occupied"] == 1
    pms = occ.set_occupancy(cur, slug, day, {"rooms_occupied": 2}, source="PMS")
    assert pms["source"] == "PMS"
    occ.derive_occupancy(cur, slug, day, day)
    assert occ.get_occupancy(cur, slug, day, day)[0]["rooms_occupied"] == 2  # PMS beats DERIVED
    with pytest.raises(Exception):
        occ.set_occupancy(cur, slug, day, {"rooms_occupied": 99})


def test_otb_snapshot_photographs_live_future_nights(cur, slug):
    book(cur, slug, "P1", "2025-10-25", "2025-10-27", status="CONFIRMED", rev=100000)
    book(cur, slug, "P2", "2025-10-26", "2025-10-27", room="102", status="CONFIRMED", rev=30000, channel="DIRECT_WEB")
    book(cur, slug, "P3", "2025-10-26", "2025-10-27", room="103", status="CANCELLED", rev=30000)
    book(cur, slug, "P4", "2025-10-19", "2025-10-22", room="104", status="IN_HOUSE", rev=90000)
    n = otb.snapshot_otb(cur, slug, horizon_days=10)
    assert n == 11
    rows = {r["stay_date"]: r for r in otb.get_otb(cur, slug, snapshot_date=D("2025-10-20"), start=D("2025-10-20"),
                                              end=D("2025-10-30"))}
    assert rows[D("2025-10-26")]["rooms_otb"] == 2 and rows[D("2025-10-26")]["revenue_otb_minor"] == 80000
    assert rows[D("2025-10-26")]["by_channel"] == {"BOOKING": 1, "DIRECT_WEB": 1}
    assert rows[D("2025-10-21")]["rooms_otb"] == 1 and rows[D("2025-10-23")]["rooms_otb"] == 0
    assert otb.snapshot_otb(cur, slug, horizon_days=10) == 11  # same day: replaced, not doubled
    assert otb.latest_snapshot_date(cur, slug) == D("2025-10-20")
    assert [p["days_before"] for p in otb.pace(cur, slug, D("2025-10-26"))] == [6]


def test_baseline_exclusions_include_the_global_conflict_row(cur, slug):
    rows = occ.baseline_exclusions(cur, slug)
    assert any(r["hotel_slug"] is None and r["from_date"] == D("2026-03-01") and r["to_date"] == D("2026-07-31")
               for r in rows)
    assert occ.is_excluded(cur, slug, D("2026-05-01")) and not occ.is_excluded(cur, slug, D("2025-05-01"))
    own = occ.add_exclusion(cur, slug, D("2025-01-10"), D("2025-01-20"), "renovation")
    assert occ.is_excluded(cur, slug, D("2025-01-15"))
    glob = next(r for r in rows if r["hotel_slug"] is None)
    assert occ.remove_exclusion(cur, slug, glob["id"]) is False  # global rows are not the hotel's
    assert occ.remove_exclusion(cur, slug, own["id"]) is True


def test_scheduled_derive_and_snapshot_jobs(cur, slug, run_jobs, clock):
    from clefs import jobs

    names = {j.name: j for j in jobs.registered_jobs()}
    assert {"stays.occupancy_derive", "stays.otb_snapshot", "stays.contact_purge"} <= set(names)
    book(cur, slug, "J1", "2025-10-19", "2025-10-22", room="105", status="IN_HOUSE", rev=90000)
    clock.freeze(datetime(2025, 10, 21, 0, 40, tzinfo=timezone.utc))  # 04:40 Dubai
    run_jobs("stays.occupancy_derive", scheduled=True)
    day = occ.get_occupancy(cur, slug, D("2025-10-21"), D("2025-10-21"))
    assert day and day[0]["rooms_occupied"] == 1  # the 21st's night started at the business-day cutoff
    clock.freeze(datetime(2025, 10, 21, 19, 56, tzinfo=timezone.utc))  # 23:56 Dubai
    run_jobs("stays.otb_snapshot", scheduled=True)
    assert otb.latest_snapshot_date(cur, slug) == D("2025-10-21")
