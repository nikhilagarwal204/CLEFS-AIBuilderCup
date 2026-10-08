"""
Wave 1 concierge gaps:
- concierge.api.run_tool: the one executor door for voice and MCP (room
  from the session, dedupe by call_id, tenant check, dry-run specs);
- the phone session mint (open_session transport sip) enforces the same
  concierge.live gate as the web mint;
- GET /hotels/{slug}/concierge/transcripts[/{id}], masked by role.
"""

from __future__ import annotations

import pytest

from clefs.core import clock
from tests.modules.concierge.conftest import switch_live
from tests.modules.concierge.test_tools import FakeService, fake_service  # noqa: F401  (fixture re-export)


def _phone_session(cur, slug, room="205", verified=True):
    from clefs.modules.concierge import api

    spec = api.build_surface_session(slug, surface="phone", room=room, verified=verified, cur=cur)
    return api.open_session(cur, spec, call_ref="rtc_gaps", consents={"recording": True})


# ─── run_tool ───


def test_run_tool_uses_the_session_room_and_dedupes(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    args = {"request_type": "water", "description_en": "Two bottles of water", "room": "999"}
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid, "call_id": "k1"}, name="notify_staff", args=args)
    assert out["status"] == "created" and out["verified_room"] is True
    assert fake_service.requests[0]["room"] == "205" and fake_service.requests[0]["source"] == "sip"
    again = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid, "call_id": "k1"}, name="notify_staff", args=args)
    assert again["deduped"] is True and again["request_ref"] == out["request_ref"]
    assert len(fake_service.requests) == 1
    cur.execute("SELECT count(*) AS n FROM cc_tool_calls WHERE session_id = %s", (sid,))
    assert cur.fetchone()["n"] == 1


def test_run_tool_parses_a_raw_arguments_string(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid}, name="notify_staff",
                       args='{"request_type": "towels", "description_en": "Towels"}')
    assert out["status"] == "created" and fake_service.requests[0]["room"] == "205"
    broken = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid}, name="get_request_status", args="{not json")
    assert broken["status"] in ("ok", "none")


def test_run_tool_refuses_another_hotels_session(cur, hotel_uae, legacy_slug, fake_service):
    from clefs.modules.concierge import api

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    out = api.run_tool(cur, legacy_slug, session_ctx={"session_id": sid, "call_id": "x"}, name="notify_staff",
                       args={"request_type": "water", "description_en": "water"})
    assert out["status"] == "session_ended" and fake_service.requests == []
    cur.execute("SELECT count(*) AS n FROM cc_tool_calls WHERE session_id = %s", (sid,))
    assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("ctx", [{}, {"session_id": "not-a-uuid"}, {"session_id": "00000000-0000-0000-0000-000000000000"}])
def test_run_tool_without_a_live_session_is_speakable(cur, hotel_uae, ctx):
    from clefs.modules.concierge import api

    out = api.run_tool(cur, hotel_uae.slug, session_ctx=ctx, name="get_menu", args={})
    assert out["status"] == "session_ended" and out["followup_instructions"]


def test_run_tool_on_an_ended_session(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    cur.execute("UPDATE cc_sessions SET ended_at = %s WHERE id = %s", (clock.now(), sid))
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid}, name="notify_staff",
                       args={"request_type": "water", "description_en": "water"})
    assert out["status"] == "session_ended" and fake_service.requests == []


def test_run_tool_unknown_tool(cur, hotel_uae):
    from clefs.modules.concierge import api

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid}, name="drop_tables", args={})
    assert out["status"] == "unknown_tool"


def test_run_tool_dry_run_spec_files_nothing(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api

    spec = api.build_surface_session(hotel_uae.slug, surface="eval", room="504", cur=cur)
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"spec": spec}, name="notify_staff",
                       args={"request_type": "water", "description_en": "water"})
    assert out["status"] == "created" and out["dry_run"] is True and fake_service.requests == []
    live = api.build_surface_session(hotel_uae.slug, surface="phone", room="504", verified=True, cur=cur)
    refused = api.run_tool(cur, hotel_uae.slug, session_ctx={"spec": live}, name="notify_staff",
                           args={"request_type": "water", "description_en": "water"})
    assert refused["status"] == "session_ended" and fake_service.requests == []


# ─── Phone mint gate ───


def test_phone_session_mint_needs_the_concierge_live(cur, hotel_uae):
    from clefs.core.errors import ClefsError
    from clefs.modules.concierge import api

    switch_live(cur, hotel_uae.slug, False)
    phone = api.build_surface_session(hotel_uae.slug, surface="phone", room="205", verified=True, cur=cur)
    with pytest.raises(ClefsError) as err:
        api.open_session(cur, phone, call_ref="rtc_off")
    assert err.value.code == "concierge_not_live" and err.value.status == 409
    # Evals and the text door never need the switch.
    ev = api.build_surface_session(hotel_uae.slug, surface="eval", room="504", cur=cur)
    assert api.open_session(cur, ev, key_status="eval", transport="none")["session_id"]
    switch_live(cur, hotel_uae.slug, None)
    with pytest.raises(ClefsError):
        api.open_session(cur, phone, call_ref="rtc_never")
