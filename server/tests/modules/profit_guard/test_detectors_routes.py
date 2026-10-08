"""POST /profit-guard/detectors/run and GET /profit-guard/detectors.

The manifest mounts the detectors router; the fixture still includes it if a
narrowed app (CLEFS_ONLY_MODULES) left it out, so the routes are tested either
way. Routes are walked through route_walk (FastAPI 0.137+ nests them)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from clefs.modules.profit_guard.detectors.router import router as detectors_router
from tests.foundation.route_walk import route_paths
from tests.modules.profit_guard.fixtures import pg_data as pg

NOW = datetime(2026, 10, 2, 6, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def app():
    from clefs.app import create_app

    a = create_app()
    if "/hotels/{slug}/profit-guard/detectors/run" not in route_paths(a.routes):
        a.include_router(detectors_router)
    return a


@pytest.fixture(autouse=True)
def frozen(clock):
    clock.freeze(NOW)


def test_manual_run_and_detector_list(cur, client_as, hotel_uae):
    s = hotel_uae.slug
    rid = pg.reservation(cur, s, "P20", ota_id="4000000020", status="NO_SHOW")
    pg.statement(cur, s, [{"ota_res_id": "4000000020", "gross_minor": 105000, "commissionable_minor": 105000,
                           "commission_rate": Decimal("0.15"), "commission_minor": 15750, "reservation_id": rid,
                           "match_status": "LINKED", "match_score": 1}])
    owner = client_as("owner")
    r = owner.post(f"/hotels/{s}/profit-guard/detectors/run", json={"detectors": ["L3"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detectors"]["L3"]["created"] == 1 and body["signals"]["groups"] == 1
    again = owner.post(f"/hotels/{s}/profit-guard/detectors/run", json={}).json()
    assert again["created"] == 0 and "L3" in again["detectors"]

    lst = client_as("finance").get(f"/hotels/{s}/profit-guard/detectors").json()
    l3 = next(d for d in lst["detectors"] if d["detector"] == "L3")
    assert (l3["open_findings"], l3["open_amount_minor"], l3["feedback"]) == (1, 15750, "0.800")
    assert l3["min_amount"] == "25.00" and l3["signal_kind"] == "pg.leak.detected"
    assert [d["detector"] for d in lst["detectors"]][:3] == ["L1", "L1b", "L2"]


def test_run_refuses_unknown_detectors_and_other_roles(client_as, hotel_uae):
    s = hotel_uae.slug
    r = client_as("owner").post(f"/hotels/{s}/profit-guard/detectors/run", json={"detectors": ["L99"]})
    assert r.status_code == 400 and r.json()["code"] == "unknown_detector"
    assert client_as("front_desk").post(f"/hotels/{s}/profit-guard/detectors/run", json={}).status_code == 403
    assert client_as("revenue").get(f"/hotels/{s}/profit-guard/detectors").status_code == 403
