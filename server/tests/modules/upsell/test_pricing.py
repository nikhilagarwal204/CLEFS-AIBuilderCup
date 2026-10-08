"""Fee maths: golden values from 02 spec 2.3 and the rate sources."""

from __future__ import annotations

from datetime import date
from decimal import Decimal as D

import pytest

from clefs.modules.upsell import pricing
from clefs.modules.upsell.settings import UpsellSettings

DUBAI = (D("0.10"), D("0.07"), D("0.05"))


@pytest.mark.parametrize(
    "conv, expected",
    [("A", (39649, 3965, 3053, 2333)), ("B", (39886, 3989, 2792, 2333)), ("C", (40165, 4016, 2811, 2008))],
)
def test_decompose_golden_values(conv, expected):
    # AED 500 gross, 3-star, TD 10 -> base 490.00 (02 spec 2.3 golden table)
    r = pricing.Rates(*DUBAI, conv, "test")
    assert pricing.decompose(49000, r) == expected
    assert sum(expected) == 49000


def test_compose_and_factor():
    r = pricing.Rates(*DUBAI, "A", "test")
    assert pricing.factor(r) == D("1.23585")
    assert pricing.compose(10000, r) == (1000, 770, 589)
    assert pricing.factor(pricing.Rates(*DUBAI, "B", "t")) == D("1.2285")
    assert pricing.factor(pricing.Rates(*DUBAI, "C", "t")) == D("1.22")


def test_zero_rates_leave_price_alone():
    r = pricing.Rates(D(0), D(0), D(0), "A", "t")
    assert pricing.decompose(12345, r) == (12345, 0, 0, 0)


def test_settings_fallback_and_manual(cur, hotel_uae):
    s = UpsellSettings()
    r = pricing.resolve_rates(cur, hotel_uae, s, date(2026, 9, 30))
    assert (r.sc, r.mf, r.vat, r.convention) == (D("0.1"), D("0.07"), D("0.05"), "A")
    s2 = UpsellSettings(fee_source="manual", service_charge_pct=D("0"), convention="C")
    r2 = pricing.resolve_rates(cur, hotel_uae, s2, date(2026, 9, 30))
    assert r2.sc == 0 and r2.convention == "C" and r2.source == "settings"


def test_fee_rules_win_when_present(cur, hotel_uae):
    cur.execute(
        """
        INSERT INTO fee_rules (hotel_slug, emirate, fee_code, calc_mode, rate, effective_from, rule_status, label,
            verified_by)
        VALUES (%s, 'DXB', 'SC', 'PERCENT', 0.12, '2026-01-01', 'OWNER_CONFIGURED', 'SC 12%%', 'owner')
        """,
        (hotel_uae.slug,),
    )
    cur.execute(
        "INSERT INTO hotel_tax_profiles (hotel_slug, effective_from, property_category, convention)"
        " VALUES (%s, '2026-01-01', 'HOTEL_4', 'B')",
        (hotel_uae.slug,),
    )
    r = pricing.resolve_rates(cur, hotel_uae, UpsellSettings(), date(2026, 9, 30))
    # core/tax when installed, else this module's own fee_rules reader
    assert r.source in ("tax", "fee_rules") and r.sc == D("0.12") and r.convention == "B"
    # the hotel's own SC wins; MF and VAT come from the seeded Dubai rules
    assert r.mf == D("0.07") and r.vat == D("0.05")


def test_hotel_outside_the_uae_uses_settings(cur, legacy_slug):
    from clefs.core.hotels import find_hotel

    h = find_hotel(cur, legacy_slug)  # the seeded Indian hotel: no emirate, no fee rules
    r = pricing.resolve_rates(cur, h, UpsellSettings(vat_pct=D("0")), date(2026, 9, 30))
    assert r.source == "settings" and r.vat == 0


def test_fee_rules_reader_directly(cur, hotel_uae):
    """The fallback reader used while core/tax is absent."""
    r = pricing._fee_rule_rates(cur, hotel_uae, date(2026, 9, 30), pricing.settings_rates(UpsellSettings()))
    assert r is not None and r.source == "fee_rules"
    assert (r.sc, r.mf, r.vat, r.convention) == (D("0.1"), D("0.07"), D("0.05"), "A")


def test_announced_relief_applies_only_with_owner_toggle(cur, hotel_uae):
    cur.execute("SELECT id FROM fee_rules WHERE rule_key = 'dxb_2026_exempt_mf'")
    row = cur.fetchone()
    if row is None:
        pytest.skip("core tax seed without the 2026 MF relief row")
    on = date(2026, 9, 30)
    assert pricing.resolve_rates(cur, hotel_uae, UpsellSettings(), on).mf == D("0.07")
    cur.execute("INSERT INTO hotel_tax_overrides (hotel_slug, fee_rule_id, apply) VALUES (%s, %s, TRUE)",
                (hotel_uae.slug, row["id"]))
    assert pricing.resolve_rates(cur, hotel_uae, UpsellSettings(), on).mf == 0
