"""Per-recipient quiet hours (review completeness-07): escalations and the
scheduled brief wait for the window to end; critical items and "Send now"
do not."""

from __future__ import annotations

from datetime import datetime, time, timezone

from clefs.core import actions as A
from clefs.core import signals as S
from clefs.core.hotels import set_module_settings
from clefs.modules.brief import brief, quiet
from clefs.modules.brief import escalate as E


def _quiet(cur, hotel, *windows):
    s = brief.load_settings(cur, hotel.slug).model_copy(
        update={"quiet_hours": [quiet.QuietWindow(user_id=u, start=a, end=b) for u, a, b in windows]})
    set_module_settings(cur, hotel.slug, "brief", s, actor=None)


def _levels(cur, action_id):
    cur.execute("SELECT level, notified FROM brf_escalations WHERE action_id = %s ORDER BY level", (action_id,))
    return {r["level"]: r["notified"] for r in cur.fetchall()}


# ─── Pure window arithmetic ───


def test_windows_cover_overnight_and_end_on_the_local_wall_clock(hotel_uae):
    assert quiet.in_window("22:00", "07:00", time(23, 30)) and quiet.in_window("22:00", "07:00", time(6, 59))
    assert not quiet.in_window("22:00", "07:00", time(7, 0)) and not quiet.in_window("22:00", "07:00", time(12, 0))
    assert quiet.in_window("13:00", "15:00", time(14, 0)) and not quiet.in_window("09:00", "09:00", time(9, 0))
    w = [{"user_id": 5, "start": "22:00", "end": "07:00"}]
    # 23:30 Dubai (19:30 UTC) -> 07:00 Dubai next day (03:00 UTC)
    now = datetime(2026, 10, 1, 19, 30, tzinfo=timezone.utc)
    assert quiet.quiet_until(hotel_uae, w, 5, now) == datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
    assert quiet.quiet_until(hotel_uae, w, 6, now) is None  # someone else
    assert quiet.quiet_until(hotel_uae, w, 5, datetime(2026, 10, 1, 8, tzinfo=timezone.utc)) is None  # 12:00 local


# ─── Escalations ───


def test_escalation_waits_for_quiet_hours_then_sends(cur, hotel_uae, clock, outbox, act, owner, gm):
    clock.freeze(datetime(2026, 10, 1, 17, 0, tzinfo=timezone.utc))  # 21:00 Dubai
    a = act("Restock minibar floor 2", due_hours=2)  # due 23:00 Dubai
    A.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel_uae.slug)
    A.decide(cur, a["id"], "assign", actor=None, assignee_user_id=gm["id"], hotel_slug=hotel_uae.slug)
    _quiet(cur, hotel_uae, (gm["id"], "22:00", "07:00"))
    clock.advance(hours=2, minutes=5)  # 23:05 Dubai, inside gm's window
    E.run(cur, hotel_uae.slug)
    lv = _levels(cur, a["id"])
    assert lv[1][0]["status"] == "deferred_quiet" and not outbox.captured("email")
    clock.advance(hours=3)  # 02:05: still quiet (level 2 goes to the gm too, also held)
    E.run(cur, hotel_uae.slug)
    assert all(n["status"] == "deferred_quiet" for n in _levels(cur, a["id"])[1])
    clock.freeze(datetime(2026, 10, 2, 3, 10, tzinfo=timezone.utc))  # 07:10 Dubai
    out = E.run(cur, hotel_uae.slug)
    assert out["deferred_sent"] >= 1
    sent = _levels(cur, a["id"])[1][0]
    assert sent["ok"] and sent["channel"] == "email" and datetime.fromisoformat(sent["deferred_until"]) == datetime(2026, 10, 2, 3, tzinfo=timezone.utc)


def test_critical_items_break_through_quiet_hours(cur, hotel_uae, clock, outbox, act, owner, gm):
    clock.freeze(datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc))  # 23:00 Dubai
    s = S.upsert_signal(cur, hotel_uae.slug, S.SignalIn(
        module="service", kind="brief.test_l1", dedupe_key="l1", title="Guest stuck in lift", section="service_risk",
        cadence="daily", severity=4, confidence=1.0))
    a = act("Free the lift", due_hours=0.5, signal_id=s["id"])
    A.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel_uae.slug)
    A.decide(cur, a["id"], "assign", actor=None, assignee_user_id=gm["id"], hotel_slug=hotel_uae.slug)
    _quiet(cur, hotel_uae, (gm["id"], "22:00", "07:00"))
    clock.advance(hours=1)
    E.run(cur, hotel_uae.slug)
    assert _levels(cur, a["id"])[1][0]["ok"] is True


# ─── The scheduled brief ───


def test_scheduled_brief_waits_for_quiet_hours_but_send_now_does_not(cur, hotel_uae, clock, outbox, owner, gm):
    clock.freeze(datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc))  # 07:00 Dubai
    _quiet(cur, hotel_uae, (owner["id"], "22:00", "08:00"))
    run = brief.generate(cur, hotel_uae, "daily", trigger="schedule")
    out = brief.deliver(cur, run, respect_quiet=True)
    by_user = {d["user_id"]: d for d in out["delivery"]}
    assert by_user[owner["id"]]["status"] == "deferred_quiet" and by_user[gm["id"]]["ok"]
    assert out["status"] == "partial"
    assert brief.deliver_deferred(cur, hotel_uae.slug) == 0  # 07:00: still quiet
    clock.advance(hours=1, minutes=5)
    assert brief.deliver_deferred(cur, hotel_uae.slug) == 1
    cur.execute("SELECT status FROM brf_runs WHERE id = %s", (run["id"],))
    assert cur.fetchone()["status"] == "sent"
    clock.freeze(datetime(2026, 10, 1, 19, 0, tzinfo=timezone.utc))  # 23:00: send now ignores the window
    assert brief.push_now(cur, hotel_uae.slug, user_id=owner["id"])["ok"] is True


def test_run_status_uses_the_latest_outcome_per_person():
    assert brief.run_status([{"user_id": 1, "status": "deferred_quiet", "ok": False}]) == "generated"
    assert brief.run_status([{"user_id": 1, "status": "deferred_quiet", "ok": False},
                             {"user_id": 1, "status": "queued", "ok": True}]) == "sent"
    assert brief.run_status([{"user_id": 1, "ok": True}, {"user_id": 2, "ok": False, "status": "failed"}]) == "partial"
    assert brief.run_status([]) == "failed"


def test_settings_route_validates_quiet_hours(client_as, hotel_uae, frozen, make_user):
    owner = client_as("owner")
    base = f"/hotels/{hotel_uae.slug}/brief/settings"
    uid = owner.user["id"]
    r = owner.put(base, json={"quiet_hours": [{"user_id": uid, "start": "22:00", "end": "07:00"}]})
    assert r.status_code == 200, r.text
    assert r.json()["quiet_hours"] == [{"user_id": uid, "start": "22:00", "end": "07:00"}]
    assert owner.put(base, json={"quiet_hours": [{"user_id": 999999, "start": "22:00", "end": "07:00"}]}).status_code == 400
    assert owner.put(base, json={"quiet_hours": [{"user_id": uid, "start": "25:00", "end": "07:00"}]}).status_code == 422
    dup = [{"user_id": uid, "start": "22:00", "end": "07:00"}] * 2
    assert owner.put(base, json={"quiet_hours": dup}).json()["code"] == "invalid_quiet_hours"
    assert owner.put(base, json={"quiet_hours": []}).json()["quiet_hours"] == []
