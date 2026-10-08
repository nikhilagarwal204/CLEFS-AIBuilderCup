"""upsell.stuck_orders: a service_risk signal per order nobody moved along."""

from __future__ import annotations

from clefs.core import signals
from clefs.core.privacy import pii_registry
from clefs.modules.upsell import api
from clefs.modules.upsell import jobs as ups_jobs
from clefs.modules.upsell import orders as orders_mod


def test_stuck_order_signal_opens_and_resolves(cur, hotel_uae, menu_setup, lunch):
    slug = menu_setup["slug"]
    lines = [{"item_id": menu_setup["club"]["id"]}]
    api.quote(cur, slug, room="101", lines=lines)
    o = api.create_order(cur, slug, room="101", lines=lines, source="web")
    assert ups_jobs.detect_stuck(cur, hotel_uae, lunch.now())["stuck"] == 0
    lunch.advance(minutes=91)
    assert ups_jobs.detect_stuck(cur, hotel_uae, lunch.now())["stuck"] == 1
    rows = signals.list_signals(cur, slug, modules=["upsell"])
    assert len(rows) == 1 and rows[0]["section"] == "service_risk" and "Room 101" in rows[0]["title"]
    orders_mod.set_status(cur, hotel_uae, o["order_id"], "delivered")
    r = ups_jobs.detect_stuck(cur, hotel_uae, lunch.now())
    assert r == {"stuck": 0, "resolved": 1}
    assert signals.list_signals(cur, slug, modules=["upsell"]) == []


def test_job_runs_through_the_scheduler(cur, hotel_uae, menu_setup, lunch, run_jobs):
    report = run_jobs(only=["upsell.stuck_orders"], scheduled=True)
    assert report is not None


def test_pii_registered():
    tables = {(s.module, s.table) for s in pii_registry()}
    assert {("upsell", "ups_orders"), ("upsell", "ups_order_items"), ("upsell", "ups_quotes")} <= tables
