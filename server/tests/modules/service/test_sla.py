"""SLA sweep (03 spec 1.4, 1.9): the ack ladder under a frozen clock,
roster-resolved recipients (including a request that crosses the night
shift boundary), coalesced digests, shadow mode, the resolution breach,
evaluate-on-read, the advisory lock, urgent paging and pre-v2 exclusion."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.service import api, sla, state

DXB = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=DXB)


@pytest.fixture
def wa_on(monkeypatch):
    """A configured platform WhatsApp number and SMS, so escalations reach
    the capture transport."""
    from clefs.settings import reload_settings

    monkeypatch.setenv("WA_PLATFORM_PHONE_NUMBER_ID", "PNID-PLATFORM")
    monkeypatch.setenv("SMS_ENABLED", "true")
    reload_settings()
    yield
    monkeypatch.undo()
    reload_settings()


def _staff(cur, slug, name, department="Housekeeping", *, phone=None, opt_in=True, online=True, role_key=None):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, whatsapp_e164,
                                      whatsapp_opt_in, role_key)
           VALUES (%s, %s, 'A', %s, TRUE, %s, %s, %s, %s) RETURNING id""",
        (slug, name, department, online, phone, opt_in, role_key),
    )
    return cur.fetchone()["id"]


def _roster(cur, slug, staff_id, role, *, start, hours=8, dept=None):
    cur.execute(
        """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at, shift_role, department_key)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (slug, staff_id, start.date(), start, start + timedelta(hours=hours), role, dept),
    )


def _live(cur, slug, mode="live"):
    cur.execute(
        "INSERT INTO hotel_module_settings (hotel_slug, module, enabled, settings) VALUES (%s, 'service', TRUE, %s) "
        "ON CONFLICT (hotel_slug, module) DO UPDATE SET settings = EXCLUDED.settings",
        (slug, f'{{"escalation_mode": "{mode}"}}'),
    )


def _team(cur, slug, make_user, *, start=T0 - timedelta(hours=2)):
    """An attendant, a rostered supervisor and duty manager, and a GM user."""
    team = {
        "attendant": _staff(cur, slug, "Amina", online=True),
        "supervisor": _staff(cur, slug, "Sara Sup", phone="+971500000001"),
        "duty_manager": _staff(cur, slug, "Dan DM", "Front Desk", phone="+971500000002"),
    }
    _roster(cur, slug, team["supervisor"], "supervisor", start=start, dept="housekeeping")
    _roster(cur, slug, team["duty_manager"], "duty_manager", start=start)
    gm = make_user("gm", slug)
    cur.execute("UPDATE users SET phone_e164 = '+971500000003' WHERE id = %s", (gm["id"],))
    team["gm_user"] = gm["id"]
    return team


def _file(cur, slug, room="101", type_="towels", **kw):
    return api.create_request(cur, slug, room=room, type=type_, description=kw.pop("description", "please"),
                              source=kw.pop("source", "phone_desk"), **kw)


def _escalations(cur, request_id):
    cur.execute("SELECT level, target_role, shadow, recipients, cycle FROM svc_escalations WHERE request_id = %s ORDER BY level",
                (request_id,))
    return [dict(r) for r in cur.fetchall()]


def _texts(outbox):
    out = []
    for m in outbox.captured():
        p = m["payload"]
        out.append(str(p.get("text") or p.get("body") or p.get("TextBody") or p.get("template") or p))
    return out


# ─── The ack ladder ───


def test_ladder_steps_fire_once_each_at_3_5_and_10_minutes(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = _file(cur, s)
    assert row["state"] == "assigned" and row["ack_due_at"] == row["timestamp"] + 3 * 60_000
    clock.advance(minutes=2)
    assert sla.sweep(cur, s)["escalated"] == 0
    clock.advance(minutes=1, seconds=30)
    res = sla.sweep(cur, s)
    assert res["escalated"] == 1 and res["ack_breached"] == 1
    esc = _escalations(cur, row["id"])
    assert [(e["level"], e["target_role"]) for e in esc] == [(1, "supervisor")]
    assert esc[0]["recipients"][0]["name"] == "Sara Sup" and esc[0]["shadow"] is True
    assert sla.sweep(cur, s)["escalated"] == 0  # the same step never fires twice
    clock.advance(minutes=2)
    sla.sweep(cur, s)
    clock.advance(minutes=5)
    sla.sweep(cur, s)
    esc = _escalations(cur, row["id"])
    assert [(e["level"], e["target_role"]) for e in esc] == [(1, "supervisor"), (2, "duty_manager"), (3, "gm")]
    assert esc[2]["recipients"][0]["user_id"]
    cur.execute("SELECT escalation_level, sla_ack_breached FROM staff_requests WHERE id = %s", (row["id"],))
    assert dict(cur.fetchone()) == {"escalation_level": 3, "sla_ack_breached": True}


def test_ack_stops_the_ladder_and_closes_open_steps(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = _file(cur, s)
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    state.transition(cur, s, row["id"], "ack", state.Actor("staff", staff_id=row["assigned_staff_id"], label="Amina"))
    clock.advance(minutes=10)
    assert sla.sweep(cur, s)["escalated"] == 0
    esc = _escalations(cur, row["id"])
    assert len(esc) == 1
    cur.execute("SELECT acked_at FROM svc_escalations WHERE request_id = %s", (row["id"],))
    assert cur.fetchone()["acked_at"] is not None


# ─── Shadow mode, live sends, coalescing ───


def test_shadow_mode_records_but_sends_nothing(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    _file(cur, s)
    outbox.reset()
    clock.advance(minutes=11)
    res = sla.sweep(cur, s)
    assert res["shadow"] is True and res["escalated"] == 3 and res["sent"] == 0
    assert outbox.captured() == []
    cur.execute("SELECT COUNT(*) AS n FROM outbound_messages WHERE purpose = 'service.escalation'")
    assert cur.fetchone()["n"] == 0


def test_auto_mode_goes_live_after_the_shadow_days(cur, hotel_uae, clock):
    from clefs.modules.service import settings as svc_settings

    s = hotel_uae.slug
    clock.freeze(T0)
    sla.sweep(cur, s)  # the first sweep starts the shadow period
    assert svc_settings.escalation_live(cur, s) is False
    clock.advance(days=7, minutes=1)
    assert svc_settings.escalation_live(cur, s) is True


def test_live_digest_coalesces_waiting_requests_per_recipient(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    _live(cur, s)
    _file(cur, s, "101", "towels")
    clock.advance(minutes=1)
    _file(cur, s, "102", "linen")
    outbox.reset()
    clock.advance(minutes=3)  # 4 and 3 minutes: both cross the supervisor step
    res = sla.sweep(cur, s)
    assert res["escalated"] == 2 and res["recipients"] == 1 and res["sent"] == 1
    sent = outbox.captured()
    assert len(sent) == 1 and sent[0]["channel"] == "whatsapp"
    text = _texts(outbox)[0]
    assert "2 waiting" in text and "101 Towels" in text and "102 Linen" in text
    cur.execute("SELECT notify_ids FROM svc_escalations WHERE hotel_slug = %s", (s,))
    ids = {tuple(r["notify_ids"]) for r in cur.fetchall()}
    assert len(ids) == 1 and len(next(iter(ids))) == 1  # both steps point at the one digest


def test_supervisor_on_a_live_device_gets_the_device_alert_only(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    team = _team(cur, s, make_user)
    _live(cur, s)
    cur.execute("INSERT INTO devices (hotel_slug, name, kind) VALUES (%s, 'hk tablet', 'floor') RETURNING id", (s,))
    dev = cur.fetchone()["id"]
    cur.execute("INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id) VALUES (%s, %s, %s)",
                (dev, s, team["supervisor"]))
    row = _file(cur, s)
    outbox.reset()
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    assert outbox.captured() == []
    cur.execute("SELECT payload FROM svc_request_events WHERE request_id = %s AND kind = 'escalated'", (row["id"],))
    assert cur.fetchone()["payload"]["to"] == "supervisor"


def test_whatsapp_needs_the_staff_opt_in_else_sms(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    team = _team(cur, s, make_user)
    cur.execute("UPDATE staff_members SET whatsapp_opt_in = FALSE WHERE id = %s", (team["supervisor"],))
    _live(cur, s)
    _file(cur, s)
    outbox.reset()
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    assert [m["channel"] for m in outbox.captured()] == ["sms"]


# ─── Resolution ───


def test_resolution_breach_flags_once_and_tells_the_supervisor(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    _live(cur, s)
    row = _file(cur, s)  # towels: 3 / 15
    state.transition(cur, s, row["id"], "ack", state.Actor("staff", staff_id=row["assigned_staff_id"]))
    outbox.reset()
    clock.advance(minutes=16)
    res = sla.sweep(cur, s)
    assert res["resolve_breached"] == 1 and res["escalated"] == 0
    assert "(overdue)" in _texts(outbox)[0]
    outbox.reset()
    clock.advance(minutes=5)
    assert sla.sweep(cur, s)["resolve_breached"] == 0 and outbox.captured() == []
    cur.execute("SELECT kind, action FROM svc_request_events WHERE request_id = %s AND kind = 'sla_breach'", (row["id"],))
    assert [dict(r) for r in cur.fetchall()] == [{"kind": "sla_breach", "action": "resolve"}]


# ─── Roster at the moment of the step ───


def test_request_at_2358_escalates_to_the_night_supervisor(cur, hotel_uae, clock):
    s = hotel_uae.slug
    late = datetime(2026, 10, 5, 23, 58, tzinfo=DXB)
    clock.freeze(late)
    _staff(cur, s, "Amina", online=True)
    eve, night = _staff(cur, s, "Evening Sup", phone="+971500000011"), _staff(cur, s, "Night Sup", phone="+971500000012")
    _roster(cur, s, eve, "supervisor", start=datetime(2026, 10, 5, 15, 0, tzinfo=DXB), hours=9)  # until 00:00
    _roster(cur, s, night, "supervisor", start=datetime(2026, 10, 6, 0, 0, tzinfo=DXB), hours=8)
    row = _file(cur, s)
    clock.advance(minutes=3, seconds=30)  # 00:01:30, the next business shift
    sla.sweep(cur, s)
    names = [p["name"] for p in _escalations(cur, row["id"])[0]["recipients"]]
    assert names == ["Night Sup"]


def test_no_roster_falls_back_to_the_job_title_online(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s, "Amina", online=True)
    _staff(cur, s, "Titled Sup", phone="+971500000021", role_key="supervisor", online=True)
    _staff(cur, s, "Off Sup", phone="+971500000022", role_key="supervisor", online=False)
    row = _file(cur, s)
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    assert [p["name"] for p in _escalations(cur, row["id"])[0]["recipients"]] == ["Titled Sup"]


# ─── Evaluate-on-read and the lock ───


def test_maybe_sweep_runs_at_most_every_30_seconds(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    assert sla.maybe_sweep(cur, s) is not None
    clock.advance(seconds=20)
    assert sla.maybe_sweep(cur, s) is None
    clock.advance(seconds=15)
    assert sla.maybe_sweep(cur, s) is not None


def test_a_second_sweeper_skips_while_the_lock_is_held(cur, hotel_uae):
    from clefs.db import raw_connection

    other = raw_connection()
    try:
        with other.cursor() as c:
            c.execute("SELECT pg_advisory_lock(hashtext(%s))", (f"sla:{hotel_uae.slug}",))
        assert sla.sweep(cur, hotel_uae.slug) == {"skipped": "locked"}
    finally:
        other.close()


# ─── Urgent path and exclusions ───


def test_emergency_pages_security_duty_manager_and_gm_at_once(cur, hotel_uae, make_user, clock, outbox, wa_on):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    guard = _staff(cur, s, "Sam Security", "Security", phone="+971500000009")
    _roster(cur, s, guard, "attendant", start=T0 - timedelta(hours=1), dept="security")
    _live(cur, s)
    outbox.reset()
    # intake pages at once (05 spec: safety first), no sweep needed
    row = _file(cur, s, "501", "Other", description="there is smoke in the bathroom", source="voice")
    assert row["severity"] == "L1" and row["escalation_level"] == 3
    assert len(outbox.captured()) == 3
    assert sla.page_now(cur, s, row["id"])["escalated"] == 0  # the steps are taken: nobody is paged twice
    assert [(e["level"], e["target_role"]) for e in _escalations(cur, row["id"])] == [
        (1, "security"), (2, "duty_manager"), (3, "gm")]
    assert all(t.startswith("URGENT") or "URGENT" in t for t in _texts(outbox))
    assert not any("bathroom" in t for t in _texts(outbox))  # never the guest's words
    cur.execute("SELECT department_key, floor, payload FROM svc_request_events WHERE request_id = %s AND action = 'security'",
                (row["id"],))
    ev = cur.fetchone()
    assert ev["department_key"] == "security" and ev["floor"] is None and ev["payload"]["alarm"] == "loop"
    assert sla.sweep(cur, s)["escalated"] == 0  # the sweep finds every step taken


def test_sensitive_pages_the_gm_only_after_10_minutes(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = api.escalate_to_human(cur, s, room="102", reason="guest reports a theft", channel="web", sensitive=True,
                                excerpt=None)
    sla.page_now(cur, s, row["request_id"])
    roles = [e["target_role"] for e in _escalations(cur, row["request_id"])]
    assert "duty_manager" in roles and "gm" not in roles
    clock.advance(minutes=10, seconds=5)
    sla.sweep(cur, s)
    assert "gm" in [e["target_role"] for e in _escalations(cur, row["request_id"])]


def test_page_now_ignores_ordinary_requests(cur, hotel_uae, clock):
    clock.freeze(T0)
    row = _file(cur, hotel_uae.slug)
    assert sla.page_now(cur, hotel_uae.slug, row["id"])["skipped"] == "not_urgent"


def test_pre_v2_rows_never_escalate(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    old = int((T0 - timedelta(days=3)).timestamp() * 1000)
    cur.execute(
        """INSERT INTO staff_requests (hotel_slug, room, type, description, category, priority, status, "timestamp")
           VALUES (%s, '101', 'Towel', 'x', 'Housekeeping', 'medium', 'new', %s) RETURNING id, intake_version""",
        (s, old),
    )
    r = cur.fetchone()
    assert r["intake_version"] is None
    assert sla.sweep(cur, s)["escalated"] == 0 and _escalations(cur, r["id"]) == []


def test_reassign_restarts_the_ladder_in_a_new_cycle(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    team = _team(cur, s, make_user)
    other = _staff(cur, s, "Other Attendant", online=True)
    row = _file(cur, s)
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    state.transition(cur, s, row["id"], "reassign", state.SYSTEM, target={"staff_id": other})
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    cycles = [(e["cycle"], e["level"]) for e in _escalations(cur, row["id"])]
    assert len(cycles) == 2 and cycles[0][0] != cycles[1][0]
    assert team  # keeps the roster alive for the second cycle


def test_l1_without_a_safety_case_skips_security(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = _file(cur, s, "103", "ac", description="AC not cooling at all", priority_hint="L1")
    assert row["severity"] == "L1" and row["sensitivity"] == "none"
    sla.page_now(cur, s, row["id"])
    assert [e["target_role"] for e in _escalations(cur, row["id"])] == ["duty_manager", "gm"]
