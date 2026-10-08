"""Service recovery (03 spec 1.5): LEARN cases, gestures under the
compensation authority matrix (minor units), the actions ledger, approvals
by the next role up, reconciliation with console decisions, and the cost
roll-up for api.recovery_cost."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from clefs.core import actions
from clefs.modules.service import api, config_api, recovery

DXB = ZoneInfo("Asia/Dubai")


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _case(client, slug, **kw):
    r = client.post(f"/hotels/{slug}/service/recovery-cases", json={"room": "101", "category_key": "noise", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_within_authority_is_given_and_ledgered(client_as, cur, hotel_uae, run_jobs):
    s = hotel_uae.slug
    desk = client_as("front_desk")
    case = _case(desk, s)
    assert case["learn"] == {"listen": False, "empathize": False, "apologize": False, "resolve": False, "notify": False}
    r = desk.post(f"/hotels/{s}/service/recovery-cases/{case['id']}/actions", json={"kind": "amenity", "value": "80.00"})
    assert r.status_code == 200, r.text
    g = r.json()
    assert (g["status"], g["within_authority"], g["value_minor"], g["currency"], g["value"]) == ("given", True, 8000, "AED", "80.00")
    assert g["authorized_by"]["role"] == "staff" and g["action_id"]
    a = actions.get_action(cur, s, g["action_id"])
    assert a["type"] == "service.recovery_gesture" and a["status"] == "approved" and a["expected_minor"] == 8000
    run_jobs("core.actions.execute")
    assert actions.get_action(cur, s, g["action_id"])["status"] == "done"
    # LEARN ticks merge one box at a time
    p = desk.patch(f"/hotels/{s}/service/recovery-cases/{case['id']}", json={"learn": {"listen": True, "apologize": True}})
    assert p.json()["learn_done"] == 2 and p.json()["given_minor"] == 8000
    worker = client_as("staff")
    assert worker.patch(f"/hotels/{s}/service/recovery-cases/{case['id']}", json={"status": "closed"}).status_code == 403
    assert desk.patch(f"/hotels/{s}/service/recovery-cases/{case['id']}", json={"status": "closed"}).json()["status"] == "closed"


def test_above_authority_waits_for_the_next_role(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    desk = client_as("front_desk")
    case = _case(desk, s)
    r = desk.post(f"/hotels/{s}/service/recovery-cases/{case['id']}/actions",
                  json={"kind": "fnb_credit", "value_minor": 15000, "description": "dinner voucher"})
    g = r.json()
    assert (g["status"], g["within_authority"], g["approver_role"]) == ("pending_approval", False, "supervisor")
    a = actions.get_action(cur, s, g["action_id"])
    assert a["status"] == "proposed" and a["owner_role"] == "dept_head"
    cur.execute("SELECT kind, status FROM signals WHERE hotel_slug = %s AND dedupe_key = %s", (s, f"svc:gesture:{g['id']}"))
    assert cur.fetchone()["kind"] == "service.recovery_over_authority"
    # a front desk user cannot approve; a department head (supervisor authority) can
    own = desk.post(f"/hotels/{s}/service/recovery-actions/{g['id']}/decide", json={"decision": "approve"})
    assert own.status_code == 403 and own.json()["code"] == "above_authority"
    head = client_as("dept_head")
    d = head.post(f"/hotels/{s}/service/recovery-actions/{g['id']}/decide", json={"decision": "approve", "note": "ok"})
    assert d.status_code == 200, d.text
    assert d.json()["status"] == "given" and d.json()["authorized_by"]["role"] == "supervisor"
    again = head.post(f"/hotels/{s}/service/recovery-actions/{g['id']}/decide", json={"decision": "reject"})
    assert again.status_code == 409 and again.json()["code"] == "already_decided"


def test_approver_needs_covering_authority(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    desk = client_as("front_desk")
    case = _case(desk, s)
    g = desk.post(f"/hotels/{s}/service/recovery-cases/{case['id']}/actions",
                  json={"kind": "room_night", "value": "600"}).json()
    assert g["approver_role"] == "duty_manager"
    head = client_as("dept_head")
    r = head.post(f"/hotels/{s}/service/recovery-actions/{g['id']}/decide", json={"decision": "approve"})
    assert r.status_code == 403 and r.json()["code"] == "above_authority"
    gm = client_as("gm")
    assert gm.post(f"/hotels/{s}/service/recovery-actions/{g['id']}/decide", json={"decision": "approve"}).json()["status"] == "given"


def test_console_ignore_reconciles_on_read(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    desk = client_as("front_desk")
    case = _case(desk, s)
    g = desk.post(f"/hotels/{s}/service/recovery-cases/{case['id']}/actions", json={"kind": "late_checkout", "value": "0"}).json()
    assert g["status"] == "pending_approval" and g["approver_role"] == "supervisor"
    gm = client_as("gm")
    assert gm.post(f"/hotels/{s}/actions/{g['action_id']}/decide", json={"decision": "ignore"}).status_code == 200
    got = desk.get(f"/hotels/{s}/service/recovery-cases/{case['id']}").json()
    assert got["gestures"][0]["status"] == "rejected"


def test_hotel_matrix_override_and_cost_rollup(client_as, cur, hotel_uae, clock):
    s = hotel_uae.slug
    cur.execute("""INSERT INTO svc_compensation_authority (hotel_slug, role_key, max_minor, currency, allowed_kinds)
                   VALUES (%s, 'staff', 20000, 'AED', '{amenity}')""", (s,))
    m = recovery.authority_matrix(cur, hotel_uae)
    assert m["staff"]["max_minor"] == 20000 and m["staff"]["source"] == "hotel" and m["gm"]["max_minor"] is None
    assert recovery.approver_for(m, "staff", "upgrade", 10) == "duty_manager"
    clock.freeze(datetime(2026, 10, 5, 23, 30, tzinfo=DXB))  # 19:30 UTC, still 5 Oct in Dubai
    desk = client_as("front_desk")
    case = _case(desk, s)
    assert desk.post(f"/hotels/{s}/service/recovery-cases/{case['id']}/actions",
                     json={"kind": "amenity", "value": "150"}).json()["status"] == "given"
    assert api.recovery_cost(cur, s, start=date(2026, 10, 5), end=date(2026, 10, 5)) in (0, 15000)
    assert recovery.recovery_cost(cur, s, start=date(2026, 10, 5), end=date(2026, 10, 5)) == 15000
    assert recovery.recovery_cost(cur, s, start=date(2026, 10, 6), end=date(2026, 10, 6)) == 0
    assert recovery.has_open_complaint(cur, s, room="101", since=datetime(2026, 10, 1, tzinfo=DXB))


def test_one_open_case_per_request_and_device_replay(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    desk = client_as("front_desk")
    a = _case(desk, s, request_id=row["id"], reason="complaint")
    b = _case(desk, s, request_id=row["id"])
    assert a["id"] == b["id"] and a["created"] is True and b["created"] is False
    cid = "0b7c6a55-2a7e-4ad4-9a31-8a3d5a3b1f00"
    first = desk.post(f"/hotels/{s}/service/recovery-cases/{a['id']}/actions",
                      json={"kind": "amenity", "value": "10", "client_event_id": cid}).json()
    second = desk.post(f"/hotels/{s}/service/recovery-cases/{a['id']}/actions",
                       json={"kind": "amenity", "value": "10", "client_event_id": cid}).json()
    assert first["id"] == second["id"] and second["replayed"] is True
    assert len(desk.get(f"/hotels/{s}/service/recovery-cases/{a['id']}").json()["gestures"]) == 1
