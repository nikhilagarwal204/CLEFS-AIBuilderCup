"""Device gaps: POST|DELETE /device/service/push-subscriptions (Web Push
storage, log-only send without VAPID) and POST /device/service/sos/{ref}/cancel
(a supervisor PIN typed on the pressing person's device)."""

from __future__ import annotations

import uuid

import pytest

from clefs.auth import devices
from clefs.modules.service import config_api, push

PIN = "4826"
SUB = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "expirationTime": None,
       "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
                "auth": "tBHItJI5svbpez7KI4CCXg"}}


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _staff(cur, slug, name, *, role_key=None, pin=PIN):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, role_key)
           VALUES (%s, %s, 'A', 'Housekeeping', TRUE, FALSE, %s) RETURNING id""",
        (slug, name, role_key),
    )
    sid = cur.fetchone()["id"]
    if pin:
        devices.set_staff_pin(cur, slug, sid, pin, actor=None)
    return sid


def _login(dev, sid):
    r = dev.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": PIN})
    assert r.status_code == 200, r.text


# ─── Push subscriptions ───


def test_push_subscription_upsert_revoke_and_log_only_send(device_client, cur, hotel_uae):
    slug = hotel_uae.slug
    amina = _staff(cur, slug, "Amina")
    dev = device_client(floors=["2"])
    _login(dev, amina)
    r = dev.post("/device/service/push-subscriptions", json={"subscription": SUB}, headers={"X-Clefs-Staff": str(amina)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["staff_member_id"] == amina and body["mode"] == "log"
    assert body["device_id"] == dev.device["id"] and body["revoked_at"] is None
    again = dev.post("/device/service/push-subscriptions", json={"subscription": SUB}).json()
    assert again["created"] is False and again["id"] == body["id"] and again["staff_member_id"] is None
    assert len(push.live_for(cur, slug)) == 1
    res = push.send_to_devices(cur, slug, title="SOS", body="Staff SOS, floor 2.", tag="t", urgent=True)
    assert res == {"subscriptions": 1, "sent": 0, "failed": 0, "revoked": 0, "mode": "log"}
    gone = dev.request("DELETE", "/device/service/push-subscriptions", json={"endpoint": SUB["endpoint"]})
    assert gone.status_code == 200 and gone.json()["revoked"] == 1
    assert push.live_for(cur, slug) == []
    # the same browser subscribing again brings the row back
    back = dev.post("/device/service/push-subscriptions", json={"subscription": SUB}).json()
    assert back["id"] == body["id"] and back["revoked_at"] is None


def test_push_subscription_validation_and_auth(device_client, client, cur, hotel_uae):
    dev = device_client()
    bad = dev.post("/device/service/push-subscriptions", json={"subscription": {"endpoint": "http://x", "keys": {}}})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_subscription"
    nokeys = dev.post("/device/service/push-subscriptions", json={"subscription": {"endpoint": "https://x.example/p"}})
    assert nokeys.status_code == 422
    assert client.post("/device/service/push-subscriptions", json={"subscription": SUB}).status_code == 401


def test_only_the_newest_subscriptions_per_device_stay_live(device_client, cur, hotel_uae):
    dev = device_client()
    for i in range(push.MAX_PER_DEVICE + 2):
        sub = {**SUB, "endpoint": f"https://fcm.googleapis.com/fcm/send/n{i}"}
        assert dev.post("/device/service/push-subscriptions", json={"subscription": sub}).status_code == 200
    live = push.live_for(cur, hotel_uae.slug, [dev.device["id"]])
    assert len(live) == push.MAX_PER_DEVICE
    assert {s["endpoint"][-2:] for s in live} == {f"n{i}" for i in range(2, push.MAX_PER_DEVICE + 2)}


# ─── SOS cancel with a supervisor PIN ───


def _sos(dev, sid):
    cid = str(uuid.uuid4())
    r = dev.post("/device/service/sos", json={"client_event_id": cid}, headers={"X-Clefs-Staff": str(sid)})
    assert r.status_code == 200, r.text
    return cid, r.json()


def _cancel(dev, ref, sid, pin=PIN, **kw):
    return dev.post(f"/device/service/sos/{ref}/cancel", json={"supervisor_staff_id": sid, "pin": pin, **kw})


def test_supervisor_pin_cancels_by_client_event_id_or_id(device_client, cur, hotel_uae, outbox):
    slug = hotel_uae.slug
    amina = _staff(cur, slug, "Amina")
    sara = _staff(cur, slug, "Sara", role_key="supervisor")
    dev = device_client(floors=["2"])
    _login(dev, amina)
    cid, inc = _sos(dev, amina)
    ok = _cancel(dev, cid, sara, note="false alarm")  # the device only knows its client_event_id
    assert ok.status_code == 200, ok.text
    out = ok.json()
    assert out["id"] == inc["id"] and out["status"] == "cancelled" and out["changed"] is True
    assert out["closed_by"]["staff_id"] == sara and out["close_note"] == "false alarm"
    again = _cancel(dev, str(inc["id"]), sara)  # a double tap
    assert again.status_code == 200 and again.json()["changed"] is False
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'service.%%' ORDER BY id", (slug,))
    assert "service.incident.cancel" in [r["action"] for r in cur.fetchall()]


def test_pressing_staff_and_wrong_pins_cannot_cancel(device_client, cur, hotel_uae, outbox):
    slug = hotel_uae.slug
    amina = _staff(cur, slug, "Amina")
    sara = _staff(cur, slug, "Sara", role_key="supervisor")
    nopin = _staff(cur, slug, "Omar", role_key="supervisor", pin=None)
    dev = device_client(floors=["2"])
    _login(dev, amina)
    cid, inc = _sos(dev, amina)
    own = _cancel(dev, cid, amina)
    assert own.status_code == 403 and own.json()["code"] == "supervisor_required"
    assert _cancel(dev, cid, nopin).json()["code"] == "pin_not_set"
    assert _cancel(dev, cid, 999_999).status_code == 404
    for n in range(5):
        bad = _cancel(dev, cid, sara, pin="0000")
        assert bad.status_code == 401 and bad.json()["code"] == "invalid_pin", bad.text
    locked = _cancel(dev, cid, sara)  # even the right PIN waits out the lock
    assert locked.status_code == 423 and locked.json()["code"] == "pin_locked"
    cur.execute("SELECT status FROM svc_incidents WHERE id = %s", (inc["id"],))
    assert cur.fetchone()["status"] == "open"


def test_unknown_or_non_sos_refs(device_client, cur, hotel_uae, outbox):
    slug = hotel_uae.slug
    sara = _staff(cur, slug, "Sara", role_key="supervisor")
    dev = device_client()
    missing = _cancel(dev, str(uuid.uuid4()), sara)  # still in the outbox
    assert missing.status_code == 404 and missing.json()["code"] == "incident_not_found"
    assert _cancel(dev, "not-a-ref", sara).status_code == 404
    cur.execute("""INSERT INTO svc_incidents (hotel_slug, kind, level, severity, summary, opened_by, opened_at)
                   VALUES (%s, 'emergency', 'emergency', 'L1', 'x', '{}', NOW()) RETURNING id""", (slug,))
    other = cur.fetchone()["id"]
    assert _cancel(dev, str(other), sara).json()["code"] == "not_an_sos"
