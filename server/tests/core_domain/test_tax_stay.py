"""clefs.core.tax stay composition (04 spec D3 and 4.6 goldens) and gross
decomposition for one night through the hotel's rules (02 spec 2.3)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from clefs.core import tax
from clefs.core.tax import StayLine

D0 = date(2026, 1, 10)  # before every 2026 announcement: no notices


@pytest.fixture
def dubai3(cur, hotel_uae):
    """hotel_uae as a Dubai 3-star, convention A (TD AED 10), with a
    two-bedroom apartment type."""
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    cur.execute(
        "INSERT INTO room_types (hotel_slug, code, name, bedrooms) VALUES (%s, 'STD', 'Standard', 1),"
        " (%s, 'FAM_2BR', 'Family two-bedroom', 2)",
        (hotel_uae.slug, hotel_uae.slug),
    )
    return hotel_uae.slug


def nights(start: date, n: int, rate_aed: int, *, rooms: int = 1, code: str = "STD") -> list[StayLine]:
    return [StayLine(start + timedelta(days=i), code, rooms, rate_aed * 100) for i in range(n)]


def parts(b) -> tuple:
    return (b.room, b.sc, b.mf, b.vat, b.td, b.total)


# ─── 4.6 goldens ───


def test_one_room_two_nights(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(D0, 2, 400))
    assert parts(b) == (80000, 8000, 6160, 4708, 2000, 100868)
    assert (b.td_nights_charged, b.td_units, b.currency, b.convention) == (2, 1, "AED", "A")
    assert b.notices == [] and b.td_label == "Tourism Dirham fee"
    assert {r["rule_key"] for r in b.fee_rules} == {"dxb_sc_10", "dxb_mf_7", "dxb_vat_5", "dxb_td_hotel_3"}


def test_45_nights_td_capped_at_30(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(date(2025, 11, 1), 45, 250))
    assert parts(b) == (1125000, 112500, 86625, 66206, 30000, 1420331)
    assert b.td_nights_charged == 30


def test_group_twelve_rooms(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(D0, 3, 350, rooms=12))
    assert parts(b) == (1260000, 126000, 97020, 74151, 36000, 1593171)
    assert b.td_units == 12


def test_two_bedroom_per_bedroom_rule(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(D0, 2, 600, code="FAM_2BR"))
    assert parts(b) == (120000, 12000, 9240, 7062, 4000, 152302)
    assert b.td_units == 2


def test_mixed_room_types_one_call(cur, dubai3):
    lines = nights(D0, 2, 400, rooms=8) + nights(D0, 2, 600, rooms=2, code="FAM_2BR")
    b = tax.compose_stay(cur, dubai3, lines=lines)
    assert parts(b) == (880000, 88000, 67760, 51788, 24000, 1111548)
    assert b.td_units == 12 and b.td_nights_charged == 2


def test_exemption_toggle_on(cur, dubai3):
    tax.set_override(cur, dubai3, "dxb_2026_exempt_mf", apply=True)
    tax.set_override(cur, dubai3, "dxb_2026_exempt_td", apply=True)
    b = tax.compose_stay(cur, dubai3, lines=nights(date(2026, 9, 1), 2, 400))
    assert parts(b) == (80000, 8000, 0, 4400, 0, 92400)
    assert b.notices and all("not yet verified" in n for n in b.notices)
    statuses = {r["rule_key"]: r["rule_status"] for r in b.fee_rules}
    assert statuses["dxb_2026_exempt_mf"] == "ANNOUNCED_UNVERIFIED"


def test_stay_across_the_exemption_start(cur, dubai3):
    # Each night uses its own date's rules: 20 May charged, 21 May exempt.
    tax.set_override(cur, dubai3, "dxb_2026_exempt_mf", apply=True)
    tax.set_override(cur, dubai3, "dxb_2026_exempt_td", apply=True)
    b = tax.compose_stay(cur, dubai3, lines=nights(date(2026, 5, 20), 2, 400))
    assert (b.mf, b.td, b.td_nights_charged) == (3080, 1000, 1)


def test_exemption_without_toggle_charges_but_warns(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(date(2026, 9, 1), 2, 400))
    assert parts(b) == (80000, 8000, 6160, 4708, 2000, 100868)
    assert any("not applied" in n for n in b.notices)


# ─── Night index across a chain and rounding ───


def test_prior_chain_nights_continue_the_cap(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=nights(D0, 5, 300), prior_chain_nights=28)
    assert b.td == 2000 and b.td_nights_charged == 2


def test_gap_restarts_the_count(cur, dubai3):
    lines = nights(date(2025, 10, 1), 30, 300) + nights(date(2025, 11, 2), 2, 300)
    b = tax.compose_stay(cur, dubai3, lines=lines)
    assert b.td_nights_charged == 32 and b.td == 32000


def test_empty_stay(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=[])
    assert parts(b) == (0, 0, 0, 0, 0, 0)


def test_bedrooms_on_the_line_win(cur, dubai3):
    b = tax.compose_stay(cur, dubai3, lines=[StayLine(D0, "UNKNOWN", 1, 50000, bedrooms=3)])
    assert b.td == 3000 and b.td_units == 3


# ─── decompose one night ───


def test_decompose_night_golden(cur, dubai3):
    r = tax.decompose_night(cur, dubai3, gross_minor=50000, stay_date=D0)
    assert (r["room"], r["sc"], r["mf"], r["vat"], r["td"], r["gross"]) == (39649, 3965, 3053, 2333, 1000, 50000)
    assert r["factor"] == "1.23585" and r["convention"] == "A"


def test_decompose_night_conventions_and_no_show(cur, dubai3):
    tax.set_tax_profile(cur, dubai3, effective_from=date(2025, 12, 1), property_category="hotel_3", convention="C")
    r = tax.decompose_night(cur, dubai3, gross_minor=50000, stay_date=D0)
    assert (r["room"], r["sc"], r["mf"], r["vat"]) == (40165, 4016, 2811, 2008)
    # A night that never started owes no TD, so nothing is stripped.
    r = tax.decompose_night(cur, dubai3, gross_minor=49000, stay_date=D0, td_liable=False)
    assert r["td"] == 0 and r["room"] == 40165


def test_decompose_night_two_bedroom(cur, dubai3):
    r = tax.decompose_night(cur, dubai3, gross_minor=100000, stay_date=D0, room_type_code="FAM_2BR")
    assert r["td"] == 2000 and r["td_units"] == 2
    assert sum(r[k] for k in ("room", "sc", "mf", "tf", "dest", "vat", "td")) == 100000
