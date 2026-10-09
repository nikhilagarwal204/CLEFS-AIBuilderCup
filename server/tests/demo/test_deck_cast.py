"""The AI Builder Cup deck cast (05 spec section 4) after a seed: Room 204
on floor 2, Maria on shift round the clock for 14 days with her phone
paired, Priya on floors 5-8, the supervisor, duty manager and GM on the
ladder, the duty manager's login, the shadow week over and the 22 standard
tests queued. Routing then sends the deck's first job, towels for Room 204,
to Maria (05 spec decision 5: the free attendant nearest the room)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from clefs.seed import demo_uae
from clefs.seed.demo_uae import core as cast, deck, ops

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-deck"
PARTS = ["core", "core_catalog", "core_stays", "ops", "concierge_live", "deck"]
OPEN = ["new", "assigned", "acknowledged", "in_progress", "reopened"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")


def _sid(cur, name):
    cur.execute("SELECT id FROM staff_members WHERE hotel_slug = %s AND name = %s", (SLUG, name))
    return cur.fetchone()["id"]


def test_the_cast_is_complete(cur, seeded):
    results = deck.deck_checks(cur, SLUG)
    assert [(name, detail) for name, ok, detail in results if not ok] == []
    assert len(results) == 11
    assert seeded["parts"]["deck"] == {"deck_phones": 1, "shadow_days_done": 7, "guest_room_limit": 100,
                                       "tests_queued": 1}
    links = seeded["credentials"]["demo_links"]
    assert links["room_204"].endswith(f"/stay/{SLUG}/204?k=" + links["room_204"].split("?k=")[1])
    assert links["try"].endswith("/try")


def test_the_fortnight_roster(cur, seeded):
    """From the anchor, the cast works round the clock for DEMO_DAYS more
    days; nobody else covers Housekeeping floors; Omar is the one duty manager."""
    end = ANCHOR + timedelta(days=ops.DEMO_DAYS)
    cur.execute(
        """SELECT s.name, a.shift_role, count(*) AS days, min(a.business_date) AS first, max(a.business_date) AS last
           FROM svc_shift_assignments a JOIN staff_members s ON s.id = a.staff_member_id
           WHERE a.hotel_slug = %s AND a.business_date >= %s AND a.department_key = 'housekeeping'
           GROUP BY 1, 2 ORDER BY 1""", (SLUG, ANCHOR))
    rows = {r["name"]: (r["shift_role"], r["days"], r["first"], r["last"]) for r in cur.fetchall()}
    assert rows == {
        cast.DECK_ATTENDANT: ("attendant", ops.DEMO_DAYS + 1, ANCHOR, end),
        cast.DECK_SECOND_ATTENDANT: ("attendant", ops.DEMO_DAYS + 1, ANCHOR, end),
        cast.DECK_SUPERVISOR: ("supervisor", ops.DEMO_DAYS + 1, ANCHOR, end),
    }
    cur.execute("""SELECT DISTINCT s.name FROM svc_shift_assignments a JOIN staff_members s ON s.id = a.staff_member_id
                   WHERE a.hotel_slug = %s AND a.business_date >= %s AND a.shift_role = 'duty_manager'""", (SLUG, ANCHOR))
    assert [r["name"] for r in cur.fetchall()] == [cast.DECK_DUTY_MANAGER]
    # Before the anchor the history roster keeps its shape (the analytics by shift).
    cur.execute("""SELECT count(DISTINCT s.name) AS n FROM svc_shift_assignments a
                   JOIN staff_members s ON s.id = a.staff_member_id
                   WHERE a.hotel_slug = %s AND a.business_date < %s AND a.department_key = 'housekeeping'""",
                (SLUG, ANCHOR))
    assert cur.fetchone()["n"] == 7


def test_housekeeping_starts_the_fortnight_with_no_open_job(cur, seeded):
    cur.execute("""SELECT count(*) AS n FROM staff_requests WHERE hotel_slug = %s AND state = ANY(%s)
                   AND assigned_staff_id = ANY(%s)""",
                (SLUG, OPEN, [_sid(cur, cast.DECK_ATTENDANT), _sid(cur, cast.DECK_SECOND_ATTENDANT)]))
    assert cur.fetchone()["n"] == 0


def test_room_204_towels_go_to_maria_and_floor_7_to_priya(cur, seeded, clock):
    from clefs.modules.service import api as service_api

    clock.freeze(demo_uae.DemoCtx(cur=None, slug=SLUG, anchor=ANCHOR).now + timedelta(minutes=10))
    towels = service_api.create_request(cur, SLUG, room=cast.DECK_ROOM, type="towels",
                                        description="Can we get extra towels?", source="web", language="en")
    assert towels["assigned_staff_id"] == _sid(cur, cast.DECK_ATTENDANT), towels
    water = service_api.create_request(cur, SLUG, room="704", type="water", description="Two bottles of water please",
                                       source="web", language="en")
    assert water["assigned_staff_id"] == _sid(cur, cast.DECK_SECOND_ATTENDANT), water


def test_the_staff_login_and_the_ladder_people_are_linked(cur, seeded):
    cur.execute("""SELECT s.name, s.role_key, u.email FROM staff_members s JOIN users u ON u.id = s.user_id
                   WHERE s.hotel_slug = %s ORDER BY s.name""", (SLUG,))
    linked = {r["name"]: (r["role_key"], r["email"]) for r in cur.fetchall()}
    assert linked == {
        cast.DECK_DUTY_MANAGER: ("duty_manager", cast.demo_email(cast.DUTY_MANAGER_LOCAL, SLUG)),
        cast.DECK_GM: ("gm", cast.demo_email("gm", SLUG)),
    }
    creds = seeded["credentials"]["users"]
    assert creds[cast.DUTY_MANAGER_ROLE]["name"] == cast.DECK_DUTY_MANAGER


def test_room_204_takes_many_guests_at_once(cur, seeded, clock):
    """Several judges ask from Room 204 during the pitch: the guest-channel
    limit per room (default 5 per 10 minutes) is at its maximum, and the
    seed kept the rest of the hotel's Service settings."""
    from clefs.core.hotels import module_settings_row
    from clefs.modules.service import api as service_api

    assert (module_settings_row(cur, SLUG, "service") or {})["settings"]["guest_room_limit"] == 100
    clock.freeze(demo_uae.DemoCtx(cur=None, slug=SLUG, anchor=ANCHOR).now + timedelta(minutes=10))
    asks = ("towels", "water", "amenities", "pillows", "linen", "extra_bed", "make_up_room", "drinks")
    rows = [service_api.create_request(cur, SLUG, room=cast.DECK_ROOM, type=t, description=f"Please, {t}", source="web",
                                       language="en") for t in asks]
    assert len({r["id"] for r in rows}) == len(asks)

