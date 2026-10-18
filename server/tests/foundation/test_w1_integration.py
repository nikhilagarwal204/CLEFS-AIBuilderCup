"""Wave 1 integration: foundation changes made to apply lane requests.

- auth.devices.close_session (public) ends a PIN session and the legacy
  online flag with it; Service's auto-logout calls it.
- A device write that names an on-shift person touches
  device_sessions.last_activity_at; a GET (feed polling) does not.
- clefs.ai.structured takes an optional temperature, sent only when set.
- TRIPADVISOR_API_KEY is a Settings field.
- Trust's compliance jobs run for a hotel that switched Trust off.
- PUT /rooms creates unknown room types; GET|PUT /room-types edits them.
- InboundEmail exposes the SPF and DKIM results our verifier stamps.
"""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel

from clefs import ai
from clefs.auth import devices


def _staff(cur, slug, pin="1234"):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department) VALUES (%s, 'Asha', 'Attendant', 'Housekeeping') RETURNING id",
        (slug,),
    )
    sid = cur.fetchone()["id"]
    devices.set_staff_pin(cur, slug, sid, pin, actor=None)
    return sid


def test_close_session_is_public_and_clears_online(cur, device_client, hotel_uae):
    sid = _staff(cur, hotel_uae.slug)
    tablet = device_client(departments=["Housekeeping"])
    body = tablet.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"}).json()
    devices.close_session(cur, hotel_uae.slug, body["session_id"], sid, reason="auto_idle")
    cur.execute("SELECT end_reason, ended_at FROM device_sessions WHERE id = %s", (body["session_id"],))
    row = cur.fetchone()
    assert row["end_reason"] == "auto_idle" and row["ended_at"] is not None
    cur.execute("SELECT is_online FROM staff_members WHERE id = %s", (sid,))
    assert cur.fetchone()["is_online"] is False


def test_device_writes_touch_last_activity_but_reads_do_not(cur, device_client, hotel_uae, clock):
    sid = _staff(cur, hotel_uae.slug)
    tablet = device_client(departments=["Housekeeping"])
    t0 = clock.freeze(clock.now())
    body = tablet.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"}).json()
    devices.clear_cache()
    clock.advance(minutes=30)
    head = {"X-Clefs-Staff": str(sid)}
    tablet.get(f"/hotels/{hotel_uae.slug}/context", headers=head)
    cur.execute("SELECT last_activity_at FROM device_sessions WHERE id = %s", (body["session_id"],))
    assert cur.fetchone()["last_activity_at"] <= t0 + timedelta(minutes=1)
    tablet.post("/auth/stream-ticket", json={"hotel_slug": hotel_uae.slug}, headers=head)
    cur.execute("SELECT last_activity_at FROM device_sessions WHERE id = %s", (body["session_id"],))
    assert cur.fetchone()["last_activity_at"] >= t0 + timedelta(minutes=30)


class _Out(BaseModel):
    ok: bool


def test_structured_sends_temperature_only_when_set():
    fake = ai.FakeBackend({"ok": True}, {"ok": True})
    ai.set_backend(fake)
    try:
        ai.structured("test.judge", system="s", input="i", schema=_Out)
        ai.structured("test.judge", system="s", input="i", schema=_Out, temperature=0)
    finally:
        ai.reset_backend()
    assert [c.temperature for c in fake.calls] == [None, 0]


def test_tripadvisor_key_is_a_setting():
    from clefs.settings import Settings

    assert "TRIPADVISOR_API_KEY" in Settings.model_fields


def test_compliance_jobs_run_for_a_hotel_that_switched_trust_off():
    from types import SimpleNamespace

    from clefs import jobs

    hotel = SimpleNamespace(slug="h1")
    off = {"h1": {"trust"}}
    purge = SimpleNamespace(per_hotel=True, module="trust", name="trust.retention_purge")
    evals = SimpleNamespace(per_hotel=True, module="trust", name="trust.eval_nightly")
    assert jobs._target_hotels(purge, [hotel], off) == [hotel]
    assert jobs._target_hotels(evals, [hotel], off) == []


def test_room_type_codes_are_created_and_editable(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    owner = client_as("owner")
    r = owner.put(f"/hotels/{s}/rooms", json={"rooms": [{"room_number": "901", "room_type_code": "ZX9"}]})
    assert r.status_code == 200, r.text
    types = {t["code"]: t for t in owner.get(f"/hotels/{s}/room-types").json()}
    assert types["ZX9"]["name"] == "ZX9"
    r = owner.put(f"/hotels/{s}/room-types", json={"room_types": [{"code": "ZX9", "name": "Zed Suite", "bedrooms": 1}]})
    assert r.status_code == 200 and {t["code"]: t["name"] for t in r.json()}["ZX9"] == "Zed Suite"
    # a partial update keeps the name
    r = owner.put(f"/hotels/{s}/room-types", json={"room_types": [{"code": "ZX9", "is_active": False}]})
    dlx = {t["code"]: t for t in r.json()}["ZX9"]
    assert (dlx["name"], dlx["is_active"]) == ("Zed Suite", False)
    assert client_as("front_desk").put(f"/hotels/{s}/room-types", json={"room_types": [{"code": "X"}]}).status_code == 403


def test_inbound_email_exposes_spf_and_dkim():
    from clefs.notify.inbound import InboundEmail

    base = dict(cur=None, inbound_id=1, hotel_slug="h", purpose="reviews", purpose_token=None, ref=None, alias="a",
                sender="x@example.com", sender_name=None, to="a@in.clefs.world", subject=None, text=None, html=None,
                stripped_reply=None, message_id="m1")
    ok = InboundEmail(**base, headers={"received-spf": "Pass (sender SPF authorized) identity=mailfrom; "
                                                       "receiver=inbound.resend.com",
                                       "Authentication-Results": "mx.example; dkim=pass header.d=example.com"})
    # SPF from our verifier's stamp counts; DKIM from an authserv-id nobody
    # configured is a header the sender could have written (test_final_foundation).
    assert (ok.spf, ok.dkim) == ("pass", None)
    bare = InboundEmail(**base)
    assert (bare.spf, bare.dkim) == (None, None)
