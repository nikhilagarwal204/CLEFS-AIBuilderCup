"""Voice console routes (03 spec 3.6): lines, extensions (single, CSV,
rule), the webhook event log and the call log."""

from __future__ import annotations

from tests.modules.voice.conftest import incoming

BASE = "/hotels/test-dubai/voice"


def test_line_crud_and_secret_shown_once(client_as, cur):
    owner = client_as("owner")
    r = owner.post(f"{BASE}/lines", json={"label": "Main", "trunk_id": "proj_abc", "pbx_kind": "3cx",
                                          "front_desk_uri": "sip:100@pbx.local", "allowed_sources": ["10.0.0.0/24"]})
    assert r.status_code == 201, r.text
    body = r.json()
    line_id, secret = body["line"]["id"], body["secret"]
    assert secret.startswith("vcl_") and body["line"]["has_secret"] and "line_secret_hash" not in body["line"]
    assert body["line"]["sip_uri"] == "sip:proj_abc@sip.voice.clefs.app;transport=tls"
    listed = owner.get(f"{BASE}/lines").json()["lines"]
    assert [ln["id"] for ln in listed] == [line_id] and secret not in str(listed)
    assert owner.patch(f"{BASE}/lines/{line_id}", json={"enabled": True, "max_call_s": 900}).json()["line"]["enabled"]
    rotated = owner.post(f"{BASE}/lines/{line_id}/rotate-secret").json()
    assert rotated["secret"] != secret and rotated["line"]["line_secret_hint"] == rotated["secret"][-4:]
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = 'test-dubai' AND action LIKE 'voice.%' ORDER BY id")
    assert [a["action"] for a in cur.fetchall()] == ["voice.line_created", "voice.line_updated", "voice.line_secret_rotated"]
    assert owner.delete(f"{BASE}/lines/{line_id}").status_code == 204
    assert owner.get(f"{BASE}/lines/{line_id}").status_code == 404


def test_line_validation_codes(client_as):
    owner = client_as("owner")
    bad = {
        "invalid_front_desk_uri": {"label": "x", "front_desk_uri": "tel:+971400000"},
        "invalid_tool_transport": {"label": "x", "tool_transport": "carrier-pigeon"},
        "invalid_max_call_s": {"label": "x", "max_call_s": 9000},
        "invalid_allowed_sources": {"label": "x", "allowed_sources": ["not a host!"]},
        "label_required": {"pbx_kind": "3cx"},
    }
    for code, body in bad.items():
        r = owner.post(f"{BASE}/lines", json=body)
        assert r.status_code == 400 and r.json()["code"] == code, (code, r.text)


def test_allowed_sources_need_the_project(client_as):
    """Source identification only matches lines of the dialled project, so
    sources without a project id would 486 every call: refused at save."""
    owner = client_as("owner")
    r = owner.post(f"{BASE}/lines", json={"label": "x", "allowed_sources": ["pbx.hotel.ae"]})
    assert r.status_code == 400 and r.json()["code"] == "sources_need_project"
    ok = owner.post(f"{BASE}/lines", json={"label": "x", "trunk_id": "proj_abc", "allowed_sources": ["pbx.hotel.ae"]})
    assert ok.status_code == 201, ok.text
    lid = ok.json()["line"]["id"]
    r = owner.patch(f"{BASE}/lines/{lid}", json={"trunk_id": None})
    assert r.status_code == 400 and r.json()["code"] == "sources_need_project"
    # Clearing both together is fine, and so is a header-only line.
    assert owner.patch(f"{BASE}/lines/{lid}", json={"trunk_id": None, "allowed_sources": []}).status_code == 200
    assert owner.post(f"{BASE}/lines", json={"label": "hdr only"}).status_code == 201


def test_permissions(client_as, line):
    fd = client_as("front_desk")
    assert fd.get(f"{BASE}/lines").status_code == 200
    assert fd.post(f"{BASE}/lines", json={"label": "x"}).status_code == 403
    assert client_as("staff").get(f"{BASE}/lines").status_code == 403


def test_extensions_single_csv_and_rule(client_as, line):
    owner = client_as("owner")
    lid = line[0]["id"]
    r = owner.post(f"{BASE}/lines/{lid}/extensions", json={"extension": "2101", "room": "101"})
    assert r.status_code == 201 and r.json()["extension"]["room"] == "101"
    assert owner.post(f"{BASE}/lines/{lid}/extensions", json={"extension": "2999", "room": "999"}).json()["code"] == "unknown_room"
    csv_text = "extension,room\n3101,101\n3102,102\n3999,999\nbad ext,101\n"
    out = owner.post(f"{BASE}/lines/{lid}/extensions/import", json={"csv": csv_text}).json()
    assert out["imported"] == 2 and out["skipped_count"] == 2
    preview = owner.post(f"{BASE}/lines/{lid}/extensions/rule", json={"prefix": "4", "dry_run": True}).json()
    assert preview["rooms"] == 13 and preview["would_map"] == 13 and preview["preview"][0]["extension"].startswith("4")
    applied = owner.post(f"{BASE}/lines/{lid}/extensions/rule", json={"prefix": "4"}).json()
    assert applied["created"] == 13
    exts = owner.get(f"{BASE}/lines/{lid}/extensions").json()["extensions"]
    by_ext = {e["extension"]: e for e in exts}
    assert by_ext["4501"]["room"] == "501" and by_ext["4501"]["source"] == "rule" and by_ext["3101"]["source"] == "csv"
    assert owner.delete(f"{BASE}/lines/{lid}/extensions/{by_ext['2101']['id']}").status_code == 204


def test_webhook_events_and_call_log(client_as, post_event, voice_env, recorder, line, cur):
    post_event(incoming("rtc_c1", line_secret=line[1]), webhook_id="msg_c1")
    post_event(incoming("rtc_c2", from_host="stranger.example"), webhook_id="msg_c2")
    fd = client_as("front_desk")
    events = fd.get(f"{BASE}/webhook-events").json()["events"]
    assert {e["webhook_id"] for e in events} == {"msg_c1", "msg_c2"}
    calls = fd.get(f"{BASE}/calls").json()
    assert [c["provider_call_id"] for c in calls["calls"]] == ["rtc_c1"]
    call_id = calls["calls"][0]["id"]
    cur.execute(
        """INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp, source, call_id)
           VALUES ('test-dubai', '501', 's', 'user', 'Two towels please', 0, 'phone', 'rtc_c1')"""
    )
    detail = fd.get(f"{BASE}/calls/{call_id}").json()
    assert detail["call"]["room"] == "501" and detail["transcript"][0]["message"] == "Two towels please"
    assert fd.get(f"{BASE}/calls/999999").status_code == 404
    status = fd.get(f"{BASE}/status").json()
    assert status["webhook_secret_set"] and status["webhook_url"].endswith("/webhooks/voice/incoming")


def test_api_call_stats(cur, post_event, voice_env, recorder, line, hotel_uae):
    from datetime import timedelta

    from clefs.core import clock
    from clefs.modules.voice import api

    now = clock.now()
    empty = api.call_stats(cur, "test-dubai", start=now - timedelta(hours=1), end=now + timedelta(hours=1))
    assert empty["calls"] == 0 and empty["avg_duration_s"] is None
    post_event(incoming("rtc_s1", line_secret=line[1]))
    post_event(incoming("rtc_s2", from_host="stranger.example"))
    stats = api.call_stats(cur, "test-dubai", start=now - timedelta(hours=1), end=now + timedelta(hours=1))
    assert stats["calls"] == 1 and stats["accepted"] == 1
