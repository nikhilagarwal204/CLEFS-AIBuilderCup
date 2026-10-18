"""Public Google check: limits, the 503 on an exhausted budget, teaser gating, sealed leads (04 spec 5.9, 5.12)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))  # Tuesday
URL = "/public/visibility/google-check"
BODY = {"hotel_name": "Test Hotel Dubai", "city": "Dubai", "consent_processing": True, "consent_transfer_abroad": True}


def _prop(name, token, sponsored=False):
    return {"type": "hotel", "name": name, "property_token": token, **({"sponsored": True} if sponsored else {})}


def serp_router(*, search_props, official=True):
    def answer(params):
        if params.get("property_token"):
            prices = [{"source": "Booking.com", "total_rate": {"extracted_lowest": 450}}]
            if official:
                prices.append({"source": "Test Hotel Dubai", "official": True, "total_rate": {"extracted_lowest": 500}})
            return {"type": "hotel", "name": "Test Hotel Dubai", "prices": prices}
        if params["q"].startswith("hotels in"):
            return {"properties": [_prop("Ad", "ad", True), _prop("A", "a"), _prop("Test Hotel Dubai", "self")]}
        return {"properties": search_props}
    return answer


@pytest.fixture
def env(monkeypatch):
    from clefs.settings import reload_settings

    def _set(**values):
        for k, v in values.items():
            if v is None:
                monkeypatch.delenv(k, raising=False)
            else:
                monkeypatch.setenv(k, str(v))
        reload_settings()

    yield _set
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def serp(env, clock, client):
    from clefs.core import serpapi

    clock.freeze(NOW)
    env(SERPAPI_KEY="k", SERPAPI_MONTHLY_BUDGET_USD=None, PUBLIC_SERPAPI_MONTHLY_CAP=None, PUBLIC_CHECK_DAILY_CAP=None)
    fake = serpapi.use_fake(default=None)
    fake.default = None
    yield fake
    serpapi.set_transport(None)


def _use(fake, answer):
    fake.queue.clear()
    fake.default = None
    fake.push(*[answer] * 20)


def test_consent_ticks_are_required(client, serp):
    r = client.post(URL, json={**BODY, "consent_transfer_abroad": False})
    assert r.status_code == 422 and r.json()["code"] == "invalid_input"


def test_ready_flow_teaser_then_unlock(client, serp, cur):
    _use(serp, serp_router(search_props=[_prop("Test Hotel Dubai", "self"), _prop("Other", "o")]))
    r = client.post(URL, json=BODY)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "ready" and out["teaser"] == {"official_present": True} and out["candidates"] == []
    assert "unlocked" not in out
    token = out["token"]
    got = client.get(f"{URL}/{token}").json()
    assert got["teaser"] == {"official_present": True} and "unlocked" not in got
    lead = client.post(f"{URL}/{token}/lead", json={"name": "Sara", "email": "sara@hotel.ae",
                                                    "whatsapp": "+971501234567", "consent_contact_email": True})
    assert lead.status_code == 200, lead.text
    u = lead.json()["unlocked"]
    assert u["organic_rank"] == 2 and u["gap_minor"] == 5000 and u["gap_pct"] == "10.000"
    assert len(u["stay_dates"]) == 2 and "google_third_party_rates_unavailable_2026_09_30" in u["note_claim_ids"]
    assert {p["source"] for p in u["parity"]} == {"Booking.com", "Test Hotel Dubai"}
    assert client.get(f"{URL}/{token}").json()["unlocked"]["gap_minor"] == 5000
    cur.execute("SELECT lead_email_enc, lead_whatsapp_enc, consent_contact_whatsapp, token_hash FROM vis_public_checks")
    row = cur.fetchone()
    assert b"sara@hotel.ae" not in bytes(row["lead_email_enc"]) and row["consent_contact_whatsapp"] is False
    assert token.encode() not in bytes(row["token_hash"])
    cur.execute("SELECT COUNT(*) AS n FROM serpapi_calls WHERE purpose = 'public_check' AND hotel_slug IS NULL")
    assert cur.fetchone()["n"] == 4  # search, 2 property pages, the area rank search


def test_needs_pick_then_pick(client, serp):
    cands = [_prop(f"Hotel {i}", f"t{i}") for i in range(7)]
    _use(serp, serp_router(search_props=cands, official=False))
    out = client.post(URL, json=BODY).json()
    assert out["status"] == "needs_pick" and len(out["candidates"]) == 5 and out["teaser"] is None
    bad = client.post(f"{URL}/{out['token']}/pick", json={"property_token": "nope"})
    assert bad.status_code == 422
    r = client.post(f"{URL}/{out["token"]}/pick", json={"property_token": "t1"})
    picked = r.json()
    assert r.status_code == 200, r.text
    assert picked["status"] == "ready" and picked["teaser"] == {"official_present": False}


# ─── Pick claim (review finding security-privacy-6) ───


def _needs_pick(client, serp):
    cands = [_prop(f"Hotel {i}", f"t{i}") for i in range(7)]
    _use(serp, serp_router(search_props=cands, official=False))
    out = client.post(URL, json=BODY).json()
    assert out["status"] == "needs_pick"
    return out["token"]


def test_parallel_pick_is_refused_without_spending_searches(client, serp):
    """A second pick that lands while the first is spending its 3 searches
    gets 409 and spends none (it used to pass the status check and spend 3)."""
    from clefs.core.errors import ClefsError
    from clefs.modules.visibility import public_check

    token = _needs_pick(client, serp)
    inner = serp_router(search_props=[], official=False)
    seen: list = []

    def racing(params):
        if params.get("property_token") and not seen:
            try:
                public_check.pick(token, public_check.PickIn(property_token="t2"))
                seen.append("passed")
            except ClefsError as exc:
                seen.append((exc.status, exc.code, serp.calls))
        return inner(params)

    _use(serp, racing)
    before = serp.calls
    r = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert r.status_code == 200 and r.json()["status"] == "ready", r.text
    assert seen == [(409, "pick_in_progress", before + 1)]
    assert serp.calls - before == 3  # one pick's searches, not two
    again = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert again.status_code == 422  # a finished pick cannot be re-run


def test_failed_pick_releases_the_claim(client, serp, env, cur):
    token = _needs_pick(client, serp)
    env(PUBLIC_SERPAPI_MONTHLY_CAP="0")
    r = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert r.status_code == 503 and r.json()["code"] == "budget_exhausted"
    cur.execute("SELECT status, property_token, picking_at FROM vis_public_checks")
    assert dict(cur.fetchone()) == {"status": "needs_pick", "property_token": None, "picking_at": None}
    env(PUBLIC_SERPAPI_MONTHLY_CAP=None)
    ok = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert ok.status_code == 200 and ok.json()["status"] == "ready", ok.text


def test_stale_pick_claim_can_be_retaken(client, serp, cur, clock):
    from clefs.core import clock as core_clock

    token = _needs_pick(client, serp)
    cur.execute("UPDATE vis_public_checks SET status = 'picking', property_token = 't1', picking_at = %s",
                (core_clock.now(),))
    got = client.get(f"{URL}/{token}").json()
    assert got["status"] == "needs_pick" and len(got["candidates"]) == 5  # the wire keeps 3 statuses
    busy = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert busy.status_code == 409 and busy.json()["code"] == "pick_in_progress"
    clock.advance(minutes=6)  # past PICK_LEASE: the claim's worker is presumed dead
    ok = client.post(f"{URL}/{token}/pick", json={"property_token": "t1"})
    assert ok.status_code == 200 and ok.json()["status"] == "ready", ok.text


def test_picks_are_rate_limited_per_ip(client, serp, env):
    token = _needs_pick(client, serp)
    env(PUBLIC_SERPAPI_MONTHLY_CAP="0")  # each pick is claimed, fails, and releases
    codes = [client.post(f"{URL}/{token}/pick", json={"property_token": "t1"}).status_code for _ in range(4)]
    assert codes == [503, 503, 503, 429]


def test_no_match(client, serp):
    _use(serp, serp_router(search_props=[]))
    r = client.post(URL, json=BODY)
    assert r.json().get("status") == "no_match", r.text


def test_three_per_ip_per_day(client, serp):
    _use(serp, serp_router(search_props=[]))
    codes = [client.post(URL, json=BODY).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_global_daily_cap(client, serp, env):
    env(PUBLIC_CHECK_DAILY_CAP="1")
    _use(serp, serp_router(search_props=[]))
    assert client.post(URL, json=BODY).status_code == 200
    r = client.post(URL, json=BODY)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"


def test_budget_exhausted_fails_closed(client, serp, env):
    env(PUBLIC_SERPAPI_MONTHLY_CAP="0")
    _use(serp, serp_router(search_props=[]))
    r = client.post(URL, json=BODY)
    assert r.status_code == 503 and r.json()["code"] == "budget_exhausted"


def test_unknown_token_is_404(client, serp):
    r = client.get(f"{URL}/nope")
    assert r.status_code == 404 and r.json()["code"] == "not_found"


def test_whatsapp_follow_up_only_with_consent_inside_the_window():
    from clefs.modules.visibility import public_check

    evening = datetime(2026, 9, 29, 19, 30, tzinfo=ZoneInfo("Asia/Dubai"))
    assert public_check.outreach_at(evening) == datetime(2026, 9, 30, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    early = datetime(2026, 9, 29, 7, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    assert public_check.outreach_at(early) == datetime(2026, 9, 29, 9, 0, tzinfo=ZoneInfo("Asia/Dubai"))
    assert public_check.outreach_at(NOW) == NOW


def test_missing_fields_are_invalid_input(client, serp):
    r = client.post(URL, json={"consent_processing": True, "consent_transfer_abroad": True, "city": "Dubai"})
    assert r.status_code == 422 and r.json()["code"] == "invalid_input"


def test_whatsapp_follow_up_is_queued_only_with_consent(client, serp, cur):
    _use(serp, serp_router(search_props=[_prop("Test Hotel Dubai", "self")]))
    token = client.post(URL, json=BODY).json()["token"]
    r = client.post(f"{URL}/{token}/lead", json={"name": "Ali", "whatsapp": "+971501234567",
                                                 "consent_contact_whatsapp": True})
    assert r.status_code == 200, r.text
    cur.execute("SELECT run_at FROM job_queue WHERE name = 'visibility.public_lead_followup'")
    assert cur.fetchone()["run_at"] == NOW  # 10:00 Dubai is inside the window
    bad = client.post(f"{URL}/{token}/lead", json={"name": "Ali", "whatsapp": "0501234567"})
    assert bad.status_code == 422 and bad.json()["code"] == "invalid_input"
