"""Automatic escalation as the 05 spec shows it: no acceptance in 3, 5 and
10 minutes reaches the supervisor, the duty manager and the GM; the job also
moves to the next free person at the supervisor step without restarting the
ladder; request rows name the step reached; an emergency pages for real even
in shadow mode; demo hotels can move a job's clock; and a completed job the
guest never answered closes after 30 minutes."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from clefs.core import hotels
from clefs.modules.service import api, sla, state
from clefs.modules.service import constants as C
from clefs.modules.service.router import card
from tests.modules.service.desk_helpers import events, roster, staff

DXB = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=DXB)


def _ladder_team(cur, slug, make_user):
    sup = staff(cur, slug, "Sara Sup", phone="+971500000001")
    roster(cur, slug, sup, role="supervisor", hours_before=2)
    dm = staff(cur, slug, "Dan DM", "Front Desk", phone="+971500000002")
    roster(cur, slug, dm, role="duty_manager", dept=None, hours_before=2)
    gm = make_user("gm", slug)
    cur.execute("UPDATE users SET phone_e164 = '+971500000003' WHERE id = %s", (gm["id"],))
    return sup, dm


def _escalations(cur, request_id):
    cur.execute("SELECT level, target_role, cycle, shadow FROM svc_escalations WHERE request_id = %s ORDER BY level",
                (request_id,))
    return [dict(r) for r in cur.fetchall()]


def test_defaults_are_supervisor_3_duty_manager_5_gm_10():
    assert [(s["after_min"], s["to"]) for s in C.DEFAULT_LADDER] == [
        (0, "assignee"), (3, "supervisor"), (5, "duty_manager"), (10, "gm")]


def test_unaccepted_job_moves_to_the_next_free_person_and_keeps_the_clock(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _ladder_team(cur, s, make_user)
    maria, priya = staff(cur, s, "Maria Santos"), staff(cur, s, "Priya Sharma")
    roster(cur, s, maria, floors=("1", "2"))
    roster(cur, s, priya, floors=("5",))
    row = api.create_job(cur, s, room="204", request_type="towels", quantity=2, description_en="2 towels",
                         original_text=None, guest_language="en")
    assert row["staff_id"] == maria
    clock.advance(minutes=3, seconds=10)
    res = sla.sweep(cur, s)
    assert res["escalated"] == 1 and res["reassigned"] == 1
    moved = state.load(cur, s, row["request_id"])
    assert moved["assigned_staff_id"] == priya and moved["state"] == "assigned"
    # the ladder keeps counting from the guest's request: same cycle, level 1
    assert moved["ladder_cycle"] == 0 and moved["escalation_level"] == 1
    assert moved["eta_minutes"] == 5 + 3  # Priya's floors are three away
    ev = [e for e in events(cur, row["request_id"], "transition") if e["action"] == "reassign"][0]
    assert ev["payload"]["auto"] is True and ev["payload"]["from_staff_id"] == maria
    assert card(moved)["escalation_level"] == "supervisor" and card(moved)["escalation_step"] == 1
    clock.advance(minutes=2)  # 5 minutes after the request, not 5 after the hand-over
    sla.sweep(cur, s)
    assert [e["target_role"] for e in _escalations(cur, row["request_id"])] == ["supervisor", "duty_manager"]
    assert card(state.load(cur, s, row["request_id"]))["escalation_level"] == "duty_manager"
    clock.advance(minutes=5)
    res = sla.sweep(cur, s)
    assert res["reassigned"] == 0  # only the supervisor step moves a job
    assert card(state.load(cur, s, row["request_id"]))["escalation_level"] == "gm"


def test_no_other_free_person_keeps_the_holder(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _ladder_team(cur, s, make_user)
    maria = staff(cur, s, "Maria Santos")
    roster(cur, s, maria, floors=("2",))
    row = api.create_request(cur, s, room="204", type="towels", description="towels", source="phone_desk")
    clock.advance(minutes=4)
    assert sla.sweep(cur, s)["reassigned"] == 0
    assert state.load(cur, s, row["id"])["assigned_staff_id"] == maria


def test_an_accepted_job_never_moves(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _ladder_team(cur, s, make_user)
    maria, priya = staff(cur, s, "Maria Santos"), staff(cur, s, "Priya Sharma")
    roster(cur, s, maria, floors=("2",))
    roster(cur, s, priya, floors=("2",))
    row = api.create_request(cur, s, room="204", type="towels", description="towels", source="phone_desk")
    holder = row["assigned_staff_id"]
    state.transition(cur, s, row["id"], "ack", state.Actor("staff", staff_id=holder))
    clock.advance(minutes=12)
    res = sla.sweep(cur, s)
    assert res["escalated"] == 0 and res["reassigned"] == 0
    after = state.load(cur, s, row["id"])
    assert after["assigned_staff_id"] == holder and card(after)["accepted_at"] == after["acknowledged_at"]
    assert card(after)["escalation_level"] is None


def test_a_job_nobody_could_take_goes_to_someone_who_came_on_shift(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    row = api.create_request(cur, s, room="204", type="towels", description="towels", source="phone_desk")
    assert row["state"] == "new"  # the hotel has nobody at all yet
    late = staff(cur, s, "Late Starter")
    roster(cur, s, late, floors=("2",))
    clock.advance(minutes=3, seconds=30)
    assert sla.sweep(cur, s)["reassigned"] == 1
    after = state.load(cur, s, row["id"])
    assert after["assigned_staff_id"] == late and after["ladder_cycle"] == 0


def test_emergency_pages_at_intake_even_in_shadow_mode(cur, hotel_uae, make_user, clock, outbox, monkeypatch):
    from clefs.settings import reload_settings

    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", "PNID-PLATFORM")
    monkeypatch.setenv("SMS_ENABLED", "true")
    reload_settings()
    try:
        s = hotel_uae.slug
        clock.freeze(T0)
        _ladder_team(cur, s, make_user)
        cur.execute("INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) "
                    "VALUES (%s, 'service', TRUE, '{\"escalation_mode\": \"shadow\"}') "
                    "ON CONFLICT (hotel_slug, module) DO UPDATE SET settings = EXCLUDED.settings", (s,))
        outbox.reset()
        fire = api.create_request(cur, s, room="501", type="Other", description="there is smoke in the bathroom",
                                  source="voice")
        assert fire["sensitivity"] == "emergency"
        esc = _escalations(cur, fire["id"])
        assert {e["target_role"] for e in esc} >= {"duty_manager", "gm"} and not any(e["shadow"] for e in esc)
        assert len(outbox.captured()) >= 2  # the duty manager and the GM, for real
        outbox.reset()
        towels = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
        clock.advance(minutes=11)
        sla.sweep(cur, s)
        assert all(e["shadow"] for e in _escalations(cur, towels["id"])) and outbox.captured() == []
    finally:
        monkeypatch.undo()
        reload_settings()


# ─── Closure ───


def test_a_completed_job_with_no_answer_closes_after_30_minutes(cur, hotel_uae, client, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    staff(cur, s, "Amina")
    cur.execute("UPDATE staff_members SET is_online = TRUE WHERE hotel_slug = %s", (s,))
    quiet = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    happy = api.create_request(cur, s, room="102", type="water", description="water", source="web")
    for r in (quiet, happy):
        state.transition(cur, s, r["id"], "complete", state.SYSTEM)
    assert client.get(f"/public/r/{quiet['tracking_token']}").json()["needs_guest_confirmation"] is True
    yes = client.post(f"/public/r/{happy['tracking_token']}/feedback", json={"fixed": True})
    assert yes.status_code == 200, yes.text
    done = state.load(cur, s, happy["id"])
    assert (done["closure_reason"], done["guest_confirmation"]) == ("guest_confirmed", "yes")
    assert done["closed_at"] == done["guest_confirmed_at"]
    clock.advance(minutes=29)
    assert sla.sweep(cur, s)["closed"] == 0
    clock.advance(minutes=2)
    assert sla.sweep(cur, s)["closed"] == 1
    closed = state.load(cur, s, quiet["id"])
    assert closed["closure_reason"] == "no_reply" and closed["closed_at"] == int(clock.now().timestamp() * 1000)
    assert card(closed)["awaiting_guest"] is False and events(cur, quiet["id"], "closed")
    view = client.get(f"/public/r/{quiet['tracking_token']}").json()
    assert view["needs_guest_confirmation"] is False and view["can_confirm"] is True
    # the guest may still say it is not fixed: the job reopens and closure clears
    no = client.post(f"/public/r/{quiet['tracking_token']}/feedback", json={"fixed": False})
    assert no.status_code == 200, no.text
    again = state.load(cur, s, quiet["id"])
    assert again["closed_at"] is None and again["closure_reason"] is None and again["state"] == "assigned"
    assert again["eta_minutes"] == 5  # routed again: a fresh promise, not the old one
    reopened = state.transition(cur, s, happy["id"], "reopen", state.SYSTEM)["row"]
    assert reopened["eta_minutes"] is None and reopened["closed_at"] is None  # a manager's reopen: no promise yet


def test_cancel_closes_the_job(cur, hotel_uae):
    s = hotel_uae.slug
    row = api.create_request(cur, s, room="101", type="towels", description="towels", source="phone_desk")
    out = state.transition(cur, s, row["id"], "cancel", state.SYSTEM)["row"]
    assert out["closure_reason"] == "cancelled" and out["closed_at"] == out["cancelled_at"]


# ─── Demo hotels: move a job's clock ───


def test_demo_advance_walks_the_ladder_on_demo_hotels_only(client_as, cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    _ladder_team(cur, s, make_user)
    row = api.create_request(cur, s, room="204", type="towels", description="towels", source="phone_desk")
    manager = client_as("dept_head")
    url = f"/hotels/{s}/service/requests/{row['id']}/demo-advance"
    assert manager.post(url, json={"minutes": 4}).status_code == 404  # a pilot hotel
    cur.execute("UPDATE hotels SET status = 'demo' WHERE slug = %s", (s,))
    hotels.clear_cache()
    assert client_as("staff").post(url, json={"minutes": 4}).status_code == 403
    r = manager.post(url, json={"minutes": 4})
    assert r.status_code == 200, r.text
    assert r.json()["request"]["escalation_level"] == "supervisor" and r.json()["sweep"]["escalated"] == 1
    r = manager.post(url, json={"minutes": 2})
    assert r.json()["request"]["escalation_level"] == "duty_manager"
    r = manager.post(url, json={"minutes": 5})
    assert r.json()["request"]["escalation_level"] == "gm"
    assert [e["target_role"] for e in _escalations(cur, row["id"])] == ["supervisor", "duty_manager", "gm"]
    assert manager.post(url, json={"minutes": 0}).status_code == 422
    cur.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'service.request.demo_advance'")
    assert cur.fetchone()["n"] == 3
    assert timedelta(minutes=11) == timedelta(milliseconds=row["timestamp"] - state.load(cur, s, row["id"])["timestamp"])
