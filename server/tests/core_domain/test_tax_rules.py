"""clefs.core.tax rules: seeds, sources, date-effective resolution, the owner
override for ANNOUNCED_UNVERIFIED rules, profiles and the peg (02 spec 2.3)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clefs.core import hotels, money
from clefs.core.errors import ClefsError
from clefs.core import tax

SEP = date(2026, 9, 15)
PRE_2026 = date(2025, 11, 10)

# ─── Seeds ───


def test_every_seeded_rule_has_a_source(cur):
    cur.execute("SELECT rule_key, source_url, rule_status FROM fee_rules WHERE hotel_slug IS NULL")
    rows = cur.fetchall()
    keys = {r["rule_key"] for r in rows}
    assert {"dxb_sc_10", "dxb_mf_7", "dxb_vat_5", "dxb_td_hotel_3", "auh_tf_4", "auh_mf_4"} <= keys
    assert all(r["source_url"] and r["source_url"].startswith("https://") for r in rows)
    unverified = {r["rule_key"] for r in rows if r["rule_status"] == "ANNOUNCED_UNVERIFIED"}
    assert unverified == {"dxb_2026_deferral_td", "dxb_2026_deferral_mf", "dxb_2026_exempt_td", "dxb_2026_exempt_mf"}


def test_td_schedule_seeded(cur):
    cur.execute("SELECT property_category, rate, cap_consecutive_nights, per_bedroom, vat_applies FROM fee_rules"
                " WHERE rule_key LIKE 'dxb_td_%'")
    rows = {r["property_category"]: r for r in cur.fetchall()}
    assert {k: int(v["rate"]) for k, v in rows.items()} == {
        "hotel_5": 20, "hotel_4": 15, "hotel_3": 10, "hotel_2": 10, "hotel_1": 7,
        "apartment_luxury": 20, "apartment_deluxe": 15, "apartment_standard": 10, "guest_house": 7,
        "holiday_home_luxury": 15, "holiday_home_standard": 10,
    }
    assert all(r["cap_consecutive_nights"] == 30 and r["per_bedroom"] and not r["vat_applies"] for r in rows.values())


def test_peg_row_and_money_fx(cur):
    cur.execute("SELECT rate, source FROM fx_rates WHERE base = 'USD' AND quote = 'AED'")
    row = cur.fetchone()
    assert Decimal(row["rate"]) == Decimal("3.6725") and row["source"] == "PEG"
    assert money.fx_rate(cur, "USD", "AED", SEP) == Decimal("3.6725")
    assert money.convert(cur, Decimal("100"), "USD", "AED", SEP) == Decimal("367.25")
    assert money.convert(cur, Decimal("367.25"), "AED", "USD", SEP) == Decimal("100.00")


# ─── Resolution ───


def test_dubai_profile_from_hotel_row(cur, hotel_uae):
    p = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert p.emirate == "DXB" and p.property_category == "hotel_4" and p.convention == "A"
    assert {f.code: f.rate for f in p.fees} == {"SC": Decimal("0.1"), "MF": Decimal("0.07"), "VAT": Decimal("0.05")}
    assert p.td_rate == Decimal(15) and p.td_cap_nights == 30 and p.td_per_bedroom
    # The unverified exemption meets this date but is not applied: banner only.
    assert any("not applied" in n for n in p.notices)
    assert tax.factor(p) == Decimal("1.23585")


def test_unverified_needs_the_override(cur, hotel_uae):
    before = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert before.fee("MF").rate == Decimal("0.07") and before.td_rate == 15
    tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_mf", apply=True)
    tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_td", apply=True)
    after = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert after.fee("MF").rate == 0 and after.td_rate == 0
    assert after.fee("MF").rule_status == "ANNOUNCED_UNVERIFIED"
    assert any("applied at the owner's request" in n for n in after.notices)
    # Before the announced window the verified rules still apply.
    old = tax.resolve_profile(cur, hotel_uae.slug, PRE_2026)
    assert old.fee("MF").rate == Decimal("0.07") and old.td_rate == 15 and old.notices == ()


def test_override_narrows_the_window(cur, hotel_uae):
    tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_td", apply=True,
                     effective_from=date(2026, 7, 1), effective_to=date(2026, 8, 31), note="DET circular")
    assert tax.resolve_profile(cur, hotel_uae.slug, date(2026, 6, 30)).td_rate == 15
    assert tax.resolve_profile(cur, hotel_uae.slug, date(2026, 7, 1)).td_rate == 0
    assert tax.resolve_profile(cur, hotel_uae.slug, SEP).td_rate == 15
    # An override never widens the announcement (announced from 2026-05-21).
    tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_td", apply=True, effective_from=date(2026, 1, 1))
    assert tax.resolve_profile(cur, hotel_uae.slug, date(2026, 3, 1)).td_rate == 15
    with pytest.raises(ClefsError):
        tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_td", apply=True,
                         effective_from=date(2026, 9, 1), effective_to=date(2026, 8, 1))


def test_override_can_switch_a_verified_rule_off(cur, hotel_uae):
    tax.set_override(cur, hotel_uae.slug, "dxb_sc_10", apply=False, note="no service charge here")
    p = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert p.fee("SC") is None
    with pytest.raises(ClefsError) as e:
        tax.set_override(cur, hotel_uae.slug, "no_such_rule", apply=True)
    assert e.value.status == 404


# ─── Profiles ───


def test_profile_is_date_effective(cur, hotel_uae):
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2026, 9, 1), property_category="hotel_3",
                        convention="C", trn="100000000000003")
    assert tax.resolve_profile(cur, hotel_uae.slug, date(2026, 8, 31)).convention == "A"
    p = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert p.convention == "C" and p.td_rate == 10
    got = tax.get_tax_profile(cur, hotel_uae.slug, on=SEP)
    assert got["source"] == "profile" and got["trn"] == "100000000000003" and got["emirate"] == "DXB"
    with pytest.raises(ClefsError):
        tax.set_tax_profile(cur, hotel_uae.slug, effective_from=SEP, property_category="castle")
    with pytest.raises(ClefsError):
        tax.set_tax_profile(cur, hotel_uae.slug, effective_from=SEP, property_category="hotel_3", convention="D")


def test_profile_td_per_bedroom_override(cur, hotel_uae):
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="apartment_deluxe",
                        td_per_bedroom=False)
    p = tax.resolve_profile(cur, hotel_uae.slug, SEP)
    assert p.td_rate == 15 and p.td_per_bedroom is False


def test_derive_category():
    assert tax.derive_category(None, 5) == "hotel_5"
    assert tax.derive_category("Hotel Apartments", 4) == "apartment_deluxe"
    assert tax.derive_category("hotel apartment", None) == "apartment_standard"
    assert tax.derive_category("Guest House", None) == "guest_house"
    assert tax.derive_category("guest_house", 3) == "guest_house"
    assert tax.derive_category("General", None) is None
    assert tax.emirate_code("abu_dhabi") == "AUH" and tax.emirate_code("DXB") == "DXB" and tax.emirate_code(None) is None


# ─── Abu Dhabi and owner-configured rules ───


def _abu_dhabi(cur):
    return hotels.create_hotel(cur, slug="test-auh", name="Test AUH", country="AE", emirate="abu_dhabi",
                               timezone="Asia/Dubai", currency="AED", star_rating=4)


def test_abu_dhabi_has_no_per_night_fee_and_owner_sc(cur):
    h = _abu_dhabi(cur)
    p = tax.resolve_profile(cur, h.slug, SEP)
    assert {f.code for f in p.fees} == {"MF", "TF", "VAT"} and p.td_rate == 0 and p.notices == ()
    with pytest.raises(ClefsError) as e:
        tax.configure_rule(cur, h.slug, fee_code="SC", calc_mode="PERCENT", rate="0.10",
                           effective_from=date(2024, 1, 1), label="Service charge", verified_by=" ")
    assert e.value.code == "verified_by_required"
    row = tax.configure_rule(cur, h.slug, fee_code="SC", calc_mode="PERCENT", rate="0.10",
                             effective_from=date(2024, 1, 1), label="Service charge 10%", verified_by="owner")
    assert row["rule_status"] == "OWNER_CONFIGURED" and row["emirate"] == "AUH"
    p = tax.resolve_profile(cur, h.slug, SEP)
    assert tax.factor(p) == Decimal("1.2474")
    # Another hotel in the same emirate never sees this hotel's own rule.
    other = hotels.create_hotel(cur, slug="test-auh-2", name="AUH 2", country="AE", emirate="abu_dhabi",
                                timezone="Asia/Dubai", currency="AED")
    assert tax.resolve_profile(cur, other.slug, SEP).fee("SC") is None


def test_other_emirates_start_empty(cur):
    h = hotels.create_hotel(cur, slug="test-shj", name="SHJ", country="AE", emirate="sharjah",
                            timezone="Asia/Dubai", currency="AED", star_rating=3)
    p = tax.resolve_profile(cur, h.slug, SEP)
    assert p.fees == () and p.td_rate == 0


def test_non_uae_hotel_has_no_rules(cur, legacy_slug):
    p = tax.resolve_profile(cur, legacy_slug, SEP)
    assert p.fees == () and p.emirate is None


# ─── Listing, banner, deferral ───


def test_list_fee_rules_marks_what_applies(cur, hotel_uae):
    tax.set_override(cur, hotel_uae.slug, "dxb_2026_exempt_td", apply=True)
    rows = {r["rule_key"]: r for r in tax.list_fee_rules(cur, hotel_uae.slug, on=SEP)}
    assert rows["dxb_mf_7"]["applies_on"] is True and rows["dxb_mf_7"]["rate"] == "0.07"
    assert rows["dxb_2026_exempt_mf"]["applies_on"] is False
    assert rows["dxb_2026_exempt_td"]["applies_on"] is True and rows["dxb_2026_exempt_td"]["override"]["apply"]
    assert rows["dxb_td_hotel_5"]["applies_on"] is False  # not this hotel's category
    assert not any(k and k.startswith("auh_") for k in rows)


def test_banner_for_next_90_days(cur, hotel_uae):
    assert tax.notices(cur, hotel_uae.slug, start=date(2025, 1, 1)) == []
    lines = tax.notices(cur, hotel_uae.slug, start=date(2026, 3, 15))
    assert len(lines) == 2  # the deferral and the exemption, one line per announcement
    assert all("not yet verified" in n for n in lines)
