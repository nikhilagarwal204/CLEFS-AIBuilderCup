"""The real service.api.create_request behind an order (runs only when the
Service module is enabled, i.e. not in the scoped upsell,reviews run)."""

from __future__ import annotations

import pytest

from clefs.registry import enabled_modules

pytestmark = [
    pytest.mark.real_service,
    pytest.mark.skipif("service" not in enabled_modules(), reason="Service module not enabled in this run"),
]


def test_order_creates_a_real_fnb_request(cur, menu_setup):
    from clefs.modules.upsell import api

    slug = menu_setup["slug"]
    lines = [{"item_id": menu_setup["club"]["id"], "qty": 1}]
    api.quote(cur, slug, room="101", lines=lines)
    o = api.create_order(cur, slug, room="101", lines=lines, source="web")
    assert o["request_id"] and o["tracking_token"]
    cur.execute("SELECT hotel_slug, room, category, description FROM staff_requests WHERE id = %s", (o["request_id"],))
    req = cur.fetchone()
    assert req["hotel_slug"] == slug and req["room"] == "101"
    assert "Club Sandwich" in req["description"]
