"""Service manifest wiring (Wave 1 integration): the app mounts the ops
routers through module.py (no config_api.mount_ops in these tests), the 1.7
job set is registered by importing the manifest, a complaint opens a LEARN
recovery case, and the api.py doors reach the recovery and roster bodies."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from clefs.modules.service import api

DXB = ZoneInfo("Asia/Dubai")


def test_manifest_mounts_the_ops_routes(app):
    from tests.foundation.route_walk import route_paths

    paths = route_paths(app.routes)
    for p in ("/device/service/actions", "/device/service/handover", "/public/r/{token}/whatsapp",
              "/hotels/{slug}/service/roster", "/hotels/{slug}/service/recovery-cases"):
        assert p in paths, p


def test_manifest_import_registers_the_job_set():
    import clefs.modules.service.module  # noqa: F401
    from clefs import jobs

    names = {j.name for j in jobs.registered_jobs()}
    assert {"service.sla_sweep", "service.tracker_push", "service.daily_rollup"} <= names


def test_a_complaint_opens_a_recovery_case(cur, hotel_uae):
    s = hotel_uae.slug
    since = datetime(2020, 1, 1, tzinfo=DXB)
    assert api.has_open_complaint(cur, s, room="101", since=since) is False
    row = api.create_request(cur, s, room="101", type="complaint", description="The room next door is very loud",
                             source="console")
    cur.execute("SELECT reason, status FROM svc_recovery_cases WHERE request_id = %s", (row["id"],))
    case = cur.fetchone()
    assert case is not None and (case["reason"], case["status"]) == ("complaint", "open")
    assert api.has_open_complaint(cur, s, room="101", since=since) is True


def test_api_doors_delegate(cur, hotel_uae):
    s = hotel_uae.slug
    assert api.recovery_cost(cur, s, start=date(2026, 10, 1), end=date(2026, 10, 31)) == 0
    assert api.on_duty(cur, s, role="gm", at=datetime(2026, 10, 1, 9, tzinfo=DXB)) == []


def test_roster_accepts_the_instants_it_returns(client_as, cur, hotel_uae):
    """The console round-trips start_at/end_at (UTC ISO) instead of HH:MM;
    the server reads them in the hotel's timezone."""
    s = hotel_uae.slug
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department, is_active) VALUES (%s, 'Rui', 'A', 'Housekeeping', TRUE) RETURNING id",
                (s,))
    sid = cur.fetchone()["id"]
    gm = client_as("gm")
    cell = {"staff_member_id": sid, "business_date": "2026-10-06", "start_at": "2026-10-06T05:00:00.000Z",
            "end_at": "2026-10-06T13:00:00.000Z", "floors": ["5"], "shift_role": "attendant"}
    r = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-10-06", "assignments": [cell]})
    assert r.status_code == 200, r.text
    a = r.json()["assignments"][0]
    assert a["start_at"].startswith("2026-10-06T09:00:00+04:00") and a["end_at"].startswith("2026-10-06T17:00:00+04:00")


def test_event_feed_latest_cursor_starts_at_the_head(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    api.create_request(cur, s, room="101", type="towels", description="Two towels please", source="console")
    gm = client_as("gm")
    head = gm.get(f"/hotels/{s}/service/events?cursor=latest").json()
    assert head["events"] == [] and head["cursor"] > 0
    assert gm.get(f"/hotels/{s}/service/events?cursor={head['cursor']}").json()["events"] == []
    assert gm.get(f"/hotels/{s}/service/events?cursor=0").json()["events"]
    assert gm.get(f"/hotels/{s}/service/events?cursor=abc").json()["code"] == "invalid_cursor"
