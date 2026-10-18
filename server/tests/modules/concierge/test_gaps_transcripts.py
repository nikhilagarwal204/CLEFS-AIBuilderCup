"""
GET /hotels/{slug}/concierge/transcripts[/{id}]: conversations grouped
from conversation_logs, newest first, masked by role on the server
(clefs.core.pii, field class "transcript").
"""

from __future__ import annotations

import pytest

from tests.modules.concierge.test_tools import fake_service  # noqa: F401  (fixture re-export)

BASE_MS = 1_790_000_000_000


def _log(cur, slug, sid, role, message, *, ms, room="504", source="web_room", safety=None, stay=None):
    cur.execute(
        """
        INSERT INTO conversation_logs (hotel_slug, room_number, session_id, role, message, timestamp, source,
                                       safety_level, stay_session_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (slug, room, sid, role, message, ms, source, safety, stay),
    )


@pytest.fixture
def convs(cur, hotel_uae, legacy_slug):
    s = hotel_uae.slug
    _log(cur, s, "conv-a", "user", "Call me on +971 50 123 4567 about late checkout", ms=BASE_MS, safety="none")
    _log(cur, s, "conv-a", "assistant", "I have asked the front desk.", ms=BASE_MS + 1000)
    _log(cur, s, "conv-b", "user", "There is smoke in the corridor", ms=BASE_MS + 5000, room="310", safety="emergency")
    _log(cur, s, "conv-b", "assistant", "I have alerted the team.", ms=BASE_MS + 6000, room="310")
    _log(cur, s, "conv-c", "user", "What time is breakfast?", ms=BASE_MS + 9000, room="1", source="phone")
    # Another hotel's conversation never shows.
    _log(cur, legacy_slug, "conv-x", "user", "other hotel", ms=BASE_MS + 20000)
    return s


def test_list_groups_newest_first_and_redacts_contacts(client_as, convs):
    r = client_as("owner").get(f"/hotels/{convs}/concierge/transcripts")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["visibility"] == "full"
    assert [c["id"] for c in body["conversations"]] == ["conv-c", "conv-b", "conv-a"]
    a = body["conversations"][2]
    assert a["turns"] == 2 and a["guest_turns"] == 1 and a["room"] == "504" and a["source"] == "web_room"
    assert "123 4567" not in a["preview"] and a["preview"].startswith("Call me on +97")
    assert body["conversations"][1]["safety_level"] == "emergency"
    assert a["started_at"] and a["last_at"] and body["next_before"] is None


def test_filters_and_paging(client_as, convs):
    c = client_as("front_desk")
    flagged = c.get(f"/hotels/{convs}/concierge/transcripts", params={"flagged": "true"}).json()
    assert [x["id"] for x in flagged["conversations"]] == ["conv-b"]
    room = c.get(f"/hotels/{convs}/concierge/transcripts", params={"room": "504"}).json()
    assert [x["id"] for x in room["conversations"]] == ["conv-a"]
    phone = c.get(f"/hotels/{convs}/concierge/transcripts", params={"source": "phone"}).json()
    assert [x["id"] for x in phone["conversations"]] == ["conv-c"]
    assert c.get(f"/hotels/{convs}/concierge/transcripts", params={"source": "fax"}).status_code == 400
    page1 = c.get(f"/hotels/{convs}/concierge/transcripts", params={"limit": 2}).json()
    assert [x["id"] for x in page1["conversations"]] == ["conv-c", "conv-b"] and page1["next_before"]
    page2 = c.get(f"/hotels/{convs}/concierge/transcripts", params={"limit": 2, "before": page1["next_before"]}).json()
    assert [x["id"] for x in page2["conversations"]] == ["conv-a"] and page2["next_before"] is None
    junk = c.get(f"/hotels/{convs}/concierge/transcripts", params={"before": "garbage"}).json()
    assert len(junk["conversations"]) == 3
    huge = c.get(f"/hotels/{convs}/concierge/transcripts", params={"before": "9" * 40 + ":x"})
    assert huge.status_code == 200 and len(huge.json()["conversations"]) == 3


def test_detail_shows_turns_in_order(client_as, convs):
    r = client_as("gm").get(f"/hotels/{convs}/concierge/transcripts/conv-a")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["role"] for t in body["turns"]] == ["user", "assistant"]
    # Inside one conversation the words are whole for a role that may read them.
    assert "+971 50 123 4567" in body["turns"][0]["message"]
    assert body["conversation"]["surface"] is None and body["tools"] == []
    other = client_as("gm").get(f"/hotels/{convs}/concierge/transcripts/conv-x")
    assert other.status_code == 404 and other.json()["code"] == "conversation_not_found"


def test_roles_without_transcript_access_see_no_words(client_as, convs, monkeypatch):
    """finance may be granted concierge.view (a custom setup); the pii
    matrix still gives it no transcript text."""
    from clefs.auth import permissions

    monkeypatch.setitem(permissions._registry, "concierge.view",
                        permissions._registry["concierge.view"] | {"finance"})
    c = client_as("finance")
    body = c.get(f"/hotels/{convs}/concierge/transcripts").json()
    assert body["visibility"] == "none"
    assert [x["id"] for x in body["conversations"]] == ["conv-c", "conv-b", "conv-a"]
    assert all(x["preview"] is None for x in body["conversations"])
    detail = c.get(f"/hotels/{convs}/concierge/transcripts/conv-b").json()
    assert detail["visibility"] == "none" and detail["conversation"]["preview"] is None
    assert [t["message"] for t in detail["turns"]] == [None, None] and detail["conversation"]["safety_level"] == "emergency"


def test_staff_cannot_read_transcripts(client_as, convs):
    assert client_as("staff").get(f"/hotels/{convs}/concierge/transcripts").status_code == 403
    assert client_as("revenue").get(f"/hotels/{convs}/concierge/transcripts/conv-a").status_code == 403


def test_detail_of_a_stored_session_lists_its_tool_calls(client_as, cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api, turns
    from tests.modules.concierge.test_gaps import _phone_session

    sid = _phone_session(cur, hotel_uae.slug)["session_id"]
    turns.record_for_session(cur, sid, role="user", text="Two bottles of water please")
    out = api.run_tool(cur, hotel_uae.slug, session_ctx={"session_id": sid, "call_id": "w1"}, name="notify_staff",
                       args={"request_type": "water", "description_en": "Two bottles of water"})
    turns.record_for_session(cur, sid, role="assistant", text="On its way.")
    body = client_as("owner").get(f"/hotels/{hotel_uae.slug}/concierge/transcripts/{sid}").json()
    conv = body["conversation"]
    assert conv["session_id"] == sid and conv["surface"] == "phone" and conv["verified"] is True
    assert conv["room"] == "205" and conv["preview"] == "Two bottles of water please"
    assert [(t["name"], t["outcome"], t["request_ref"]) for t in body["tools"]] == [("notify_staff", "created", out["request_ref"])]
    assert [t["message"] for t in body["turns"]] == ["Two bottles of water please", "On its way."]
