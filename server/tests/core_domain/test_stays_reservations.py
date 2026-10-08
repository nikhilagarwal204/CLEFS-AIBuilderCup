"""core/stays reservations: upsert, derived nights and fees, stay events,
stay chains (02 spec 2.1, 04 spec D1)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import stays, tax

NOW = datetime(2025, 10, 20, 8, 0, tzinfo=timezone.utc)  # 12:00 in Dubai


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(NOW)
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    return hotel_uae.slug


def res(cur, slug, pms_id, **kw):
    data = {"pms_res_id": pms_id, "check_in": "2025-10-10", "check_out": "2025-10-13", "guest_name": "Jane Doe",
            "channel_code": "BOOKING", "room_number": "101", "room_type_code": "DLX"} | kw
    return stays.upsert_reservation(cur, slug, data)


def nights(cur, rid):
    cur.execute("SELECT * FROM reservation_nights WHERE reservation_id = %s ORDER BY stay_date, room_seq", (rid,))
    return [dict(r) for r in cur.fetchall()]


def test_create_normalises_keys_and_emits_created(cur, slug):
    out = res(cur, slug, "A1", ota_res_id="BDC-4 123 456 789", guest_name="Mr. DOE/JANE", room_revenue_minor=120000)
    r = stays.get_reservation(cur, out["id"])
    assert (r["ota_res_id_norm"], r["guest_name_norm"], r["nights"]) == ("4123456789", "doe jane", 3)
    assert r["status"] == "CONFIRMED" and r["currency"] == "AED" and r["stay_chain_id"] == r["id"]
    assert out["events"] == ["created"] and out["created"]
    ev = stays.events_for(cur, slug, out["id"])[0]
    assert ev["changes"] == {"status": [None, "CONFIRMED"], "check_in": [None, "2025-10-10"],
                             "check_out": [None, "2025-10-13"], "channel_code": [None, "BOOKING"]}
    cur.execute("SELECT kind FROM channels WHERE hotel_slug = %s AND code = 'BOOKING'", (slug,))
    assert cur.fetchone()["kind"] == "ota"


def test_room_revenue_is_spread_exactly_and_fees_follow_the_rules(cur, slug):
    out = res(cur, slug, "A2", room_revenue_minor=100001, rooms_count=1)
    rows = nights(cur, out["id"])
    assert [n["rate_minor"] for n in rows] == [33334, 33334, 33333]
    assert sum(n["rate_minor"] for n in rows) == 100001
    # Dubai hotel_3, convention A: SC 10%, MF 7% of R+SC, VAT 5% of R+SC+MF, TD 10 per night.
    first = rows[0]
    assert (first["sc_minor"], first["td_minor"]) == (3333, 1000)
    assert first["mf_minor"] == round((33334 + 3333) * 0.07)
    assert not any(n["stayed"] for n in rows)  # CONFIRMED: not started
    assert stays.get_reservation(cur, out["id"])["rate_basis"] == "room_revenue"


def test_gross_is_decomposed_to_room_revenue(cur, slug):
    out = res(cur, slug, "A3", check_out="2025-10-11", gross_minor=40165 + 4016 + 2811 + 2008 + 1000)
    [n] = nights(cur, out["id"])
    assert n["rate_minor"] + n["sc_minor"] + n["mf_minor"] + n["vat_minor"] + n["td_minor"] == 50000
    assert n["td_minor"] == 1000
    assert stays.get_reservation(cur, out["id"])["rate_basis"] == "gross_decomposed"


def test_multi_room_booking_gets_a_row_per_room(cur, slug):
    out = res(cur, slug, "A4", rooms_count=2, room_revenue_minor=120001)
    rows = nights(cur, out["id"])
    assert len(rows) == 6 and {n["room_seq"] for n in rows} == {1, 2} and all(n["rooms"] == 1 for n in rows)
    assert sum(n["rate_minor"] for n in rows) == 120001


def test_status_moves_emit_frozen_events_in_order(cur, slug, clock):
    out = res(cur, slug, "B1", check_in="2025-10-19", check_out="2025-10-22", room_revenue_minor=90000)
    rid = out["id"]
    moved = stays.upsert_reservation(cur, slug, {"pms_res_id": "B1", "check_out": "2025-10-23"})
    assert moved["events"] == ["dates_changed"]
    assert stays.events_for(cur, slug, rid)[-1]["changes"]["check_out"] == ["2025-10-22", "2025-10-23"]
    assert len(nights(cur, rid)) == 4
    inh = stays.upsert_reservation(cur, slug, {"pms_res_id": "B1", "status": "IN_HOUSE"})
    assert inh["events"] == ["checked_in"]
    stayed = [n["stay_date"] for n in nights(cur, rid) if n["stayed"]]
    assert stayed == [date(2025, 10, 19), date(2025, 10, 20)]  # business date is the 20th
    out2 = stays.upsert_reservation(cur, slug, {"pms_res_id": "B1", "status": "CHECKED_OUT",
                                                "checked_out_at": "2025-10-21T10:00:00+04:00"})
    assert out2["events"] == ["checked_out"]
    r = stays.get_reservation(cur, rid)
    assert r["actual_check_out"] == date(2025, 10, 21)  # left two nights early
    assert [n["stayed"] for n in nights(cur, rid)] == [True, True, False, False]
    assert [n["td_minor"] for n in nights(cur, rid)][2:] == [0, 0]  # no TD on nights never stayed
    evs = stays.events_for(cur, slug, rid)
    assert [e["event"] for e in evs] == ["created", "dates_changed", "checked_in", "checked_out"]
    assert all(a["occurred_at"] <= b["occurred_at"] for a, b in zip(evs, evs[1:]))


def test_jump_to_checked_out_implies_checked_in_and_cancel_is_one_event(cur, slug):
    a = res(cur, slug, "C1", status="CHECKED_OUT")
    assert a["events"] == ["created", "checked_in", "checked_out"]
    assert all(n["stayed"] for n in nights(cur, a["id"]))
    b = res(cur, slug, "C2", room_number="102")
    c = stays.upsert_reservation(cur, slug, {"pms_res_id": "C2", "status": "CANCELLED"})
    assert c["events"] == ["cancelled"]
    assert stays.get_reservation(cur, b["id"])["cancelled_at"] is not None
    assert all(n["td_minor"] == 0 for n in nights(cur, b["id"]))
    again = stays.upsert_reservation(cur, slug, {"pms_res_id": "C2", "status": "CANCELLED"})
    assert again["events"] == [] and again["changed"] == []


def test_back_to_back_stays_form_a_chain(cur, slug):
    a = res(cur, slug, "D1", check_in="2025-10-01", check_out="2025-10-05", status="CHECKED_OUT")
    b = res(cur, slug, "D2", check_in="2025-10-05", check_out="2025-10-08", guest_name="DOE, Jane")
    other_room = res(cur, slug, "D3", check_in="2025-10-05", check_out="2025-10-06", room_number="102")
    assert stays.get_reservation(cur, b["id"])["stay_chain_id"] == a["id"]
    assert stays.get_reservation(cur, other_room["id"])["stay_chain_id"] == other_room["id"]
    # A third stay imported before the middle one still joins once the gap closes.
    c = res(cur, slug, "D5", check_in="2025-10-10", check_out="2025-10-12")
    assert stays.get_reservation(cur, c["id"])["stay_chain_id"] == c["id"]
    res(cur, slug, "D4", check_in="2025-10-08", check_out="2025-10-10")
    assert stays.get_reservation(cur, c["id"])["stay_chain_id"] == a["id"]
    # Cancelling the first stay breaks the chain at its head.
    stays.upsert_reservation(cur, slug, {"pms_res_id": "D1", "status": "CANCELLED"})
    assert stays.get_reservation(cur, a["id"])["stay_chain_id"] is None
    assert stays.get_reservation(cur, b["id"])["stay_chain_id"] == b["id"]
    assert stays.get_reservation(cur, c["id"])["stay_chain_id"] == b["id"]


def test_invalid_input_is_refused(cur, slug):
    from clefs.core.errors import ClefsError

    with pytest.raises(ClefsError):
        res(cur, slug, "E1", check_out="2025-10-01")
    with pytest.raises(ClefsError):
        res(cur, slug, "E2", status="MAYBE")
    with pytest.raises(ClefsError):
        stays.upsert_reservation(cur, slug, {"check_in": "2025-10-01", "check_out": "2025-10-02"})


def test_booking_engine_attribution_matches_direct_bookings_only(cur, slug):
    direct = res(cur, slug, "G1", channel_code="DIRECT_WEB", booking_engine_ref="BE-77")
    by_pms = res(cur, slug, "G2", channel_code="DIRECT_WEB", room_number="102")
    ota = res(cur, slug, "G3", channel_code="BOOKING", room_number="103")
    out = stays.apply_attribution(cur, slug, [
        {"booking_ref": "BE-77", "utm_source": "google", "utm_medium": "organic", "landing_url": "https://x.test/"},
        {"booking_ref": "G2", "utm_source": "gbp", "promo_code": "DIRECT10"},
        {"booking_ref": "G3", "utm_source": "google"},
    ])
    assert out == {"matched": 2, "unmatched": ["G3"]}
    a = stays.get_reservation(cur, direct["id"])
    assert a["utm"] == {"utm_source": "google", "utm_medium": "organic", "landing_url": "https://x.test/"}
    b = stays.get_reservation(cur, by_pms["id"])
    assert (b["promo_code"], b["booking_engine_ref"]) == ("DIRECT10", "G2")
    assert stays.get_reservation(cur, ota["id"])["utm"] is None
    assert [r["id"] for r in stays.attributed_bookings(cur, slug, utm_source="gbp")] == [by_pms["id"]]
