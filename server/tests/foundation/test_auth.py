"""
Auth and RBAC: passwords, tokens, login/logout/me, CSRF, permissions,
admin bypass, magic links, stream/upload tickets, devices and PIN shifts,
team and settings management. Shapes follow the SHARED HTTP CONTRACT.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import Depends, Request
from fastapi.testclient import TestClient

from clefs.auth import magic, passwords, tokens
from clefs.auth.deps import acting_staff, resolve_ctx

GOOD_ORIGIN = {"Origin": "http://localhost:3000"}


# ─── Passwords and tokens ───


def test_scrypt_hash_roundtrip_and_encoding():
    h = passwords.hash_password("s3cret-password")
    assert h.startswith("scrypt$16384$8$1$")
    assert passwords.verify_password("s3cret-password", h)
    assert not passwords.verify_password("wrong", h)
    assert not passwords.verify_password("x", None)
    assert not passwords.verify_password("x", "garbage")
    assert not passwords.needs_rehash(h)


def test_pins_must_be_4_to_6_digits():
    for bad in ("123", "1234567", "12a4", ""):
        with pytest.raises(ValueError):
            passwords.hash_pin(bad)
    h = passwords.hash_pin("0420")
    assert passwords.verify_pin("0420", h) and not passwords.verify_pin("0421", h)


def test_tickets_are_purpose_bound_and_expire():
    t = tokens.mint_ticket("stream", {"h": "x"}, ttl_s=60)
    assert tokens.verify_ticket("stream", t)["h"] == "x"
    assert tokens.verify_ticket("upload", t) is None
    payload, sig = t.split(".")
    assert tokens.verify_ticket("stream", payload + "." + sig[:-2] + "AA") is None
    expired = tokens.mint_ticket("stream", {"h": "x"}, ttl_s=-1)
    assert tokens.verify_ticket("stream", expired) is None


def test_signed_refs_for_whatsapp_buttons():
    signed = tokens.sign_ref("brief:approve:42")
    assert tokens.verify_ref(signed) == "brief:approve:42"
    assert tokens.verify_ref(signed.replace("42", "43")) is None
    assert tokens.verify_ref("no-tag") is None
    assert len(signed) <= 256


def test_pairing_codes_are_typeable():
    code = tokens.new_pairing_code()
    assert len(code) == 9 and code[4] == "-"
    assert not set(code.replace("-", "")) & set("01OIL")
    assert tokens.normalize_pairing_code(code.lower().replace("-", " ")) == code


# ─── Login / logout / me ───


def test_login_sets_httponly_cookie_and_returns_contract_shape(client, make_user, hotel_uae):
    user = make_user("owner", hotel_uae.slug)
    r = client.post("/auth/login", json={"email": user["email"].upper(), "password": user["password"]})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"user", "memberships", "invites"}
    assert body["invites"] == []
    assert {"id", "name", "email", "is_clefs_admin"} <= set(body["user"])
    assert body["memberships"] == [{"hotel_slug": "test-dubai", "hotel_name": "Test Hotel Dubai", "role": "owner"}]
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("clefs_session=")
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert "Secure" not in cookie  # only when COOKIE_SECURE=true
    assert "Domain" not in cookie


def test_login_failures_are_uniform(client, make_user):
    user = make_user("owner")
    for email, pw in ((user["email"], "wrong-password"), ("nobody@example.test", "whatever")):
        r = client.post("/auth/login", json={"email": email, "password": pw})
        assert r.status_code == 401
        assert r.json() == {"detail": "Email or password is incorrect", "code": "invalid_credentials"}


def test_login_is_rate_limited_per_email(client, make_user):
    user = make_user("owner")
    codes = [client.post("/auth/login", json={"email": user["email"], "password": "nope"}).status_code for _ in range(11)]
    assert codes[:10] == [401] * 10 and codes[10] == 429


def test_inactive_users_cannot_log_in(client, cur, make_user):
    user = make_user("owner")
    cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (user["id"],))
    assert client.post("/auth/login", json={"email": user["email"], "password": user["password"]}).status_code == 401


def test_me_with_session_resolves_role_and_permissions(client_as, hotel_uae):
    c = client_as("finance")
    me = c.get("/auth/me", params={"hotel": hotel_uae.slug}).json()
    assert me["role"] == "finance" and me["legacy_mode"] is False and me["hotel_slug"] == "test-dubai"
    assert "profit.view" in me["permissions"] and "core.settings.manage" not in me["permissions"]
    assert me["user"]["email"] == c.user["email"]
    assert c.get("/auth/me").json()["permissions"] == []


def test_me_anonymous_is_401_unless_hotel_is_legacy(client, hotel_uae, legacy_slug):
    assert client.get("/auth/me").status_code == 401
    r = client.get("/auth/me", params={"hotel": hotel_uae.slug})
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    legacy = client.get("/auth/me", params={"hotel": legacy_slug}).json()
    assert legacy["user"] is None and legacy["legacy_mode"] is True and legacy["role"] is None
    assert "service.view" in legacy["permissions"]


def test_logout_revokes_session_and_clears_cookie(client_as, cur, hotel_uae):
    c = client_as("gm")
    token = c.cookies.get("clefs_session")
    r = c.post("/auth/logout", headers=GOOD_ORIGIN)
    assert r.status_code == 204
    assert 'clefs_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
    cur.execute("SELECT revoked_at FROM auth_sessions WHERE token_hash = %s", (tokens.hash_token(token),))
    assert cur.fetchone()["revoked_at"] is not None
    stale = TestClient(c.app, cookies={"clefs_session": token})
    assert stale.get("/auth/me").status_code == 401


def test_bearer_session_token_works_for_scripts(app, client_as, hotel_uae):
    c = client_as("owner")
    token = c.cookies.get("clefs_session")
    bare = TestClient(app)
    r = bare.get("/auth/me", params={"hotel": hotel_uae.slug}, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["role"] == "owner"


def test_expired_session_is_rejected(client_as, clock, hotel_uae):
    c = client_as("owner")
    clock.freeze(clock.now() + timedelta(days=31))
    from clefs.auth import sessions

    sessions.clear_cache()
    assert c.get("/auth/me").status_code == 401


# ─── CSRF ───


def test_cookie_mutations_require_our_origin(client_as, hotel_uae):
    c = client_as("owner")
    path = f"/hotels/{hotel_uae.slug}/settings"
    evil = c.patch(path, json={"city": "Evil"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403 and evil.json()["code"] == "csrf_failed"
    cross = c.patch(path, json={"city": "X"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert cross.status_code == 403
    assert c.patch(path, json={"city": "Abu Dhabi"}, headers=GOOD_ORIGIN).status_code == 200
    # No Origin and no Sec-Fetch-Site: a non-browser client, not a CSRF victim.
    assert c.patch(path, json={"city": "Dubai"}).status_code == 200
    # Reads are never blocked.
    assert c.get(path, headers={"Origin": "https://evil.example"}).status_code == 200


def test_cookieless_requests_skip_csrf(client, legacy_slug):
    r = client.post(
        f"/hotels/{legacy_slug}/conversation-logs",
        json={"session_id": "s", "room_number": "1", "role": "user", "message": "hi", "timestamp": 1},
        headers={"Origin": "https://some-guest-browser.example"},
    )
    assert r.status_code == 200


def test_cors_allowlist_without_credentials(client):
    ok = client.options(
        "/auth/login",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-credentials" not in ok.headers
    bad = client.options(
        "/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert bad.headers.get("access-control-allow-origin") is None


# ─── Permissions ───


def test_require_perm_401_403_200(client, client_as, hotel_uae):
    path = f"/hotels/{hotel_uae.slug}/settings"
    assert client.get(path).status_code == 401
    staff = client_as("staff")
    assert staff.get(path).status_code == 403
    assert client_as("finance").get(path).status_code == 200
    assert client_as("finance").patch(path, json={"city": "x"}).status_code == 403
    assert client_as("gm").patch(path, json={"city": "Sharjah"}).json()["city"] == "Sharjah"


def test_membership_in_another_hotel_gives_nothing(cur, client_as, hotel_uae):
    from clefs.core import hotels

    hotels.create_hotel(cur, slug="other-hotel", name="Other")
    c = client_as("owner", hotel="other-hotel")
    assert c.get(f"/hotels/{hotel_uae.slug}/settings").status_code == 403
    assert c.get(f"/hotels/{hotel_uae.slug}/requests").status_code == 403


def test_clefs_admin_bypass_is_audited_once(cur, app, make_user, login, hotel_uae):
    admin = make_user(None, admin=True)
    c = TestClient(app)
    login(c, admin)
    for _ in range(3):
        assert c.get(f"/hotels/{hotel_uae.slug}/settings").status_code == 200
    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'auth.admin_bypass' AND hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 1
    me = c.get("/auth/me", params={"hotel": hotel_uae.slug}).json()
    assert "core.settings.manage" in me["permissions"] and me["role"] is None


def test_dept_head_scope_is_in_context(cur, make_user, hotel_uae):
    from clefs.auth.deps import Principal

    u = make_user("dept_head", hotel_uae.slug, departments=["Housekeeping"], floors=["5"])
    ctx = resolve_ctx(Principal(kind="user", user_id=u["id"]), hotel_uae.slug, cur=cur)
    assert ctx.departments == ("Housekeeping",) and ctx.floors == ("5",)
    assert ctx.can("service.manage") and not ctx.can("profit.view")


def test_require_service_tokens(app):
    from clefs.app import create_app
    from clefs.auth.deps import require_service

    a = create_app()

    @a.post("/internal/test-tick")
    def tick(p=Depends(require_service("cron"))):
        return {"by": p.label}

    c = TestClient(a)
    assert c.post("/internal/test-tick").status_code == 401
    assert c.post("/internal/test-tick", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert c.post("/internal/test-tick", headers={"Authorization": "Bearer test-cron-secret"}).json() == {"by": "cron"}
    assert c.post("/internal/test-tick", headers={"Authorization": "Bearer test-worker-token"}).json() == {"by": "worker"}


# ─── Magic links ───


def test_magic_link_exchange_is_post_only_scoped_and_limited(cur, app, make_user, hotel_uae):
    u = make_user("owner", hotel_uae.slug)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief", next_path="/app/test-dubai/brief")
    c = TestClient(app)
    assert c.get(f"/auth/magic/exchange?token={token}").status_code == 405
    r = c.post("/auth/magic/exchange", json={"token": token})
    assert r.status_code == 200
    assert r.json() == {
        "next_path": "/app/test-dubai/brief",
        "user": r.json()["user"],
        "hotel_slug": "test-dubai",
    }
    assert r.json()["user"]["id"] == u["id"]
    me = c.get("/auth/me", params={"hotel": hotel_uae.slug}).json()
    assert me["permissions"] and all(p.startswith(("brief.", "core.signals.view", "core.actions.")) for p in me["permissions"])
    assert c.patch(f"/hotels/{hotel_uae.slug}/settings", json={"city": "x"}).status_code == 403
    cur.execute("SELECT kind, expires_at - created_at AS ttl FROM auth_sessions WHERE user_id = %s", (u["id"],))
    row = cur.fetchone()
    assert row["kind"] == "magic" and row["ttl"] <= timedelta(hours=12, minutes=1)
    for _ in range(2):
        assert TestClient(app).post("/auth/magic/exchange", json={"token": token}).status_code == 200
    fourth = TestClient(app).post("/auth/magic/exchange", json={"token": token})
    assert fourth.status_code == 401 and fourth.json()["code"] == "magic_link_invalid"


def test_magic_link_expires_and_rejects_open_redirects(cur, client, make_user, clock, hotel_uae):
    u = make_user("gm", hotel_uae.slug)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="login", next_path="//evil.example")
    cur.execute("SELECT next_path FROM magic_links WHERE user_id = %s", (u["id"],))
    assert cur.fetchone()["next_path"] == "/app/test-dubai"
    clock.freeze(clock.now() + timedelta(hours=25))
    assert client.post("/auth/magic/exchange", json={"token": token}).status_code == 401


def test_magic_session_for_another_hotel_has_no_permissions(cur, app, make_user, hotel_uae):
    from clefs.core import hotels

    hotels.create_hotel(cur, slug="second-hotel", name="Second")
    u = make_user("owner", hotel_uae.slug)
    cur.execute("INSERT INTO hotel_memberships (user_id, hotel_slug, role) VALUES (%s, 'second-hotel', 'owner')", (u["id"],))
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    c = TestClient(app)
    c.post("/auth/magic/exchange", json={"token": token})
    assert c.get("/auth/me", params={"hotel": "second-hotel"}).json()["permissions"] == []


# ─── Tickets ───


def test_stream_ticket_and_ticketed_stream(client_as, client, monkeypatch, hotel_uae):
    from clefs import events

    monkeypatch.setattr(events, "STREAM_MAX_S", 0.1)
    c = client_as("dept_head", departments=["Housekeeping"])
    r = c.post("/auth/stream-ticket", json={"hotel_slug": hotel_uae.slug}, headers=GOOD_ORIGIN)
    assert r.status_code == 200 and r.json()["expires_in"] == 60
    url = r.json()["url"]
    assert url.startswith("/hotels/test-dubai/stream?t=")
    claims = tokens.verify_ticket("stream", url.split("t=", 1)[1])
    assert claims["h"] == "test-dubai" and claims["r"] == "dept_head" and claims["dep"] == ["Housekeeping"]
    assert client.get(url).status_code == 200  # ticket only, no cookie
    assert client.get(f"/hotels/{hotel_uae.slug}/stream").status_code == 401
    assert client.get(f"/hotels/{hotel_uae.slug}/stream?t=forged.ticket").status_code == 401


def test_stream_ticket_for_another_hotel_is_refused(cur, client, client_as, hotel_uae):
    from clefs.core import hotels

    hotels.create_hotel(cur, slug="elsewhere", name="Elsewhere")
    t = tokens.mint_ticket("stream", {"k": "user", "h": "elsewhere"})
    assert client.get(f"/hotels/{hotel_uae.slug}/stream?t={t}").status_code == 403
    assert client.post("/auth/stream-ticket", json={"hotel_slug": hotel_uae.slug}).status_code == 401


def test_upload_ticket_needs_a_member(client, client_as, hotel_uae, legacy_slug):
    c = client_as("finance")
    r = c.post("/files/upload-ticket", json={"hotel_slug": hotel_uae.slug, "purpose": "ota_statement"}, headers=GOOD_ORIGIN)
    assert r.status_code == 200
    t = r.json()["url"].split("t=", 1)[1]
    assert r.json()["url"].startswith("/hotels/test-dubai/files?t=")
    assert tokens.verify_ticket("upload", t)["purpose"] == "ota_statement"
    assert client.post("/files/upload-ticket", json={"hotel_slug": legacy_slug, "purpose": "x"}).status_code == 401
    assert c.post("/files/upload-ticket", json={"hotel_slug": hotel_uae.slug, "purpose": "Bad Purpose!"}).status_code == 400


# ─── Devices and PIN shifts ───


def _staff(cur, slug, name, department, pin=None, floors=()):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, floors, pin_hash) VALUES (%s, %s, 'Attendant', %s, %s, %s) RETURNING id",
        (slug, name, department, list(floors), passwords.hash_pin(pin) if pin else None),
    )
    return cur.fetchone()["id"]


def test_pairing_flow_contract(client_as, hotel_uae):
    owner = client_as("owner")
    r = owner.post(
        f"/hotels/{hotel_uae.slug}/devices/pairing-codes",
        json={"name": "Floor 5", "kind": "floor", "floors": ["5"], "departments": ["Housekeeping"]},
        headers=GOOD_ORIGIN,
    )
    assert r.status_code == 200 and set(r.json()) == {"code", "expires_at", "pair_url"}
    code = r.json()["code"]
    tablet = TestClient(owner.app)
    p = tablet.post("/auth/devices/pair", json={"code": code.lower()})
    assert p.status_code == 200
    assert p.json() == {
        "device": {"id": p.json()["device"]["id"], "name": "Floor 5", "kind": "floor", "floors": ["5"], "departments": ["Housekeeping"]},
        "hotel_slug": "test-dubai",
    }
    assert p.headers["set-cookie"].startswith("clefs_device=") and "HttpOnly" in p.headers["set-cookie"]
    assert TestClient(owner.app).post("/auth/devices/pair", json={"code": code}).status_code == 400  # single use
    listed = owner.get(f"/hotels/{hotel_uae.slug}/devices").json()
    assert [d["name"] for d in listed] == ["Floor 5"]
    ctx = tablet.get(f"/hotels/{hotel_uae.slug}/context").json()
    assert ctx["principal_kind"] == "device" and "service.view" in ctx["permissions"]


def test_pairing_codes_expire(cur, client, clock, hotel_uae):
    from clefs.auth import devices

    out = devices.create_pairing_code(cur, hotel_uae.slug, name="T", kind="desk", floors=[], departments=[], actor=None)
    clock.freeze(clock.now() + timedelta(minutes=16))
    assert client.post("/auth/devices/pair", json={"code": out["code"]}).status_code == 400


def test_roster_is_scoped_to_the_device(cur, device_client, hotel_uae):
    hk5 = _staff(cur, hotel_uae.slug, "Asha", "Housekeeping", pin="1234", floors=["5"])
    _staff(cur, hotel_uae.slug, "Ben", "Housekeeping", floors=["2"])
    anyfloor = _staff(cur, hotel_uae.slug, "Cara", "Housekeeping")
    _staff(cur, hotel_uae.slug, "Dev", "Engineering", pin="9999")
    tablet = device_client(floors=["5"], departments=["housekeeping"])
    roster = tablet.get("/auth/devices/roster").json()
    assert [(s["id"], s["name"]) for s in roster] == [(hk5, "Asha"), (anyfloor, "Cara")]
    assert roster[0]["has_pin"] is True and roster[0]["on_shift"] is False
    assert {"id", "name", "department", "on_shift", "has_pin"} <= set(roster[0])


def test_pin_shift_start_end_and_lockout(cur, device_client, hotel_uae):
    sid = _staff(cur, hotel_uae.slug, "Asha", "Housekeeping", pin="1234")
    a = device_client(departments=["Housekeeping"], name="A")
    b = device_client(departments=["Housekeeping"], name="B")
    for i in range(4):
        r = a.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "0000"})
        assert r.status_code == 401 and r.json()["code"] == "invalid_pin"
    fifth = a.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "0000"})
    assert fifth.status_code == 423 and fifth.json()["code"] == "pin_locked"
    # Locked on device A even with the right PIN; device B is unaffected.
    assert a.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"}).status_code == 423
    ok = b.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"session_id", "staff", "planned_end_at"}
    assert body["staff"] == {"id": sid, "name": "Asha", "department": "Housekeeping"}
    cur.execute("SELECT is_online FROM staff_members WHERE id = %s", (sid,))
    assert cur.fetchone()["is_online"] is True
    again = b.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"})
    assert again.json()["session_id"] == body["session_id"]  # idempotent
    end = b.post("/auth/devices/shift/end", json={"session_id": body["session_id"], "handover_note": "Room 502 needs a cot"})
    assert end.status_code == 200 and end.json()["ok"] is True
    cur.execute("SELECT is_online FROM staff_members WHERE id = %s", (sid,))
    assert cur.fetchone()["is_online"] is False
    cur.execute("SELECT handover_note, end_reason FROM device_sessions WHERE id = %s", (body["session_id"],))
    assert cur.fetchone() == {"handover_note": "Room 502 needs a cot", "end_reason": "manual"}


def test_pin_lock_lifts_after_five_minutes(cur, device_client, clock, hotel_uae):
    sid = _staff(cur, hotel_uae.slug, "Asha", "Housekeeping", pin="1234")
    a = device_client()
    t0 = clock.freeze(clock.now())
    for _ in range(5):
        a.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "0000"})
    clock.freeze(t0 + timedelta(minutes=5, seconds=1))
    assert a.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"}).status_code == 200


def test_staff_outside_device_scope_and_missing_pin(cur, device_client, hotel_uae):
    eng = _staff(cur, hotel_uae.slug, "Dev", "Engineering", pin="1234")
    nopin = _staff(cur, hotel_uae.slug, "Nia", "Housekeeping")
    tablet = device_client(departments=["Housekeeping"])
    assert tablet.post("/auth/devices/shift/start", json={"staff_member_id": eng, "pin": "1234"}).status_code == 404
    r = tablet.post("/auth/devices/shift/start", json={"staff_member_id": nopin, "pin": "1234"})
    assert r.status_code == 409 and r.json()["code"] == "pin_not_set"


def test_manager_sets_pin_and_revokes_device(cur, client_as, device_client, hotel_uae):
    sid = _staff(cur, hotel_uae.slug, "Asha", "Housekeeping")
    owner = client_as("owner")
    assert owner.put(f"/hotels/{hotel_uae.slug}/staff/{sid}/pin", json={"pin": "12"}).status_code == 400
    assert owner.put(f"/hotels/{hotel_uae.slug}/staff/{sid}/pin", json={"pin": "2468"}).json() == {"ok": True, "has_pin": True}
    tablet = device_client()
    session = tablet.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "2468"}).json()
    assert owner.delete(f"/hotels/{hotel_uae.slug}/devices/{tablet.device['id']}").status_code == 204
    assert tablet.get("/auth/devices/roster").status_code == 401
    cur.execute("SELECT end_reason FROM device_sessions WHERE id = %s", (session["session_id"],))
    assert cur.fetchone()["end_reason"] == "device_revoked"


def test_device_permissions_on_legacy_endpoints(cur, device_client, hotel_uae):
    tablet = device_client()
    assert tablet.get(f"/hotels/{hotel_uae.slug}/requests").status_code == 200
    assert tablet.get(f"/hotels/{hotel_uae.slug}/guests").status_code == 403  # guest PII
    assert tablet.get(f"/hotels/{hotel_uae.slug}/settings").status_code == 403


def test_acting_staff_header_is_checked_against_open_shifts(cur, app, device_client, hotel_uae):
    from clefs.app import create_app
    from clefs.auth.deps import HotelCtx, require_device

    a = create_app()

    @a.get("/hotels/{slug}/whoami")
    def whoami(request: Request, ctx: HotelCtx = Depends(require_device())):
        return {"staff": acting_staff(ctx, request)}

    sid = _staff(cur, hotel_uae.slug, "Asha", "Housekeeping", pin="1234")
    other = _staff(cur, hotel_uae.slug, "Ben", "Housekeeping", pin="1234")
    tablet = device_client()
    t = TestClient(a, cookies=dict(tablet.cookies))
    assert t.get(f"/hotels/{hotel_uae.slug}/whoami").json() == {"staff": None}
    tablet.post("/auth/devices/shift/start", json={"staff_member_id": sid, "pin": "1234"})
    assert t.get(f"/hotels/{hotel_uae.slug}/whoami", headers={"X-Clefs-Staff": str(sid)}).json() == {"staff": sid}
    r = t.get(f"/hotels/{hotel_uae.slug}/whoami", headers={"X-Clefs-Staff": str(other)})
    assert r.status_code == 403 and r.json()["code"] == "staff_not_on_shift"


# ─── Context, settings, module settings ───


def test_context_contract_for_member_and_legacy_visitor(client, client_as, hotel_uae, legacy_slug):
    ctx = client_as("revenue").get(f"/hotels/{hotel_uae.slug}/context").json()
    assert set(ctx) == {"hotel", "modules_enabled", "role", "permissions", "legacy_open_access", "principal_kind"}
    assert set(ctx["hotel"]) == {
        "slug", "name", "city", "country", "emirate", "timezone", "currency", "locale", "guest_languages",
        "weekend_days", "brief_time_local", "star_rating", "rooms_count", "assistant_name", "status",
    }
    assert ctx["hotel"]["currency"] == "AED" and ctx["hotel"]["weekend_days"] == [6, 7]
    assert ctx["hotel"]["brief_time_local"] == "07:00" and ctx["role"] == "revenue"
    assert ctx["principal_kind"] == "user" and ctx["legacy_open_access"] is False
    assert "demand" in ctx["modules_enabled"] and len(ctx["modules_enabled"]) == 15
    assert client.get(f"/hotels/{hotel_uae.slug}/context").status_code == 401
    legacy = client.get(f"/hotels/{legacy_slug}/context").json()
    assert legacy["principal_kind"] == "anonymous" and legacy["legacy_open_access"] is True
    assert "service.view" in legacy["permissions"]


def test_settings_patch_validates_and_audits(cur, client_as, hotel_uae):
    c = client_as("owner")
    path = f"/hotels/{hotel_uae.slug}/settings"
    assert c.patch(path, json={"timezone": "Mars/Olympus"}).status_code == 422
    assert c.patch(path, json={"weekend_days": [0, 6]}).status_code == 422
    assert c.patch(path, json={"emirate": "atlantis"}).status_code == 422
    assert c.patch(path, json={"legacy_open_access": True}).status_code == 403
    r = c.patch(path, json={"brief_time_local": "06:30", "currency": "aed", "weekend_days": [7, 6]})
    assert r.status_code == 200
    assert (r.json()["brief_time_local"], r.json()["currency"], r.json()["weekend_days"]) == ("06:30", "AED", [6, 7])
    assert c.get(f"/hotels/{hotel_uae.slug}/context").json()["hotel"]["brief_time_local"] == "06:30"
    cur.execute("SELECT before, after FROM audit_log WHERE action = 'core.settings.update' AND hotel_slug = %s", (hotel_uae.slug,))
    row = cur.fetchone()
    assert row["before"] == {"brief_time_local": "07:00"} and row["after"] == {"brief_time_local": "06:30"}


def test_module_settings_roundtrip(cur, client_as, hotel_uae):
    c = client_as("owner")
    assert c.get(f"/hotels/{hotel_uae.slug}/module-settings/nope").status_code == 404
    got = c.get(f"/hotels/{hotel_uae.slug}/module-settings/demand").json()
    assert got == {"module": "demand", "enabled": True, "settings": {}, "schema": None, "updated_at": None}
    put = c.put(f"/hotels/{hotel_uae.slug}/module-settings/demand", json={"enabled": False, "settings": {"comp_set": ["a"]}})
    assert put.status_code == 200 and put.json()["enabled"] is False and put.json()["settings"] == {"comp_set": ["a"]}
    ctx = c.get(f"/hotels/{hotel_uae.slug}/context").json()
    assert "demand" not in ctx["modules_enabled"]
    assert client_as("finance").put(f"/hotels/{hotel_uae.slug}/module-settings/demand", json={"settings": {}}).status_code == 403


# ─── Team ───


def test_team_crud_and_owner_guards(cur, client_as, hotel_uae):
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/team"
    r = owner.post(base, json={"email": "GM@Example.test", "name": "Gina", "phone": "+971 50 123 4567", "role": "gm", "departments": [], "floors": []})
    assert r.status_code == 201
    gm_row = r.json()
    assert set(gm_row) >= {"id", "user_id", "name", "email", "phone", "role", "departments", "floors", "is_active", "last_login_at"}
    assert gm_row["email"] == "gm@example.test" and gm_row["phone"] == "+971501234567"
    assert owner.post(base, json={"email": "gm@example.test", "name": "Gina", "role": "gm"}).status_code == 409
    assert owner.post(base, json={"email": "x@example.test", "name": "X", "role": "chef"}).status_code == 400
    listed = owner.get(base).json()
    assert [m["role"] for m in listed] == ["owner", "gm"]

    gm = client_as("gm")
    assert gm.post(base, json={"email": "o2@example.test", "name": "O2", "role": "owner"}).status_code == 403
    owner_row = next(m for m in listed if m["role"] == "owner")
    assert gm.patch(f"{base}/{owner_row['id']}", json={"role": "gm"}).status_code == 403
    assert owner.patch(f"{base}/{owner_row['id']}", json={"role": "gm"}).status_code == 409  # last owner
    assert owner.delete(f"{base}/{owner_row['id']}").status_code == 409

    patched = owner.patch(f"{base}/{gm_row['id']}", json={"role": "dept_head", "departments": ["Housekeeping"], "name": "Gina H"})
    assert patched.status_code == 200
    assert (patched.json()["role"], patched.json()["departments"], patched.json()["name"]) == ("dept_head", ["Housekeeping"], "Gina H")
    assert owner.delete(f"{base}/{gm_row['id']}").status_code == 204
    assert all(m["id"] != gm_row["id"] for m in owner.get(base).json())
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'core.team.%%' ORDER BY id", (hotel_uae.slug,))
    assert [r["action"] for r in cur.fetchall()] == ["core.team.add", "core.team.update", "core.team.remove"]


def test_removed_member_loses_access_immediately(client_as, cur, hotel_uae):
    staff = client_as("front_desk")
    assert staff.get(f"/hotels/{hotel_uae.slug}/guests").status_code == 200
    cur.execute("UPDATE hotel_memberships SET is_active = FALSE WHERE id = %s", (staff.user["membership_id"],))
    assert staff.get(f"/hotels/{hotel_uae.slug}/guests").status_code == 403


def test_magic_request_is_uniform_and_hands_off_to_a_sender(cur, client, make_user, monkeypatch, hotel_uae):
    sent: list[tuple] = []
    monkeypatch.setattr(magic, "_senders", [lambda user, slug, url, purpose: sent.append((user["id"], slug, url, purpose)) or True])
    u = make_user("gm", hotel_uae.slug)
    unknown = client.post("/auth/magic/request", json={"email": "nobody@example.test"})
    known = client.post("/auth/magic/request", json={"email": u["email"].upper(), "purpose": "login"})
    assert unknown.status_code == known.status_code == 202 and unknown.json() == known.json() == {"ok": True}
    assert len(sent) == 1 and sent[0][0] == u["id"] and sent[0][1] == hotel_uae.slug
    token = sent[0][2].rsplit("/", 1)[1]
    assert client.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    assert client.post("/auth/magic/request", json={"email": u["email"], "purpose": "nope"}).status_code == 400


def test_module_settings_are_validated_by_the_module_model(client_as, monkeypatch, hotel_uae):
    from pydantic import BaseModel, Field

    from clefs.core import routes as core_routes

    class DemandSettings(BaseModel):
        comp_set: list[str] = []
        min_rate_aed: int = Field(default=100, ge=0)

    monkeypatch.setitem(core_routes.SETTINGS_MODELS, "demand", DemandSettings)
    c = client_as("owner")
    path = f"/hotels/{hotel_uae.slug}/module-settings/demand"
    got = c.get(path).json()
    assert got["settings"] == {"comp_set": [], "min_rate_aed": 100} and got["schema"]["title"] == "DemandSettings"
    bad = c.put(path, json={"settings": {"min_rate_aed": -5}})
    assert bad.status_code == 422
    assert bad.json()["detail"][0]["loc"] == ["body", "settings", "min_rate_aed"]
    ok = c.put(path, json={"settings": {"min_rate_aed": 250}})
    assert ok.json()["settings"] == {"comp_set": [], "min_rate_aed": 250}
