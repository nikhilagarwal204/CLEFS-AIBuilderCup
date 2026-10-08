"""State machine (03 spec 1.2): the transition table, legacy status
derivation, versions, replays, implicit acks, reopen rules and the feed."""

from __future__ import annotations

import random
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.service import api, state
from clefs.modules.service import constants as C

DUBAI = ZoneInfo("Asia/Dubai")


def _staff(cur, slug, name, department="Housekeeping", online=True):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) VALUES (%s, %s, 'A', %s, TRUE, %s) RETURNING id",
        (slug, name, department, online),
    )
    return cur.fetchone()["id"]


def _new(cur, slug, room="101", **kw):
    return api.create_request(cur, slug, room=room, type=kw.pop("type", "towels"), description="towels",
                              source=kw.pop("source", "phone_desk"), **kw)


STAFF = state.Actor("user", user_id=1, role="staff", label="u")
MANAGER = state.Actor("user", user_id=2, role="gm", label="gm")


# ─── Table ───


def test_every_state_has_a_legacy_status_and_the_table_is_closed():
    assert set(state.TRANSITIONS) == set(C.STATES)
    for frm, actions in state.TRANSITIONS.items():
        for action, to in actions.items():
            assert to in C.STATES, (frm, action)
    assert state.legacy_status("cancelled") == "completed" and state.legacy_status("reopened") == "progress"
    assert state.allowed("new", "complete") is None and state.allowed("new", "complete", legacy=True) == "completed"


def test_happy_path_ack_start_complete_writes_events_and_versions(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 3, 1, 10, 0, tzinfo=DUBAI))
    amina = _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    assert row["state"] == "assigned" and row["version"] == 1
    clock.advance(minutes=2)
    who = state.Actor("device", staff_id=amina, device_id=None, role="device")
    r = state.transition(cur, hotel_uae.slug, row["id"], "ack", who, expected_version=1)["row"]
    assert r["state"] == "acknowledged" and r["status"] == "progress" and r["version"] == 2
    assert r["acknowledged_at"] - r["timestamp"] == 2 * 60_000 and r["ack_implicit"] is False
    r = state.transition(cur, hotel_uae.slug, row["id"], "start", who)["row"]
    assert r["state"] == "in_progress" and r["started_at"] is not None
    clock.advance(minutes=6)
    r = state.transition(cur, hotel_uae.slug, row["id"], "complete", who, resolution_code="delivered")["row"]
    assert r["state"] == "completed" and r["status"] == "completed" and r["completed_by_staff_id"] == amina
    assert r["resolved_at"] - r["timestamp"] == 8 * 60_000 and r["resolution_code"] == "delivered"
    feed = state.events_after(cur, hotel_uae.slug, 0, request_id=row["id"])
    assert [(e["kind"], e["action"], e["to_state"]) for e in feed] == [
        ("created", None, "new"), ("transition", "assign", "assigned"), ("transition", "ack", "acknowledged"),
        ("transition", "start", "in_progress"), ("transition", "complete", "completed"),
    ]
    assert [e["version"] for e in feed][1:] == [1, 2, 3, 4]


def test_complete_without_ack_is_an_implicit_ack(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    r = state.transition(cur, hotel_uae.slug, row["id"], "complete", STAFF)["row"]
    assert r["ack_implicit"] is True and r["acknowledged_at"] == r["resolved_at"]


def test_illegal_transitions_and_version_conflicts(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    with pytest.raises(ClefsError) as exc:
        state.transition(cur, hotel_uae.slug, row["id"], "start", STAFF)  # assigned: ack first
    assert exc.value.code == "illegal_transition"
    with pytest.raises(state.TransitionConflict) as exc:
        state.transition(cur, hotel_uae.slug, row["id"], "ack", STAFF, expected_version=0)
    assert exc.value.code == "version_conflict" and exc.value.info["version"] == 1
    state.transition(cur, hotel_uae.slug, row["id"], "complete", STAFF)
    with pytest.raises(state.TransitionConflict) as exc:
        state.transition(cur, hotel_uae.slug, row["id"], "complete", STAFF)
    assert exc.value.code == "already_closed" and exc.value.detail == "Already done by Amina"
    assert exc.value.info["by"] == "Amina" and exc.value.info["at"] is not None


def test_client_event_id_replays_without_a_second_change(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    ceid = str(uuid.uuid4())
    first = state.transition(cur, hotel_uae.slug, row["id"], "ack", STAFF, client_event_id=ceid)
    again = state.transition(cur, hotel_uae.slug, row["id"], "ack", STAFF, client_event_id=ceid)
    assert again["replayed"] is True and again["event"]["id"] == first["event"]["id"]
    assert again["row"]["version"] == first["row"]["version"] == 2


def test_cancel_reads_as_completed_to_the_legacy_page(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    r = state.transition(cur, hotel_uae.slug, row["id"], "cancel", MANAGER)["row"]
    assert (r["state"], r["status"], r["resolution_code"]) == ("cancelled", "completed", "cancelled")
    assert r["cancelled_at"] is not None
    with pytest.raises(ClefsError) as exc:
        state.transition(cur, hotel_uae.slug, row["id"], "reopen", STAFF)
    assert exc.value.status == 403
    r = state.transition(cur, hotel_uae.slug, row["id"], "reopen", MANAGER)["row"]
    assert (r["state"], r["status"], r["reopen_count"], r["resolution_code"]) == ("reopened", "progress", 1, None)


def test_reopen_after_completion_rules(cur, hotel_uae, clock):
    clock.freeze(datetime(2026, 3, 1, 10, 0, tzinfo=DUBAI))
    _staff(cur, hotel_uae.slug, "Amina")
    row = _new(cur, hotel_uae.slug)
    state.transition(cur, hotel_uae.slug, row["id"], "complete", STAFF)
    r = state.transition(cur, hotel_uae.slug, row["id"], "confirm", state.GUEST)["row"]
    assert r["state"] == "completed" and r["guest_confirmation"] == "yes"
    clock.advance(hours=25)
    with pytest.raises(ClefsError):
        state.transition(cur, hotel_uae.slug, row["id"], "reopen", STAFF)  # a supervisor only within 24 h
    r = state.transition(cur, hotel_uae.slug, row["id"], "reopen", state.GUEST)["row"]
    assert r["state"] == "reopened" and r["guest_confirmation"] == "no" and r["resolved_at"] is None
    # the reopen restarts both clocks from now
    assert r["ack_due_at"] == r["ladder_started_at"] + 3 * 60_000 and r["ladder_cycle"] == 1


def test_decline_hands_the_job_to_someone_else(cur, hotel_uae):
    amina = _staff(cur, hotel_uae.slug, "Amina")
    bilal = _staff(cur, hotel_uae.slug, "Bilal")
    row = _new(cur, hotel_uae.slug)
    assert row["assigned_staff_id"] == amina
    state.transition(cur, hotel_uae.slug, row["id"], "ack", state.Actor("device", staff_id=amina, role="device"))
    r = state.transition(cur, hotel_uae.slug, row["id"], "decline", state.Actor("device", staff_id=amina, role="device"))["row"]
    assert r["state"] == "assigned" and r["assigned_staff_id"] == bilal and r["assigned_to"] == "Bilal"
    assert r["ladder_cycle"] == 1 and r["escalation_level"] == 0
    # the first ack is kept for "first response" metrics
    assert r["acknowledged_at"] is not None


def test_random_action_sequences_never_leave_an_illegal_legacy_status(cur, hotel_uae):
    """Property test (03 spec 1.9): whatever order actions arrive in, the
    legacy status always matches the state, and illegal ones change nothing."""
    _staff(cur, hotel_uae.slug, "Amina")
    _staff(cur, hotel_uae.slug, "Bilal")
    rng = random.Random(20260929)
    actions = [a for a in state.ACTIONS if a != "release"]
    for _ in range(12):
        row = _new(cur, hotel_uae.slug, room=rng.choice(["101", "102", "201"]), type=rng.choice(["towels", "water", "linen"]))
        for _ in range(10):
            action = rng.choice(actions)
            before = state.load(cur, hotel_uae.slug, row["id"])
            try:
                row = state.transition(cur, hotel_uae.slug, row["id"], action, MANAGER, legacy=rng.random() < 0.3)["row"]
            except ClefsError:
                after = state.load(cur, hotel_uae.slug, row["id"])
                assert after["version"] == before["version"] and after["state"] == before["state"]
                row = after
            assert row["status"] == C.LEGACY_STATUS[row["state"]]
            assert (row["status"] == "completed") == (row["state"] in C.TERMINAL_STATES)


def test_events_feed_filters_by_device_scope(cur, hotel_uae):
    _staff(cur, hotel_uae.slug, "Amina")
    _staff(cur, hotel_uae.slug, "Omar", department="Maintenance")
    a = _new(cur, hotel_uae.slug, room="101")
    b = _new(cur, hotel_uae.slug, room="201", type="plumbing")
    floor1 = state.events_after(cur, hotel_uae.slug, 0, floors=["1"])
    assert {e["request_id"] for e in floor1} == {a["id"]}
    eng = state.events_after(cur, hotel_uae.slug, 0, departments=["engineering"])
    assert {e["request_id"] for e in eng} == {b["id"]}
    cursor = max(e["id"] for e in state.events_after(cur, hotel_uae.slug, 0))
    assert state.events_after(cur, hotel_uae.slug, cursor) == []


# ─── Legacy PATCH adapter ───


def test_legacy_accept_is_assign_plus_ack_and_complete_is_complete(client, cur, legacy_slug):
    h = f"/hotels/{legacy_slug}"
    cur.execute("UPDATE staff_members SET is_online = FALSE WHERE hotel_slug = %s", (legacy_slug,))
    pooja = _staff(cur, legacy_slug, "Legacy Pooja", department="Concierge", online=False)
    created = client.post(f"{h}/requests", json={"room": "305", "type": "Spa Booking", "description": "massage",
                                                  "category": "Concierge", "timestamp": 1}).json()
    rid = created["id"]
    assert set(created) == {"id", "hotel_slug", "room", "type", "subtype", "description", "category", "priority",
                            "status", "timestamp", "assigned_to", "accepted_at", "resolved_at"}
    # The old "Accept": a person typing their own name.
    assert client.patch(f"{h}/requests/{rid}", json={"status": "progress", "assigned_to": "Legacy Pooja"}).json() == {"ok": True}
    row = state.load(cur, legacy_slug, rid)
    assert (row["state"], row["status"], row["assigned_staff_id"]) == ("acknowledged", "progress", pooja)
    assert row["acknowledged_at"] is not None and row["assigned_at"] == row["accepted_at"]
    # A free-text name that is nobody on the roster still works, as it did.
    client.patch(f"{h}/requests/{rid}", json={"status": "progress", "assigned_to": "Night Porter"})
    row = state.load(cur, legacy_slug, rid)
    assert row["assigned_to"] == "Night Porter" and row["assigned_staff_id"] is None
    client.patch(f"{h}/requests/{rid}", json={"status": "new"})
    row = state.load(cur, legacy_slug, rid)
    assert (row["state"], row["status"], row["assigned_to"]) == ("new", "new", None)
    client.patch(f"{h}/requests/{rid}", json={"status": "completed", "resolved_at": 123456})
    row = state.load(cur, legacy_slug, rid)
    assert (row["state"], row["status"], row["resolved_at"]) == ("completed", "completed", 123456)
    kinds = [(e["kind"], e["action"], e["actor_type"]) for e in state.events_after(cur, legacy_slug, 0, request_id=rid)]
    assert ("transition", "complete", "legacy_client") in kinds


def test_legacy_list_defaults_to_seven_days_but_keeps_open_work(client, cur, legacy_slug, clock):
    h = f"/hotels/{legacy_slug}"
    old_ms = int((datetime.now(DUBAI) - timedelta(days=9)).timestamp() * 1000)
    done = client.post(f"{h}/requests", json={"room": "1", "type": "Other", "description": "old", "status": "completed",
                                               "timestamp": old_ms}).json()
    still_open = client.post(f"{h}/requests", json={"room": "2", "type": "Other", "description": "old open",
                                                     "timestamp": old_ms}).json()
    ids = {r["id"] for r in client.get(f"{h}/requests").json()}
    assert done["id"] not in ids and still_open["id"] in ids
    ids = {r["id"] for r in client.get(f"{h}/requests", params={"since": old_ms - 1}).json()}
    assert done["id"] in ids
