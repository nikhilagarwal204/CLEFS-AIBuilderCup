"""Ad lead webhooks (04 spec 4.3, 4.12): Meta signature, page mapping and
the Graph fetch mapping; Google key, lead_id dedupe and is_test."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    queued,
)

APP_SECRET = "meta-app-secret-for-tests"


@pytest.fixture
def meta_env(monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.setenv("META_APP_SECRET", APP_SECRET)
    monkeypatch.setenv("META_LEADGEN_VERIFY_TOKEN", "verify-me")
    reload_settings()
    yield
    monkeypatch.undo()
    reload_settings()


@pytest.fixture
def graph():
    """A MockTransport for the module's Graph calls: answers[(method, path fragment)]."""
    from clefs.modules.inquiries import channels

    answers: dict[tuple[str, str], object] = {}
    seen: list[httpx.Request] = []

    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        for (method, frag), ans in answers.items():
            if req.method == method and frag in req.url.path:
                status, body = ans if isinstance(ans, tuple) else (200, ans)
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"error": "no fake"})

    channels.set_transport(httpx.MockTransport(handle))
    yield answers, seen
    channels.set_transport(None)


def _page(cur, hotel, graph):
    from clefs.modules.inquiries import channels

    graph[0][("POST", "/subscribed_apps")] = {"success": True}
    return channels.connect_meta_page(cur, hotel.slug, page_id="1234567890", page_access_token="PAGE-TOKEN")


def _signed_post(client, payload):
    raw = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/webhooks/inquiries/meta-leadgen", content=raw,
                       headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig})


def _leadgen(leadgen_id="LG-1", page_id="1234567890"):
    return {"object": "page", "entry": [{"id": page_id, "changes": [{"field": "leadgen", "value": {
        "leadgen_id": leadgen_id, "page_id": page_id, "form_id": "F-9", "ad_id": "AD-3", "created_time": 1790000000}}]}]}


def test_meta_verify_challenge(client, meta_env):
    ok = client.get("/webhooks/inquiries/meta-leadgen",
                    params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    bad = client.get("/webhooks/inquiries/meta-leadgen",
                     params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


def test_meta_bad_signature_is_401(client, meta_env, cur, hotel, graph):
    _page(cur, hotel, graph)
    r = client.post("/webhooks/inquiries/meta-leadgen", json=_leadgen(), headers={"X-Hub-Signature-256": "sha256=00"})
    assert r.status_code == 401
    assert queued(cur, "inquiries.meta_lead_fetch") == []


def test_meta_lead_is_fetched_and_mapped(client, meta_env, cur, hotel, graph, run_jobs):
    acct = _page(cur, hotel, graph)
    assert acct["status"] == "active"
    r = _signed_post(client, _leadgen())
    assert r.status_code == 200 and r.json()["queued"] == 1
    assert _signed_post(client, _leadgen()).json()["queued"] == 0  # re-delivery: deduped on leadgen_id
    graph[0][("GET", "/LG-1")] = {"id": "LG-1", "form_id": "F-9", "ad_id": "AD-3", "campaign_id": "C-1",
                                  "created_time": "2026-09-29T06:00:00+0000", "field_data": [
        {"name": "full_name", "values": ["Omar Haddad"]},
        {"name": "email", "values": ["omar@example.com"]},
        {"name": "phone_number", "values": ["+971509876543"]},
        {"name": "arrival_date", "values": ["2026-11-10"]},
        {"name": "number_of_rooms", "values": ["3"]},
        {"name": "Can we reply on WhatsApp?", "values": ["Yes"]},
        {"name": "Anything else?", "values": ["Sea view please"]},
    ]}
    run_jobs("inquiries.meta_lead_fetch")
    row = inquiries(cur, hotel.slug)[0]
    assert row["source"] == "meta_leadgen" and row["external_lead_id"] == "LG-1"
    assert row["contact_name"] == "Omar Haddad" and row["rooms"] == 3 and str(row["check_in"]) == "2026-11-10"
    assert row["ad_referral"]["whatsapp_optin"] is True and row["ad_referral"]["campaign_id"] == "C-1"
    assert row["consent_basis"] == "pre_contract_notice"
    fetch = [q for q in graph[1] if q.method == "GET"][0]
    assert fetch.url.params["access_token"] == "PAGE-TOKEN"


def test_meta_fetch_failing_72h_raises_the_signal(client, meta_env, cur, hotel, graph, run_jobs, now):
    _page(cur, hotel, graph)
    _signed_post(client, _leadgen("LG-2"))
    graph[0][("GET", "/LG-2")] = (500, {"error": "boom"})
    run_jobs("inquiries.meta_lead_fetch")  # retried, not failed
    assert inquiries(cur, hotel.slug) == []
    now.advance(hours=73)
    cur.execute("UPDATE job_queue SET run_at = %s WHERE name = 'inquiries.meta_lead_fetch'", (now.now() - timedelta(minutes=1),))
    run_jobs("inquiries.meta_lead_fetch")
    cur.execute("SELECT kind FROM signals WHERE hotel_slug = %s AND kind = 'inq.lead_fetch_failed'", (hotel.slug,))
    assert cur.fetchone() is not None


@pytest.fixture
def gform(cur, hotel):
    from clefs.modules.inquiries import channels

    return channels.connect_google_leadform(cur, hotel.slug, google_key="g-key-0123456789abcdef")


def _gpayload(**kw):
    body = {"lead_id": "GL-1", "api_version": "1.0", "form_id": 11, "campaign_id": 22, "google_key": "g-key-0123456789abcdef",
            "gcl_id": "gcl-xyz", "is_test": False, "user_column_data": [
                {"column_id": "FULL_NAME", "string_value": "Lina M"},
                {"column_id": "EMAIL", "string_value": "lina@example.com"},
                {"column_id": "PHONE_NUMBER", "string_value": "+971501112233"},
                {"column_name": "How many rooms?", "string_value": "2", "column_id": "q1"}]}
    body.update(kw)
    return body


def test_google_lead_is_stored_and_deduped(client, cur, hotel, gform):
    path = gform["webhook_path"]
    assert client.post(path, json=_gpayload()).status_code == 200
    again = client.post(path, json=_gpayload())
    assert again.status_code == 200 and again.json()["outcome"] == "duplicate"
    rows = inquiries(cur, hotel.slug)
    assert len(rows) == 1 and rows[0]["source"] == "google_leadform" and rows[0]["gclid"] == "gcl-xyz"
    assert rows[0]["external_lead_id"] == "GL-1" and rows[0]["contact_name"] == "Lina M"


def test_google_wrong_key_and_unknown_path(client, cur, hotel, gform):
    assert client.post(gform["webhook_path"], json=_gpayload(google_key="nope")).status_code == 403
    assert client.post("/webhooks/inquiries/google-leadform/unknown", json=_gpayload()).status_code == 404
    assert inquiries(cur, hotel.slug) == []


def test_google_test_lead_is_flagged_and_not_acked(client, cur, hotel, gform):
    client.post(gform["webhook_path"], json=_gpayload(lead_id="GL-T", is_test=True))
    row = inquiries(cur, hotel.slug)[0]
    assert row["is_test"] is True
    cur.execute("SELECT count(*) AS n FROM actions WHERE hotel_slug = %s AND type = 'inquiries.ack'", (hotel.slug,))
    assert cur.fetchone()["n"] == 0
