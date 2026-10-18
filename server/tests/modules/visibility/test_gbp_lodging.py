"""GBP Lodging vs the KB (04 spec 5.4, 5.12): allowlist-only diff, the sync
action and its PATCH, and an override giving not_verified."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest

from tests.modules.visibility.test_gbp import LOC, GoogleFake, env  # noqa: F401  (shared fixtures)

NOW = datetime(2026, 10, 3, 6, 15, tzinfo=ZoneInfo("Asia/Dubai"))

KB = [
    {"fact_key": "checkin_time", "value": "14:00"},
    {"fact_key": "checkout_time", "value": "12:00"},
    {"fact_key": "parking", "value": "paid"},
    {"fact_key": "pool", "value": "true"},
    {"fact_key": "wifi", "value": "true"},
]
LODGING = {
    "name": f"{LOC}/lodging",
    "policies": {"checkinTime": {"hours": 12}, "checkoutTime": {"hours": 12, "minutes": 15}},
    "parking": {"parkingAvailable": False},
    "pools": {"pool": True},
    "accessibility": {"mobilityAccessible": False},
}


@pytest.fixture
def google(monkeypatch):
    import httpx

    from clefs.core import oauth
    from clefs.modules.visibility import http

    fake = GoogleFake()
    http.set_transport(httpx.MockTransport(fake.handle))
    monkeypatch.setattr(oauth, "get_access_token", lambda cur, slug, provider: "tok")
    yield fake
    http.set_transport(None)


@pytest.fixture
def hotel(cur, hotel_uae, clock, env, monkeypatch):  # noqa: F811
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.visibility import action_types, store  # noqa: F401  (registers the types)

    clock.freeze(NOW)
    env(GBP_PROJECT_QPM="300")
    monkeypatch.setattr(concierge_api, "kb_facts", lambda cur, slug, **kw: KB)
    cur.execute("INSERT INTO oauth_connections (hotel_slug, provider) VALUES (%s, 'google_business')",
                (hotel_uae.slug,))
    store.update_profile(cur, hotel_uae.slug, {"gbp_location": LOC}, user_id=None)
    return hotel_uae


def test_mapping_and_diff_use_only_the_allowlist():
    from clefs.modules.visibility import lodging

    truth = {k["fact_key"]: k["value"] for k in KB} | {"star_rating": "4"}
    d = {x["field"]: x for x in lodging.diff(truth, LODGING)}
    # check-in 12:00 vs 14:00 differs; check-out 12:15 vs 12:00 is inside 30 minutes; pool matches.
    assert set(d) == {"policies.checkinTime", "parking.parkingAvailable", "parking.freeParking",
                      "connectivity.wifiAvailable"}
    assert d["policies.checkinTime"]["ours"] == {"hours": 14, "minutes": 0}
    assert d["connectivity.wifiAvailable"]["kind"] == "missing"
    assert all(f in lodging.ALLOWED_FIELDS for f in d) and not any(f.startswith("accessibility") for f in d)
    # parking: none -> neither available nor free; free -> both; paid -> available, not free.
    assert lodging.desired_fields({"parking": "free"}) == {"parking.parkingAvailable": True,
                                                            "parking.freeParking": True}
    assert lodging.desired_fields({"parking": "none"}) == {"parking.parkingAvailable": False,
                                                            "parking.freeParking": False}


def test_check_proposes_sync_and_executor_patches_allowlist(cur, hotel, google):
    from clefs.core import actions
    from clefs.modules.visibility import api, lodging

    google.on("GET", "/lodging:getGoogleUpdated", {"lodging": LODGING, "diffMask": ""})
    google.on("GET", f"/{LOC}/lodging?readMask", LODGING)
    out = lodging.run_check(cur, hotel)
    assert out["diff"] == 4 and out["action_id"]
    act = actions.get_action(cur, hotel.slug, out["action_id"])
    assert act["type"] == "visibility.lodging_sync" and act["status"] == "proposed"
    # The same diff tomorrow is the same action (deduped), not a new one.
    assert lodging.run_check(cur, hotel)["action_id"] == out["action_id"]
    errs = {e["fact_key"]: e for e in api.open_fact_errors(cur, hotel.slug)}
    assert errs["checkin_time"]["ours"] == "14:00" and errs["checkin_time"]["theirs"] == "12:00"
    assert errs["parking"]["theirs"] == "none" and "wifi" not in errs   # missing on Google is not an error

    patched = {}

    def patch(request):
        patched["mask"] = parse_qs(urlsplit(str(request.url)).query)["updateMask"][0]
        patched["body"] = json.loads(request.content)
        return patched["body"]

    google.on("PATCH", f"/{LOC}/lodging", patch)
    t = actions.get_action_type("visibility.lodging_sync")
    res = t.executor(actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None))
    assert res.ok
    assert set(patched["mask"].split(",")) == {"policies.checkinTime", "parking.parkingAvailable",
                                               "parking.freeParking", "connectivity.wifiAvailable"}
    assert patched["body"]["policies"] == {"checkinTime": {"hours": 14, "minutes": 0}}
    assert "accessibility" not in patched["body"] and "pools" not in patched["body"]
    assert res.reversal_handle["previous"]["policies.checkinTime"] == {"hours": 12}


def test_override_is_not_verified_with_signal(cur, hotel, google):
    from clefs.core import actions, signals
    from clefs.modules.visibility import lodging

    fields = {"policies.checkinTime": {"hours": 14, "minutes": 0}, "pools.pool": True}
    act = {"id": None, "hotel_slug": hotel.slug, "type": "visibility.lodging_sync",
           "params": {"location": LOC, "fields": fields}, "execution_result": {"location": LOC, "fields": fields}}
    t = actions.get_action_type("visibility.lodging_sync")
    google.on("GET", "/lodging:getGoogleUpdated", {
        "lodging": {"policies": {"checkinTime": {"hours": 15}}, "pools": {"pool": True}},
        "diffMask": "policies.checkinTime"})
    v = t.verifier(actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None))
    assert v.status == "not_verified" and v.evidence[0].value[0]["field"] == "policies.checkinTime"
    kinds = [s["kind"] for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])]
    assert "vis.lodging_overridden" in kinds

    google.on("GET", "/lodging:getGoogleUpdated", {"lodging": {}, "diffMask": ""})
    assert lodging.overridden_fields({"lodging": {}, "diffMask": ""}, fields) == []
    assert t.verifier(actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None)
                      ).status == "verified"
