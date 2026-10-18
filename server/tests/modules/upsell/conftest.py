"""Upsell test fixtures: a Dubai in-room dining menu at a frozen local time."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

DXB = ZoneInfo("Asia/Dubai")
# Wednesday 2026-09-30 13:00 Dubai: lunch is on, breakfast is over.
LUNCHTIME = datetime(2026, 9, 30, 13, 0, tzinfo=DXB)


@pytest.fixture
def lunch(clock):
    clock.freeze(LUNCHTIME)
    return clock


@pytest.fixture
def menu_setup(cur, hotel_uae, lunch):
    """ird outlet (always open), bar outlet (no room delivery), items:
    club (AED 50), fries (AED 20, paired with club), juice (AED 25),
    breakfast (07:00-11:00), wine (alcohol)."""
    from clefs.modules.upsell import menu

    slug = hotel_uae.slug
    ird = menu.create_outlet(cur, slug, menu.OutletIn(key="ird", name_en="In-Room Dining", name_ar="خدمة الغرف"))
    bar = menu.create_outlet(cur, slug, menu.OutletIn(key="pool-bar", name_en="Pool Bar", kind="bar",
                                                      delivers_to_room=False))

    def item(outlet, **kw):
        return menu.create_item(cur, slug, "AED", menu.ItemIn(outlet_id=outlet["id"], **kw))

    club = item(ird, name_en="Club Sandwich", name_ar="كلوب ساندويتش", price="50.00", category="Mains",
                dietary=["contains_pork"], prep_min=20)
    fries = item(ird, name_en="Truffle Fries", price="20", category="Sides", dietary=["vegetarian"])
    juice = item(ird, name_en="Orange Juice", price="25", category="Drinks", dietary=["vegan"])
    breakfast = item(ird, name_en="Eggs Benedict", price="60", category="Breakfast",
                     available_from="07:00", available_to="11:00")
    wine = item(ird, name_en="House Red", price="80", category="Wine", alcohol=True)
    mojito = item(bar, name_en="Virgin Mojito", price="30", category="Drinks")
    menu.upsert_pair(cur, slug, menu.PairIn(item_id=club["id"], suggest_item_id=fries["id"],
                                            reason_en="Great with fries"))
    return {"slug": slug, "ird": ird, "bar": bar, "club": club, "fries": fries, "juice": juice,
            "breakfast": breakfast, "wine": wine, "mojito": mojito}


@pytest.fixture
def set_settings(cur, hotel_uae):
    def _set(**kw):
        from clefs.core.hotels import set_module_settings

        set_module_settings(cur, hotel_uae.slug, "upsell", kw, actor=None)

    return _set


class _ServiceStub:
    """Stands in for service.api.create_request: the scoped test run
    (CLEFS_ONLY_MODULES=upsell,reviews) does not migrate Service's tables, and
    the Service lane rewrites intake in parallel. test_service_link.py covers
    the real call when Service is enabled."""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail: Exception | None = None

    def __call__(self, cur, hotel_slug, **kw):
        if self.fail is not None:
            raise self.fail
        self.calls.append({"hotel_slug": hotel_slug, **kw})
        n = len(self.calls)
        return {"id": 9000 + n, "tracking_token": f"tok-{n}"}


@pytest.fixture(autouse=True)
def service_stub(request, monkeypatch):
    if request.node.get_closest_marker("real_service"):
        yield None
        return
    from clefs.modules.service import api as service_api

    stub = _ServiceStub()
    monkeypatch.setattr(service_api, "create_request", stub)
    yield stub


def pytest_configure(config):
    config.addinivalue_line("markers", "real_service: call the real service.api.create_request")
