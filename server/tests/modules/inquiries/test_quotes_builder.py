"""Quote builder (04 spec 4.6): rates inside guardrails, the tax goldens in
one compose_stay call, validity, deposits, group terms and the no-guardrails
path."""

from __future__ import annotations

from datetime import timedelta

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.inquiries import quotes
from clefs.modules.inquiries.quotes import RoomRequest
from tests.modules.inquiries.test_quotes_support import CHECK_IN, NOW, demand, hotel, make_inquiry, now  # noqa: F401


def parts(q) -> tuple:
    return (q["room_minor"], q["sc_minor"], q["mf_minor"], q["vat_minor"], q["td_minor"], q["total_minor"])


# ─── Goldens (4.6 table, exemption toggle off) ───


def test_fit_two_nights_golden(cur, hotel, demand):
    inq = make_inquiry(cur, hotel)
    q = quotes.build(cur, hotel, inq)
    assert parts(q) == (80000, 8000, 6160, 4708, 2000, 100868)
    assert q["version"] == 1 and q["status"] == "draft" and q["currency"] == "AED"
    assert [ln["rate_minor"] for ln in q["lines"]] == [40000, 40000]
    assert q["below_bar"] is False and q["auto_send_eligible"] is True and q["auto_send_reasons"] == []
    assert q["tax_convention"] == "A" and q["fee_notices"]  # the 2026 exemption banner
    # FIT deposit: the first night, TD shown as refundable on a no-show.
    dep = q["deposit_schedule"]
    assert [(m["purpose"], m["amount_minor"], m["includes_td_minor"]) for m in dep] == [
        ("deposit", 50434, 1000), ("balance", 50434, 1000)]
    assert dep[0]["td_note"]["en"].startswith("Tourism Dirham fee") and dep[1]["td_note"] is None


def test_long_stay_td_capped_at_30_nights(cur, hotel, demand):
    demand.bar["STD"] = "250.00"
    inq = make_inquiry(cur, hotel, segment="long_stay", nights=45)
    quotes.save_quote_settings(cur, hotel.slug, {"long_stay_discount_pct": "0"}, actor=None)
    q = quotes.build(cur, hotel, inq)
    assert parts(q) == (1125000, 112500, 86625, 66206, 30000, 1420331)
    assert q["td_nights_charged"] == 30
    assert q["deposit_schedule"][0]["label"] == "First 30 nights"


def test_group_twelve_rooms_and_terms(cur, hotel, demand):
    demand.bar["STD"] = "350.00"
    inq = make_inquiry(cur, hotel, segment="group", nights=3, rooms=12)
    quotes.save_quote_settings(cur, hotel.slug, {"group_discount_pct": "0"}, actor=None)
    q = quotes.build(cur, hotel, inq)
    assert parts(q) == (1260000, 126000, 97020, 74151, 36000, 1593171)
    gt = q["group_terms"]
    assert gt["cutoff_date"] == (CHECK_IN - timedelta(days=30)).isoformat() == gt["rooming_list_due"]
    assert gt["attrition_pct"] == "80" and gt["label"] == "Dubai norms UNVERIFIED"
    assert q["valid_until"] == NOW + timedelta(days=7)
    # 25% at signing; 50% cumulative 60 d out is already inside the window.
    assert [m["purpose"] for m in q["deposit_schedule"]][-1] == "balance"
    assert q["deposit_schedule"][0]["amount_minor"] == 398293  # 25% of 15,931.71


def test_mixed_room_types_one_call(cur, hotel, demand):
    inq = make_inquiry(cur, hotel, segment="group", rooms=10)
    quotes.save_quote_settings(cur, hotel.slug, {"group_discount_pct": "0"}, actor=None)
    q = quotes.build(cur, hotel, inq, rooms=[RoomRequest("STD", 8), RoomRequest("FAM_2BR", 2)])
    assert parts(q) == (880000, 88000, 67760, 51788, 24000, 1111548)
    assert q["td_units"] == 12


def test_two_bedroom_per_bedroom_td(cur, hotel, demand):
    inq = make_inquiry(cur, hotel, room_type_code="FAM_2BR")
    q = quotes.build(cur, hotel, inq)
    assert parts(q) == (120000, 12000, 9240, 7062, 4000, 152302)


# ─── Policy discounts and staff edits ───


def test_group_discount_makes_it_below_bar(cur, hotel, demand):
    inq = make_inquiry(cur, hotel, segment="group", rooms=9)
    q = quotes.build(cur, hotel, inq)
    assert {ln["rate_minor"] for ln in q["lines"]} == {36000}  # 10% off 400
    assert q["below_bar"] is True and "discount" in q["auto_send_reasons"]
    assert q["pricing_trace"]["policy"] == "group" and q["max_discount_pct"] == 10


def test_staff_edit_bounds(cur, hotel, demand):
    inq = make_inquiry(cur, hotel)
    d0 = CHECK_IN.isoformat()
    with pytest.raises(ClefsError) as e:
        quotes.build(cur, hotel, inq, edits=[{"stay_date": d0, "rate_minor": 60001}])
    assert e.value.code == "above_ceiling"
    with pytest.raises(ClefsError) as e:
        quotes.build(cur, hotel, inq, edits=[{"stay_date": d0, "rate_minor": 31999}])
    assert e.value.code == "below_floor_needs_approval"
    with pytest.raises(ClefsError) as e:
        quotes.build(cur, hotel, inq, edits=[{"stay_date": d0, "rate_minor": 31999}], can_approve=True)
    assert e.value.code == "reason_required"
    q = quotes.build(cur, hotel, inq, edits=[{"stay_date": d0, "rate_minor": 31999}], can_approve=True,
                     reason="repeat corporate guest")
    assert q["lines"][0]["source"] == "staff" and q["below_bar"] is True
    assert "below_bar" in q["auto_send_reasons"]


# ─── No guardrails (the Demand stub) ───


def test_no_guardrails_needs_manual_rates(cur, hotel, demand):
    demand.none_dates = {CHECK_IN, CHECK_IN + timedelta(days=1)}
    inq = make_inquiry(cur, hotel)
    q = quotes.build(cur, hotel, inq)
    assert all(ln["source"] == "manual" and ln["rate_minor"] is None for ln in q["lines"])
    assert q["auto_send_eligible"] is False and "no_guardrails" in q["auto_send_reasons"]
    assert q["below_bar"] is True
    q2 = quotes.revise(cur, hotel, q, edits=[{"stay_date": CHECK_IN.isoformat(), "rate_minor": 40000},
                                            {"stay_date": (CHECK_IN + timedelta(days=1)).isoformat(),
                                             "rate_minor": 40000}], reason=None, can_approve=False)
    assert q2["total_minor"] == 100868 and q2["lines"][0]["source"] == "manual"
    assert "no_guardrails" in q2["auto_send_reasons"]


# ─── Validity ───


def test_validity_rules(cur, hotel, demand):
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert q["valid_until"] == NOW + timedelta(hours=48)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, segment="corporate", email="c@example.com"))
    assert q["valid_until"] == NOW + timedelta(hours=72)
    demand.demand_index[CHECK_IN + timedelta(days=1)] = 85
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, email="h@example.com"))
    assert q["valid_until"] == NOW + timedelta(hours=24)
    # Arrival in two days: never later than 00:00 on the arrival day.
    soon = NOW.date() + timedelta(days=2)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel, check_in=soon, email="s@example.com"))
    assert q["valid_until"].astimezone(hotel.tz).date() == soon and q["valid_until"] < NOW + timedelta(hours=48)


def test_new_version_supersedes(cur, hotel, demand):
    inq = make_inquiry(cur, hotel)
    q1 = quotes.build(cur, hotel, inq)
    q2 = quotes.build(cur, hotel, inq)
    assert q2["version"] == 2
    assert quotes.quote_by_id(cur, q1["id"])["status"] == "superseded"


# ─── Fee notices follow the override window (QA OWN-09) ───


def test_exemption_notice_matches_the_quoted_nights(cur, hotel, demand):
    """An owner override for one September night must not print "applied at
    the owner's request" on an October quote that charges the TD."""
    from datetime import date

    from clefs.core.tax import set_override

    one_night = date(2026, 9, 22)
    set_override(cur, hotel.slug, "dxb_2026_exempt_td", apply=True, effective_from=one_night, effective_to=one_night)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert q["td_minor"] == 2000  # charged: the override does not cover these nights
    td_lines = [n for n in q["fee_notices"] if n.startswith("Dubai 2026: Tourism Dirham")]
    assert td_lines and all("applied at the owner's request" not in n for n in td_lines)
    assert any("(not applied to these dates)" in n for n in td_lines)
    # Covering one of the two nights says so.
    set_override(cur, hotel.slug, "dxb_2026_exempt_td", apply=True, effective_from=CHECK_IN, effective_to=CHECK_IN)
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert any("on 1 of 2 nights" in n for n in q["fee_notices"])
    # Covering the whole stay keeps core.tax's wording.
    set_override(cur, hotel.slug, "dxb_2026_exempt_td", apply=True, effective_from=CHECK_IN,
                 effective_to=CHECK_IN + timedelta(days=5))
    q = quotes.build(cur, hotel, make_inquiry(cur, hotel))
    assert q["td_minor"] == 0 and any("(applied at the owner's request)" in n for n in q["fee_notices"])
