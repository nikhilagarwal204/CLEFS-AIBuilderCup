"""Escalation ladder: deadline, +4 h, +24 h (Asia/Dubai clock), once each."""

from __future__ import annotations

from datetime import timedelta

from clefs.core import actions as A
from clefs.modules.brief import brief
from clefs.modules.brief import escalate as E


def _approved(cur, hotel_uae, act, gm, *, due_hours=2):
    a = act("Restock minibar floor 2", due_hours=due_hours)
    A.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel_uae.slug)
    return A.decide(cur, a["id"], "assign", actor=None, assignee_user_id=gm["id"], hotel_slug=hotel_uae.slug)


def _levels(cur, action_id):
    cur.execute("SELECT level, notified FROM brf_escalations WHERE action_id = %s ORDER BY level", (action_id,))
    return {r["level"]: r["notified"] for r in cur.fetchall()}


def test_level_for():
    from datetime import datetime, timezone

    due = datetime(2026, 10, 1, 6, tzinfo=timezone.utc)
    assert E.level_for(due, due - timedelta(minutes=1)) == 0
    assert E.level_for(due, due) == 1
    assert E.level_for(due, due + timedelta(hours=4)) == 2
    assert E.level_for(due, due + timedelta(hours=24)) == 3


def test_ladder_fires_each_level_once(cur, hotel_uae, frozen, outbox, act, owner, gm):
    a = _approved(cur, hotel_uae, act, gm)
    assert E.run(cur, hotel_uae.slug)["escalated"] == 0  # not due yet

    frozen.advance(hours=2, minutes=1)
    assert E.run(cur, hotel_uae.slug)["escalated"] == 1
    row = A.get_action(cur, hotel_uae.slug, a["id"])
    assert row["reminders_sent"] == 1 and row["escalation_level"] == 1 and row["status"] == "assigned"
    assert any(e["event"] == "remind" for e in row["events"])
    lv = _levels(cur, a["id"])
    assert list(lv) == [1] and lv[1][0]["to"] == f"u{gm['id']}" and lv[1][0]["channel"] == "email"
    assert E.run(cur, hotel_uae.slug)["escalated"] == 0  # once only

    frozen.advance(hours=4)
    E.run(cur, hotel_uae.slug)
    assert list(_levels(cur, a["id"])) == [1, 2]

    frozen.advance(hours=20)
    E.run(cur, hotel_uae.slug)
    lv = _levels(cur, a["id"])
    assert list(lv) == [1, 2, 3] and lv[3][0]["to"] == f"u{owner['id']}"
    sigs = [s for s in brief.core_signals.list_signals(cur, hotel_uae.slug, modules=["brief"]) if s["kind"] == E.OVERDUE_KIND]
    assert len(sigs) == 1


def test_level_three_pins_the_item_in_the_next_brief(cur, hotel_uae, frozen, outbox, act, owner, gm):
    a = _approved(cur, hotel_uae, act, gm, due_hours=1)
    frozen.advance(hours=26)
    out = E.run(cur, hotel_uae.slug)
    # A worker that was down records every level but notifies once, at the top one
    lv = _levels(cur, a["id"])
    assert list(lv) == [1, 2, 3] and lv[1] == [] and lv[2] == [] and len(lv[3]) == 1
    assert out["levels"] == 3
    run = brief.generate(cur, hotel_uae, "daily", trigger="send_now")
    assert [i["action_id"] for i in run["overdue"]] == [a["id"]]
    assert run["overdue"][0]["title"].startswith("Overdue: ")


def test_done_action_resolves_the_overdue_signal_and_moved_deadline_restarts(cur, hotel_uae, frozen, outbox, act,
                                                                             owner, gm):
    a = _approved(cur, hotel_uae, act, gm, due_hours=1)
    frozen.advance(hours=26)
    E.run(cur, hotel_uae.slug)
    A.mark_done(cur, a["id"], actor=None)
    out = E.run(cur, hotel_uae.slug)
    assert out["resolved"] == 1

    b = _approved(cur, hotel_uae, act, gm, due_hours=1)
    frozen.advance(hours=2)
    E.run(cur, hotel_uae.slug)
    cur.execute("UPDATE actions SET due_at = %s WHERE id = %s", (frozen.now() + timedelta(hours=3), b["id"]))
    E.run(cur, hotel_uae.slug)
    assert A.get_action(cur, hotel_uae.slug, b["id"])["escalation_level"] == 0
    frozen.advance(hours=3, minutes=1)
    E.run(cur, hotel_uae.slug)
    assert list(_levels(cur, b["id"])) == [1]
