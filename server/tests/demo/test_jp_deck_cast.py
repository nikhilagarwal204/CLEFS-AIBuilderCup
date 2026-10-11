"""The AI Builder Cup deck cast at the Tokyo demo hotel: Room 204 on floor 2,
Maria (Housekeeping, floors 1-4, reads Tagalog) on shift round the clock for
14 days with her phone paired, Priya on floors 5-8, Hana Suzuki
(supervisor), Yuki Sato (duty manager, the console login) and Kenji Tanaka
(GM) on the ladder, the shadow week over and the 22 standard tests queued.
Routing sends Room 204's towels to Maria and floor 7's water to Priya."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from clefs.seed import demo_jp
from clefs.seed.demo_jp import core as cast, deck
from clefs.seed.demo_uae import DemoCtx, ops

ANCHOR = date(2026, 10, 21)
SLUG = "demo-t-jp-deck"
PARTS = ["core", "core_catalog", "core_stays", "ops", "concierge_live", "deck"]
OPEN = ["new", "assigned", "acknowledged", "in_progress", "reopened"]


@pytest.fixture
def seeded(cur):
    return demo_jp.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.05, password="demo-pass-123")


def _sid(cur, name):
    cur.execute("SELECT id FROM staff_members WHERE hotel_slug = %s AND name = %s", (SLUG, name))
    return cur.fetchone()["id"]


def _evening():
    return DemoCtx(cur=None, slug=SLUG, anchor=ANCHOR, tz=demo_jp.TZ).now


def test_the_cast_is_complete(cur, seeded):
    results = deck.deck_checks(cur, SLUG)
    assert [(name, detail) for name, ok, detail in results if not ok] == []
    assert len(results) == 11
    assert results[0][0] == "assistant Clefs in all 11 guest languages"
    assert seeded["parts"]["deck"] == {"deck_phones": 1, "shadow_days_done": 7, "guest_room_limit": 100,
                                       "heat_threshold_c": 35.0, "tests_queued": 1}
    links = seeded["credentials"]["demo_links"]
    assert f"/stay/{SLUG}/204?k=" in links["room_204"] and links["try"].endswith("/try")
    # The names the deck and the landing page use.
    assert (cast.DECK_ATTENDANT, cast.DECK_SECOND_ATTENDANT, cast.DECK_SUPERVISOR, cast.DECK_DUTY_MANAGER,
            cast.DECK_GM) == ("Maria Santos", "Priya Sharma", "Hana Suzuki", "Yuki Sato", "Kenji Tanaka")
    assert cast.DECK_ATTENDANT_PIN == "2046"


def test_the_fortnight_roster(cur, seeded):
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
                   WHERE a.hotel_slug = %s AND a.business_date >= %s AND a.shift_role = 'duty_manager'""",
                (SLUG, ANCHOR))
    assert [r["name"] for r in cur.fetchall()] == [cast.DECK_DUTY_MANAGER]
    # Security and Concierge keep their own shifts through the fortnight.
    cur.execute("""SELECT DISTINCT department_key FROM svc_shift_assignments WHERE hotel_slug = %s
                   AND business_date >= %s""", (SLUG, ANCHOR))
    assert {r["department_key"] for r in cur.fetchall()} >= {"security", "concierge", "engineering", "ird"}


def test_housekeeping_starts_the_fortnight_with_no_open_job(cur, seeded):
    cur.execute("""SELECT count(*) AS n FROM staff_requests WHERE hotel_slug = %s AND state = ANY(%s)
                   AND assigned_staff_id = ANY(%s)""",
                (SLUG, OPEN, [_sid(cur, cast.DECK_ATTENDANT), _sid(cur, cast.DECK_SECOND_ATTENDANT)]))
    assert cur.fetchone()["n"] == 0


def test_room_204_towels_go_to_maria_and_floor_7_to_priya(cur, seeded, clock):
    from clefs.modules.service import api as service_api

    clock.freeze(_evening() + timedelta(minutes=10))
    towels = service_api.create_request(cur, SLUG, room=cast.DECK_ROOM, type="towels",
                                        description="Can we get extra towels?", source="web", language="en")
    assert towels["assigned_staff_id"] == _sid(cur, cast.DECK_ATTENDANT), towels
    water = service_api.create_request(cur, SLUG, room="704", type="water", description="Two bottles of water please",
                                       source="web", language="en")
    assert water["assigned_staff_id"] == _sid(cur, cast.DECK_SECOND_ATTENDANT), water


def test_the_duty_manager_and_gm_are_logins_too(cur, seeded):
    cur.execute("""SELECT s.name, s.role_key, u.email FROM staff_members s JOIN users u ON u.id = s.user_id
                   WHERE s.hotel_slug = %s ORDER BY s.name""", (SLUG,))
    linked = {r["name"]: (r["role_key"], r["email"]) for r in cur.fetchall()}
    assert linked == {
        cast.DECK_DUTY_MANAGER: ("duty_manager", cast.demo_email(cast.DUTY_MANAGER_LOCAL, SLUG)),
        cast.DECK_GM: ("gm", cast.demo_email("gm", SLUG)),
    }
    assert seeded["credentials"]["users"][cast.DUTY_MANAGER_ROLE]["name"] == "Yuki Sato"


def test_service_settings_for_tokyo(cur, seeded, clock):
    """Room 204 takes many guests at once; an AC request is urgent above
    35 C; the emergency card names the front desk's extension."""
    from clefs.core.hotels import module_settings_row
    from clefs.modules.service import api as service_api

    settings = (module_settings_row(cur, SLUG, "service") or {})["settings"]
    assert (settings["guest_room_limit"], settings["heat_threshold_c"], settings["security_extension"]) == (100, 35.0,
                                                                                                           "9")
    clock.freeze(_evening() + timedelta(minutes=10))
    asks = ("towels", "water", "amenities", "pillows", "linen", "extra_bed", "make_up_room", "drinks")
    rows = [service_api.create_request(cur, SLUG, room=cast.DECK_ROOM, type=t, description=f"Please, {t}", source="web",
                                       language="en") for t in asks]
    assert len({r["id"] for r in rows}) == len(asks)
