"""Device routes (03 spec 1.6): the scoped feed with handover gating and
server time, the offline outbox batch (idempotent replay, "Already done by",
the 60 s reassignment rule, per-item savepoints), staff SOS with supervisor
cancel, handover read/ack, staff-created requests, recovery with a
supervisor PIN approval, and lost and found."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from clefs.auth import devices
from clefs.modules.service import api, config_api

PIN = "4826"


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _staff(cur, slug, name, *, department="Housekeeping", role_key=None, phone=None, opt_in=False):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, role_key,
               whatsapp_e164, whatsapp_opt_in) VALUES (%s, %s, 'A', %s, TRUE, FALSE, %s, %s, %s) RETURNING id""",
        (slug, name, department, role_key, phone, opt_in),
    )
    sid = cur.fetchone()["id"]
    devices.set_staff_pin(cur, slug, sid, PIN, actor=None)
    return sid


def _login(dev, sid):
    r = dev.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": PIN})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def _h(sid):
    return {"X-Clefs-Staff": str(sid)}


def _cid():
    return str(uuid.uuid4())


def test_feed_scope_handover_gate_and_ack(device_client, cur, hotel_uae):
    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    dev = device_client(floors=["5"])
    session = _login(dev, amina)
    api.create_request(cur, s, room="501", type="towels", description="towels", source="phone_desk")
    api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    f = dev.get("/device/service/feed", params={"cursor": 0}, headers=_h(amina)).json()
    assert {e["floor"] for e in f["events"]} == {"5"} and f["cursor"] > 0
    assert f["handover_pending"] is True and f["session_id"] == session and f["server_time"]
    sheet = dev.get("/device/service/handover", headers=_h(amina)).json()
    assert sheet["pending"] is True and [r["room"] for r in sheet["open_requests"]] == ["501"]
    note = dev.post("/device/service/handover/notes", json={"body": "504 asked for a quiet room"}, headers=_h(amina))
    assert note.status_code == 200 and note.json()["floor"] == "5"
    ack = dev.post("/device/service/handover/ack", json={}, headers=_h(amina))
    assert ack.status_code == 200 and ack.json()["counts"]["open_requests"] == 1 and ack.json()["counts"]["notes"] == 1
    f2 = dev.get("/device/service/feed", params={"cursor": f["cursor"]}, headers=_h(amina)).json()
    assert f2["handover_pending"] is False and f2["events"] == []
    cur.execute("SELECT handover_ack_by FROM device_sessions WHERE id = %s", (session,))
    assert cur.fetchone()["handover_ack_by"] == amina
    assert dev.post("/device/service/handover/ack", json={}).status_code == 400  # no PIN session named


def test_batch_is_idempotent_and_reports_conflicts(device_client, cur, hotel_uae):
    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    bilal = _staff(cur, s, "Bilal")
    dev = device_client(floors=["1"])
    _login(dev, amina)
    _login(dev, bilal)
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    done = api.create_request(cur, s, room="102", type="water", description="water", source="phone_desk")
    c1, c2, c3 = _cid(), _cid(), _cid()
    body = {"actions": [
        {"client_event_id": c1, "action": "ack", "request_id": row["id"]},
        {"client_event_id": c2, "action": "note", "request_id": row["id"], "note": "on it"},
        {"client_event_id": c3, "action": "complete", "request_id": done["id"], "actor_staff_id": bilal},
    ]}
    r = dev.post("/device/service/actions", json=body, headers=_h(amina))
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert [x["status"] for x in res] == ["applied", "applied", "applied"]
    assert res[0]["request"]["state"] == "acknowledged" and res[0]["request"]["assigned_staff_id"] == amina
    again = dev.post("/device/service/actions", json=body, headers=_h(amina)).json()["results"]
    assert [x["status"] for x in again] == ["replayed", "replayed", "replayed"]
    # completing something Bilal already closed: "Already done by Bilal", the rest still applies
    late = dev.post("/device/service/actions", headers=_h(amina), json={"actions": [
        {"client_event_id": _cid(), "action": "complete", "request_id": done["id"]},
        {"client_event_id": _cid(), "action": "start", "request_id": row["id"]},
    ]}).json()["results"]
    assert late[0]["status"] == "conflict" and late[0]["conflict"]["by"] == "Bilal"
    assert late[0]["error"]["code"] == "already_closed" and late[1]["status"] == "applied"
    # someone not on shift on this device is refused per item
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department) VALUES (%s, 'Zed', 'A', 'x') RETURNING id", (s,))
    zed = cur.fetchone()["id"]
    bad = dev.post("/device/service/actions", headers=_h(amina), json={"actions": [
        {"client_event_id": _cid(), "action": "ack", "request_id": row["id"], "actor_staff_id": zed}]}).json()["results"]
    assert bad[0]["error"]["code"] == "staff_not_on_shift"


def test_stale_ack_after_reassignment_conflicts(device_client, cur, hotel_uae, clock):
    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    bilal = _staff(cur, s, "Bilal")
    dev = device_client(floors=["1"])
    _login(dev, amina)
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    cur.execute("UPDATE staff_requests SET assigned_staff_id = %s, assigned_to = 'Bilal', state = 'assigned' WHERE id = %s",
                (bilal, row["id"]))
    from clefs.core import clock as clock_mod

    old = (clock_mod.now() - timedelta(minutes=5)).isoformat()
    r = dev.post("/device/service/actions", headers=_h(amina), json={"actions": [
        {"client_event_id": _cid(), "action": "ack", "request_id": row["id"], "client_at": old}]}).json()["results"]
    assert r[0]["status"] == "conflict" and r[0]["error"]["code"] == "reassigned" and r[0]["conflict"]["by"] == "Bilal"
    fresh = clock_mod.now().isoformat()
    r = dev.post("/device/service/actions", headers=_h(amina), json={"actions": [
        {"client_event_id": _cid(), "action": "ack", "request_id": row["id"], "client_at": fresh}]}).json()["results"]
    assert r[0]["status"] == "applied" and r[0]["request"]["assigned_staff_id"] == amina


def _roster(client_as, slug, rows):
    gm = client_as("gm")
    from clefs.core import clock as clock_mod

    week = clock_mod.now().date().isoformat()
    r = gm.put(f"/hotels/{slug}/service/roster", json={"week": week, "assignments": rows})
    assert r.status_code == 200, r.text
    return r.json()


def test_sos_pages_security_and_only_a_supervisor_cancels(device_client, client_as, cur, hotel_uae, outbox, clock):
    s = hotel_uae.slug
    from zoneinfo import ZoneInfo

    clock.freeze(datetime(2026, 10, 6, 10, 0, tzinfo=ZoneInfo("Asia/Dubai")))
    amina = _staff(cur, s, "Amina")
    sam = _staff(cur, s, "Sam", department="Security", phone="+971501234567", opt_in=True)
    sara = _staff(cur, s, "Sara", role_key="supervisor")
    _roster(client_as, s, [
        {"staff_member_id": amina, "business_date": "2026-10-06", "template_key": "morning", "floors": ["2"]},
        {"staff_member_id": sam, "business_date": "2026-10-06", "template_key": "morning", "department_key": "security"},
        {"staff_member_id": sara, "business_date": "2026-10-06", "template_key": "morning", "shift_role": "supervisor"},
    ])
    dev = device_client(floors=["2", "5"])
    _login(dev, amina)
    cid = _cid()
    r = dev.post("/device/service/sos", json={"client_event_id": cid}, headers=_h(amina))
    assert r.status_code == 200, r.text
    inc = r.json()
    assert (inc["kind"], inc["level"], inc["severity"], inc["floor"], inc["staff_member_id"]) == ("staff_sos", "emergency", "L1", "2", amina)
    assert inc["paged"] >= 1 and inc["replayed"] is False
    sent = [m for m in outbox.captured() if "SOS" in str(m)]
    assert sent, outbox.captured()
    assert dev.post("/device/service/sos", json={"client_event_id": cid}, headers=_h(amina)).json()["replayed"] is True
    feed = dev.get("/device/service/feed", headers=_h(amina)).json()
    assert [i["id"] for i in feed["open_incidents"]] == [inc["id"]]
    no = dev.post(f"/device/service/incidents/{inc['id']}/close", json={"status": "cancelled"}, headers=_h(amina))
    assert no.status_code == 403 and no.json()["code"] == "supervisor_required"
    _login(dev, sara)
    ok = dev.post(f"/device/service/incidents/{inc['id']}/close", json={"status": "cancelled", "note": "false alarm"},
                  headers=_h(sara))
    assert ok.status_code == 200 and ok.json()["status"] == "cancelled" and ok.json()["closed_by"]["staff_id"] == sara
    tiles = dev.get("/device/service/roster/today").json()
    # a shift with no floors (Security) is shown on every floor device
    assert {t["name"] for t in tiles} == {"Amina", "Sara", "Sam"}
    assert next(t for t in tiles if t["name"] == "Sara")["on_shift_here"] is True


def test_staff_request_recovery_and_lost_found(device_client, client_as, cur, hotel_uae, clock):
    s = hotel_uae.slug
    from zoneinfo import ZoneInfo

    clock.freeze(datetime(2026, 10, 6, 10, 0, tzinfo=ZoneInfo("Asia/Dubai")))
    amina = _staff(cur, s, "Amina")
    sara = _staff(cur, s, "Sara")
    _roster(client_as, s, [
        {"staff_member_id": sara, "business_date": "2026-10-06", "template_key": "morning", "shift_role": "supervisor"}])
    dev = device_client(floors=["1"])
    _login(dev, amina)
    _login(dev, sara)
    cid = _cid()
    r = dev.post("/device/service/requests", json={"room": "101", "description": "stained carpet", "client_event_id": cid},
                 headers=_h(amina))
    assert r.status_code == 200, r.text
    req = r.json()["request"]
    assert req["source"] == "staff"
    assert dev.post("/device/service/requests", json={"room": "101", "description": "stained carpet", "client_event_id": cid},
                    headers=_h(amina)).json()["request"]["id"] == req["id"]
    case = dev.post("/device/service/recovery-cases", json={"request_id": req["id"], "category_key": "cleanliness",
                                                            "reason": "complaint"}, headers=_h(amina)).json()
    assert case["created"] is True and case["room"] == "101"
    g = dev.post(f"/device/service/recovery-cases/{case['id']}/actions", json={"kind": "fnb_credit", "value": "200"},
                 headers=_h(amina)).json()
    assert g["status"] == "pending_approval" and g["approver_role"] == "supervisor"
    assert dev.post(f"/device/service/recovery-actions/{g['id']}/decide", json={"decision": "approve"},
                    headers=_h(amina)).json()["code"] == "above_authority"
    ok = dev.post(f"/device/service/recovery-actions/{g['id']}/decide", json={"decision": "approve"}, headers=_h(sara))
    assert ok.status_code == 200 and ok.json()["status"] == "given" and ok.json()["decided_by"]["staff_id"] == sara
    learn = dev.patch(f"/device/service/recovery-cases/{case['id']}", json={"learn": {"listen": True}}, headers=_h(amina))
    assert learn.json()["learn"]["listen"] is True
    assert dev.patch(f"/device/service/recovery-cases/{case['id']}", json={"status": "resolved"},
                     headers=_h(amina)).status_code == 403
    lf_id = _cid()
    lf = dev.post("/device/service/lost-found", json={"description": "black wallet", "room": "101", "client_event_id": lf_id},
                  headers=_h(amina)).json()
    assert lf["status"] == "stored" and lf["replayed"] is False
    assert dev.post("/device/service/lost-found", json={"description": "black wallet", "client_event_id": lf_id},
                    headers=_h(amina)).json()["id"] == lf["id"]
    desk = client_as("front_desk")
    assert [x["id"] for x in desk.get(f"/hotels/{s}/service/lost-found").json()] == [lf["id"]]
    assert desk.patch(f"/hotels/{s}/service/lost-found/{lf['id']}", json={"status": "returned"}).json()["status"] == "returned"


def test_long_poll_waits_then_returns_empty_and_console_views(device_client, client_as, cur, hotel_uae):
    import time

    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    dev = device_client(floors=["5"])
    _login(dev, amina)
    first = dev.get("/device/service/feed", headers=_h(amina)).json()
    t0 = time.monotonic()
    out = dev.get("/device/service/feed", params={"cursor": first["cursor"], "wait": 1}, headers=_h(amina)).json()
    assert out["events"] == [] and out["cursor"] == first["cursor"] and time.monotonic() - t0 >= 0.9
    assert dev.get("/device/service/feed", params={"wait": 26}).status_code == 422
    inc = dev.post("/device/service/sos", json={}).json()
    assert inc["staff_member_id"] is None and inc["floor"] == "5"  # no PIN: the device's floor
    gm = client_as("gm")
    assert [i["id"] for i in gm.get(f"/hotels/{s}/service/incidents").json()] == [inc["id"]]
    assert client_as("staff").post(f"/hotels/{s}/service/incidents/{inc['id']}/close", json={}).status_code == 403
    closed = gm.post(f"/hotels/{s}/service/incidents/{inc['id']}/close", json={"note": "checked"}).json()
    assert closed["status"] == "closed" and closed["closed_by"]["user_id"] == gm.user["id"]
    gm.post(f"/hotels/{s}/service/handover/notes", json={"body": "VIP in 502 at 18:00", "floor": "5"})
    sheet = gm.get(f"/hotels/{s}/service/handover", params={"floor": "5"}).json()
    assert sheet["notes"][0]["body"] == "VIP in 502 at 18:00" and sheet["incidents"][0]["status"] == "closed"
