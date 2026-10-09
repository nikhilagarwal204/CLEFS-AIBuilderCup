"""Reviewer access (05 spec section 5): /public/demo/{slug} hands a reviewer
the guest page of Room 204, Maria's staff app and the duty manager's
console, for demo hotels only, rate-limited and scoped to that hotel."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from clefs.core import demo_access, rooms
from clefs.seed import demo_uae
from clefs.seed.demo_uae import core as cast

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-access"
PARTS = ["core", "core_catalog", "deck"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, password="demo-pass-123")


def _fresh(app) -> TestClient:
    return TestClient(app)


# ─── Demo hotels only ───


def test_every_door_answers_404_for_a_hotel_that_is_not_a_demo(app, cur, seeded, hotel_uae, legacy_slug):
    cur.execute("SELECT count(*) AS n FROM devices")
    devices_before = cur.fetchone()["n"]
    for slug in (hotel_uae.slug, legacy_slug, "no-such-hotel"):
        c = _fresh(app)
        for method, path in (("get", ""), ("post", "/staff-app"), ("post", "/console")):
            r = getattr(c, method)(f"/public/demo/{slug}{path}")
            assert r.status_code == 404 and r.json()["code"] == "hotel_not_found", (slug, path, r.text)
        assert "clefs_device" not in c.cookies and "clefs_session" not in c.cookies
    cur.execute("SELECT count(*) AS n FROM devices")
    assert cur.fetchone()["n"] == devices_before


def test_info_gives_the_keyed_room_link_the_cast_and_the_gate(app, client, seeded):
    r = client.get(f"/public/demo/{SLUG}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hotel"]["assistant_name"] == "Clefs" and len(body["hotel"]["guest_languages"]) == 8
    assert body["guest"] == {"room": cast.DECK_ROOM, "floor": "2", "path": rooms.stay_path(SLUG, cast.DECK_ROOM, 1)}
    assert body["staff"]["name"] == cast.DECK_ATTENDANT and body["staff"]["first_name"] == "Maria"
    assert body["staff"]["floors"] == ["1", "2", "3", "4"] and body["staff"]["languages"][0] == "fil"
    assert body["staff"]["pin"] == cast.DECK_ATTENDANT_PIN
    assert body["console"] == {"name": cast.DECK_DUTY_MANAGER, "role": "Duty manager"}
    # The seed queued the 22 standard tests; the shadow week is long over.
    assert body["gate"]["tests"]["state"] == "running" and body["gate"]["tests"]["expected"] == 22
    assert body["gate"]["shadow"]["state"] == "done"
    # The link is the printed QR card's: the guest page opens as that room.
    k = body["guest"]["path"].split("?k=")[1]
    ctx = client.get(f"/public/stay/{SLUG}/{cast.DECK_ROOM}/context", params={"k": k})
    assert ctx.status_code == 200, ctx.text
    assert ctx.json()["verified"] is True


# ─── Maria's staff app ───


def test_staff_app_pairs_this_browser_as_maria_and_opens_her_shift(app, cur, seeded):
    c = _fresh(app)
    r = c.post(f"/public/demo/{SLUG}/staff-app")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "clefs_device" in c.cookies
    assert body["path"] == f"/device/{SLUG}" and body["pin"] == cast.DECK_ATTENDANT_PIN
    assert body["device"]["kind"] == "personal" and body["device"]["floors"] == ["1", "2", "3", "4"]
    assert body["device"]["departments"] == ["Housekeeping"]
    session = body["session"]
    assert session["staff"]["name"] == cast.DECK_ATTENDANT
    # The device app's roster shows her on shift here, and her PIN on the
    # lock screen resumes this very session.
    roster = {s["name"]: s for s in c.get("/auth/devices/roster").json()}
    assert roster[cast.DECK_ATTENDANT]["on_shift"] is True
    again = c.post("/auth/devices/shift/start", json={"staff_member_id": session["staff"]["id"],
                                                      "pin": cast.DECK_ATTENDANT_PIN})
    assert again.status_code == 200 and again.json()["session_id"] == session["session_id"]
    feed = c.get("/device/service/feed", params={"cursor": 0, "wait": 0},
                 headers={"X-Clefs-Staff": str(session["staff"]["id"])})
    assert feed.status_code == 200, feed.text
    # What the staff app reads for the worker screen once Maria is at the
    # screen ("Hi, Maria", her reading language), with no PIN typed.
    me = c.get("/device/service/me", headers={"X-Clefs-Staff": str(session["staff"]["id"])})
    assert me.status_code == 200, me.text
    assert me.json()["staff"]["first_name"] == "Maria" and me.json()["on_duty"] is True
    assert me.json()["staff"]["languages"][0] == "fil"
    ctx = c.get(f"/hotels/{SLUG}/context").json()
    assert (ctx["principal_kind"], ctx["role"]) == ("device", "device")
    cur.execute("SELECT hotel_slug, kind FROM devices WHERE id = %s", (body["device"]["id"],))
    assert dict(cur.fetchone()) == {"hotel_slug": SLUG, "kind": "personal"}
    cur.execute("SELECT count(*) AS n FROM audit_log WHERE hotel_slug = %s AND action = 'core.demo_access.staff_app'",
                (SLUG,))
    assert cur.fetchone()["n"] == 1


def test_each_tap_is_a_new_browser_and_the_oldest_are_retired(app, cur, seeded, monkeypatch):
    monkeypatch.setattr(demo_access, "MAX_TRY_DEVICES", 2)
    ids = [_fresh(app).post(f"/public/demo/{SLUG}/staff-app").json()["device"]["id"] for _ in range(3)]
    cur.execute("SELECT id, revoked_at IS NOT NULL AS gone FROM devices WHERE id = ANY(%s) ORDER BY id", (ids,))
    assert [r["gone"] for r in cur.fetchall()] == [True, False, False]
    cur.execute("SELECT count(*) AS n FROM device_sessions WHERE device_id = %s AND ended_at IS NULL", (ids[0],))
    assert cur.fetchone()["n"] == 0
    # The seed's own phone for Maria is never retired.
    cur.execute("SELECT count(*) AS n FROM devices WHERE hotel_slug = %s AND name = %s AND revoked_at IS NULL",
                (SLUG, "Maria's phone"))
    assert cur.fetchone()["n"] == 1


# ─── The manager console ───


def test_console_signs_in_the_duty_manager_at_the_demo_hotel_only(app, cur, seeded, hotel_uae):
    c = _fresh(app)
    r = c.post(f"/public/demo/{SLUG}/console")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["path"] == f"/app/{SLUG}/service" and body["role"] == cast.DUTY_MANAGER_ROLE
    assert "clefs_session" in c.cookies
    expires = datetime.fromisoformat(body["expires_at"])
    assert timedelta(hours=1, minutes=55) < expires - datetime.now(timezone.utc) <= timedelta(hours=2, minutes=1)
    me = c.get("/auth/me").json()
    assert me["user"]["name"] == cast.DECK_DUTY_MANAGER and me["user"]["is_clefs_admin"] is False
    assert [(m["hotel_slug"], m["role"]) for m in me["memberships"]] == [(SLUG, "dept_head")]
    assert c.get(f"/hotels/{SLUG}/context").status_code == 200
    assert c.get(f"/hotels/{hotel_uae.slug}/context").status_code == 403
    # A duty manager works the desk and reads the quality gate, but cannot
    # change the hotel (its demo status included) or its team.
    assert c.get(f"/hotels/{SLUG}/trust/readiness").status_code == 200
    assert c.patch(f"/hotels/{SLUG}/settings", json={"status": "active"}).status_code == 403
    assert c.get(f"/hotels/{SLUG}/team").status_code == 403
    cur.execute("SELECT kind, scope FROM auth_sessions s JOIN users u ON u.id = s.user_id WHERE u.email = %s "
                "ORDER BY s.id DESC LIMIT 1", (cast.demo_email(cast.DUTY_MANAGER_LOCAL, SLUG),))
    assert dict(cur.fetchone()) == {"kind": "web", "scope": {"demo_access": SLUG}}


def test_console_refuses_a_demo_login_that_reaches_a_real_hotel(app, cur, seeded, hotel_uae):
    email = cast.demo_email(cast.DUTY_MANAGER_LOCAL, SLUG)
    cur.execute("UPDATE users SET is_clefs_admin = TRUE WHERE email = %s RETURNING id", (email,))
    uid = cur.fetchone()["id"]
    # The door strips admin powers on every sign-in.
    assert _fresh(app).post(f"/public/demo/{SLUG}/console").status_code == 200
    cur.execute("SELECT is_clefs_admin FROM users WHERE id = %s", (uid,))
    assert cur.fetchone()["is_clefs_admin"] is False
    cur.execute("INSERT INTO hotel_memberships (user_id, hotel_slug, role) VALUES (%s, %s, 'gm')", (uid, hotel_uae.slug))
    c = _fresh(app)
    r = c.post(f"/public/demo/{SLUG}/console")
    assert r.status_code == 409 and r.json()["code"] == "demo_login_not_isolated"
    assert "clefs_session" not in c.cookies


def test_console_creates_the_login_when_no_reseed_made_it(app, cur, seeded):
    email = cast.demo_email(cast.DUTY_MANAGER_LOCAL, SLUG)
    cur.execute("DELETE FROM hotel_memberships WHERE hotel_slug = %s AND role = 'dept_head'", (SLUG,))
    r = _fresh(app).post(f"/public/demo/{SLUG}/console")
    assert r.status_code == 200, r.text
    cur.execute("SELECT m.role, m.is_active FROM hotel_memberships m JOIN users u ON u.id = m.user_id "
                "WHERE u.email = %s AND m.hotel_slug = %s", (email, SLUG))
    assert dict(cur.fetchone()) == {"role": "dept_head", "is_active": True}


# ─── Abuse ───


def test_grants_are_rate_limited_per_ip(app, seeded, monkeypatch):
    monkeypatch.setattr(demo_access, "GRANT_IP_LIMIT", (2, 600))
    c = _fresh(app)
    codes = [c.post(f"/public/demo/{SLUG}/console").status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert c.post(f"/public/demo/{SLUG}/console").json()["code"] == "rate_limited"
    staff = [_fresh(app).post(f"/public/demo/{SLUG}/staff-app").status_code for _ in range(3)]
    assert staff == [200, 200, 429]


def test_reads_are_rate_limited_per_ip(app, seeded, monkeypatch):
    monkeypatch.setattr(demo_access, "INFO_IP_LIMIT", (2, 60))
    c = _fresh(app)
    assert [c.get(f"/public/demo/{SLUG}").status_code for _ in range(3)] == [200, 200, 429]


def test_grants_have_a_daily_ceiling_per_hotel(app, seeded, monkeypatch):
    monkeypatch.setattr(demo_access, "GRANT_HOTEL_LIMIT", (1, 86400))
    assert _fresh(app).post(f"/public/demo/{SLUG}/staff-app").status_code == 200
    assert _fresh(app).post(f"/public/demo/{SLUG}/staff-app").status_code == 429


def test_another_site_cannot_sign_a_browser_in(app, seeded):
    c = _fresh(app)
    for path in ("/staff-app", "/console"):
        r = c.post(f"/public/demo/{SLUG}{path}", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403 and r.json()["code"] == "csrf_failed"
    assert not c.cookies
