"""Concierge tools (03 spec 2.4): room binding, dedupe, requests, escalations."""

from __future__ import annotations

import json

import pytest

from clefs.core import rooms
from clefs.modules.concierge import tools

GRANTS = {"ai_processing": True, "transfer_abroad": True, "recording": True, "ai_analytics": False}


class FakeService:
    """service.api stand-in: the scoped suite runs without Service's tables,
    and these tests pin what the concierge sends across the boundary."""

    def __init__(self):
        self.requests: list[dict] = []
        self.escalations: list[dict] = []
        self.merge_next = False

    def create_request(self, cur, hotel_slug, **kw):
        n = len(self.requests) + len(self.escalations) + 1
        self.requests.append({"hotel_slug": hotel_slug, **kw})
        merged, self.merge_next = self.merge_next, False
        return {"id": 1000 + n, "tracking_token": f"tok-{1000 + n}", "merged": merged, "replayed": False}

    def escalate_to_human(self, cur, hotel_slug, **kw):
        n = len(self.requests) + len(self.escalations) + 1
        self.escalations.append({"hotel_slug": hotel_slug, **kw})
        return {"ok": True, "request_id": 1000 + n, "assigned_to": None, "tracking_token": f"tok-{1000 + n}"}

    def request_status_for_guest(self, cur, hotel_slug, token):
        rid = int(token.split("-")[1])
        return {"id": rid, "state": "on_the_way", "request_type_label": "Towels", "cancelled": False}

    def classify_safety(self, text, **kw):
        if "fire" in text.lower():
            return {"level": "emergency", "kind": "fire", "source": "lexicon", "confidence": 1.0}
        return {"level": "none", "kind": None, "source": None, "confidence": 0.0}


@pytest.fixture
def fake_service(monkeypatch):
    from clefs.modules.service import api as service_api

    fake = FakeService()
    for name in ("create_request", "escalate_to_human", "request_status_for_guest", "classify_safety"):
        monkeypatch.setattr(service_api, name, getattr(fake, name))
    return fake


def mint(client, slug, *, room="101", k=True, location=None, **kw):
    target = {"location": location} if location else {"room": room}
    body = {"hotel": slug, "target": target, "lang": "en", "mode": "text", "transport": "http", "consents": GRANTS}
    if k and not location:
        body["k"] = rooms.qr_key(slug, room)
    body.update(kw)
    r = client.post("/public/concierge/sessions", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def call(client, s, name, args, call_id="c1"):
    return client.post(
        f"/public/concierge/sessions/{s['session_id']}/tools/{name}",
        json={"call_id": call_id, "arguments": json.dumps(args)},
        headers={"Authorization": f"Bearer {s['session_token']}"},
    )


def test_schemas_offer_room_only_where_the_session_has_none():
    room = {t["name"]: t for t in tools.tool_schemas("web_room", search=True, can_order=True)}
    assert set(room) == set(tools.TOOL_NAMES)
    assert "room" not in room["notify_staff"]["parameters"]["properties"]
    assert "room" not in room["place_order"]["parameters"]["properties"]
    venue = {t["name"]: t for t in tools.tool_schemas("web_location", can_order=True)}
    assert "room" in venue["notify_staff"]["parameters"]["properties"]
    assert "search_hotel_info" not in venue
    wa = {t["name"] for t in tools.tool_schemas("whatsapp", can_order=True)}
    assert "place_order" not in wa and "notify_staff" in wa


def test_notify_staff_uses_session_room_and_ignores_room_argument(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug, room="101")
    r = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "2 bath towels", "quantity": 2,
                                         "room": "205", "original_text": "two towels please", "language": "en"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "created" and out["request_ref"] == "1001"
    assert out["ui"] == {"kind": "request", "token": "tok-1001"} and out["followup_instructions"]
    sent = fake_service.requests[0]
    assert sent["room"] == "101" and sent["source"] == "text" and sent["type"] == "towels"
    assert 'Guest said: "two towels please"' in sent["description"] and "205" not in sent["description"]
    cur.execute("SELECT requests_created, tool_calls FROM cc_sessions WHERE id = %s", (s["session_id"],))
    assert dict(cur.fetchone()) == {"requests_created": 1, "tool_calls": 1}


def test_same_call_id_is_deduped(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    args = {"request_type": "water", "description_en": "Two bottles of water"}
    first = call(client, s, "notify_staff", args, call_id="dup-1").json()
    second = call(client, s, "notify_staff", args, call_id="dup-1").json()
    assert second["deduped"] is True and second["request_ref"] == first["request_ref"]
    assert len(fake_service.requests) == 1
    third = call(client, s, "notify_staff", args, call_id="dup-2").json()
    assert "deduped" not in third and len(fake_service.requests) == 2


def test_unverified_room_is_marked_for_door_check(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug, room="102", k=False)
    assert s["verified"] is False
    out = call(client, s, "notify_staff", {"request_type": "pillows", "description_en": "One more pillow"}).json()
    assert out["status"] == "created" and out["verified_room"] is False
    assert "confirm the room" in out["followup_instructions"]
    assert "Room not verified" in fake_service.requests[0]["description"]


def test_location_session_takes_the_room_argument_unverified(client, cur, hotel_uae, fake_service):
    cur.execute("INSERT INTO hotel_locations (hotel_slug, slug, name, type) VALUES (%s, 'pool', 'Pool Deck', 'public')",
                (hotel_uae.slug,))
    s = mint(client, hotel_uae.slug, location="pool")
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Pool towels"}).json()
    assert out["status"] == "created"
    assert fake_service.requests[0]["room"] == "" and "Pool Deck" in fake_service.requests[0]["description"]
    out = call(client, s, "notify_staff", {"request_type": "water", "description_en": "Water", "room": "205"}, "c2").json()
    assert fake_service.requests[1]["room"] == "205" and out["verified_room"] is False


def test_sensitive_request_goes_the_discreet_path(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "notify_staff", {"request_type": "security", "description_en": "Someone keeps knocking",
                                           "sensitivity": "sensitive", "original_text": "a man keeps knocking"}).json()
    assert out["status"] == "escalated" and not fake_service.requests
    esc = fake_service.escalations[0]
    assert esc["sensitive"] is True and esc["excerpt"] is None and esc["room"] == "101"


def test_tool_route_needs_the_bearer_token(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    r = client.post(f"/public/concierge/sessions/{s['session_id']}/tools/notify_staff", json={"arguments": {}})
    assert r.status_code == 401
    r = call(client, {**s, "session_token": "nope"}, "notify_staff", {})
    assert r.status_code == 401
    assert call(client, s, "drop_tables", {}).status_code == 404
    r = client.post(f"/public/concierge/sessions/{s['session_id']}/tools/notify_staff",
                    json={"arguments": "[1,2]"}, headers={"Authorization": f"Bearer {s['session_token']}"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_arguments"


# ─── escalate_to_human, status, search ───


def test_unknown_answer_escalates_and_queues_the_question(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "escalate_to_human", {"reason": "unknown_answer", "summary_en": "Asked about camel rides",
                                                "original_text": "Can I book a camel ride?"}).json()
    assert out["status"] == "escalated" and out["reason"] == "unknown_answer" and out["ui"]["kind"] == "handoff"
    assert fake_service.escalations[0]["room"] == "101" and fake_service.escalations[0]["channel"] == "text"
    cur.execute("SELECT title, count FROM cc_kb_unknowns WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert dict(cur.fetchone()) == {"title": "Can I book a camel ride?", "count": 1}
    cur.execute("SELECT handoffs, unknowns FROM cc_sessions WHERE id = %s", (s["session_id"],))
    assert dict(cur.fetchone()) == {"handoffs": 1, "unknowns": 1}


def test_emergency_files_an_l1_request_and_shows_the_numbers(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "escalate_to_human", {"reason": "emergency", "urgency": "high",
                                                "summary_en": "Guest reports smoke in the corridor"}).json()
    assert out["status"] == "escalated" and out["ui"]["kind"] == "emergency"
    assert [n["number"] for n in out["ui"]["numbers"]][:3] == ["999", "998", "997"]
    assert "999" in out["followup_instructions"]
    sent = fake_service.requests[0]
    assert sent["type"] == "emergency" and sent["priority_hint"] == "L1" and sent["room"] == "101"
    again = call(client, s, "escalate_to_human", {"reason": "emergency", "summary_en": "Smoke"}, "c2").json()
    assert again["deduped"] is True and len(fake_service.requests) == 1


def test_request_status_is_this_rooms_only(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug, room="101")
    call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Towels"})
    other = mint(client, hotel_uae.slug, room="102")
    call(client, other, "notify_staff", {"request_type": "water", "description_en": "Water"})
    mine = call(client, s, "get_request_status", {}, "c9").json()
    assert mine["status"] == "ok" and [r["ref"] for r in mine["requests"]] == ["1001"]
    assert mine["requests"][0]["state"] == "on_the_way" and mine["ui"]["kind"] == "tracker"
    # A later session of the same verified room sees it too (a reload).
    again = mint(client, hotel_uae.slug, room="101")
    assert [r["ref"] for r in call(client, again, "get_request_status", {}).json()["requests"]] == ["1001"]
    # An unverified session of that room does not.
    guess = mint(client, hotel_uae.slug, room="101", k=False)
    assert call(client, guess, "get_request_status", {}).json()["status"] == "none"


def test_search_hotel_info_reads_approved_entries_only(client, cur, hotel_uae):
    from clefs.modules.concierge import kb

    e = kb.create_entry(cur, hotel_uae.slug, {"section_key": "pool", "kind": "faq", "question_en": "Pool hours?",
                                              "answer_en": "The rooftop pool is open 7am to 10pm."})
    s = mint(client, hotel_uae.slug)
    assert call(client, s, "search_hotel_info", {"query": "rooftop pool"}).json()["status"] == "not_found"
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    out = call(client, s, "search_hotel_info", {"query": "rooftop pool"}, "c2").json()
    assert out["status"] == "ok" and out["results"][0]["kb_ref"] == f"KB:{e['id']}"


def test_service_refusal_becomes_a_speakable_result(client, cur, hotel_uae, fake_service, monkeypatch):
    from clefs.core.errors import too_many
    from clefs.modules.service import api as service_api

    def refuse(cur, hotel_slug, **kw):
        raise too_many("Room 101 has sent 5 requests")

    monkeypatch.setattr(service_api, "create_request", refuse)
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Towels"}).json()
    assert out["status"] == "rate_limited" and "front desk" in out["followup_instructions"]
    cur.execute("SELECT status, result FROM cc_tool_calls WHERE session_id = %s", (s["session_id"],))
    row = cur.fetchone()
    assert row["status"] == "refused" and row["result"]["status"] == "rate_limited"


def test_phone_entry_point_by_session_id(cur, hotel_uae, fake_service):
    from clefs.modules.concierge import api, turns

    spec = api.build_surface_session(hotel_uae.slug, surface="phone", room="101", verified=True, cur=cur)
    opened = api.open_session(cur, spec, call_ref="rtc_1")
    out = tools.execute_for_session(cur, opened["session_id"], "notify_staff",
                                    {"request_type": "water", "description_en": "Water", "room": "999"}, call_id="c1")
    assert out["status"] == "created" and fake_service.requests[0]["room"] == "101"
    assert fake_service.requests[0]["source"] == "sip"
    logged = turns.record_for_session(cur, opened["session_id"], role="user", text="there is a fire")
    assert logged["escalated"] is True and fake_service.requests[1]["type"] == "emergency"
    gone = tools.execute_for_session(cur, "00000000-0000-0000-0000-000000000000", "get_menu", {})
    assert gone["status"] == "session_ended"


def test_real_service_request_when_service_is_installed(client, cur, hotel_uae):
    """End to end through Service's intake when its tables exist (the full
    suite); the scoped concierge run skips it."""
    cur.execute("SELECT to_regclass('svc_shift_templates') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        pytest.skip("service module not migrated in this run")
    s = mint(client, hotel_uae.slug, room="101")
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "2 bath towels", "quantity": 2,
                                           "room": "205"}).json()
    assert out["status"] in ("created", "merged"), out
    cur.execute("SELECT room, source FROM staff_requests WHERE id = %s", (int(out["request_ref"]),))
    assert dict(cur.fetchone()) == {"room": "101", "source": "text"}
    status = call(client, s, "get_request_status", {}, "c2").json()
    assert status["status"] == "ok" and status["requests"][0]["ref"] == out["request_ref"]
