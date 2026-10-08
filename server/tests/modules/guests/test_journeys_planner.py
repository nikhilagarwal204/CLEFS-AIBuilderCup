"""The journeys planner (04 spec 3.1 and 3.3): stay events to plan rows."""

from __future__ import annotations

from datetime import date

from clefs.core import clock as clock_mod
from clefs.modules.guests.journeys import planner

from tests.modules.guests.test_journeys_support import at, event, make_res, plans, set_res

CI, CO = date(2026, 10, 20), date(2026, 10, 23)


def test_created_plans_pre_stay_steps_in_hotel_time(cur, hotel_uae, clock):
    clock.freeze(at(2026, 9, 1, 10, 5))
    s = hotel_uae.slug
    rid = make_res(cur, s, booked_at=at(2026, 9, 1, 10))
    out = event(cur, s, rid, "created")
    assert out["planned"] == 5
    p = plans(cur, s, rid)
    assert set(p) == {("j1", 1), ("j2", 1), ("j3", 1), ("j4", 1), ("j6_pre", 1)}
    assert p[("j1", 1)]["due_at"] == at(2026, 9, 1, 10, 5)
    assert p[("j1", 1)]["valid_until"] == at(2026, 10, 19, 21)
    assert p[("j2", 1)]["due_at"] == at(2026, 10, 13, 10)
    assert p[("j2", 1)]["valid_until"] == at(2026, 10, 18)
    assert p[("j3", 1)]["due_at"] == at(2026, 10, 17, 11)
    assert p[("j4", 1)]["due_at"] == at(2026, 10, 19, 16)
    assert p[("j4", 1)]["valid_until"] == at(2026, 10, 20, 15)
    assert p[("j6_pre", 1)]["due_at"] == at(2026, 10, 23, 9)
    assert all(r["status"] == "planned" for r in p.values())


def test_redelivered_event_is_a_no_op(cur, hotel_uae, clock):
    clock.freeze(at(2026, 9, 1, 10, 5))
    s = hotel_uae.slug
    rid = make_res(cur, s)
    ev = {"id": 777_001, "event": "created", "hotel_slug": s, "reservation_id": rid, "occurred_at": None, "changes": {}}
    planner.handle_event(cur, ev)
    assert planner.handle_event(cur, ev) == {"duplicate": True}
    assert len(plans(cur, s, rid)) == 5


def test_not_sent_when_rules_are_written_as_skips(cur, hotel_uae, clock):
    clock.freeze(at(2026, 10, 12, 10))
    s = hotel_uae.slug
    walkin = make_res(cur, s, code="WALKIN", booked_at=at(2026, 10, 12, 9))
    event(cur, s, walkin, "created")
    p = plans(cur, s, walkin)
    assert p[("j1", 1)]["skip_reason"] == "walkin"
    assert p[("j2", 1)]["skip_reason"] == "lead_time"  # 8 days of lead <= 10
    late = make_res(cur, s, check_in=date(2026, 10, 13), check_out=date(2026, 10, 14), booked_at=at(2026, 10, 12, 20))
    clock.freeze(at(2026, 10, 12, 20, 1))
    event(cur, s, late, "created")
    p = plans(cur, s, late)
    assert p[("j1", 1)]["skip_reason"] == "late_booking"
    # booked after J4's usual slot: 09:00 on arrival day instead
    assert p[("j4", 1)]["due_at"] == at(2026, 10, 13, 9)
    group = make_res(cur, s, segment="GROUP", booked_at=at(2026, 9, 1))
    event(cur, s, group, "created")
    assert plans(cur, s, group)[("j3", 1)]["skip_reason"] == "segment"


def test_dates_changed_replans_and_revives_j2(cur, hotel_uae, clock):
    clock.freeze(at(2026, 10, 12, 10))
    s = hotel_uae.slug
    rid = make_res(cur, s, booked_at=at(2026, 10, 12, 9))
    event(cur, s, rid, "created")
    assert plans(cur, s, rid)[("j2", 1)]["skip_reason"] == "lead_time"
    set_res(cur, rid, check_in=date(2026, 11, 20), check_out=date(2026, 11, 22), nights=2)
    out = event(cur, s, rid, "dates_changed", check_in=["2026-10-20", "2026-11-20"], check_out=["2026-10-23", "2026-11-22"])
    assert out["revived"] == 1
    p = plans(cur, s, rid)
    assert p[("j2", 1)]["status"] == "planned" and p[("j2", 1)]["due_at"] == at(2026, 11, 13, 10)
    assert p[("j3", 1)]["due_at"] == at(2026, 11, 17, 11) and p[("j3", 1)]["plan_version"] == 2


def test_cancellation_cancels_unsent_but_keeps_a_sent_j3(cur, hotel_uae, clock):
    clock.freeze(at(2026, 9, 1, 10, 5))
    s = hotel_uae.slug
    rid = make_res(cur, s)
    event(cur, s, rid, "created")
    cur.execute("UPDATE gst_journey_plans SET status = 'sent' WHERE reservation_id = %s AND step = 'j3'", (rid,))
    event(cur, s, rid, "cancelled", status=["CONFIRMED", "CANCELLED"])
    all_rows = plans(cur, s, rid, live=False)
    assert all_rows[("j3", 1)]["status"] == "sent"
    assert {r["status"] for k, r in all_rows.items() if k[0] != "j3"} == {"cancelled"}


def test_stay_lifecycle_plans_j5_then_departure_steps(cur, hotel_uae, clock):
    clock.freeze(at(2026, 10, 20, 15, 30))
    s = hotel_uae.slug
    rid = make_res(cur, s, status="IN_HOUSE", checked_in_at=at(2026, 10, 20, 15, 30))
    event(cur, s, rid, "created")
    event(cur, s, rid, "checked_in", status=["CONFIRMED", "IN_HOUSE"])
    j5 = plans(cur, s, rid)[("j5", 1)]
    assert j5["due_at"] == at(2026, 10, 21, 9, 30)  # +18 h, already inside 08:00-21:00
    assert j5["valid_until"] == at(2026, 10, 23, 12)
    clock.freeze(at(2026, 10, 21, 7))
    set_res(cur, rid, status="CHECKED_OUT", checked_out_at=at(2026, 10, 21, 7))
    event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"], occurred_at=at(2026, 10, 21, 7))
    p = plans(cur, s, rid, live=False)
    assert p[("j5", 1)]["status"] == "cancelled"
    assert p[("j7", 1)]["due_at"] == at(2026, 10, 23, 11)
    assert p[("j8", 1)]["due_at"] == at(2026, 11, 22, 11) and p[("j8", 2)]["due_at"] == at(2026, 12, 22, 11)
    assert p[("j6_post", 1)]["due_at"] == at(2026, 10, 21, 7)


def test_three_reservation_chain_asks_once_after_final_checkout(cur, hotel_uae, clock):
    clock.freeze(at(2026, 9, 1, 10, 5))
    s = hotel_uae.slug
    a = make_res(cur, s, check_in=date(2026, 10, 20), check_out=date(2026, 10, 22))
    set_res(cur, a, stay_chain_id=a)
    event(cur, s, a, "created")
    b = make_res(cur, s, check_in=date(2026, 10, 22), check_out=date(2026, 10, 24), chain=a)
    event(cur, s, b, "created")
    c = make_res(cur, s, check_in=date(2026, 10, 24), check_out=date(2026, 10, 26), chain=a)
    event(cur, s, c, "created")
    assert plans(cur, s, b)[("j1", 1)]["skip_reason"] == "chain_continued"
    assert plans(cur, s, c)[("j2", 1)]["skip_reason"] == "chain_continued"
    assert ("j6_pre", 1) not in plans(cur, s, a) and ("j6_pre", 1) not in plans(cur, s, b)
    assert plans(cur, s, c)[("j6_pre", 1)]["status"] == "planned"
    for rid in (a, b):
        set_res(cur, rid, status="CHECKED_OUT")
        assert event(cur, s, rid, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])["planned"] == 0
    set_res(cur, c, status="CHECKED_OUT")
    assert event(cur, s, c, "checked_out", status=["IN_HOUSE", "CHECKED_OUT"])["planned"] == 4
    assert ("j7", 1) in plans(cur, s, c)


def test_reconcile_infers_checkin_time(cur, hotel_uae, clock):
    clock.freeze(at(2026, 10, 20, 18))
    s = hotel_uae.slug
    rid = make_res(cur, s, status="IN_HOUSE")
    out = planner.reconcile(cur, s, today=date(2026, 10, 20))
    assert out["reservations"] >= 1
    j5 = plans(cur, s, rid)[("j5", 1)]
    assert j5["checkin_time_inferred"] is True
    assert j5["due_at"] == at(2026, 10, 21, 9)  # 15:00 + 18 h
    assert clock_mod.now() == at(2026, 10, 20, 18)
