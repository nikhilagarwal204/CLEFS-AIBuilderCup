"""Roster, shift templates and labour guardrails (03 spec 1.6): UAE template
seeds, week save/replace, the rules at their exact boundaries (15 Jun
12:30, Ramadan dates, a seventh day across a week edge), the midday-ban
block and its audited override, and who is on shift."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from clefs.modules.service import config_api, labour, roster

DXB = ZoneInfo("Asia/Dubai")


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _staff(cur, slug, name, *, outdoor=False, role_key=None, department="Housekeeping"):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_outdoor_role, role_key)
           VALUES (%s, %s, 'A', %s, TRUE, %s, %s) RETURNING id""",
        (slug, name, department, outdoor, role_key),
    )
    return cur.fetchone()["id"]


def _a(sid, d, start, end, *, brk=0, outdoor=False, ref=None, reason=None):
    s = datetime.combine(d, time.fromisoformat(start), tzinfo=DXB)
    e = datetime.combine(d, time.fromisoformat(end), tzinfo=DXB)
    if e <= s:
        e += timedelta(days=1)
    return {"ref": ref or f"{sid}:{d}", "staff_member_id": sid, "business_date": d, "start_at": s, "end_at": e,
            "break_min": brk, "is_outdoor": outdoor, "override_reason": reason}


# ─── Pure rules ───


def test_midday_ban_boundaries():
    rules = labour.LabourRules()
    # 15 Jun is inside the season (inclusive); a shift ending at exactly 12:30 does not overlap
    ok = labour.check([_a(1, date(2026, 6, 15), "06:00", "12:30", outdoor=True)], rules, tz=DXB)
    assert ok == []
    hit = labour.check([_a(1, date(2026, 6, 15), "06:00", "12:31", outdoor=True)], rules, tz=DXB)
    assert [f["code"] for f in hit] == ["midday_ban"] and hit[0]["level"] == "blocked"
    # 14 Jun and 16 Sep are outside; 15 Sep is inside
    assert labour.check([_a(1, date(2026, 6, 14), "12:00", "16:00", outdoor=True)], rules, tz=DXB) == []
    assert labour.check([_a(1, date(2026, 9, 16), "12:00", "16:00", outdoor=True)], rules, tz=DXB) == []
    assert labour.check([_a(1, date(2026, 9, 15), "14:59", "18:00", outdoor=True)], rules, tz=DXB)
    # a 15:00 start is clear (half-open window); indoor roles are never flagged
    assert labour.check([_a(1, date(2026, 7, 1), "15:00", "23:00", outdoor=True)], rules, tz=DXB) == []
    assert labour.check([_a(1, date(2026, 7, 1), "12:00", "16:00")], rules, tz=DXB) == []
    # a night shift running into the next midday is caught on its second day
    night = labour.check([_a(1, date(2026, 7, 1), "23:00", "13:00", outdoor=True)], rules, tz=DXB)
    ban = [f for f in night if f["code"] == "midday_ban"]
    assert ban[0]["business_date"] == "2026-07-02" and {f["code"] for f in night} == {"midday_ban", "daily_hours"}
    over = labour.check([_a(1, date(2026, 7, 1), "12:00", "16:00", outdoor=True, reason="heat shelter duty")],
                        rules, tz=DXB)
    assert over[0]["level"] == "overridden" and labour.blocking(over) == []


def test_daily_and_ramadan_hours():
    rules = labour.LabourRules(ramadan_start=date(2027, 2, 8), ramadan_end=date(2027, 3, 9))
    # 07-15 with a 30 min break = 7.5 paid hours: fine normally
    assert labour.check([_a(1, date(2027, 2, 7), "07:00", "15:00", brk=30)], rules, tz=DXB) == []
    # the first Ramadan day allows 6 hours
    flags = labour.check([_a(1, date(2027, 2, 8), "07:00", "15:00", brk=30)], rules, tz=DXB)
    assert [(f["code"], f["limit"], f["value"]) for f in flags] == [("ramadan_hours", 6, 7.5)]
    assert labour.check([_a(1, date(2027, 3, 9), "07:00", "13:00")], rules, tz=DXB) == []
    assert labour.check([_a(1, date(2027, 3, 10), "07:00", "15:00")], rules, tz=DXB) == []
    # two shifts on one day add up
    two = labour.check([_a(1, date(2027, 4, 1), "07:00", "12:00", ref="a"), _a(1, date(2027, 4, 1), "17:00", "21:00", ref="b")],
                       rules, tz=DXB)
    assert two[0]["code"] == "daily_hours" and two[0]["refs"] == ["a", "b"]


def test_weekly_hours_and_consecutive_days_across_week_edge():
    rules = labour.LabourRules()
    monday = date(2026, 10, 5)
    week = [_a(1, monday + timedelta(days=i), "07:00", "15:00") for i in range(7)]  # 7 x 8 = 56 h
    flags = labour.check(week, rules, tz=DXB)
    codes = {f["code"] for f in flags}
    assert codes == {"weekly_hours", "consecutive_days"}
    weekly = next(f for f in flags if f["code"] == "weekly_hours")
    assert weekly["value"] == 56 and weekly["business_date"] == (monday + timedelta(days=6)).isoformat()
    # Fri-Sun then Mon-Thu: the 7th day in a row is found across the week edge
    run = [_a(2, date(2026, 10, 2) + timedelta(days=i), "07:00", "12:00") for i in range(7)]
    focus = (date(2026, 10, 5), date(2026, 10, 11))
    cons = [f for f in labour.check(run, rules, tz=DXB, focus=focus) if f["code"] == "consecutive_days"]
    assert cons and cons[0]["business_date"] == "2026-10-08"


# ─── HTTP ───


def test_templates_seed_uae_defaults(client_as, hotel_uae):
    gm = client_as("gm")
    t = gm.get(f"/hotels/{hotel_uae.slug}/service/shift-templates").json()
    by = {x["key"]: x for x in t}
    assert set(by) == {"morning", "evening", "night", "hk_day", "hk_eve"}
    assert (by["night"]["start_time"], by["night"]["end_time"], by["night"]["crosses_midnight"]) == ("23:00", "07:00", True)
    assert by["hk_eve"]["end_time"] == "00:00" and by["hk_eve"]["crosses_midnight"] is True
    r = gm.put(f"/hotels/{hotel_uae.slug}/service/shift-templates",
               json={"templates": [{"key": "morning", "name": "Early", "start_time": "06:00", "end_time": "14:00", "break_min": 45}]})
    assert r.status_code == 200
    assert next(x for x in r.json() if x["key"] == "morning")["name"] == "Early"
    assert client_as("staff").put(f"/hotels/{hotel_uae.slug}/service/shift-templates", json={"templates": []}).status_code == 403


def test_roster_week_save_replace_and_flags(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    amina = _staff(cur, s, "Amina")
    gm = client_as("gm")
    monday = date(2026, 10, 5)
    cells = [{"key": f"c{i}", "staff_member_id": amina, "business_date": (monday + timedelta(days=i)).isoformat(),
              "template_key": "morning", "floors": ["5"]} for i in range(6)]
    cells.append({"key": "c6", "staff_member_id": amina, "business_date": (monday + timedelta(days=6)).isoformat(),
                  "start_time": "07:00", "end_time": "19:00", "break_min": 60, "shift_role": "supervisor"})
    v = gm.post(f"/hotels/{s}/service/roster/validate", json={"week": "2026-10-07", "assignments": cells})
    assert v.status_code == 200 and v.json()["blocked"] is False
    assert {f["code"] for f in v.json()["flags"]} == {"daily_hours", "weekly_hours", "consecutive_days"}
    r = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-10-07", "assignments": cells})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["week_start"] == "2026-10-05" and len(body["assignments"]) == 7
    assert body["caption"] == "Guidance, not legal advice"
    sun = next(a for a in body["assignments"] if a["business_date"] == "2026-10-11")
    assert sun["shift_role"] == "supervisor" and sun["start_at"].startswith("2026-10-11T07:00:00+04:00")
    assert {f["code"] for f in sun["labour_flags"]} >= {"daily_hours", "consecutive_days"}
    # a night template ends the next local morning
    ids = [a["id"] for a in body["assignments"]]
    keep = [{"id": ids[0], "staff_member_id": amina, "business_date": "2026-10-05", "template_key": "night"}]
    r = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-10-05", "assignments": keep})
    got = gm.get(f"/hotels/{s}/service/roster", params={"week": "2026-10-09"}).json()
    assert [a["id"] for a in got["assignments"]] == [ids[0]]
    assert got["assignments"][0]["end_at"].startswith("2026-10-06T07:00:00+04:00")
    cur.execute("SELECT count(*) AS n FROM svc_shift_assignments WHERE hotel_slug = %s AND status = 'cancelled'", (s,))
    assert cur.fetchone()["n"] == 6
    bad = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-10-05", "assignments": [
        {"staff_member_id": amina, "business_date": "2026-10-20", "template_key": "morning"}]})
    assert bad.status_code == 400 and bad.json()["code"] == "outside_week"


def test_midday_ban_blocks_save_until_reason_given(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    ravi = _staff(cur, s, "Ravi", outdoor=True, department="Engineering")
    gm = client_as("gm")
    cell = {"staff_member_id": ravi, "business_date": "2026-07-01", "start_time": "10:00", "end_time": "16:00"}
    r = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-07-01", "assignments": [cell]})
    assert r.status_code == 409 and r.json()["code"] == "labour_blocked"
    assert r.json()["flags"][0]["code"] == "midday_ban"
    cur.execute("SELECT count(*) AS n FROM svc_shift_assignments WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 0
    cell["override_reason"] = "Pool pump failure, shaded plant room"
    r = gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-07-01", "assignments": [cell]})
    assert r.status_code == 200, r.text
    assert r.json()["assignments"][0]["override_reason"] == "Pool pump failure, shaded plant room"
    cur.execute("SELECT after FROM audit_log WHERE action = 'service.roster.labour_override' AND hotel_slug = %s", (s,))
    assert cur.fetchone()["after"]["reason"] == "Pool pump failure, shaded plant room"


def test_on_shift_and_on_duty(client_as, cur, hotel_uae, clock):
    s = hotel_uae.slug
    dm = _staff(cur, s, "Dana")
    gm = client_as("gm")
    gm.put(f"/hotels/{s}/service/roster", json={"week": "2026-10-05", "assignments": [
        {"staff_member_id": dm, "business_date": "2026-10-05", "template_key": "night", "shift_role": "duty_manager"}]})
    clock.freeze(datetime(2026, 10, 6, 2, 0, tzinfo=DXB))
    assert [p["name"] for p in roster.on_duty(cur, s, role="duty_manager", at=datetime(2026, 10, 6, 2, 0, tzinfo=DXB))] == ["Dana"]
    assert roster.on_duty(cur, s, role="duty_manager", at=datetime(2026, 10, 6, 7, 0, tzinfo=DXB)) == []
    assert roster.shift_role_of(cur, s, dm) == "duty_manager"
    rules = gm.put(f"/hotels/{s}/service/labour-rules", json={"ramadan_start": "2027-02-08", "ramadan_end": "2027-03-09"})
    assert rules.status_code == 200 and rules.json()["ramadan_reduction_h"] == 2
    assert gm.get(f"/hotels/{s}/service/labour-rules").json()["ramadan_start"] == "2027-02-08"


def test_shift_role_capped_at_callers_authority(client_as, cur, hotel_uae):
    """Review security-front-desk-authority-escalation: a duty-manager shift
    lifts a device user's gesture authority to room nights and upgrades, so
    service.manage holders below the GM may not grant one (a dept head may
    roster a supervisor, the front desk only attendants)."""
    s = hotel_uae.slug
    kim = _staff(cur, s, "Kim")
    week = {"week": "2026-10-05"}

    def cell(role, **kw):
        return {"staff_member_id": kim, "business_date": "2026-10-05", "template_key": "morning", "shift_role": role, **kw}

    fd, dh, gm = client_as("front_desk"), client_as("dept_head"), client_as("gm")
    r = fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [cell("duty_manager")]})
    assert r.status_code == 403 and r.json()["code"] == "shift_role_forbidden"
    assert fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [cell("supervisor")]}).status_code == 403
    assert dh.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [cell("duty_manager")]}).status_code == 403
    cur.execute("SELECT count(*) AS n FROM svc_shift_assignments WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["n"] == 0
    r = dh.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [cell("supervisor")]})
    assert r.status_code == 200, r.text
    # the GM grants a duty-manager shift; the front desk may re-save that week untouched...
    r = gm.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [cell("duty_manager")]})
    assert r.status_code == 200, r.text
    dm = r.json()["assignments"][0]
    same = cell("duty_manager", id=dm["id"], start_at=dm["start_at"], end_at=dm["end_at"])
    other = _staff(cur, s, "Lee")
    add = {"staff_member_id": other, "business_date": "2026-10-06", "template_key": "morning"}
    assert fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [same, add]}).status_code == 200
    # ...but not move it to someone else or stretch it
    moved = {**same, "staff_member_id": other}
    assert fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [moved]}).status_code == 403
    longer = {**same, "start_at": None, "end_at": None, "start_time": "07:00", "end_time": "23:00"}
    assert fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [longer]}).status_code == 403
    # demoting (or dropping) an elevated shift only takes authority away
    r = fd.put(f"/hotels/{s}/service/roster", json={**week, "assignments": [{**same, "shift_role": "attendant"}]})
    assert r.status_code == 200 and r.json()["assignments"][0]["shift_role"] == "attendant"
    assert roster.assignable_rank(None) == 3
