"""upsell.* metrics through the core metrics route."""

from __future__ import annotations

from clefs.modules.upsell import api
from clefs.modules.upsell import orders as orders_mod


def test_revenue_and_accept_rate(client_as, cur, hotel_uae, menu_setup):
    slug = menu_setup["slug"]
    club, fries = menu_setup["club"]["id"], menu_setup["fries"]["id"]
    api.quote_for_session(cur, slug, room="101", lines=[{"item_id": club}], session_ref="s1")
    q = api.quote_for_session(cur, slug, room="101", lines=[{"item_id": club}, {"item_id": fries}], session_ref="s1")
    api.confirm_order(cur, slug, quote_id=q["quote_id"], room="101", source="web", session_ref="s1")
    # a second session sees the suggestion and declines it; a third order is cancelled
    api.quote_for_session(cur, slug, room="102", lines=[{"item_id": club}], session_ref="s2")
    api.quote(cur, slug, room="103", lines=[{"item_id": menu_setup["juice"]["id"]}])
    o3 = api.create_order(cur, slug, room="103", lines=[{"item_id": menu_setup["juice"]["id"]}], source="web")
    orders_mod.set_status(cur, hotel_uae, o3["order_id"], "cancelled")

    owner = client_as("owner")

    def run(metric):
        r = owner.post(f"/hotels/{slug}/metrics/query",
                       json={"metric": metric, "start": "2026-09-30", "end": "2026-09-30", "grain": "total"})
        assert r.status_code == 200, r.text
        return r.json()["total"]

    assert run("upsell.orders") == 1
    assert float(run("upsell.order_revenue")) == 70.0
    assert float(run("upsell.upsell_revenue")) == 20.0
    assert float(run("upsell.upsell_accept_rate")) == 50.0
    names = {m["key"] for m in owner.get(f"/hotels/{slug}/metrics").json()}
    assert {"upsell.orders", "upsell.order_revenue", "upsell.upsell_revenue", "upsell.upsell_accept_rate",
            "upsell.average_order_value"} <= names
