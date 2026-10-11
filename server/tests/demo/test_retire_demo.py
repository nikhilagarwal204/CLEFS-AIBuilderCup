"""retire-demo: a retired demo hotel is unreachable to the public (the guest
page, the landing page's doors, device pairing and the staff app, console
sign-in), every row is kept, and --restore brings it back."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from clefs import cli
from clefs.auth import devices
from clefs.core import rooms
from clefs.core.errors import ClefsError
from clefs.seed import demo_uae, retire

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-retire"
PASSWORD = "demo-pass-123"


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core", "core_catalog", "concierge_live", "deck"],
                         password=PASSWORD)


def _counts(cur) -> dict:
    out = {}
    for table in ("hotel_rooms", "staff_members", "cc_kb_entries", "devices", "hotel_memberships", "ups_menu_items"):
        cur.execute(f"SELECT count(*) AS n FROM {table} WHERE hotel_slug = %s", (SLUG,))
        out[table] = cur.fetchone()["n"]
    return out


def test_a_retired_demo_is_unreachable_and_restore_brings_it_back(app, cur, seeded, capsys):
    k = rooms.qr_key(SLUG, "204")
    guest, phone, desk = TestClient(app), TestClient(app), TestClient(app)
    # Before: every door works.
    assert guest.get(f"/public/stay/{SLUG}/204/context", params={"k": k}).status_code == 200
    staff = phone.post(f"/public/demo/{SLUG}/staff-app")
    assert staff.status_code == 200
    staff_id = staff.json()["session"]["staff"]["id"]
    assert desk.post("/auth/login", json={"email": f"dutymanager+{SLUG}@example.com", "password": PASSWORD}
                     ).status_code == 200
    assert desk.get(f"/hotels/{SLUG}/context").status_code == 200
    spare = devices.create_pairing_code(cur, SLUG, name="Spare tablet", kind="floor", floors=[], departments=[],
                                        actor=None)
    before = _counts(cur)

    assert cli.main(["retire-demo", "--hotel", SLUG]) == 0
    assert f"retired {SLUG}: status archived" in capsys.readouterr().out
    cur.execute("SELECT status FROM hotels WHERE slug = %s", (SLUG,))
    assert cur.fetchone()["status"] == "archived"
    assert _counts(cur) == before  # a status change, never a deleted row
    # The guest page and the concierge's session mint.
    r = TestClient(app).get(f"/public/stay/{SLUG}/204/context", params={"k": k})
    assert r.status_code == 404 and r.json()["code"] == "hotel_not_found"
    mint = TestClient(app).post("/public/concierge/sessions", json={
        "hotel": SLUG, "target": {"room": "204"}, "k": k, "lang": "en", "mode": "text",
        "consents": {"ai_processing": True, "transfer_abroad": True}})
    assert mint.status_code == 404
    # The landing page's doors.
    for method, path in (("get", ""), ("post", "/staff-app"), ("post", "/console")):
        r = getattr(TestClient(app), method)(f"/public/demo/{SLUG}{path}")
        assert r.status_code == 404 and r.json()["code"] == "hotel_not_found", (path, r.text)
    # The staff app already paired stops, and no new device can pair.
    devices.clear_cache()
    assert phone.get("/device/service/feed", params={"cursor": 0, "wait": 0},
                     headers={"X-Clefs-Staff": str(staff_id)}).status_code == 401
    assert TestClient(app).post("/auth/devices/pair", json={"code": spare["code"]}).status_code == 400
    # Console sign-in: neither the password nor the open session.
    from clefs.auth import sessions

    sessions.clear_cache()
    assert desk.get(f"/hotels/{SLUG}/context").status_code == 401
    r = TestClient(app).post("/auth/login", json={"email": f"dutymanager+{SLUG}@example.com", "password": PASSWORD})
    assert r.status_code == 401
    # A reseed is refused, and a second retire changes nothing.
    with pytest.raises(ClefsError) as exc:
        demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core"])
    assert exc.value.code == "not_demo"
    assert retire.retire(cur, SLUG)["already"] is True

    assert cli.main(["retire-demo", "--hotel", SLUG, "--restore"]) == 0
    assert f"restored {SLUG}: status demo" in capsys.readouterr().out
    sessions.clear_cache()
    assert TestClient(app).get(f"/public/stay/{SLUG}/204/context", params={"k": k}).status_code == 200
    assert TestClient(app).post(f"/public/demo/{SLUG}/staff-app").status_code == 200
    again = TestClient(app)
    assert again.post("/auth/login", json={"email": f"dutymanager+{SLUG}@example.com", "password": PASSWORD}
                      ).status_code == 200
    assert again.get(f"/hotels/{SLUG}/context").status_code == 200
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'core.demo.%%' ORDER BY id",
                (SLUG,))
    assert [r["action"] for r in cur.fetchall()] == ["core.demo.retired", "core.demo.restored"]


def test_only_a_demo_is_retired_and_only_a_retired_demo_restored(cur, hotel_uae, capsys):
    assert cli.main(["retire-demo", "--hotel", hotel_uae.slug]) == 1
    assert "not a demo hotel" in capsys.readouterr().err
    cur.execute("UPDATE hotels SET status = 'archived' WHERE slug = %s", (hotel_uae.slug,))
    with pytest.raises(ClefsError) as exc:
        retire.restore(cur, hotel_uae.slug)
    assert exc.value.code == "not_retired"
    with pytest.raises(ClefsError) as exc:
        retire.retire(cur, "no-such-hotel")
    assert exc.value.code == "hotel_not_found"
