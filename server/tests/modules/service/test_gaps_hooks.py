"""service.api.on_request_state: subscribers hear every real transition,
inside the transaction, isolated in savepoints, never on a replay."""

from __future__ import annotations

import uuid

import pytest

from clefs.modules.service import api, hooks, state

STAFF = state.Actor("user", user_id=1, role="staff", label="u")


def _staff(cur, slug, name="Amina"):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) "
        "VALUES (%s, %s, 'A', 'Housekeeping', TRUE, TRUE) RETURNING id",
        (slug, name),
    )
    return cur.fetchone()["id"]


@pytest.fixture
def seen():
    got: list[dict] = []

    def fn(cur, change):
        got.append(change)

    api.on_request_state(fn)
    yield got
    hooks.unsubscribe(fn)


def test_subscriber_hears_each_transition_with_the_documented_shape(cur, hotel_uae, seen):
    slug = hotel_uae.slug
    sid = _staff(cur, slug)
    req = api.create_request(cur, slug, room="301", type="towels", description="towels", source="phone_desk")
    # creation itself is not a transition; routing's assignment right after is
    assert [c["action"] for c in seen] == (["assign"] if req["state"] == "assigned" else [])
    if req["state"] == "new":
        state.transition(cur, slug, req["id"], "assign", STAFF, target={"staff_id": sid})
    seen.clear()
    state.transition(cur, slug, req["id"], "ack", STAFF)
    state.transition(cur, slug, req["id"], "complete", STAFF)
    assert [c["to_state"] for c in seen] == ["acknowledged", "completed"]
    last = seen[-1]
    assert last["hotel_slug"] == slug and last["request_id"] == req["id"] and last["action"] == "complete"
    assert last["from_state"] == "acknowledged" and last["status"] == "completed" and last["actor_type"] == "user"
    assert last["request_type_key"] == "towels" and last["room"] == "301" and isinstance(last["event_id"], int)
    assert "description" not in last and "original_text" not in last  # never the guest's words


def test_router_assignment_at_intake_is_a_transition(cur, hotel_uae, seen):
    slug = hotel_uae.slug
    _staff(cur, slug)
    req = api.create_request(cur, slug, room="302", type="towels", description="towels", source="phone_desk")
    if req["state"] == "assigned":
        assert [c["to_state"] for c in seen] == ["assigned"]


def test_replay_notifies_nobody_and_decorator_registration_is_idempotent(cur, hotel_uae, seen):
    slug = hotel_uae.slug
    req = api.create_request(cur, slug, room="303", type="towels", description="towels", source="phone_desk")
    seen.clear()
    cid = str(uuid.uuid4())
    state.transition(cur, slug, req["id"], "cancel", state.SYSTEM, client_event_id=cid)
    state.transition(cur, slug, req["id"], "cancel", state.SYSTEM, client_event_id=cid)  # outbox resend
    assert [c["action"] for c in seen] == ["cancel"]
    n = len(hooks.subscribers())
    fn = hooks.subscribers()[-1]
    assert api.on_request_state(fn) is fn and len(hooks.subscribers()) == n


def test_a_failing_subscriber_never_undoes_the_transition(cur, hotel_uae, seen):
    slug = hotel_uae.slug

    def broken(cur, change):
        cur.execute("SELECT 1 / 0")  # aborts only its own savepoint

    api.on_request_state(broken)
    try:
        req = api.create_request(cur, slug, room="304", type="towels", description="towels", source="phone_desk")
        seen.clear()
        out = state.transition(cur, slug, req["id"], "cancel", state.SYSTEM)
    finally:
        hooks.unsubscribe(broken)
    assert out["row"]["state"] == "cancelled"
    assert [c["to_state"] for c in seen] == ["cancelled"]  # a later subscriber still ran
    cur.execute("SELECT state FROM staff_requests WHERE id = %s", (req["id"],))
    assert cur.fetchone()["state"] == "cancelled"


def test_subscriber_writes_join_the_transaction(cur, hotel_uae):
    slug = hotel_uae.slug

    def mark(cur, change):
        cur.execute("UPDATE staff_requests SET subtype = %s WHERE id = %s", (f"hook:{change['to_state']}", change["request_id"]))

    api.on_request_state(mark)
    try:
        req = api.create_request(cur, slug, room="305", type="towels", description="towels", source="phone_desk")
        state.transition(cur, slug, req["id"], "cancel", state.SYSTEM)
    finally:
        hooks.unsubscribe(mark)
    cur.execute("SELECT subtype FROM staff_requests WHERE id = %s", (req["id"],))
    assert cur.fetchone()["subtype"] == "hook:cancelled"


def test_non_callable_is_refused():
    with pytest.raises(TypeError):
        api.on_request_state("not a function")
