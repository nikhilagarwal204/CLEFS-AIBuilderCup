"""The landing page's one-tap doors at the Tokyo demo hotel: the guest page
of Room 204, Maria's staff app and the console as Yuki Sato, the duty
manager (clefs.core.demo_access serves each demo hotel's own cast)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from clefs.core import rooms
from clefs.seed import demo_jp
from clefs.seed.demo_jp import core as cast

ANCHOR = date(2026, 10, 21)
SLUG = "demo-t-jp-access"


@pytest.fixture
def seeded(cur):
    return demo_jp.seed(cur, SLUG, anchor=ANCHOR, parts=["core", "core_catalog", "concierge_live", "deck"],
                        password="demo-pass-123")


def test_info_names_the_tokyo_cast(client, seeded):
    r = client.get(f"/public/demo/{SLUG}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["hotel"]["city"] == "Tokyo" and body["hotel"]["name"].startswith("Hotel Aozora Shinjuku")
    assert body["hotel"]["guest_languages"] == list(cast.GUEST_LANGUAGES)
    assert body["guest"] == {"room": "204", "floor": "2", "path": rooms.stay_path(SLUG, "204", 1)}
    assert body["staff"]["name"] == "Maria Santos" and body["staff"]["pin"] == "2046"
    assert body["staff"]["languages"][0] == "fil" and body["staff"]["floors"] == ["1", "2", "3", "4"]
    assert body["console"] == {"name": "Yuki Sato", "role": "Duty manager"}
    assert body["gate"]["tests"]["expected"] == 22


def test_staff_app_opens_marias_shift(app, seeded):
    c = TestClient(app)
    r = c.post(f"/public/demo/{SLUG}/staff-app")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session"]["staff"]["name"] == "Maria Santos" and body["device"]["floors"] == ["1", "2", "3", "4"]
    me = c.get("/device/service/me", headers={"X-Clefs-Staff": str(body["session"]["staff"]["id"])})
    assert me.status_code == 200 and me.json()["staff"]["first_name"] == "Maria"


def test_console_signs_in_as_yuki_sato(app, seeded, hotel_uae):
    c = TestClient(app)
    r = c.post(f"/public/demo/{SLUG}/console")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["name"] == "Yuki Sato" and body["user"]["email"] == f"dutymanager+{SLUG}@example.com"
    assert body["path"] == f"/app/{SLUG}/service" and body["role"] == "dept_head"
    me = c.get("/auth/me").json()
    assert [(m["hotel_slug"], m["role"]) for m in me["memberships"]] == [(SLUG, "dept_head")]
    ctx = c.get(f"/hotels/{SLUG}/context").json()
    assert (ctx["hotel"]["timezone"], ctx["hotel"]["currency"]) == ("Asia/Tokyo", "JPY")
    assert c.get(f"/hotels/{hotel_uae.slug}/context").status_code == 403
