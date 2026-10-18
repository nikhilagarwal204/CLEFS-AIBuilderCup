"""
Wave 1 voice gaps: phone tools run through concierge.api.run_tool (the one
executor); the voice-local executor is only a guarded fallback when the
concierge is disabled for the hotel or bound no session; the phone mint
refusing (concierge not live) rejects the call with a 486.
"""

from __future__ import annotations

import pytest

from tests.modules.voice.conftest import incoming
from tests.modules.voice.test_mcp import start_call, tool  # noqa: F401  (fixture re-export)

WATER = {"request_type": "water", "description_en": "Two bottles of water"}


@pytest.fixture
def spy(monkeypatch):
    """Records concierge.api.run_tool and fallback.execute calls while
    letting both run."""
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.voice import fallback

    seen: dict[str, list] = {"run_tool": [], "fallback": []}
    real_run, real_fallback = concierge_api.run_tool, fallback.execute

    def run_tool(cur, hotel_slug, *, session_ctx, name, args):
        seen["run_tool"].append({"hotel_slug": hotel_slug, "session_ctx": dict(session_ctx), "name": name, "args": dict(args)})
        return real_run(cur, hotel_slug, session_ctx=session_ctx, name=name, args=args)

    def execute(cur, call, name, args):
        seen["fallback"].append(name)
        return real_fallback(cur, call, name, args)

    monkeypatch.setattr(concierge_api, "run_tool", run_tool)
    monkeypatch.setattr(fallback, "execute", execute)
    return seen


def test_phone_tools_run_on_the_concierge_executor(client, cur, start_call, spy, fake_service, hotel_uae):
    token = start_call("rtc_gap_one", from_user="1501")
    out = tool(client, token, "create_ticket", {**WATER, "room": "999"})
    assert out["status"] == "created" and spy["fallback"] == []
    [seen] = spy["run_tool"]
    cur.execute("SELECT session_id FROM vc_calls WHERE provider_call_id = 'rtc_gap_one'")
    sid = str(cur.fetchone()["session_id"])
    assert seen["hotel_slug"] == hotel_uae.slug and seen["name"] == "notify_staff"
    assert seen["session_ctx"]["session_id"] == sid and seen["session_ctx"]["call_id"].startswith("mcp_")
    assert "room" not in seen["args"] and seen["args"]["label"] == "water"
    assert fake_service.requests[0]["room_number"] == "501" and fake_service.requests[0]["source"] == "sip"
    cur.execute("SELECT name, status FROM cc_tool_calls WHERE session_id = %s", (sid,))
    assert [tuple(r.values()) for r in cur.fetchall()] == [("notify_staff", "ok")]
    # An MCP retry inside the dedupe bucket files nothing new.
    again = tool(client, token, "create_ticket", {**WATER, "room": "999"})
    assert again.get("deduped") is True and len(fake_service.requests) == 1


def test_concierge_switched_off_for_the_hotel_uses_the_fallback(client, cur, start_call, spy, fake_service, hotel_uae):
    from clefs.core.hotels import set_module_settings

    token = start_call("rtc_gap_off", from_user="1501")
    set_module_settings(cur, hotel_uae.slug, "concierge", {}, actor=None, enabled=False)
    out = tool(client, token, "create_ticket", WATER)
    assert out["status"] == "created" and spy["run_tool"] == [] and spy["fallback"] == ["notify_staff"]
    assert fake_service.requests[0]["room_number"] == "501"


def test_a_call_with_no_concierge_session_uses_the_fallback(client, cur, start_call, spy, fake_service):
    token = start_call("rtc_gap_nosession", from_user="1501")
    cur.execute("UPDATE vc_calls SET session_id = NULL WHERE provider_call_id = 'rtc_gap_nosession'")
    out = tool(client, token, "create_ticket", WATER)
    assert out["status"] == "created" and spy["run_tool"] == [] and spy["fallback"] == ["notify_staff"]


def test_phone_mint_refusal_rejects_the_call(post_event, voice_env, recorder, line, cur, hotel_uae, monkeypatch):
    """is_live said yes, then the concierge refused the phone mint (a
    switch-off racing the call): the call is rejected, never answered
    without a session."""
    from clefs.modules.concierge import api as concierge_api

    cur.execute("DELETE FROM cc_live WHERE hotel_slug = %s", (hotel_uae.slug,))
    monkeypatch.setattr(concierge_api, "is_live", lambda cur, slug: True)
    r = post_event(incoming("rtc_gap_race", from_user="1501", line_secret=line[1]))
    assert r.json()["action"] == "rejected", r.text
    cur.execute("SELECT status, reject_reason, session_id FROM vc_calls WHERE provider_call_id = 'rtc_gap_race'")
    assert dict(cur.fetchone()) == {"status": "rejected", "reject_reason": "concierge_not_live", "session_id": None}
    cur.execute("SELECT count(*) AS n FROM cc_sessions WHERE call_ref = 'rtc_gap_race'")
    assert cur.fetchone()["n"] == 0
