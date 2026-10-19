"""Service HTTP routes: console filing, queue, detail, transitions, notes,
the request_events feed, the device feed and the public guest tracker."""

from __future__ import annotations

from fastapi.testclient import TestClient

from clefs.modules.service import api


def _staff(cur, slug, name, department="Housekeeping", online=True):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) VALUES (%s, %s, 'A', %s, TRUE, %s) RETURNING id",
        (slug, name, department, online),
    )
    return cur.fetchone()["id"]


def test_console_files_lists_and_moves_a_request(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    desk = client_as("front_desk")
    r = desk.post(f"/hotels/{s}/service/requests", json={"room": "101", "type": "towels", "description": "2 towels",
                                                          "source": "phone_desk"})
    assert r.status_code == 200, r.text
    body = r.json()
    rid = body["request"]["id"]
    assert body["request"]["source"] == "phone_desk" and body["request"]["assigned_staff_id"] == amina
    assert body["routed"]["pool"] == "dept_legacy" and len(body["tracking_token"]) == 22
    assert "idempotency_key" not in body["request"]
    queue = desk.get(f"/hotels/{s}/service/queue").json()
    assert [q["id"] for q in queue] == [rid]
    r = desk.post(f"/hotels/{s}/service/requests/{rid}/transition", json={"action": "ack", "expected_version": 1})
    assert r.status_code == 200 and r.json()["request"]["state"] == "acknowledged"
    stale = desk.post(f"/hotels/{s}/service/requests/{rid}/transition", json={"action": "start", "expected_version": 1})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"
    assert stale.json()["conflict"]["version"] == 2
    desk.post(f"/hotels/{s}/service/requests/{rid}/notes", json={"body": "guest asked for bath sheets"})
    detail = desk.get(f"/hotels/{s}/service/requests/{rid}").json()
    assert [e["kind"] for e in detail["events"]] == ["created", "transition", "transition", "note"]
    feed = desk.get(f"/hotels/{s}/service/events", params={"cursor": 0}).json()
    assert len(feed["events"]) == 4 and feed["cursor"] == detail["events"][-1]["id"]
    assert desk.get(f"/hotels/{s}/service/events", params={"cursor": feed["cursor"]}).json()["events"] == []


def test_staff_cannot_cancel_but_can_complete(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    _staff(cur, s, "Amina")
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    worker = client_as("staff")
    r = worker.post(f"/hotels/{s}/service/requests/{row['id']}/transition", json={"action": "cancel"})
    assert r.status_code == 403
    r = worker.post(f"/hotels/{s}/service/requests/{row['id']}/transition", json={"action": "complete"})
    assert r.status_code == 200 and r.json()["request"]["state"] == "completed"
    again = worker.post(f"/hotels/{s}/service/requests/{row['id']}/transition", json={"action": "complete"})
    assert again.status_code == 409 and again.json()["conflict"]["by"] == "Amina"


def test_device_feed_is_scoped_to_its_floors(device_client, cur, hotel_uae):
    s = hotel_uae.slug
    _staff(cur, s, "Amina")
    one = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    api.create_request(cur, s, room="201", type="linen", description="linen", source="phone_desk")
    dev = device_client(floors=["1"], departments=["Housekeeping"])
    feed = dev.get("/device/service/feed").json()
    assert {e["request_id"] for e in feed["events"]} == {one["id"]}
    assert TestClient(dev.app).get("/device/service/feed").status_code == 401


def test_public_tracker_shows_progress_with_a_first_name_only_and_confirms(client, cur, hotel_uae):
    s = hotel_uae.slug
    _staff(cur, s, "Amina Khan")
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    token = row["tracking_token"]
    view = client.get(f"/public/r/{token}").json()
    # 05 spec: the guest sees who is coming ("Amina is on her way"), never a surname
    assert view["state"] == "assigned" and view["can_confirm"] is False and view["staff_first_name"] == "Amina"
    assert "Khan" not in str(view) and "assigned_to" not in view and view["eta_minutes"] == 5
    assert client.get("/public/r/not-a-real-token").status_code == 404
    early = client.post(f"/public/r/{token}/confirm", json={"resolved": True})
    assert early.status_code == 409
    cur.execute("UPDATE staff_requests SET state = 'completed', status = 'completed', resolved_at = %s WHERE id = %s",
                (row["timestamp"] + 60_000, row["id"]))
    no = client.post(f"/public/r/{token}/confirm", json={"resolved": False, "comment": "still no towels"})
    assert no.status_code == 200, no.text
    assert no.json()["state"] == "assigned" and no.json()["guest_confirmation"] == "no"
    cur.execute("SELECT reopen_count, guest_comment, assigned_to FROM staff_requests WHERE id = %s", (row["id"],))
    assert dict(cur.fetchone()) == {"reopen_count": 1, "guest_comment": "still no towels", "assigned_to": "Amina Khan"}
