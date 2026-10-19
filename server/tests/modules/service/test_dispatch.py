"""Smart dispatch (05 spec, decisions 5 and 6): create_job and dispatch_info,
the one "free staff" definition (roster, department, on duty, below
max_open) and its ranking (fewest open jobs, floor distance, longest idle),
the ETA and where it is stored, and the manager's "Choose staff" list."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.core.hotels import get_hotel
from clefs.modules.service import api, routing, state
from clefs.modules.service import constants as C
from tests.modules.service.desk_helpers import roster, staff

DXB = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=DXB)

INFO_KEYS = {"request_id", "tracking_token", "item_label", "quantity", "room", "department_key", "department_label",
             "staff_id", "staff_first_name", "eta_minutes", "eta_at", "created_at", "accepted_at", "state", "status",
             "proof_status", "guest_confirmed_at", "closed_at"}


def _job(cur, slug, room="204", rtype="towels", **kw):
    args = {"quantity": 2, "description_en": "Two bath towels please", "original_text": "Dalawang tuwalya po",
            "guest_language": "fil"}
    args.update(kw)
    return api.create_job(cur, slug, room=room, request_type=rtype, **args)


def _maria_and_priya(cur, slug):
    maria = staff(cur, slug, "Maria Santos", languages=("fil", "en"))
    priya = staff(cur, slug, "Priya Sharma", languages=("hi", "en"))
    roster(cur, slug, maria, floors=("1", "2", "3", "4"))
    roster(cur, slug, priya, floors=("5", "6", "7", "8"))
    return maria, priya


# ─── create_job and dispatch_info ───


def test_create_job_dispatches_the_nearest_free_attendant_with_an_eta(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    maria, _priya = _maria_and_priya(cur, s)
    out = _job(cur, s, mood="annoyed", frustration=0.4, session_id="sess-1", priority="high",
               idempotency_key="cc:tool:abc")
    assert INFO_KEYS <= set(out) and out["merged"] is False and out["replayed"] is False
    assert out["staff_id"] == maria and out["staff_first_name"] == "Maria"
    assert (out["item_label"], out["quantity"], out["room"]) == ("Towels", 2, "204")
    assert (out["department_key"], out["department_label"]) == ("housekeeping", "Housekeeping")
    assert out["eta_minutes"] == 5 and out["eta_at"] == out["created_at"] + 5 * 60_000
    assert out["accepted_at"] is None and out["state"] == "assigned" and len(out["tracking_token"]) == 22
    row = state.load(cur, s, out["request_id"])
    # the concierge's English is what staff read; the guest's words and language ride along
    assert row["description"] == "Two bath towels please" and row["original_text"] == "Dalawang tuwalya po"
    assert row["translated"] is True and row["language"] == "fil" and row["priority"] == "high"
    assert (row["mood"], round(row["frustration"], 2), row["session_id"]) == ("annoyed", 0.4, "sess-1")
    assert row["needs_proof"] is True and row["source"] == "web"
    cur.execute("SELECT payload FROM svc_request_events WHERE request_id = %s AND action = 'assign'", (row["id"],))
    payload = cur.fetchone()["payload"]
    assert payload["pool"] == "free" and payload["floor_distance"] == 0 and payload["eta_minutes"] == 5
    again = _job(cur, s, idempotency_key="cc:tool:abc")
    assert again["request_id"] == out["request_id"] and again["replayed"] is True
    assert api.request_status_for_guest(cur, s, again["tracking_token"])["id"] == out["request_id"]


def test_dispatch_info_reads_back_and_nobody_assigned_means_no_staff(cur, hotel_uae):
    s = hotel_uae.slug
    out = _job(cur, s, rtype="make_up_room", quantity=None, description_en="Please clean the room")
    assert out["state"] == "new" and out["staff_id"] is None and out["staff_first_name"] is None
    assert out["eta_minutes"] is None and out["item_label"] == "Make up room"
    info = api.dispatch_info(cur, s, out["request_id"])
    assert INFO_KEYS <= set(info) and info["tracking_token"] != out["tracking_token"]
    assert api.request_status_for_guest(cur, s, info["tracking_token"])["id"] == out["request_id"]
    from clefs.core.errors import ClefsError

    with pytest.raises(ClefsError):
        api.dispatch_info(cur, "hotel-nandan", out["request_id"])  # another hotel's request


def test_a_shared_device_holds_the_job_with_the_team_eta(cur, hotel_uae):
    s = hotel_uae.slug
    cur.execute("INSERT INTO devices (hotel_slug, name, kind, departments) VALUES (%s, 'HK tablet', 'department', %s)",
                (s, ["Housekeeping"]))
    out = _job(cur, s, rtype="water", quantity=None)
    assert out["staff_id"] is None and out["state"] == "assigned" and out["eta_minutes"] == C.ETA_BASE_MIN["water"]


def test_bad_priority_is_refused_and_concierge_files_as_web(cur, hotel_uae):
    with pytest.raises(ValueError):
        _job(cur, hotel_uae.slug, priority="whenever")
    out = _job(cur, hotel_uae.slug, channel="voice")
    assert state.load(cur, hotel_uae.slug, out["request_id"])["source"] == "voice"


# ─── Free staff ───


def test_free_staff_is_rostered_in_department_on_duty_and_below_cap(cur, hotel_uae):
    s = hotel_uae.slug
    hotel = get_hotel(cur, s)
    free = staff(cur, s, "Free Attendant")
    roster(cur, s, free, floors=("2",))
    off = staff(cur, s, "Off Duty")
    roster(cur, s, off, floors=("2",))
    cur.execute("UPDATE staff_members SET off_duty_at = NOW() WHERE id = %s", (off,))
    full = staff(cur, s, "Full", max_open=1)
    roster(cur, s, full, floors=("2",))
    sup = staff(cur, s, "Shift Supervisor")
    roster(cur, s, sup, floors=("2",), role="supervisor")
    other = staff(cur, s, "Engineer", "Maintenance")
    roster(cur, s, other, floors=("2",), dept="engineering")
    staff(cur, s, "Not On Shift")  # no roster row
    busy = api.create_request(cur, s, room="999", type="linen", description="x", source="phone_desk")
    state.transition(cur, s, busy["id"], "reassign", state.SYSTEM, target={"staff_id": full})
    draft = {"department_key": "housekeeping", "floor": "2", "room": "201", "request_type_key": "towels",
             "language": "en", "severity": "L3", "sensitivity": "none"}
    assert [c["staff_id"] for c in routing.free_staff(cur, hotel, draft)] == [free]
    # the same person back on duty after a new PIN shift: the stale switch no longer counts
    cur.execute("INSERT INTO devices (hotel_slug, name, kind) VALUES (%s, 'phone', 'personal') RETURNING id", (s,))
    dev = cur.fetchone()["id"]
    cur.execute("INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id, started_at) VALUES (%s, %s, %s, NOW() + INTERVAL '1 second')",
                (dev, s, off))
    assert {c["staff_id"] for c in routing.free_staff(cur, hotel, draft)} == {free, off}


def test_ranking_is_fewest_open_jobs_then_floor_distance_then_longest_idle(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    hotel = get_hotel(cur, s)
    near, far, idle = staff(cur, s, "Near"), staff(cur, s, "Far"), staff(cur, s, "Idle")
    roster(cur, s, near, floors=("2",))
    roster(cur, s, far, floors=("5",))
    roster(cur, s, idle, floors=("2",))
    finished = int((T0 - timedelta(minutes=5)).timestamp() * 1000)
    cur.execute(
        """INSERT INTO staff_requests (hotel_slug, room, type, description, category, priority, status, "timestamp",
               assigned_to, resolved_at, assigned_staff_id, state) VALUES (%s, '202', 'Towel', 'x', 'Housekeeping', 'low',
               'completed', %s, 'Near', %s, %s, 'completed')""",
        (s, finished - 60_000, finished, near),
    )
    draft = {"department_key": "housekeeping", "floor": "2", "room": "204", "request_type_key": "towels", "language": "en"}
    ranked = routing.free_staff(cur, hotel, draft)
    # Near and Idle are both on floor 2 with nothing open: Idle never finished a job (longest idle)
    assert [c["name"] for c in ranked] == ["Idle", "Near", "Far"]
    assert ranked[2]["floor_distance"] == 3 and ranked[2]["eta_minutes"] == 5 + 3
    assert ranked[1]["idle_min"] == 5 and ranked[0]["idle_min"] is None
    first = api.create_request(cur, s, room="204", type="towels", description="towels", source="phone_desk")
    assert first["assigned_staff_id"] == idle
    second = api.create_request(cur, s, room="205", type="linen", description="sheets", source="phone_desk")
    assert second["assigned_staff_id"] == near  # Idle has one open job now: fewest open jobs first


def test_eta_formula_and_its_cap():
    assert routing.eta_minutes("towels", 0, 0) == 5
    assert routing.eta_minutes("amenities", 2, 3) == 7 + 6 + 3
    assert routing.eta_minutes("make_up_room", 0, None) == 20
    assert routing.eta_minutes("ird", 0, 0) == 25 and routing.eta_minutes("maintenance", 0, 0) == 15
    assert routing.eta_minutes("concierge", 0, 0) == 10  # other: 10
    assert routing.eta_minutes("ird", 9, 9) == 45  # capped
    assert routing.floor_distance("2", ["5", "6"]) == 3 and routing.floor_distance("G", ["1"]) is None


def test_eta_counts_the_queue_ahead_and_is_stored_on_every_change_of_holder(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    maria, priya = _maria_and_priya(cur, s)
    a = _job(cur, s, room="204", idempotency_key="k1")
    cur.execute("UPDATE staff_members SET max_open = 1 WHERE id = %s", (priya,))
    b = _job(cur, s, room="203", rtype="linen", idempotency_key="k2")
    # Maria already holds one: Priya is free with nothing open, three floors away
    assert b["staff_id"] == priya and b["eta_minutes"] == 5 + 3  # room 203 is on floor 2, her floors 5-8
    c = _job(cur, s, room="202", rtype="amenities", idempotency_key="k3", description_en="Shampoo and soap")
    assert c["staff_id"] == maria and c["eta_minutes"] == 7 + 3  # one job ahead (Priya is at her cap)
    moved = state.transition(cur, s, a["request_id"], "reassign", state.SYSTEM, target={"staff_id": priya})["row"]
    assert moved["eta_minutes"] == 5 + 3 + 3  # one job ahead, three floors
    gone = state.transition(cur, s, a["request_id"], "unassign", state.SYSTEM)["row"]
    assert gone["eta_minutes"] is None and gone["eta_at"] is None


def test_pick_falls_back_to_the_old_pools_without_a_roster_and_skips_off_duty(cur, hotel_uae):
    s = hotel_uae.slug
    online = staff(cur, s, "Online")
    cur.execute("UPDATE staff_members SET is_online = TRUE WHERE id = %s", (online,))
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    assert row["assigned_staff_id"] == online
    cur.execute("SELECT payload FROM svc_request_events WHERE request_id = %s AND action = 'assign'", (row["id"],))
    assert cur.fetchone()["payload"]["pool"] == "dept_legacy"
    cur.execute("UPDATE staff_members SET off_duty_at = NOW() WHERE id = %s", (online,))
    nxt = api.create_request(cur, s, room="102", type="linen", description="linen", source="phone_desk")
    assert nxt["assigned_staff_id"] is None and nxt["state"] == "new"


# ─── The manager's "Choose staff" list ───


def test_choose_staff_lists_free_people_first_and_why_the_rest_are_not(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    maria, priya = _maria_and_priya(cur, s)
    sup = staff(cur, s, "Sup", role_key="supervisor")
    roster(cur, s, sup, role="supervisor")
    desk = staff(cur, s, "Desk Agent", "Front Desk")
    out = _job(cur, s)
    r = client_as("dept_head").get(f"/hotels/{s}/service/requests/{out['request_id']}/free-staff")
    assert r.status_code == 200, r.text
    body = r.json()
    # the same order auto-assignment used; the holder is marked, not hidden
    assert [(c["staff_id"], c["current"]) for c in body["free"]] == [(maria, True), (priya, False)]
    assert {c["staff_id"]: c["reason"] for c in body["others"]} == {sup: "shift_role", desk: "other_department"}
    assert body["free"][1]["eta_minutes"] == 5 + 3 and body["free"][1]["floor_distance"] == 3
    cur.execute("UPDATE staff_members SET max_open = 1 WHERE id = %s", (priya,))
    _job(cur, s, room="205", rtype="linen", idempotency_key="k-full")  # Priya's one job
    again = client_as("dept_head").get(f"/hotels/{s}/service/requests/{out['request_id']}/free-staff").json()
    assert {c["staff_id"]: c["reason"] for c in again["others"]}[priya] == "at_capacity"
