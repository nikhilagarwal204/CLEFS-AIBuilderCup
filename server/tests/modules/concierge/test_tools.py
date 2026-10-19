"""Concierge tools (03 spec 2.4): room binding, dedupe, requests, escalations."""

from __future__ import annotations

import json

import pytest

from clefs.core import rooms
from clefs.modules.concierge import tools

GRANTS = {"ai_processing": True, "transfer_abroad": True, "recording": True, "ai_analytics": False}


CREATED_MS = 1_760_000_000_000


class FakeService:
    """service.api stand-in: the scoped suite runs without Service's tables,
    and these tests pin what the concierge sends across the boundary. The
    job functions keep the 05 spec section 3 signatures exactly, so a
    missing or misnamed argument fails here as it would against Service."""

    def __init__(self):
        self.requests: list[dict] = []
        self.escalations: list[dict] = []
        self.recoveries: list[dict] = []
        self.feedback: list[dict] = []
        self.merge_next = False
        # Who dispatch picks and the minutes it gives (None: nobody free yet).
        self.staff: str | None = "Maria"
        self.department = "Housekeeping"
        self.eta: int | None = 5
        self.states: dict[str, str] = {}  # tracking token -> tracker state
        self.refuse_feedback = False

    def _next(self) -> int:
        return 1000 + len(self.requests) + len(self.escalations) + 1

    def create_request(self, cur, hotel_slug, **kw):
        rid = self._next()
        self.requests.append({"hotel_slug": hotel_slug, **kw})
        merged, self.merge_next = self.merge_next, False
        return {"id": rid, "tracking_token": f"tok-{rid}", "merged": merged, "replayed": False}

    def create_job(self, cur, hotel_slug, *, room, request_type, quantity, description_en, original_text, guest_language,
                   priority="normal", mood=None, frustration=None, session_id=None, guest_ref=None, channel="concierge",
                   idempotency_key=None):
        rid = self._next()
        # room / type / source / description keep the keys create_request had,
        # so the room-binding assertions read the same for both.
        self.requests.append({
            "hotel_slug": hotel_slug, "room": room, "type": request_type, "source": channel, "description": description_en,
            "quantity": quantity, "original_text": original_text, "guest_language": guest_language, "priority": priority,
            "mood": mood, "frustration": frustration, "session_id": session_id, "guest_ref": guest_ref,
            "idempotency_key": idempotency_key,
        })
        merged, self.merge_next = self.merge_next, False
        return {**self.dispatch_info(cur, hotel_slug, rid), "item_label": request_type.replace("_", " ").title(),
                "quantity": quantity, "room": room, "merged": merged}

    def dispatch_info(self, cur, hotel_slug, request_id):
        return {
            "request_id": request_id, "tracking_token": f"tok-{request_id}", "item_label": "Front desk", "quantity": None,
            "room": "101", "department_key": "housekeeping", "department_label": self.department,
            "staff_id": 7 if self.staff else None, "staff_first_name": self.staff, "eta_minutes": self.eta,
            "eta_at": None, "created_at": CREATED_MS, "accepted_at": None, "state": "assigned", "status": "progress",
            "proof_status": None, "guest_confirmed_at": None, "closed_at": None,
        }

    def escalate_to_human(self, cur, hotel_slug, **kw):
        rid = self._next()
        self.escalations.append({"hotel_slug": hotel_slug, **kw})
        return {"ok": True, "request_id": rid, "assigned_to": None, "tracking_token": f"tok-{rid}"}

    def flag_recovery(self, cur, hotel_slug, *, kind, room, summary, request_id=None, session_id=None,
                      guest_language=None):
        self.recoveries.append({"hotel_slug": hotel_slug, "kind": kind, "room": room, "summary": summary,
                                "request_id": request_id, "session_id": session_id, "guest_language": guest_language})
        return {"case_id": 70 + len(self.recoveries), "status": "open"}

    def guest_feedback(self, cur, hotel_slug, tracking_token, *, fixed, comment=None):
        from clefs.core.errors import ClefsError

        if self.refuse_feedback:
            raise ClefsError(409, "This request can no longer be confirmed", "cannot_confirm")
        self.feedback.append({"hotel_slug": hotel_slug, "token": tracking_token, "fixed": fixed, "comment": comment})
        return {"ok": True, "case_id": 90 + len(self.feedback), "state": "reopened" if not fixed else "closed"}

    def request_status_for_guest(self, cur, hotel_slug, token):
        rid = int(token.split("-")[1])
        return {"id": rid, "state": self.states.get(token, "on_the_way"), "request_type_label": "Towels",
                "cancelled": False}

    def classify_safety(self, text, **kw):
        if "fire" in text.lower():
            return {"level": "emergency", "kind": "fire", "source": "lexicon", "confidence": 1.0}
        return {"level": "none", "kind": None, "source": None, "confidence": 0.0}


SERVICE_FUNCTIONS = ("create_request", "create_job", "dispatch_info", "escalate_to_human", "flag_recovery",
                     "guest_feedback", "request_status_for_guest", "classify_safety")


@pytest.fixture
def fake_service(monkeypatch):
    from clefs.modules.service import api as service_api

    fake = FakeService()
    for name in SERVICE_FUNCTIONS:
        # raising=False: the job functions are Service's to add (05 spec 3).
        monkeypatch.setattr(service_api, name, getattr(fake, name), raising=False)
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
    assert out["ui"]["kind"] == "request" and out["ui"]["token"] == "tok-1001" and out["followup_instructions"]
    sent = fake_service.requests[0]
    assert sent["room"] == "101" and sent["source"] == "text" and sent["type"] == "towels"
    assert sent["original_text"] == "two towels please" and sent["quantity"] == 2 and sent["guest_language"] == "en"
    assert "205" not in sent["description"] and "Guest said" not in sent["description"]
    assert sent["session_id"] == s["session_id"] and sent["guest_ref"]
    assert sent["idempotency_key"] == f"cc:{s['session_id']}:c1"
    cur.execute("SELECT requests_created, tool_calls FROM cc_sessions WHERE id = %s", (s["session_id"],))
    assert dict(cur.fetchone()) == {"requests_created": 1, "tool_calls": 1}


# ─── Jobs: the card and the named, timed confirmation (05 spec 3) ───


def test_notify_staff_returns_the_job_card_and_names_staff_and_minutes(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug, room="101")
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Extra bath towels",
                                           "quantity": 2, "priority": "high"}).json()
    assert out["ui"]["card"] == {
        "item_label": "Towels", "quantity": 2, "room": "101", "staff_first_name": "Maria",
        "department_label": "Housekeeping", "eta_minutes": 5, "created_at": CREATED_MS, "accepted_at": None,
        "state": "assigned", "request_type": "towels", "department_key": "housekeeping",
    }
    follow = out["followup_instructions"]
    assert "Housekeeping has been notified" in follow and "Maria is on the way, about 5 minutes" in follow
    assert out["staff_first_name"] == "Maria" and out["eta_minutes"] == 5 and out["department"] == "Housekeeping"
    assert fake_service.requests[0]["priority"] == "high", "URGENT is high (05 spec decision 11)"


def test_a_surname_never_reaches_the_guest(client, hotel_uae, fake_service):
    fake_service.staff = "Maria Santos"
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "notify_staff", {"request_type": "water", "description_en": "Two bottles of water"}).json()
    assert out["ui"]["card"]["staff_first_name"] == "Maria"
    assert "Santos" not in json.dumps(out)


def test_confirmation_without_a_person_or_a_time_promises_neither(client, hotel_uae, fake_service):
    fake_service.staff, fake_service.eta = None, 12
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "notify_staff", {"request_type": "ac", "description_en": "The AC is not cooling"}).json()
    assert "someone will be with the guest in about 12 minutes" in out["followup_instructions"]
    fake_service.eta = None
    out = call(client, s, "notify_staff", {"request_type": "pillows", "description_en": "One more pillow"}, "c2").json()
    assert "Do not promise a time" in out["followup_instructions"] and out["ui"]["card"]["eta_minutes"] is None


def test_each_ask_is_its_own_job_with_the_sessions_mood(client, cur, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    cur.execute("UPDATE cc_sessions SET last_mood = 'annoyed', max_frustration = 0.55 WHERE id = %s", (s["session_id"],))
    for i, rtype in enumerate(("towels", "water", "ac")):
        out = call(client, s, "notify_staff", {"request_type": rtype, "description_en": rtype}, f"c{i}").json()
        assert out["status"] == "created" and out["ui"]["card"]["request_type"] == rtype
    assert [r["type"] for r in fake_service.requests] == ["towels", "water", "ac"]
    assert {(r["mood"], r["frustration"]) for r in fake_service.requests} == {("annoyed", 0.55)}
    assert len({r["idempotency_key"] for r in fake_service.requests}) == 3


def test_label_and_wanted_time_reach_staff_in_english(client, hotel_uae, fake_service):
    s = mint(client, hotel_uae.slug)
    call(client, s, "notify_staff", {"request_type": "wake_up", "label": "Wake-up call", "description_en": "Call at 6",
                                     "scheduled_for": "06:00", "quantity": "1"})
    sent = fake_service.requests[0]
    assert sent["description"].startswith("Wake-up call: Call at 6") and "Wanted at: 06:00" in sent["description"]
    assert sent["quantity"] == 1


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


def test_talk_to_the_front_desk_is_a_handoff_with_a_job_card(client, hotel_uae, fake_service):
    """The guest page's "Talk to the front desk" chip calls this directly,
    with no model in between."""
    fake_service.staff, fake_service.department = "Ahmed Khan", "Front Office"
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "escalate_to_human", {"reason": "asked_for_human",
                                                "summary_en": "Guest tapped Talk to the front desk.",
                                                "original_text": "Talk to the front desk"}).json()
    assert out["status"] == "escalated" and out["ui"]["kind"] == "handoff" and out["ui"]["token"] == "tok-1001"
    card = out["ui"]["card"]
    assert card["staff_first_name"] == "Ahmed" and card["department_label"] == "Front Office"
    assert card["request_type"] == "front_desk" and card["created_at"] == CREATED_MS
    assert "Ahmed from Front Office" in out["followup_instructions"] and "Khan" not in str(out)


def test_handoff_without_a_dispatch_card_still_goes_through(client, hotel_uae, fake_service, monkeypatch):
    from clefs.modules.service import api as service_api

    def broken(*a, **kw):
        raise RuntimeError("no such request")

    monkeypatch.setattr(service_api, "dispatch_info", broken)
    s = mint(client, hotel_uae.slug)
    out = call(client, s, "escalate_to_human", {"reason": "asked_for_human", "summary_en": "Wants a person"}).json()
    assert out["status"] == "escalated" and out["ui"] == {"kind": "handoff", "token": "tok-1001"}
    assert "a team member" in out["followup_instructions"]


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


def test_request_status_may_name_the_staff_member_by_first_name(client, hotel_uae, fake_service, monkeypatch):
    from clefs.modules.service import api as service_api

    def view(cur, hotel_slug, token):
        return {"id": 1001, "state": "on_the_way", "request_type_label": "Towels", "cancelled": False,
                "staff_first_name": "Maria Santos", "eta_minutes": 4}

    s = mint(client, hotel_uae.slug)
    call(client, s, "notify_staff", {"request_type": "towels", "description_en": "Towels"})
    monkeypatch.setattr(service_api, "request_status_for_guest", view)
    out = call(client, s, "get_request_status", {}, "c2").json()
    [item] = out["requests"]
    assert item["staff_first_name"] == "Maria" and item["eta_minutes"] == 4
    assert "Santos" not in str(out) and "staff_first_name" in out["followup_instructions"]


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

    monkeypatch.setattr(service_api, "create_job", refuse)
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
    from clefs.modules.service import api as service_api

    cur.execute("SELECT to_regclass('svc_shift_templates') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        pytest.skip("service module not migrated in this run")
    if not hasattr(service_api, "create_job"):
        pytest.skip("service.api.create_job (05 spec 3) is not in this tree yet")
    s = mint(client, hotel_uae.slug, room="101")
    out = call(client, s, "notify_staff", {"request_type": "towels", "description_en": "2 bath towels", "quantity": 2,
                                           "room": "205"}).json()
    assert out["status"] in ("created", "merged"), out
    cur.execute("SELECT room, source FROM staff_requests WHERE id = %s", (int(out["request_ref"]),))
    assert dict(cur.fetchone()) == {"room": "101", "source": "text"}
    status = call(client, s, "get_request_status", {}, "c2").json()
    assert status["status"] == "ok" and status["requests"][0]["ref"] == out["request_ref"]
