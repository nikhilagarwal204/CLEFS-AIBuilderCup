"""The harness (03 spec 3.6) end to end: the real SDK transport against the
local telephony-bridge stub, the signed webhook and the MCP session."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def harness_env(monkeypatch):
    """run() writes these into os.environ; monkeypatch restores them."""
    from clefs.settings import reload_settings

    for key in ("VOICE_BRIDGE_URL", "GEMINI_API_KEY", "VOICE_WEBHOOK_SECRET", "VOICE_ENABLED"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    yield
    monkeypatch.undo()
    reload_settings()


def test_sign_is_what_the_webhook_verifies():
    from clefs.modules.voice import harness, realtime_ops

    secret = harness.new_webhook_secret()
    ev = harness.incoming_event(call_id="rtc_h", from_user="1501", from_host="pbx.x", project="proj_x")
    body = json.dumps(ev).encode()
    hdrs = {k.lower(): v for k, v in harness.sign(body, secret).items()}
    out = realtime_ops.unwrap(body, hdrs, secret)
    assert out["type"] == "call.incoming" and out["data"]["call_id"] == "rtc_h"


def test_harness_accepts_and_calls_mcp(harness_env, hotel_uae, fake_service):
    from clefs.modules.voice import harness

    report = harness.run(["--hotel", "test-dubai", "--setup", "--from", "1501", "--room", "501", "--tool", "create_ticket",
                          "--args", json.dumps({"request_type": "towels", "description_en": "2 towels"}), "--replay"])
    first, second = report["webhook"]
    assert first["status"] == 200 and first["body"]["action"] == "accepted"
    assert second["body"]["action"] == "duplicate"
    ops = [r["op"] for r in report["bridge"]]
    assert ops == ["accept"]
    assert "create_ticket" in report["mcp"]["tools"]
    result = report["mcp"]["call"]["body"]["result"]["structuredContent"]
    assert result["status"] == "created" and fake_service.requests[0]["room_number"] == "501"


def test_harness_without_line_header_gets_486(harness_env, hotel_uae):
    from clefs.modules.voice import harness

    report = harness.run(["--hotel", "test-dubai", "--setup", "--no-line-header", "--from-host", "stranger.example"])
    assert report["webhook"][0]["body"]["reason"] == "unidentified"
    assert [(r["op"], r["body"]) for r in report["bridge"]] == [("reject", {"status_code": 486})]


def test_harness_failed_accept_falls_back_to_486(harness_env, hotel_uae):
    from clefs.modules.voice import harness

    report = harness.run(["--hotel", "test-dubai", "--setup", "--fail", "accept"])
    assert report["webhook"][0]["body"]["action"] == "accept_failed"
    assert [r["op"] for r in report["bridge"]] == ["accept", "reject"]


def test_harness_refuses_remote_db(monkeypatch):
    from clefs.modules.voice import harness

    monkeypatch.setenv("DATABASE_URL", "postgresql://user@db.example.neon.tech/prod")
    with pytest.raises(SystemExit):
        harness.ensure_local_db()
