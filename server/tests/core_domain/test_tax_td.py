"""Tourism Dirham liability over stayed nights, the 30-consecutive-night cap
across a stay chain, per-bedroom units and the remittance due date (02 spec
2.3). Rows are inserted directly so this does not depend on core/stays."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from clefs.core import tax

OCT = date(2025, 10, 1)
NOV = date(2025, 11, 1)


@pytest.fixture
def dubai3(cur, hotel_uae):
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    cur.execute(
        "INSERT INTO room_types (hotel_slug, code, name, bedrooms) VALUES (%s, 'FAM_2BR', 'Family two-bedroom', 2)",
        (hotel_uae.slug,),
    )
    return hotel_uae.slug


@pytest.fixture
def stay(cur, dubai3):
    """stay(pms_id, check_in, nights, *, rooms=1, status, stayed_nights=None,
    chain=None, code='DLX') -> reservation id. stayed_nights (default all)
    marks the first n nights as stayed."""

    def _make(pms_id, check_in, n, *, rooms=1, status="CHECKED_OUT", stayed_nights=None, chain=None, code="DLX"):
        cur.execute(
            """
            INSERT INTO reservations (hotel_slug, pms_res_id, check_in, check_out, nights, rooms_count,
                                      room_type_code, status, currency, stay_chain_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'AED', %s) RETURNING id
            """,
            (dubai3, pms_id, check_in, check_in + timedelta(days=n), n, rooms, code, status, chain),
        )
        rid = cur.fetchone()["id"]
        stayed = n if stayed_nights is None else stayed_nights
        for i in range(n):
            cur.execute(
                """
                INSERT INTO reservation_nights (hotel_slug, reservation_id, stay_date, room_type_code, rooms,
                                                rate_minor, currency, stayed)
                VALUES (%s, %s, %s, %s, %s, 40000, 'AED', %s)
                """,
                (dubai3, rid, check_in + timedelta(days=i), code, rooms, i < stayed),
            )
        return rid

    return _make


def month(cur, slug, m):
    return tax.td_liability(cur, slug, start=m, end=date(m.year + (m.month == 12), m.month % 12 + 1, 1) - timedelta(days=1))


# ─── Liability ───


def test_stayed_nights_and_units(cur, dubai3, stay):
    a = stay("A", date(2025, 10, 5), 2)
    b = stay("B", date(2025, 10, 5), 3, rooms=2)
    liab = month(cur, dubai3, OCT)
    assert liab["liable_unit_nights"] == 2 + 6 and liab["td_expected_minor"] == 8000 and liab["currency"] == "AED"
    assert liab["by_reservation"] == {a: {"unit_nights": 2, "td_minor": 2000}, b: {"unit_nights": 6, "td_minor": 6000}}


def test_no_show_cancelled_and_unstayed_nights_owe_nothing(cur, dubai3, stay):
    stay("NS", date(2025, 10, 5), 2, status="NO_SHOW", stayed_nights=0)
    stay("CX", date(2025, 10, 5), 2, status="CANCELLED")
    early = stay("ED", date(2025, 10, 5), 5, stayed_nights=2)  # left after 2 of 5 nights
    liab = month(cur, dubai3, OCT)
    assert liab["liable_unit_nights"] == 2 and liab["by_reservation"] == {early: {"unit_nights": 2, "td_minor": 2000}}


def test_cap_across_a_back_to_back_chain(cur, dubai3, stay):
    first = stay("C1", date(2025, 10, 7), 25)  # 7-31 Oct: nights 1..25
    second = stay("C2", NOV, 10, chain=first)  # 1-10 Nov: nights 26..35
    assert month(cur, dubai3, OCT)["td_expected_minor"] == 25000
    nov = month(cur, dubai3, NOV)
    assert nov["liable_unit_nights"] == 5 and nov["capped_unit_nights"] == 5
    assert nov["by_reservation"] == {second: {"unit_nights": 5, "td_minor": 5000}}


def test_unchained_reservations_count_separately(cur, dubai3, stay):
    stay("U1", date(2025, 10, 7), 25)
    stay("U2", NOV, 10)  # no stay_chain_id: a new stay, nights 1..10
    assert month(cur, dubai3, NOV)["liable_unit_nights"] == 10


def test_single_long_stay_capped(cur, dubai3, stay):
    stay("L", date(2025, 10, 1), 45)
    total = tax.td_liability(cur, dubai3, start=date(2025, 10, 1), end=date(2025, 11, 30))
    assert total["liable_unit_nights"] == 30 and total["capped_unit_nights"] == 15
    assert total["td_expected_minor"] == 30000


def test_per_bedroom_units(cur, dubai3, stay):
    stay("F", date(2025, 10, 5), 2, code="FAM_2BR")
    liab = month(cur, dubai3, OCT)
    assert liab["liable_unit_nights"] == 4 and liab["td_expected_minor"] == 4000


def test_exempt_nights_owe_nothing_when_applied(cur, dubai3, stay):
    tax.set_override(cur, dubai3, "dxb_2026_exempt_td", apply=True)
    stay("X", date(2026, 5, 19), 4)  # 19, 20 charged; 21, 22 exempt
    liab = tax.td_liability(cur, dubai3, start=date(2026, 5, 1), end=date(2026, 5, 31))
    assert liab["td_expected_minor"] == 2000 and liab["notices"]


# ─── Remittance ───


def test_due_date_is_the_15th_of_next_month(cur, dubai3):
    assert tax.td_remittance_due_date(cur, dubai3, date(2025, 10, 20)) == date(2025, 11, 15)
    assert tax.td_remittance_due_date(cur, dubai3, date(2025, 12, 1)) == date(2026, 1, 15)


def test_deferral_only_with_the_override(cur, dubai3):
    assert tax.td_remittance_due_date(cur, dubai3, date(2026, 4, 1)) == date(2026, 5, 15)
    tax.set_override(cur, dubai3, "dxb_2026_deferral_td", apply=True)
    assert tax.td_remittance_due_date(cur, dubai3, date(2026, 4, 1)) == date(2026, 8, 15)
    assert tax.td_remittance_due_date(cur, dubai3, date(2026, 6, 1)) == date(2026, 10, 15)
    assert tax.td_remittance_due_date(cur, dubai3, date(2026, 7, 1)) == date(2026, 8, 15)


def test_td_month_summary(cur, dubai3, stay):
    stay("M", date(2025, 10, 30), 4)  # 30, 31 Oct and 1, 2 Nov
    got = tax.td_month(cur, dubai3, date(2025, 10, 17))
    assert got["month"] == OCT and got["liable_unit_nights"] == 2 and got["td_expected_minor"] == 2000
    assert got["due_date"] == date(2025, 11, 15) and got["deferral_months"] == 0


def test_registered_room_does_not_hide_type_bedrooms(cur, dubai3, stay):
    cur.execute("INSERT INTO hotel_rooms (hotel_slug, room_number, room_type_code) VALUES (%s, '801', 'FAM_2BR')",
                (dubai3,))
    rid = stay("R", date(2025, 10, 5), 1, code="FAM_2BR")
    cur.execute("UPDATE reservation_nights SET room_number = '801' WHERE reservation_id = %s", (rid,))
    assert month(cur, dubai3, OCT)["liable_unit_nights"] == 2
