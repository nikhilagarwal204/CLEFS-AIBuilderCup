"""Routing (03 spec 1.4): pools, floor zones, the score, the cap, shared
devices claimed by the first ack, and the urgent path."""

from __future__ import annotations

from clefs.modules.service import api, routing, state


def _staff(cur, slug, name, department="Housekeeping", *, online=False, languages=(), max_open=4, role_key=None):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, languages, max_open, role_key)
           VALUES (%s, %s, 'A', %s, TRUE, %s, %s, %s, %s) RETURNING id""",
        (slug, name, department, online, list(languages), max_open, role_key),
    )
    return cur.fetchone()["id"]


def _device(cur, slug, *, floors=(), departments=(), kind="floor"):
    cur.execute(
        "INSERT INTO devices (hotel_slug, name, kind, floors, departments) VALUES (%s, 'tablet', %s, %s, %s) RETURNING id",
        (slug, kind, list(floors), list(departments)),
    )
    return cur.fetchone()["id"]


def _login(cur, slug, device_id, staff_id):
    cur.execute("INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id) VALUES (%s, %s, %s)", (device_id, slug, staff_id))


def _roster(cur, slug, staff_id, *, floors=(), role="attendant", dept=None):
    cur.execute(
        """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at, floors, shift_role, department_key)
           VALUES (%s, %s, CURRENT_DATE, NOW() - INTERVAL '1 hour', NOW() + INTERVAL '7 hours', %s, %s, %s)""",
        (slug, staff_id, list(floors), role, dept),
    )


def _file(cur, slug, room, type_="towels", **kw):
    return api.create_request(cur, slug, room=room, type=type_, description=kw.pop("description", "please"),
                              source=kw.pop("source", "phone_desk"), **kw)


def _pool(cur, request_id):
    cur.execute("SELECT payload FROM svc_request_events WHERE request_id = %s AND action = 'assign'", (request_id,))
    return cur.fetchone()["payload"]["pool"]


def test_zoned_department_prefers_the_attendant_rostered_on_that_floor(cur, hotel_uae):
    s = hotel_uae.slug
    dev = _device(cur, s, departments=["Housekeeping"])
    one, two = _staff(cur, s, "Floor One"), _staff(cur, s, "Floor Two")
    for sid, floor in ((one, "1"), (two, "2")):
        _roster(cur, s, sid, floors=[floor])
        _login(cur, s, dev, sid)
    row = _file(cur, s, "201")
    # rostered attendants are free staff (05 decision 5): equal load, so the floor decides
    assert row["assigned_staff_id"] == two and _pool(cur, row["id"]) == "free"
    row = _file(cur, s, "102", "linen")
    assert row["assigned_staff_id"] == one


def test_language_bonus_and_continuity(cur, hotel_uae):
    s = hotel_uae.slug
    dev = _device(cur, s, departments=["Housekeeping"])
    en, ar = _staff(cur, s, "English"), _staff(cur, s, "Arabic", languages=["ar", "en"])
    for sid in (en, ar):
        _login(cur, s, dev, sid)
    row = api.create_request(cur, s, room="101", type="water", description="2 bottles of water", source="voice", language="ar")
    assert row["assigned_staff_id"] == ar  # -3 for speaking the guest's language
    # English: -30 (idle). Arabic: 2*1 open + 1 due within 5 min - 30 idle
    # - 2 continuity (already in room 101) = -29. The free attendant wins.
    nxt = _file(cur, s, "101", "linen")
    assert nxt["assigned_staff_id"] == en


def test_people_at_their_cap_are_skipped_while_anyone_is_free(cur, hotel_uae):
    s = hotel_uae.slug
    busy = _staff(cur, s, "Busy", online=True, max_open=1)
    free = _staff(cur, s, "Free", online=True)
    first = _file(cur, s, "101")
    assert first["assigned_staff_id"] == busy
    second = _file(cur, s, "102", "linen")
    assert second["assigned_staff_id"] == free


def test_continuity_keeps_a_room_with_the_same_attendant(cur, hotel_uae, clock):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    clock.freeze(datetime(2026, 3, 1, 10, 0, tzinfo=ZoneInfo("Asia/Dubai")))
    s = hotel_uae.slug
    a, b = _staff(cur, s, "A", online=True), _staff(cur, s, "B", online=True)
    cur.execute(
        """INSERT INTO staff_requests (hotel_slug, room, type, description, category, priority, status, timestamp,
               assigned_to, accepted_at, resolved_at, assigned_staff_id)
           VALUES (%s, '999', 'Other', 'x', 'Housekeeping', 'low', 'completed', 1, 'B', 1, %s, %s)""",
        (s, int(datetime(2026, 3, 1, 9, 58, tzinfo=ZoneInfo("Asia/Dubai")).timestamp() * 1000), b),
    )
    first = _file(cur, s, "201", "make_up_room")  # A never worked (idle 30) beats B (idle 2)
    assert first["assigned_staff_id"] == a
    clock.advance(minutes=20)
    # A: 2*1 open + 1 overdue - 30 idle - 2 continuity = -29; B: 0 - 22 idle = -22
    again = _file(cur, s, "201", "linen")
    assert again["assigned_staff_id"] == a


def test_shared_device_holds_the_job_until_the_first_ack_claims_it(cur, hotel_uae):
    s = hotel_uae.slug
    dev = _device(cur, s, floors=["2"], departments=["Housekeeping"])
    sara = _staff(cur, s, "Sara")
    row = _file(cur, s, "201")
    assert row["assigned_device_id"] == dev and row["assigned_staff_id"] is None and row["state"] == "assigned"
    assert _pool(cur, row["id"]) == "dept_shared"
    _login(cur, s, dev, sara)
    r = state.transition(cur, s, row["id"], "ack", state.Actor("device", staff_id=sara, device_id=dev, role="device"))["row"]
    assert r["assigned_staff_id"] == sara and r["assigned_to"] == "Sara" and r["assigned_device_id"] is None


def test_whole_department_offline_falls_to_front_office_and_signals(cur, hotel_uae):
    s = hotel_uae.slug
    _staff(cur, s, "HK Offline")
    desk = _staff(cur, s, "Desk", department="Front Desk", online=True)
    row = _file(cur, s, "101")
    assert row["assigned_staff_id"] == desk and _pool(cur, row["id"]) == "fo_online"
    cur.execute("SELECT dedupe_key FROM signals WHERE hotel_slug = %s AND kind = 'service.understaffed'", (s,))
    assert cur.fetchone()["dedupe_key"].startswith("service.understaffed:housekeeping:")


def test_legacy_online_department_member_beats_the_front_office_device(cur, hotel_uae):
    s = hotel_uae.slug
    hk = _staff(cur, s, "HK Online", online=True)
    desk = _staff(cur, s, "Desk", department="Front Desk")
    _login(cur, s, _device(cur, s, departments=["Front Desk"], kind="desk"), desk)
    row = _file(cur, s, "101")
    assert row["assigned_staff_id"] == hk and _pool(cur, row["id"]) == "dept_legacy"


def test_urgent_path_duty_manager_then_security_then_gm(cur, hotel_uae, monkeypatch):
    s = hotel_uae.slug
    _staff(cur, s, "HK", online=True)
    gm = _staff(cur, s, "The GM", department="Front Desk", role_key="gm")
    monkeypatch.setattr(api, "classify_safety", lambda text, **kw: {"level": "sensitive", "kind": "theft", "source": "lexicon",
                                                                      "confidence": 1.0})
    row = _file(cur, s, "101", description="my watch was stolen")
    assert row["assigned_staff_id"] == gm and _pool(cur, row["id"]) == "gm"
    sec = _device(cur, s, departments=["Security"], kind="department")
    row = _file(cur, s, "102", "linen", description="my phone was stolen")
    assert row["assigned_device_id"] == sec and _pool(cur, row["id"]) == "security_device"
    dm = _staff(cur, s, "DM", department="Front Desk")
    _roster(cur, s, dm, role="duty_manager")
    row = _file(cur, s, "103", "water", description="my bag was stolen")
    assert row["assigned_staff_id"] == dm and _pool(cur, row["id"]) == "duty_manager"


def test_pick_is_pure_and_honours_exclusions(cur, hotel_uae):
    from clefs.core.hotels import get_hotel

    s = hotel_uae.slug
    a, b = _staff(cur, s, "A", online=True), _staff(cur, s, "B", online=True)
    draft = {"department_key": "housekeeping", "room": "101", "floor": "1", "severity": "L3", "sensitivity": "none"}
    assert routing.pick(cur, get_hotel(cur, s), draft)["staff_id"] == a
    assert routing.pick(cur, get_hotel(cur, s), draft, exclude_staff=[a])["staff_id"] == b
    cur.execute("SELECT COUNT(*) AS n FROM staff_requests WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 0
