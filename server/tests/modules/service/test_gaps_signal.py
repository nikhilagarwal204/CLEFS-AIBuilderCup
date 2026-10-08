"""service.l1_or_ack_breach (03 spec 1.4): raised once per request and ladder
cycle when the ACK ladder first reaches level 3 (the GM)."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from clefs.core import signals as core_signals
from clefs.modules.service import api, sla, state

DXB = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=DXB)


def _staff(cur, slug, name, department="Housekeeping", *, phone=None, online=True):
    cur.execute(
        """INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online, whatsapp_e164, whatsapp_opt_in)
           VALUES (%s, %s, 'A', %s, TRUE, %s, %s, TRUE) RETURNING id""",
        (slug, name, department, online, phone),
    )
    return cur.fetchone()["id"]


def _team(cur, slug, make_user):
    start = T0 - timedelta(hours=2)
    _staff(cur, slug, "Amina")
    for name, role, dept in (("Sara Sup", "supervisor", "housekeeping"), ("Dan DM", "duty_manager", None)):
        sid = _staff(cur, slug, name, phone="+971500000001")
        cur.execute(
            """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at, shift_role,
                   department_key) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (slug, sid, start.date(), start, start + timedelta(hours=8), role, dept),
        )
    gm = make_user("gm", slug)
    cur.execute("UPDATE users SET phone_e164 = '+971500000003' WHERE id = %s", (gm["id"],))


def _signals(cur, slug):
    cur.execute("SELECT dedupe_key, severity, title, summary, subject_id FROM signals WHERE hotel_slug = %s AND kind = %s "
                "ORDER BY id", (slug, "service.l1_or_ack_breach"))
    return [dict(r) for r in cur.fetchall()]


def test_kind_is_registered_for_the_brief():
    from clefs.modules.service import signals as _svc_signals  # noqa: F401  registers the kinds at import

    k = core_signals.get_kind("service.l1_or_ack_breach")
    assert k is not None and k.default_owner_role == "gm"


def test_ack_breach_reaching_the_gm_raises_once_per_cycle(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = api.create_request(cur, s, room="101", type="towels", description="please", source="phone_desk")
    clock.advance(minutes=6)
    sla.sweep(cur, s)
    assert _signals(cur, s) == []  # supervisor and duty manager only so far
    clock.advance(minutes=5)
    sla.sweep(cur, s)
    sla.sweep(cur, s)  # a second sweep never raises it again
    got = _signals(cur, s)
    assert len(got) == 1
    sig = got[0]
    assert sig["dedupe_key"] == f"service.l1_or_ack_breach:{row['id']}:{row['ladder_cycle'] or 0}"
    assert sig["severity"] == 3 and sig["subject_id"] == str(row["id"])
    assert "room 101" in sig["title"] and "not acknowledged" in sig["title"] and "please" not in sig["title"]
    assert sig["summary"].startswith("Shadow mode")
    # a reassignment restarts the ladder in a new cycle: reaching the GM again is a new signal
    state.transition(cur, s, row["id"], "reassign", state.SYSTEM)
    clock.advance(minutes=11)
    sla.sweep(cur, s)
    assert len(_signals(cur, s)) == 2


def test_l1_reaches_the_gm_at_once(cur, hotel_uae, make_user, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _team(cur, s, make_user)
    row = api.create_request(cur, s, room="205", type="maintenance", description="no power", source="phone_desk",
                             priority_hint="L1")
    sla.page_now(cur, s, row["id"])
    sla.sweep(cur, s)
    got = _signals(cur, s)
    assert len(got) == 1 and got[0]["severity"] == 4 and "L1" in got[0]["title"]
