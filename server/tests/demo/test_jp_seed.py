"""The Tokyo demo hotel (clefs.seed.demo_jp): Hotel Aozora Shinjuku, the
prototype's hotel. The property, its knowledge in English and Japanese, the
yen menu with the consumption tax inside its prices, the amenity catalogue,
the stays, the operations' planted cases and the guest page, after one seed
at a small scale (the volume checks need scale 1: demo-check runs those)."""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from clefs.core import rooms as rooms_mod
from clefs.seed import demo_jp, variants
from clefs.seed.demo_jp import core as cast, core_catalog
from clefs.seed.demo_uae import DemoCtx, core_stays, ops

ANCHOR = date(2026, 10, 21)
SLUG = "demo-t-jp"
JAPANESE = re.compile(r"[぀-ヿ]")


@pytest.fixture
def seeded(cur):
    return demo_jp.seed(cur, SLUG, anchor=ANCHOR, scale=0.1, password="demo-pass-123")


# ─── The property ───


def test_the_hotel_is_a_mid_range_shinjuku_hotel(cur, seeded):
    assert seeded["created"] is True and seeded["as_of"] == "2026-10-21T21:00:00+09:00"
    cur.execute("SELECT * FROM hotels WHERE slug = %s", (SLUG,))
    h = cur.fetchone()
    assert (h["name"], h["city"], h["country"].strip(), h["emirate"]) == (f"Hotel Aozora Shinjuku ({SLUG})", "Tokyo",
                                                                          "JP", None)
    assert (h["timezone"], h["currency"].strip(), h["status"], h["assistant_name"]) == ("Asia/Tokyo", "JPY", "demo",
                                                                                         "Clefs")
    assert "Nishi-Shinjuku" in h["address"] and h["front_desk_phone"].startswith("+81 3 ")
    assert h["guest_languages"] == ["en", "ja", "ko", "zh", "th", "hi", "fr", "de", "ru", "ar", "ur"]
    assert h["tax_profile"]["consumption_tax_pct"] == "10" and h["tax_profile"]["prices_include_tax"] is True
    assert h["tax_profile"]["accommodation_tax"]["basis"] == "per_person_per_night"
    # The canonical slug carries the plain name, and is the CLI's default demo.
    assert demo_jp.hotel_fields("aozora-shinjuku")["name"] == "Hotel Aozora Shinjuku"
    assert variants.DEFAULT_HOTEL == demo_jp.DEMO_SLUG == "aozora-shinjuku"


def test_rooms_departments_staff_and_logins(cur, seeded):
    cur.execute("SELECT room_type_code, count(*) AS n, count(DISTINCT floor) AS floors FROM hotel_rooms "
                "WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    assert {r["room_type_code"]: r["n"] for r in cur.fetchall()} == {"MOD_DBL": 40, "STD_TWIN": 30, "FAM_QUAD": 10}
    room = rooms_mod.get_room(cur, SLUG, cast.DECK_ROOM)
    assert room["floor"] == "2"
    cur.execute("SELECT key, name FROM svc_departments WHERE hotel_slug = %s ORDER BY sort", (SLUG,))
    assert [(r["key"], r["name"]) for r in cur.fetchall()] == [
        ("housekeeping", "Housekeeping"), ("front_office", "Front Desk"), ("engineering", "Maintenance"),
        ("ird", "In-Room Dining"), ("concierge", "Concierge"), ("security", "Security")]
    cur.execute("SELECT name, department_key, role_key, languages FROM staff_members WHERE hotel_slug = %s", (SLUG,))
    staff = {r["name"]: r for r in cur.fetchall()}
    assert len(staff) == len(cast.STAFF) == 24
    first_languages = {r["languages"][0] for r in staff.values()}
    assert {"ja", "vi", "ne", "id", "fil", "zh", "hi"} <= first_languages
    assert {r["department_key"] for r in staff.values()} == {"housekeeping", "front_office", "engineering", "ird",
                                                            "concierge", "security", None}
    assert staff["Yuki Sato"]["role_key"] == "duty_manager" and staff["Kenji Tanaka"]["role_key"] == "gm"
    assert staff["Hana Suzuki"]["role_key"] == "supervisor"
    cur.execute("SELECT m.role, u.name, u.email FROM hotel_memberships m JOIN users u ON u.id = m.user_id "
                "WHERE m.hotel_slug = %s", (SLUG,))
    users = {r["role"]: (r["name"], r["email"]) for r in cur.fetchall()}
    assert users["dept_head"] == ("Yuki Sato", f"dutymanager+{SLUG}@example.com")
    assert users["gm"][0] == "Kenji Tanaka" and set(users) == {"owner", "gm", "finance", "revenue", "front_desk",
                                                               "dept_head"}
    assert all(email.endswith("@example.com") for _n, email in users.values())
    # Outside the prototype, off for the hotel as for the deployment.
    cur.execute("SELECT module FROM hotel_module_settings WHERE hotel_slug = %s AND NOT enabled", (SLUG,))
    assert {r["module"] for r in cur.fetchall()} == set(cast.OFF_MODULES)


# ─── What the concierge knows ───


def _approved(cur) -> list[dict]:
    cur.execute("SELECT e.*, s.key AS section FROM cc_kb_entries e LEFT JOIN cc_kb_sections s ON s.id = e.section_id "
                "WHERE e.hotel_slug = %s AND e.status = 'approved' ORDER BY e.id", (SLUG,))
    return [dict(r) for r in cur.fetchall()]


def test_the_knowledge_base_in_english_and_japanese(cur, seeded):
    rows = _approved(cur)
    english = [r for r in rows if "ja" not in r["tags"]]
    japanese = [r for r in rows if "ja" in r["tags"]]
    assert len(english) == len(japanese) == 30 and seeded["parts"]["core_catalog"]["kb_entries"] == 60
    assert all(JAPANESE.search(r["question_en"] + r["answer_en"]) for r in japanese)
    assert not any(JAPANESE.search(r["answer_en"]) for r in english)
    # Each answer's Japanese twin sits in the same section.
    assert sorted(r["section"] for r in english) == sorted(r["section"] for r in japanese)
    facts = {r["fact_key"]: r["fact_value"] for r in rows if r["fact_key"]}
    assert facts == {"checkin_time": "15:00", "checkout_time": "11:00", "parking": "none", "pool": "false",
                     "breakfast": "true", "wifi": "true", "pets": "false"}
    text = " ".join(f"{r['question_en']} {r['answer_en']}" for r in english)
    for fact in ("8-minute walk", "Marunouchi Line", "Narita Express", "Limousine Bus", "Haneda", "06:30 to 10:00",
                 "¥2,200", "Aozora-Guest", "bluesky2026", "coin laundry", "takkyubin", "convenience store",
                 "foreign cards", "passport", "10% consumption tax", "accommodation tax", "smoking room on 1F",
                 "22:00 and 07:00", "yukata", "humidifier", "Type A", "119", "110", "earthquake", "closet", "taxi"):
        assert fact.lower() in text.lower(), fact
    # The readiness gate's eight required sections are all approved.
    from clefs.modules.trust.evals import readiness

    assert readiness.section_status(cur, SLUG) == {k: True for k, _l, _w in readiness.REQUIRED_SECTIONS}


# ─── What the hotel sells ───


def test_the_menu_is_in_yen_with_the_tax_inside(cur, seeded):
    from clefs.modules.upsell import api as upsell_api

    cur.execute("SELECT sku, price_minor, currency FROM ups_menu_items WHERE hotel_slug = %s", (SLUG,))
    items = {r["sku"]: r for r in cur.fetchall()}
    assert {r["currency"].strip() for r in items.values()} == {"JPY"}
    # The yen has no minor unit: the breakfast buffet is 2,200 yen, stored as 2200.
    assert items["SD-BUF-01"]["price_minor"] == 2200 and items["IRD-MN-01"]["price_minor"] == 1600
    cur.execute("SELECT id FROM ups_menu_items WHERE hotel_slug = %s AND sku = 'IRD-MN-01'", (SLUG,))
    ramen = cur.fetchone()["id"]
    with_clock = DemoCtx(cur=None, slug=SLUG, anchor=ANCHOR, tz=demo_jp.TZ).at(ANCHOR, 19)
    from clefs.core import clock

    with clock.frozen(with_clock):
        q = upsell_api.quote_for_session(cur, SLUG, room="101", lines=[{"item_id": ramen, "qty": 2}],
                                         session_ref="t-jp-quote")
    assert (q["currency"], q["total"], q["subtotal"]) == ("JPY", "3200", "3200")
    taxes = {t["code"]: t for t in q["taxes"]}
    assert set(taxes) == {"vat"} and taxes["vat"]["included"] is True and taxes["vat"]["rate"] == "0.1"
    assert taxes["vat"]["amount_minor"] == 291  # 3,200 yen with 10% inside: 2,909 + 291


def test_the_amenity_catalogue(cur, seeded):
    from clefs.modules.amenities import catalog

    cur.execute("SELECT key, currency, is_single_use_plastic, unit_cost_minor FROM amn_items WHERE hotel_slug = %s",
                (SLUG,))
    items = {r["key"]: r for r in cur.fetchall()}
    assert {"BATH_TOWEL", "WATER_500", "DENTAL_KIT", "SLIPPERS", "YUKATA", "HUMIDIFIER", "PILLOW_LOW",
            "PILLOW_BUCKWHEAT", "PHONE_CHARGER", "PLUG_ADAPTER", "UMBRELLA"} <= set(items)
    assert {r["currency"].strip() for r in items.values()} == {"JPY"}
    assert not any(r["is_single_use_plastic"] for r in items.values())
    # Amenities' own default catalogue never joins on a first console visit.
    assert catalog.seed_defaults(cur, SLUG, "JPY")["already"] is True
    cur.execute("SELECT count(*) AS n FROM amn_items WHERE hotel_slug = %s", (SLUG,))
    assert cur.fetchone()["n"] == len(items)


# ─── Stays ───


def test_stays_are_yen_tokyo_time_and_japac_guests(cur, seeded):
    cur.execute("SELECT DISTINCT currency FROM reservations WHERE hotel_slug = %s", (SLUG,))
    assert [r["currency"].strip() for r in cur.fetchall()] == ["JPY"]
    cur.execute("SELECT count(*) AS n FROM reservation_nights WHERE hotel_slug = %s AND rate_minor %% 100 <> 0",
                (SLUG,))
    assert cur.fetchone()["n"] == 0  # rate tables in steps of 100 yen
    cur.execute("SELECT DISTINCT guest_country, guest_language, channel_code FROM reservations WHERE hotel_slug = %s",
                (SLUG,))
    rows = cur.fetchall()
    assert {r["guest_country"] for r in rows} <= {"JP", "KR", "CN", "TW", "US", "AU", "HK", "TH", "SG", "IN", "GB",
                                                 "FR", "DE"}
    assert {r["guest_language"] for r in rows} <= set(cast.GUEST_LANGUAGES)
    assert {"RAKUTEN", "JALAN", "BOOKING"} <= {r["channel_code"] for r in rows}
    # Check-in from 15:00 and check-out by 11:00, Tokyo time.
    cur.execute("""SELECT min(EXTRACT(HOUR FROM checked_in_at AT TIME ZONE 'Asia/Tokyo')) AS cin,
                          max(EXTRACT(HOUR FROM checked_out_at AT TIME ZONE 'Asia/Tokyo')) AS cout
                   FROM reservations WHERE hotel_slug = %s AND pms_res_id LIKE 'AZ1%%'""", (SLUG,))
    r = cur.fetchone()
    assert r["cin"] >= 15 and r["cout"] <= 10


def test_the_stay_plan_is_a_pure_function_of_seed_and_anchor():
    def plan(anchor):
        ctx = DemoCtx(cur=None, slug="x", anchor=anchor, tz=demo_jp.TZ, variant=demo_jp)
        ctx.ids["rooms"] = {r["room_number"]: r for r in cast.room_plan()}
        return [(s.pms_res_id, s.channel, s.room_number, s.check_in, s.status, tuple(s.rates), s.guest_name)
                for s in core_stays.plan_stays(ctx)]

    assert plan(ANCHOR) == plan(ANCHOR) and plan(ANCHOR) != plan(ANCHOR + timedelta(days=1))
    assert plan(ANCHOR)[0][0].startswith("AZ1")


# ─── Operations ───


def test_planted_cases_surface_and_no_decoy_fires(cur, seeded):
    plants = seeded["plants"]
    assert {p["module"] for p in plants} == {"service", "amenities", "upsell"}
    assert {p["detector"] for p in plants} >= {"service.sla_breach_cluster", "service.l1_incident",
                                                "service.repeat_fault", "service.recovery_case", "am.variance.spike",
                                                "am.request_logging_gap", "upsell.funnel"}
    missing = [(p["detector"], p["key"]) for p in plants if p["klass"] != "DECOY" and not ops.surfaced(cur, SLUG, p)]
    fired = [(p["detector"], p["key"]) for p in plants if p["klass"] == "DECOY" and ops.surfaced(cur, SLUG, p)]
    assert missing == [] and fired == []
    # Gestures are in yen and the guests wrote in their own languages.
    cur.execute("SELECT DISTINCT currency FROM svc_recovery_actions WHERE hotel_slug = %s", (SLUG,))
    assert [r["currency"].strip() for r in cur.fetchall()] == ["JPY"]
    cur.execute("SELECT DISTINCT language FROM staff_requests WHERE hotel_slug = %s AND translated", (SLUG,))
    assert {r["language"] for r in cur.fetchall()} & {"ja", "ko", "zh", "th"}
    # The reviewers' room starts clear: nothing open in Room 204 on the anchor day.
    cur.execute("SELECT count(*) AS n FROM staff_requests WHERE hotel_slug = %s AND room = %s AND state <> 'completed'",
                (SLUG, cast.DECK_ROOM))
    assert cur.fetchone()["n"] == 0


def test_the_guest_page_of_room_204(client, cur, seeded):
    k = rooms_mod.qr_key(SLUG, cast.DECK_ROOM)
    r = client.get(f"/public/stay/{SLUG}/{cast.DECK_ROOM}/context", params={"k": k})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True and body["features"]["assistant"] is True
    assert (body["hotel"]["tz"], body["hotel"]["currency"]) == ("Asia/Tokyo", "JPY")
    assert body["hotel"]["languages"] == list(cast.GUEST_LANGUAGES)
    numbers = {n["number"] for n in body["emergency_numbers"]}
    assert {"110", "119"} <= numbers and not numbers & {"999", "998", "997"}


def test_demo_check_reads_the_tokyo_hotel(cur, seeded):
    from clefs.seed.demo_uae import _check

    results = {name: (ok, detail) for name, ok, detail in _check.run_checks(cur, SLUG, demo_jp)}
    for name in ("hotel is a demo hotel", "Tokyo: JP, Asia/Tokyo, JPY", "80 rooms on 8 floors, 10 family rooms",
                 "24 demo staff with PINs", "owner, gm, finance, revenue, front desk, duty manager users",
                 "120 history days", "forward on the books", "concierge live for guests",
                 "assistant Clefs in all 11 guest languages", "Maria on shift round the clock for 14 days"):
        assert results[name][0], (name, results[name])
    assert core_catalog.UPSELL_SETTINGS["vat_pct"] == "10" and Decimal(core_catalog.MENU[0][7]) > 0
