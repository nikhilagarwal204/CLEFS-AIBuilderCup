"""
Review hardening for the foundation wave:

  - identity is global: a hotel manages memberships, not people (invitations
    for existing accounts, no edits to a shared name or phone);
  - a magic-link session has no role outside its one hotel;
  - post-sign-in destinations are parsed, not prefix-checked;
  - uploads authenticate and size-check before the body is parsed; webhook
    bodies are capped; signature headers never crash the compare;
  - public guest writes validate the room and have hotel and IP ceilings;
  - client IPs come from spoofable headers only behind a proxy that rewrites them;
  - a login for a deactivated account costs the same scrypt as a wrong password.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clefs.auth import magic, passwords
from clefs.core import actions, hotels
from clefs.settings import get_settings

# ─── Team: invitations and shared identities ───


def test_adding_an_existing_account_creates_an_invitation(cur, app, client_as, make_user, login, hotel_uae):
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    owner_b = make_user("owner", "other-dubai", email="boss@other.test", name="Real Owner Name")
    cur.execute("UPDATE users SET phone_e164 = '+971509990000', last_login_at = NOW() WHERE id = %s", (owner_b["id"],))
    gm = client_as("gm")
    base = f"/hotels/{hotel_uae.slug}/team"

    r = gm.post(base, json={"email": "BOSS@other.test", "name": "Typed Name", "phone": "+971501234567", "role": "staff"})
    assert r.status_code == 201, r.text
    row = r.json()
    # Only what the caller typed comes back: never the account's name, phone or last sign-in.
    assert (row["status"], row["is_active"], row["user_id"]) == ("invited", False, None)
    assert (row["name"], row["phone"], row["last_login_at"]) == ("Typed Name", None, None)
    listed = next(m for m in gm.get(base).json() if m["id"] == row["id"])
    assert (listed["status"], listed["name"], listed["phone"], listed["last_login_at"]) == ("invited", "Typed Name", None, None)
    cur.execute("SELECT name, phone_e164 FROM users WHERE id = %s", (owner_b["id"],))
    assert dict(cur.fetchone()) == {"name": "Real Owner Name", "phone_e164": "+971509990000"}  # identity untouched

    # No access until the person accepts, and the manager cannot switch it on or set their phone.
    assert gm.patch(f"{base}/{row['id']}", json={"is_active": True}).json()["code"] == "invite_pending"
    assert gm.patch(f"{base}/{row['id']}", json={"phone": "+971500000001"}).json()["code"] == "invite_pending"
    renamed = gm.patch(f"{base}/{row['id']}", json={"role": "front_desk", "name": "Typed Again", "phone": None, "is_active": False})
    assert renamed.status_code == 200 and (renamed.json()["role"], renamed.json()["name"]) == ("front_desk", "Typed Again")
    again = gm.post(base, json={"email": "boss@other.test", "name": "Typed Name", "role": "staff"})
    assert again.status_code == 201 and again.json()["id"] == row["id"]  # re-inviting refreshes it

    invitee = TestClient(app)
    login(invitee, owner_b)
    assert invitee.get(f"/hotels/{hotel_uae.slug}/context").status_code == 403
    me = invitee.get("/auth/me").json()
    assert [(i["hotel_slug"], i["role"]) for i in me["invites"]] == [(hotel_uae.slug, "staff")]
    assert gm.post(f"/auth/invites/{row['id']}/accept").status_code == 404  # only the invitee answers it
    accepted = invitee.post(f"/auth/invites/{row['id']}/accept")
    assert accepted.status_code == 200 and accepted.json()["hotel_slug"] == hotel_uae.slug
    assert {m["hotel_slug"] for m in accepted.json()["memberships"]} == {"other-dubai", hotel_uae.slug}
    assert accepted.json()["invites"] == []
    assert invitee.get(f"/hotels/{hotel_uae.slug}/context").json()["role"] == "staff"
    listed = next(m for m in gm.get(base).json() if m["id"] == row["id"])
    assert (listed["status"], listed["name"]) == ("active", "Real Owner Name")
    assert invitee.post(f"/auth/invites/{row['id']}/accept").status_code == 404  # answered once
    cur.execute("SELECT action FROM audit_log WHERE entity_type = 'hotel_membership' AND entity_id = %s ORDER BY id", (str(row["id"]),))
    assert [r["action"] for r in cur.fetchall()] == [
        "core.team.invite", "core.team.update", "core.team.invite", "core.team.invite_accept",
    ]


def test_an_invitation_can_be_declined_or_withdrawn(cur, app, client_as, make_user, login, hotel_uae):
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    person = make_user("gm", "other-dubai")
    gm = client_as("gm")
    base = f"/hotels/{hotel_uae.slug}/team"
    inv = gm.post(base, json={"email": person["email"], "name": "P", "role": "staff"}).json()
    c = TestClient(app)
    login(c, person)
    assert c.post(f"/auth/invites/{inv['id']}/decline").status_code == 204
    assert all(m["id"] != inv["id"] for m in gm.get(base).json())
    inv2 = gm.post(base, json={"email": person["email"], "name": "P", "role": "staff"}).json()
    assert gm.delete(f"{base}/{inv2['id']}").status_code == 204
    assert c.get("/auth/me").json()["invites"] == []


def test_a_manager_cannot_rebind_a_shared_identity(cur, client_as, make_user, hotel_uae):
    """The phone is the WhatsApp identity magic links and approval buttons go
    to. A GM of this hotel must not be able to point another hotel's owner's
    number at a phone they control."""
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    shared = make_user("owner", "other-dubai", name="Shared Person")
    cur.execute(
        "INSERT INTO hotel_memberships (user_id, hotel_slug, role) VALUES (%s, %s, 'staff') RETURNING id",
        (shared["id"], hotel_uae.slug),
    )
    mid = cur.fetchone()["id"]
    gm = client_as("gm")
    path = f"/hotels/{hotel_uae.slug}/team/{mid}"
    r = gm.patch(path, json={"phone": "+971501234567"})
    assert r.status_code == 409 and r.json()["code"] == "profile_shared"
    assert gm.patch(path, json={"name": "Renamed"}).json()["code"] == "profile_shared"
    # Membership fields stay editable, and the unchanged name/phone the console always resends is not an edit.
    ok = gm.patch(path, json={"role": "front_desk", "name": "Shared Person", "phone": None})
    assert ok.status_code == 200 and ok.json()["role"] == "front_desk"
    cur.execute("SELECT name, phone_e164 FROM users WHERE id = %s", (shared["id"],))
    assert dict(cur.fetchone()) == {"name": "Shared Person", "phone_e164": None}

    local = make_user("staff", hotel_uae.slug)  # belongs to this hotel alone: still editable
    lr = gm.patch(f"/hotels/{hotel_uae.slug}/team/{local['membership_id']}", json={"phone": "+971501234568", "name": "Local"})
    assert lr.status_code == 200 and (lr.json()["phone"], lr.json()["name"]) == ("+971501234568", "Local")


def test_a_clefs_admin_attaches_an_existing_account_directly(cur, client_as, make_user, hotel_uae):
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    person = make_user("gm", "other-dubai")
    admin = client_as("owner", admin=True)
    row = admin.post(f"/hotels/{hotel_uae.slug}/team", json={"email": person["email"], "name": "x", "role": "gm"}).json()
    assert row["status"] == "active" and row["is_active"] is True


# ─── Magic-link sessions stay in their hotel ───


def test_a_magic_session_has_no_role_at_the_users_other_hotels(cur, app, make_user, hotel_uae):
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    u = make_user("owner", hotel_uae.slug)
    cur.execute("INSERT INTO hotel_memberships (user_id, hotel_slug, role) VALUES (%s, 'other-dubai', 'owner')", (u["id"],))
    a = actions.create_action(cur, "other-dubai", type="core.task", title="Assigned to them", actor=None)
    actions.decide(cur, a["id"], "assign", actor=None, assignee_user_id=u["id"])

    c = TestClient(app)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    assert c.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    assert c.post("/auth/stream-ticket", json={"hotel_slug": hotel_uae.slug}).status_code == 200
    assert c.post("/auth/stream-ticket", json={"hotel_slug": "other-dubai"}).status_code == 403
    assert c.get("/hotels/other-dubai/context").status_code == 403
    me = c.get("/auth/me", params={"hotel": "other-dubai"}).json()
    assert me["role"] is None and me["permissions"] == []
    assert c.get("/hotels/other-dubai/metrics").status_code == 403
    assert c.post(f"/hotels/other-dubai/actions/{a['id']}/done", json={}).status_code == 403


def test_a_magic_session_of_a_clefs_admin_is_not_an_admin(cur, app, make_user, hotel_uae):
    hotels.create_hotel(cur, slug="other-dubai", name="Other Hotel")
    admin = make_user("owner", hotel_uae.slug, admin=True)
    c = TestClient(app)
    token = magic.create_link(cur, user_id=admin["id"], hotel_slug=hotel_uae.slug, purpose="brief")
    assert c.post("/auth/magic/exchange", json={"token": token}).status_code == 200
    assert c.get("/hotels/other-dubai/metrics").status_code == 403
    assert c.post("/auth/stream-ticket", json={"hotel_slug": "other-dubai"}).status_code == 403


# ─── Post-sign-in destinations ───


@pytest.mark.parametrize(
    "bad",
    ["//evil.example", "///evil.example", "/\t/evil.example/login", "/\n/evil.example", "/\\evil.example",
     "https://evil.example", "/ok\x00", "app/x"],
)
def test_next_path_must_stay_on_our_origin(cur, make_user, client, hotel_uae, bad):
    assert magic.safe_next_path(bad) is None
    u = make_user("owner", hotel_uae.slug)
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="login", next_path=bad)
    assert client.post("/auth/magic/exchange", json={"token": token}).json()["next_path"] == f"/app/{hotel_uae.slug}"


def test_a_good_next_path_survives(cur, make_user, client, hotel_uae):
    u = make_user("owner", hotel_uae.slug)
    good = f"/app/{hotel_uae.slug}/brief?d=2026-09-28#top"
    token = magic.create_link(cur, user_id=u["id"], hotel_slug=hotel_uae.slug, purpose="brief", next_path=good)
    assert client.post("/auth/magic/exchange", json={"token": token}).json()["next_path"] == good


# ─── Login timing ───


def test_a_deactivated_account_costs_the_same_scrypt_as_a_wrong_password(client, cur, make_user, monkeypatch):
    user = make_user("owner")
    cur.execute("UPDATE users SET is_active = FALSE WHERE id = %s", (user["id"],))
    burned: list[str] = []
    monkeypatch.setattr(passwords, "burn_time", lambda pw: burned.append(pw))
    r = client.post("/auth/login", json={"email": user["email"], "password": user["password"]})
    assert r.status_code == 401 and burned == [user["password"]]


# ─── Uploads and webhook bodies ───


def _multipart(boundary: str, data: bytes, purpose: str = "profit.statement") -> bytes:
    return (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\n{purpose}\r\n"
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.csv\"\r\n"
        f"Content-Type: text/csv\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()


@pytest.fixture
def form_spy(monkeypatch):
    """Records every multipart parse, to prove refusals happen before one."""
    from starlette.requests import Request

    calls: list[str] = []
    original = Request.form

    def spy(self, *a, **kw):
        calls.append(self.url.path)
        return original(self, *a, **kw)

    monkeypatch.setattr(Request, "form", spy)
    return calls


def test_uploads_authenticate_before_the_body_is_parsed(client, client_as, hotel_uae, form_spy):
    body = _multipart("zz", b"a,b\n1,2\n")
    headers = {"Content-Type": "multipart/form-data; boundary=zz"}
    assert client.post(f"/hotels/{hotel_uae.slug}/files", content=body, headers=headers).status_code == 401
    assert client.post(f"/hotels/{hotel_uae.slug}/files?t=forged", content=body, headers=headers).status_code == 401
    assert form_spy == []
    owner = client_as("owner")
    ok = owner.post(f"/hotels/{hotel_uae.slug}/files", content=body, headers=headers)
    assert ok.status_code == 200, ok.text
    assert len(form_spy) == 1


def test_oversized_uploads_are_refused_before_or_while_reading(client_as, hotel_uae, env, form_spy):
    from clefs.core import routes_contracts

    env(FILE_MAX_BYTES="100")
    owner = client_as("owner")
    headers = {"Content-Type": "multipart/form-data; boundary=zz"}
    big = _multipart("zz", b"x" * (routes_contracts.UPLOAD_OVERHEAD_BYTES + 1000))
    r = owner.post(f"/hotels/{hotel_uae.slug}/files", content=big, headers=headers)
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    assert form_spy == []  # Content-Length alone was enough

    def chunks():  # no Content-Length: the byte-capped receive stops it
        yield big[:1000]
        yield big[1000:]

    r = owner.post(f"/hotels/{hotel_uae.slug}/files", content=chunks(), headers=headers)
    assert r.status_code == 413
    small_but_over = _multipart("zz", b"x" * 500)  # under the framing allowance, over the file cap
    assert owner.post(f"/hotels/{hotel_uae.slug}/files", content=small_but_over, headers=headers).status_code == 413
    assert owner.post(f"/hotels/{hotel_uae.slug}/files", content=b"{}", headers={"Content-Type": "application/json"}).status_code == 400


def test_webhook_bodies_are_capped_and_odd_signatures_do_not_crash(client, wa):
    from clefs.core import routes_contracts

    huge = b"{" + b" " * (routes_contracts.WEBHOOK_MAX_BYTES + 10) + b"}"
    r = client.post("/webhooks/whatsapp", content=huge, headers={"Content-Type": "application/json", "X-Hub-Signature-256": wa.sign(huge)})
    assert r.status_code == 413
    odd = client.post(
        "/webhooks/whatsapp", content=b"{}", headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=\xe9".encode("latin-1")}
    )
    assert odd.status_code == 401


# ─── Public guest writes ───


def test_public_pending_checks_the_room_against_the_register(client, cur, hotel_uae):
    base = f"/hotels/{hotel_uae.slug}/pending"
    assert client.post(base, json={"data": {"room": "999", "type": "Water"}}).status_code == 404
    assert client.post(base, json={"data": {"type": "Water"}}).status_code == 400
    r = client.post(base, json={"data": {"room": "101", "type": "Water", "description": "x" * 5000, "category": "c" * 300}})
    assert r.status_code == 200
    cur.execute("SELECT length(description) AS d, length(category) AS c FROM staff_requests WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert dict(cur.fetchone()) == {"d": 2000, "c": 100}


def test_public_pending_has_hotel_and_ip_ceilings(client, cur, legacy_slug, monkeypatch):
    from clefs.modules.service import legacy as service_legacy

    base = f"/hotels/{legacy_slug}/pending"
    assert client.post(base, json={"data": {"room": "<script>", "type": "Water"}}).status_code == 400
    monkeypatch.setattr(service_legacy, "PENDING_HOTEL_LIMIT", 3)
    codes = [client.post(base, json={"data": {"room": str(100 + i), "type": "Water"}}).status_code for i in range(4)]
    assert codes == [200, 200, 200, 429]  # rotating the room no longer escapes the limit

    monkeypatch.setattr(service_legacy, "PENDING_HOTEL_LIMIT", 1000)
    # This address has 3 recorded hits: refused attempts (the bad room, the
    # 429) roll back with their transaction and are not counted.
    monkeypatch.setattr(service_legacy, "PENDING_IP_LIMIT", 3)
    cur.execute("SELECT slug FROM hotels WHERE legacy_open_access AND slug <> %s ORDER BY slug LIMIT 1", (legacy_slug,))
    other = cur.fetchone()["slug"]
    # A spoofed X-Forwarded-For does not buy a fresh per-IP budget.
    r = client.post(f"/hotels/{other}/pending", json={"data": {"room": "201", "type": "Water"}}, headers={"X-Forwarded-For": "198.51.100.7"})
    assert r.status_code == 429


def test_a_long_numeric_room_does_not_break_the_service_view(client, cur, legacy_slug):
    room = "9" * 23  # valid by ROOM_RE; a BIGINT cast of it overflows
    assert client.post(f"/hotels/{legacy_slug}/pending", json={"data": {"room": room, "type": "Water"}}).status_code == 200
    cur.execute("SELECT floor FROM service_requests_v1 WHERE hotel_slug = %s AND room = %s", (legacy_slug, room))
    assert cur.fetchone()["floor"] == "9" * 21


def test_conversation_logs_need_a_real_hotel_and_are_bounded(client, cur, legacy_slug, monkeypatch):
    from clefs.modules.concierge import legacy as concierge_legacy

    entry = {"session_id": "s", "room_number": "502", "role": "user", "message": "hi", "timestamp": 1790000000000}
    assert client.post("/hotels/no-such-hotel/conversation-logs", json=entry).status_code == 404
    long = {**entry, "message": "m" * 20000, "room_number": "r" * 500, "session_id": "s" * 500, "timestamp": 10**20}
    assert client.post(f"/hotels/{legacy_slug}/conversation-logs", json=long).status_code == 200
    cur.execute(
        "SELECT length(message) AS m, length(room_number) AS r, length(session_id) AS s, timestamp FROM conversation_logs "
        "WHERE hotel_slug = %s ORDER BY id DESC LIMIT 1",
        (legacy_slug,),
    )
    row = cur.fetchone()
    assert (row["m"], row["r"], row["s"]) == (8000, 128, 128) and row["timestamp"] < 2**53
    monkeypatch.setattr(concierge_legacy, "LOG_LIMIT_PER_HOTEL", 3)
    codes = [
        client.post(f"/hotels/{legacy_slug}/conversation-logs", json={**entry, "room_number": f"room-{i}"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]  # 1 above + 2 here, then the hotel ceiling


# ─── Client IP ───


def test_client_ip_ignores_forwarded_headers_unless_a_trusted_proxy_set_them(env):
    from starlette.datastructures import Headers

    from clefs.app import _client_ip

    scope = {"client": ("203.0.113.9", 5000)}
    spoofed = Headers({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})
    assert _client_ip(scope, spoofed, get_settings()) == "203.0.113.9"
    env(TRUST_FORWARDED_FOR="true")  # behind Vercel, which overwrites both headers
    assert _client_ip(scope, spoofed, get_settings()) == "1.2.3.4"
    assert _client_ip(scope, Headers({"x-real-ip": "9.9.9.9", "x-forwarded-for": "1.2.3.4"}), get_settings()) == "9.9.9.9"
    env(TRUST_FORWARDED_FOR=None, VERCEL="1")
    assert get_settings().TRUST_FORWARDED_FOR is True
