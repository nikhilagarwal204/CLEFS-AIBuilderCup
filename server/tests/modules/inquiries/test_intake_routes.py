"""Console routes of the intake side (04 spec 4.3, 4.11): phone-desk log,
list filters, masking by role, detail, edits, closing moves, widget,
channels and settings."""

from __future__ import annotations

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_wa,
)

BASE = "/hotels/test-dubai/inquiries"


def _log(c, **kw):
    body = {"text": "Caller wants 2 rooms 12-14 Oct", "name": "Ahmed K", "phone": "+971501234000", "rooms": 2}
    body.update(kw)
    return c.post(BASE, json=body)


def test_front_desk_logs_a_phone_inquiry(client_as, cur, hotel):
    c = client_as("front_desk", hotel.slug)
    r = _log(c)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["source"] == "phone_desk" and out["consent_basis"] == "staff_logged"
    assert out["contact_phone"] == "+971501234000" and out["contact_name"] == "Ahmed K"
    assert out["first_response_by"] == "phone_desk" and "contact_phone_enc" not in out
    assert out["money_at_risk_value"] is None or isinstance(out["money_at_risk_value"], str)


def test_revenue_sees_no_contact(client_as, cur, hotel):
    _log(client_as("front_desk", hotel.slug))
    rows = client_as("revenue", hotel.slug).get(BASE).json()
    assert len(rows) == 1 and rows[0]["contact_phone"] is None and rows[0]["contact_name"] is None


def test_staff_role_cannot_list(client_as, hotel):
    assert client_as("staff", hotel.slug).get(BASE).status_code == 403


def test_list_filters_and_detail(client_as, cur, hotel):
    send_wa("Hello from WhatsApp")
    c = client_as("gm", hotel.slug)
    _log(c, phone="+971509990000")
    assert [r["source"] for r in c.get(BASE, params={"source": "whatsapp"}).json()] == ["whatsapp"]
    assert c.get(BASE, params={"stage": "bogus"}).status_code == 400
    wa = c.get(BASE, params={"source": "whatsapp"}).json()[0]
    detail = c.get(f"{BASE}/{wa['id']}").json()
    assert detail["messages"][0]["body"] == "Hello from WhatsApp"
    assert detail["stage_events"][0]["to_stage"] == "new"
    assert c.get(f"{BASE}/999999").status_code == 404


def test_patch_segment_moves_the_sla_and_sticks(client_as, cur, hotel):
    send_wa("Hello")
    c = client_as("gm", hotel.slug)
    row = c.get(BASE).json()[0]
    r = c.patch(f"{BASE}/{row['id']}", json={"segment": "group", "rooms": 12, "check_in": "2026-10-10",
                                             "check_out": "2026-10-12"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["segment"] == "group" and out["nights"] == 2 and out["room_nights_est"] == 24
    assert out["first_response_due_at"] > row["first_response_due_at"]
    assert c.patch(f"{BASE}/{row['id']}", json={"check_out": "2026-10-01"}).status_code == 400


def test_lost_needs_a_code_and_future_a_date(client_as, cur, hotel):
    send_wa("Hello")
    c = client_as("gm", hotel.slug)
    iid = c.get(BASE).json()[0]["id"]
    assert c.post(f"{BASE}/{iid}/lost", json={"reason": "WHATEVER"}).status_code == 400
    assert c.post(f"{BASE}/{iid}/future", json={}).status_code == 400
    r = c.post(f"{BASE}/{iid}/lost", json={"reason": "PRICE_VS_COMP", "note": "Found cheaper"})
    assert r.status_code == 200 and r.json()["stage"] == "lost" and r.json()["lost_reason"] == "PRICE_VS_COMP"


def test_widget_embed_and_rotate(client_as, hotel):
    c = client_as("owner", hotel.slug)
    w = c.get(f"{BASE}/widget").json()
    assert w["widget_key"] and "inquiry.js" in w["embed_code"]
    assert c.get(f"{BASE}/widget").json()["widget_key"] == w["widget_key"]
    rotated = c.post(f"{BASE}/widget/rotate").json()
    assert rotated["widget_key"] != w["widget_key"]
    put = c.put(f"{BASE}/widget", json={"allowed_origins": ["https://hotel.example"]})
    assert put.status_code == 200 and put.json()["allowed_origins"] == ["https://hotel.example"]
    assert c.put(f"{BASE}/widget", json={"allowed_origins": ["javascript:alert(1)"]}).status_code == 400
    assert client_as("front_desk", hotel.slug).get(f"{BASE}/widget").status_code == 403


def test_channels_google_and_settings(client_as, hotel):
    c = client_as("owner", hotel.slug)
    g = c.post(f"{BASE}/channels", json={"kind": "google_leadform"})
    assert g.status_code == 201 and g.json()["webhook_path"].startswith("/webhooks/inquiries/google-leadform/")
    listed = c.get(f"{BASE}/channels").json()
    assert {a["kind"] for a in listed["accounts"]} == {"google_leadform"}
    assert listed["email_address"] == "testdxb+inq@in.clefs.world"
    assert c.delete(f"{BASE}/channels/google_leadform").status_code == 204
    s = c.put(f"{BASE}/settings", json={"sla": {"fit": 10}, "ai_on_unconsented_inbound": False})
    assert s.status_code == 200 and s.json()["sla"]["fit"] == 10 and s.json()["sla"]["group"] == 240
    assert c.put(f"{BASE}/settings", json={"sla": {"vip": 5}}).status_code == 422
