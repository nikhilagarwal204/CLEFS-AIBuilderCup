"""Intake pipeline (03 spec 1.3): normalize, idempotency, merge, DB rate
limit, translation, safety, priority, resolution, persist and route."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.service import api, intake, state

DUBAI = ZoneInfo("Asia/Dubai")


def _staff(cur, slug, name, department, *, online=True, languages=(), max_open=4, role_key=None):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, languages, max_open, role_key)
           VALUES (%s, %s, 'Attendant', %s, TRUE, %s, %s, %s, %s) RETURNING id""",
        (slug, name, department, online, list(languages), max_open, role_key),
    )
    return cur.fetchone()["id"]


def _events(cur, request_id):
    cur.execute("SELECT kind, action, from_state, to_state FROM svc_request_events WHERE request_id = %s ORDER BY id", (request_id,))
    return [dict(r) for r in cur.fetchall()]


# ─── Filing and routing ───


def test_create_request_files_routes_and_returns_a_token(cur, hotel_uae):
    amina = _staff(cur, hotel_uae.slug, "Amina", "Housekeeping")
    row = api.create_request(cur, hotel_uae.slug, room="101", type="towels", description="Two bath towels please", source="web")
    assert row["state"] == "assigned" and row["status"] == "progress"
    assert row["assigned_staff_id"] == amina and row["assigned_to"] == "Amina"
    assert row["department_key"] == "housekeeping" and row["request_type_key"] == "towels"
    assert row["category"] == "Housekeeping" and row["type"] == "Towel"
    assert row["floor"] == "1" and row["room_type"] == "DLX" and row["intake_version"] == 2
    assert row["severity"] == "L3" and row["sla_policy_ref"] == "global:request_type:towels"
    assert row["ack_due_at"] - row["timestamp"] == 3 * 60_000
    assert row["resolve_due_at"] - row["timestamp"] == 15 * 60_000
    assert row["accepted_at"] == row["assigned_at"]  # the deprecated alias keeps being written
    assert len(row["tracking_token"]) == 22 and row["merged"] is False
    assert [(e["kind"], e["action"]) for e in _events(cur, row["id"])] == [("created", None), ("transition", "assign")]
    status = api.request_status_for_guest(cur, hotel_uae.slug, row["tracking_token"])
    assert status["state"] == "assigned" and status["department_label"] == "Housekeeping"
    assert "assigned_to" not in status and "Amina" not in str(status)


def test_legacy_type_strings_and_keyword_refinement(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Omar", "Maintenance")
    row = api.create_request(cur, hotel_uae.slug, room="102", type="Maintenance",
                             description="The AC is not cooling at all", source="web")
    # A "Maintenance" request about the AC is an AC request (its own SLA)
    # while the legacy type column keeps what the caller sent.
    assert row["request_type_key"] == "ac" and row["type"] == "Maintenance"
    assert row["department_key"] == "engineering" and row["category"] == "Maintenance"
    dining = api.create_request(cur, hotel_uae.slug, room="103", type="In-Room Dining",
                                description="Club sandwich and extra towels", source="web")
    assert dining["request_type_key"] == "ird"  # a keyword never crosses departments


def test_unknown_type_routes_by_category_then_front_office(cur, hotel_uae):
    out = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(
        description="Can someone help", source="web", room="104", type="Mystery", category="Front Desk"))
    assert out.row["department_key"] == "front_office" and out.row["request_type_key"] == "other"
    out = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(
        description="Hello?", source="web", room="105", type="Mystery"))
    assert out.row["department_key"] == "front_office"


def test_nobody_on_shift_leaves_it_new_and_raises_understaffed(cur, hotel_uae):
    row = api.create_request(cur, hotel_uae.slug, room="101", type="towels", description="towels", source="web")
    assert row["state"] == "new" and row["assigned_to"] is None
    cur.execute("SELECT kind, section FROM signals WHERE hotel_slug = %s AND kind = 'service.understaffed'", (hotel_uae.slug,))
    assert cur.fetchone()["section"] == "service_risk"


# ─── Idempotency, merge, rate limit ───


def test_idempotency_key_replays_the_first_outcome(cur, hotel_uae):
    p = dict(description="water please", source="web", room="201", type="water", idempotency_key="sess1:call9")
    first = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(**p))
    again = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(**p))
    assert again.replayed and again.row["id"] == first.row["id"]
    assert again.tracking_token != first.tracking_token  # a second link to the same request
    assert api.request_status_for_guest(cur, hotel_uae.slug, again.tracking_token)["id"] == first.row["id"]
    cur.execute("SELECT COUNT(*) AS n FROM staff_requests WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 1


def test_same_room_and_type_merges_within_the_window(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 3, 1, 10, 0, tzinfo=DUBAI))
    first = api.create_request(cur, hotel_uae.slug, room="202", type="towels", description="2 towels", source="web")
    clock.advance(minutes=4)
    second = api.create_request(cur, hotel_uae.slug, room="202", type="Towel", description="and a bath mat", source="voice")
    assert second["merged"] is True and second["id"] == first["id"]
    assert second["merged_count"] == 1 and "+ and a bath mat" in second["description"]
    assert [e["kind"] for e in _events(cur, first["id"])] == ["created", "merged"]
    clock.advance(minutes=11)  # outside the 10-minute window: a new request
    third = api.create_request(cur, hotel_uae.slug, room="202", type="towels", description="more towels", source="web")
    assert third["merged"] is False and third["id"] != first["id"]


def test_complaints_are_never_merged(cur, hotel_uae):
    a = api.escalate_to_human(cur, hotel_uae.slug, room="203", reason="noise", channel="web", sensitive=False, excerpt="loud")
    b = api.escalate_to_human(cur, hotel_uae.slug, room="203", reason="noise", channel="web", sensitive=False, excerpt="loud")
    assert a["request_id"] != b["request_id"]


def test_guest_channels_are_rate_limited_per_room_in_the_database(cur, hotel_uae):
    for i in range(5):
        api.create_request(cur, hotel_uae.slug, room="204", type=f"Type{i}", description=f"thing {i}", source="web")
    with pytest.raises(ClefsError) as exc:
        api.create_request(cur, hotel_uae.slug, room="204", type="Type9", description="one more", source="whatsapp")
    assert exc.value.status == 429 and exc.value.code == "rate_limited"
    # staff channels are never limited
    api.create_request(cur, hotel_uae.slug, room="204", type="Type9", description="desk call", source="phone_desk")


def _fill_room(cur, slug, room, n=5):
    for i in range(n):
        api.create_request(cur, slug, room=room, type=f"Type{i}", description=f"thing {i}", source="web")


def test_emergency_type_and_l1_hint_skip_the_room_limit(cur, hotel_uae):
    # The concierge's raise_emergency (type emergency) and the voice
    # fallback (priority L1) from a room that already sent five requests.
    _fill_room(cur, hotel_uae.slug, "210")
    a = api.create_request(cur, hotel_uae.slug, room="210", type="emergency", description="Guest reported an emergency",
                           source="voice")
    b = api.create_request(cur, hotel_uae.slug, room="210", type="other", description="caller needs help now",
                           source="sip", priority_hint="L1")
    assert a["severity"] == "L1" and b["severity"] == "L1"
    assert a["id"] != b["id"]
    # an ordinary request from the same room is still refused
    with pytest.raises(ClefsError) as exc:
        api.create_request(cur, hotel_uae.slug, room="210", type="towels", description="more towels", source="web")
    assert exc.value.code == "rate_limited"


def test_emergency_words_over_the_limit_still_reach_a_person(cur, hotel_uae):
    # Five ordinary requests, then the guest reports a fire in plain words
    # without an emergency type: the lexicon look lets it through, flagged.
    _fill_room(cur, hotel_uae.slug, "211")
    row = api.create_request(cur, hotel_uae.slug, room="211", type="other",
                             description="There is smoke coming out of the socket", source="web")
    assert row["severity"] == "L1" and row["sensitivity"] == "emergency"
    assert row["flags"].get("over_rate_limit") is True


def test_placeholder_room_is_never_rate_limited(cur, hotel_uae):
    # Phone callers with no mapped room all share "unknown": the 6th and
    # later strangers in 10 minutes must not be refused as one room.
    for i in range(7):
        api.create_request(cur, hotel_uae.slug, room="unknown", type=f"Type{i}", description=f"caller {i}", source="sip")


# ─── Translation ───


def test_non_english_text_is_translated_and_the_original_kept(cur, hotel_uae, fake_ai):
    fake_ai.push("I need two more towels")
    row = api.create_request(cur, hotel_uae.slug, room="205", type="towels", description="أحتاج منشفتين إضافيتين",
                             source="voice", language="ar")
    assert row["description"] == "I need two more towels" and row["translated"] is True
    assert row["original_text"] == "أحتاج منشفتين إضافيتين" and row["language"] == "ar"


def test_a_failed_translation_keeps_the_guest_words_flagged(cur, hotel_uae, fake_ai):
    fake_ai.push(TimeoutError("slow"))
    row = api.create_request(cur, hotel_uae.slug, room="205", type="towels", description="Ich brauche Handtücher",
                             source="web", language="de")
    assert row["description"] == "Ich brauche Handtücher" and row["translated"] is False
    assert row["flags"]["untranslated"] is True


def test_english_text_in_a_non_latin_language_skips_the_model(cur, hotel_uae, fake_ai):
    row = api.create_request(cur, hotel_uae.slug, room="205", type="water", description="2 bottles of water",
                             source="voice", language="ar")
    # no translation call (the safety classifier's upgrade-only LLM layer still screens the text)
    assert [c for c in fake_ai.calls if c.task == "service.translate"] == []
    assert row["translated"] is None and row["original_text"] is None


# ─── Priority rules (heat, VIP, L1) ───


def test_ac_in_summer_afternoon_heat_is_l1_by_heuristic(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 7, 15, 14, 0, tzinfo=DUBAI))  # no weather reading: the demand stub says None
    row = api.create_request(cur, hotel_uae.slug, room="101", type="ac", description="room is warm", source="web")
    assert row["severity"] == "L1" and row["priority"] == "high"
    assert row["flags"]["heat"] == {"temp_c": None, "basis": "heuristic"}
    cur.execute("SELECT severity, section FROM signals WHERE hotel_slug = %s AND kind = 'service.l1_incident'", (hotel_uae.slug,))
    assert cur.fetchone()["severity"] == 4
    evening = api.create_request(cur, hotel_uae.slug, room="102", type="ac", description="room is warm", source="phone_desk")
    assert evening["severity"] == "L1"  # same afternoon
    clock.freeze(datetime(2026, 7, 15, 20, 0, tzinfo=DUBAI))
    night = api.create_request(cur, hotel_uae.slug, room="103", type="ac", description="room is warm", source="phone_desk")
    assert night["severity"] == "L2" and "heat" not in night["flags"]


def test_ac_uses_the_weather_reading_when_demand_has_one(cur, hotel_uae, clock, monkeypatch):
    from clefs.modules.demand import api as demand_api

    clock.freeze(datetime(2026, 7, 15, 14, 0, tzinfo=DUBAI))
    monkeypatch.setattr(demand_api, "outdoor_temp_c", lambda hotel, at: 38.0)
    cool = api.create_request(cur, hotel_uae.slug, room="101", type="ac", description="warm", source="web")
    assert cool["severity"] == "L2"
    monkeypatch.setattr(demand_api, "outdoor_temp_c", lambda hotel, at: 44.5)
    clock.freeze(datetime(2026, 1, 15, 9, 0, tzinfo=DUBAI))  # winter, but the reading wins
    hot = api.create_request(cur, hotel_uae.slug, room="102", type="ac", description="warm", source="web")
    assert hot["severity"] == "L1" and hot["flags"]["heat"] == {"temp_c": 44.5, "basis": "weather"}


def test_vip_guest_is_at_least_medium(cur, hotel_uae, monkeypatch):
    # guests.api is another lane's module: its documented return is stubbed here
    from clefs.modules.guests import api as guests_api

    ctx = {"201": {"loyaltyTier": "gold", "checkIn": "2026-01-01", "checkOut": "2026-01-05"}}
    monkeypatch.setattr(guests_api, "guest_context_for_room", lambda cur, slug, room: ctx.get(room))
    row = api.create_request(cur, hotel_uae.slug, room="201", type="Spa Booking", description="whenever you can",
                             source="web", priority_hint="low")
    assert row["priority"] == "medium" and row["flags"]["vip"] is True
    plain = api.create_request(cur, hotel_uae.slug, room="202", type="Spa Booking", description="whenever you can",
                               source="web", priority_hint="low")
    assert plain["priority"] == "low"


def test_journey_severity_hints_map_to_severity(cur, hotel_uae):
    row = api.create_request(cur, hotel_uae.slug, room="203", type=api.REQUEST_TYPE_FOR_DEPARTMENT["engineering"],
                             description="The shower is leaking badly", source="whatsapp_journey", priority_hint="L1")
    assert row["severity"] == "L1" and row["priority"] == "high"
    assert row["request_type_key"] == "plumbing" and row["type"] == "Maintenance"


def test_request_type_for_department_covers_the_hint_vocabulary():
    hints = {"housekeeping", "engineering", "ird", "front_office", "concierge", "security", "general"}
    assert hints <= set(api.REQUEST_TYPE_FOR_DEPARTMENT)
    from clefs.modules.service import constants as C

    for hint in hints:
        key = api.REQUEST_TYPE_FOR_DEPARTMENT[hint]
        assert C.REQUEST_TYPES[key]["department_key"] == hint


# ─── Safety ───


def test_safety_verdict_sets_sensitivity_opens_an_incident_and_routes_urgently(cur, hotel_uae, monkeypatch):
    dm = _staff(cur, hotel_uae.slug, "Duty Mgr", "Front Desk", online=False)
    cur.execute(
        """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at, shift_role)
           VALUES (%s, %s, CURRENT_DATE, NOW() - INTERVAL '1 hour', NOW() + INTERVAL '7 hours', 'duty_manager')""",
        (hotel_uae.slug, dm),
    )
    _staff(cur, hotel_uae.slug, "Amina", "Housekeeping")
    monkeypatch.setattr(api, "classify_safety", lambda text, **kw: {"level": "emergency", "kind": "medical",
                                                                      "source": "lexicon", "confidence": 1.0})
    row = api.create_request(cur, hotel_uae.slug, room="101", type="towels", description="my husband collapsed",
                             source="web")
    assert row["sensitivity"] == "emergency" and row["sensitive_kind"] == "medical"
    assert row["severity"] == "L1" and row["priority"] == "high"
    assert row["assigned_staff_id"] == dm  # the duty manager, not the towel attendant
    cur.execute("SELECT kind, level, request_id FROM svc_incidents WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert dict(cur.fetchone()) == {"kind": "medical", "level": "emergency", "request_id": row["id"]}


def test_a_classifier_crash_never_loses_the_request(cur, hotel_uae, monkeypatch):
    def boom(text, **kw):
        raise RuntimeError("lexicon missing")

    monkeypatch.setattr(api, "classify_safety", boom)
    row = api.create_request(cur, hotel_uae.slug, room="101", type="towels", description="towels", source="web")
    assert row["sensitivity"] == "none"


def test_escalation_keeps_sensitive_words_out_of_the_description(cur, hotel_uae):
    out = api.escalate_to_human(cur, hotel_uae.slug, room="102", reason="personal matter", channel="voice",
                                sensitive=True, excerpt="someone followed me to my room")
    row = state.load(cur, hotel_uae.slug, out["request_id"])
    assert "followed" not in row["description"] and row["sensitivity"] == "sensitive"
    assert row["request_type_key"] == "direct_conversation" and row["priority"] == "high"
    open_ = api.escalate_to_human(cur, hotel_uae.slug, room="103", reason="billing", channel="web",
                                  sensitive=False, excerpt="charged twice")
    assert "charged twice" in state.load(cur, hotel_uae.slug, open_["request_id"])["description"]


# ─── Normalize, schedule, windows ───


def test_room_facts_ooo_and_unverified_rooms(cur, hotel_uae):
    cur.execute("UPDATE hotel_rooms SET service_state = 'ooo' WHERE hotel_slug = %s AND room_number = '104'", (hotel_uae.slug,))
    ooo = api.create_request(cur, hotel_uae.slug, room="104", type="water", description="water", source="phone_desk")
    assert ooo["flags"]["ooo_room"] == "ooo" and ooo["verified_room"] is True
    ghost = api.create_request(cur, hotel_uae.slug, room="907", type="water", description="water", source="phone_desk")
    # never lose a request: filed, floor inferred, flagged for the front office
    assert ghost["verified_room"] is False and ghost["flags"]["unverified_room"] is True and ghost["floor"] == "9"


def test_scheduled_requests_wait_and_count_from_release(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 3, 1, 10, 0, tzinfo=DUBAI))
    _staff(cur, hotel_uae.slug, "Amina", "Housekeeping")
    at = int(datetime(2026, 3, 1, 12, 0, tzinfo=DUBAI).timestamp() * 1000)
    out = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(
        description="make up the room at noon", source="phone_desk", room="101", type="make_up_room", scheduled_for=at))
    assert out.row["state"] == "scheduled" and out.row["status"] == "new" and out.row["assigned_to"] is None
    assert out.row["ack_due_at"] == at + 5 * 60_000


def test_turndown_is_due_at_nine_in_the_evening(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 3, 1, 18, 30, tzinfo=DUBAI))
    row = api.create_request(cur, hotel_uae.slug, room="101", type="turndown", description="turndown", source="phone_desk")
    assert row["resolve_due_at"] == int(datetime(2026, 3, 1, 21, 0, tzinfo=DUBAI).timestamp() * 1000)
    clock.freeze(datetime(2026, 3, 1, 21, 30, tzinfo=DUBAI))
    late = api.create_request(cur, hotel_uae.slug, room="102", type="turndown", description="turndown", source="phone_desk")
    assert late["resolve_due_at"] - late["timestamp"] == 45 * 60_000


def test_hotel_sla_override_beats_the_global_seed(cur, hotel_uae):
    cur.execute(
        """INSERT INTO svc_sla_policies (hotel_slug, scope_type, scope_key, ack_min, resolve_min)
           VALUES (%s, 'request_type', 'towels', 2, 10)""",
        (hotel_uae.slug,),
    )
    row = api.create_request(cur, hotel_uae.slug, room="101", type="towels", description="towels", source="phone_desk")
    assert row["sla_policy_ref"] == "hotel:request_type:towels"
    assert row["resolve_due_at"] - row["timestamp"] == 10 * 60_000


def test_stay_stage_from_the_guest_dates(cur, hotel_uae, clock, monkeypatch):
    from clefs.modules.guests import api as guests_api

    clock.freeze(datetime(2026, 3, 3, 10, 0, tzinfo=DUBAI))
    ctx = {
        "101": {"checkIn": "2026-03-03", "checkOut": "2026-03-06"},
        "102": {"checkIn": "2026-03-01", "checkOut": "2026-03-06"},
        "103": {"checkIn": "2026-03-01", "checkOut": "2026-03-03"},
    }
    monkeypatch.setattr(guests_api, "guest_context_for_room", lambda cur, slug, room: ctx.get(room))
    stages = [api.create_request(cur, hotel_uae.slug, room=r, type="water", description="w", source="phone_desk")["stay_stage"]
              for r in ("101", "102", "103", "104")]
    assert stages == ["arrival", "in_stay", "departure", "unknown"]


def test_amenity_items_resolve_through_the_amenities_api(cur, hotel_uae, monkeypatch):
    from clefs.modules.amenities import api as amenities_api

    linked = []
    monkeypatch.setattr(amenities_api, "resolve_item",
                        lambda cur, slug, item_id: {"id": item_id, "name_en": "Bath robe", "request_type_key": "amenities"})
    monkeypatch.setattr(amenities_api, "link_request", lambda cur, slug, **kw: linked.append(kw))
    out = intake.create_request(cur, hotel_uae.slug, intake.IntakeIn(
        description="", source="web", room="101", amenity_item_id=7, quantity=2))
    assert out.row["request_type_key"] == "amenities" and out.row["description"] == "2 x Bath robe"
    assert linked == [{"request_id": out.row["id"], "amenity_item_id": 7, "qty": 2}]


def test_food_orders_are_never_merged(cur, hotel_uae):
    a = api.create_request(cur, hotel_uae.slug, room="101", type="Food", description="In-room order #1: 1x Soup.", source="web")
    b = api.create_request(cur, hotel_uae.slug, room="101", type="Food", description="In-room order #2: 1x Tea.", source="web")
    assert a["request_type_key"] == "ird" and a["id"] != b["id"] and b["merged"] is False
