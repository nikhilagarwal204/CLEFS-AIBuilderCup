"""Product focus (05 spec 2.17): the AI Builder Cup prototype switches off
the modules outside the deck with CLEFS_DISABLED_MODULES (the value is
documented in server/.env.example). The API it leaves must still carry the
deck's whole flow: the guest page and its menu, the concierge, the request
tracker, the staff app, the service desk, the quality gate and /try."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clefs import registry
from clefs.seed import demo_uae
from clefs.seed.demo_uae import core as cast
from tests.foundation.route_walk import route_paths

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-focus"
# The deck's modules, and upsell: the concierge reads the in-room menu and
# files dining orders through it, and the guest page calls /public/menu.
KEPT = ("service", "concierge", "amenities", "upsell", "guests", "trust")
DECK_ROUTES = (
    "/public/demo/{slug}", "/public/demo/{slug}/staff-app", "/public/demo/{slug}/console",
    "/public/stay/{slug}/{target}/context", "/public/concierge/sessions", "/public/menu/{slug}", "/public/r/{token}",
    "/device/service/feed", "/auth/devices/roster", "/hotels/{slug}/context", "/hotels/{slug}/trust/readiness",
)


def prototype_disabled() -> list[str]:
    m = re.search(r"^#\s+CLEFS_DISABLED_MODULES=([a-z_,]+)\s*$", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    assert m, "server/.env.example documents the prototype's CLEFS_DISABLED_MODULES"
    return m.group(1).split(",")


def test_the_documented_list_keeps_exactly_the_deck():
    off = prototype_disabled()
    assert len(off) == len(set(off)) and set(off) <= set(registry.INSTALLED_MODULES)
    assert tuple(m for m in registry.INSTALLED_MODULES if m not in off) == KEPT


@pytest.fixture
def focused_app(monkeypatch):
    """create_app() as clefs-api runs it for the prototype; the full app is
    rebuilt afterwards (create_app resets the shared permission registry)."""
    from clefs.app import create_app

    monkeypatch.setenv("CLEFS_DISABLED_MODULES", ",".join(prototype_disabled()))
    try:
        yield create_app()
    finally:
        monkeypatch.delenv("CLEFS_DISABLED_MODULES")
        create_app()


def test_the_focused_api_keeps_every_deck_route_and_drops_the_rest(focused_app):
    assert focused_app.state.clefs_modules == list(KEPT)
    paths = route_paths(focused_app.routes)
    assert [p for p in DECK_ROUTES if p not in paths] == []
    assert any(p.startswith("/hotels/{slug}/service/") for p in paths)
    for gone in ("/hotels/{slug}/profit", "/hotels/{slug}/brief", "/hotels/{slug}/demand", "/hotels/{slug}/finance",
                 "/hotels/{slug}/visibility", "/hotels/{slug}/inquiries", "/hotels/{slug}/reviews", "/hotels/{slug}/ask",
                 "/hotels/{slug}/voice", "/public/inquiries", "/public/visibility"):
        assert not [p for p in paths if p.startswith(gone)], gone


def test_the_deck_flow_runs_on_the_focused_api(cur, focused_app):
    demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=["core", "core_catalog", "concierge_live", "deck"],
                  password="demo-pass-123")
    guest = TestClient(focused_app)
    info = guest.get(f"/public/demo/{SLUG}")
    assert info.status_code == 200, info.text
    k = info.json()["guest"]["path"].split("?k=")[1]
    stay = guest.get(f"/public/stay/{SLUG}/{cast.DECK_ROOM}/context", params={"k": k})
    assert stay.status_code == 200 and stay.json()["verified"] is True
    menu = guest.get(f"/public/menu/{SLUG}", params={"room": cast.DECK_ROOM, "k": k})
    assert menu.status_code == 200, menu.text

    phone = TestClient(focused_app)
    staff = phone.post(f"/public/demo/{SLUG}/staff-app").json()["session"]["staff"]
    feed = phone.get("/device/service/feed", params={"cursor": 0, "wait": 0}, headers={"X-Clefs-Staff": str(staff["id"])})
    assert feed.status_code == 200, feed.text

    desk = TestClient(focused_app)
    assert desk.post(f"/public/demo/{SLUG}/console").status_code == 200
    ctx = desk.get(f"/hotels/{SLUG}/context").json()
    assert ctx["modules_enabled"] == list(KEPT)
    assert desk.get(f"/hotels/{SLUG}/trust/readiness").status_code == 200
