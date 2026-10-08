"""clefs.core.tax pure maths: conventions A/B/C, goldens, TD night index,
remittance due date (02 spec 2.3). No database."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clefs.core import money
from clefs.core.tax import calc
from clefs.core.tax.models import PercentFee, TaxProfile

# ─── Fixtures ───

DUBAI = (
    PercentFee("SC", Decimal("0.10"), ("ROOM",)),
    PercentFee("MF", Decimal("0.07"), ("ROOM", "SC")),
    PercentFee("VAT", Decimal("0.05"), ("ROOM", "SC", "MF")),
)
ABU_DHABI = (
    PercentFee("SC", Decimal("0.10"), ("ROOM",)),
    PercentFee("MF", Decimal("0.04"), ("ROOM", "SC")),
    PercentFee("TF", Decimal("0.04"), ("ROOM", "SC")),
    PercentFee("VAT", Decimal("0.05"), ("ROOM", "SC", "MF", "TF")),
)


def dubai(conv: str = "A", td: str = "10") -> TaxProfile:
    return TaxProfile(convention=conv, currency="AED", fees=DUBAI, td_rate=Decimal(td), td_cap_nights=30)


# ─── Goldens (AED 500 gross, 3-star, TD 10) ───


@pytest.mark.parametrize(
    "conv, room, sc, mf, vat, fac",
    [
        ("A", "396.49", "39.65", "30.53", "23.33", "1.23585"),
        ("B", "398.86", "39.89", "27.92", "23.33", "1.2285"),
        ("C", "401.65", "40.16", "28.11", "20.08", "1.22"),
    ],
)
def test_decompose_goldens(conv, room, sc, mf, vat, fac):
    b = calc.decompose(Decimal("500"), 1, dubai(conv))
    assert (b.room, b.sc, b.mf, b.vat, b.td) == (Decimal(room), Decimal(sc), Decimal(mf), Decimal(vat), Decimal("10.00"))
    assert b.gross == Decimal("500.00")
    assert b.room + b.sc + b.mf + b.tf + b.dest + b.vat + b.td == Decimal("500.00")
    assert b.factor == Decimal(fac)


def test_convention_c_residual_goes_to_room():
    # 490 / 1.22 = 401.639...; the fees round to 88.35, so R takes 401.65.
    b = calc.decompose(Decimal("500"), 1, dubai("C"))
    assert b.room == Decimal("401.65")


def test_abu_dhabi_factors():
    for conv, fac in (("A", "1.2474"), ("B", "1.239"), ("C", "1.23")):
        assert calc.factor(TaxProfile(convention=conv, fees=ABU_DHABI)) == Decimal(fac)


def test_exemption_factor_1_155():
    exempt = (DUBAI[0], PercentFee("MF", Decimal(0), ("ROOM", "SC")), DUBAI[2])
    p = TaxProfile(convention="A", fees=exempt, td_rate=Decimal(0))
    assert calc.factor(p) == Decimal("1.155")
    b = calc.decompose(Decimal("500"), 1, p)
    assert b.room == Decimal("432.90") and b.mf == 0 and b.td == 0


def test_money_facade_delegates():
    b = money.decompose(Decimal("500"), 1, dubai("A"))
    assert b.room == Decimal("396.49")
    c = money.compose(Decimal("800"), 2, dubai("A"))
    assert (c.sc, c.mf, c.vat, c.td, c.gross) == (
        Decimal("80.00"), Decimal("61.60"), Decimal("47.08"), Decimal("20.00"), Decimal("1008.68"),
    )


# ─── Compose ───


def test_compose_roundtrip_every_convention():
    for conv in ("A", "B", "C"):
        c = calc.compose(Decimal("396.49"), 1, dubai(conv))
        d = calc.decompose(c.gross, 1, dubai(conv))
        assert d.room == c.room and d.gross == c.gross


def test_compose_45_nights_golden():
    c = calc.compose(Decimal("11250"), 30, dubai("A"))
    assert (c.sc, c.mf, c.vat, c.td, c.gross) == (
        Decimal("1125.00"), Decimal("866.25"), Decimal("662.06"), Decimal("300.00"), Decimal("14203.31"),
    )


def test_minor_units_and_unknown_convention():
    assert calc.decompose(Decimal("500"), 1, dubai("A")).minor()["room"] == 39649
    with pytest.raises(ValueError):
        calc.apply_convention(DUBAI, "Z")


def test_vat_never_on_td():
    # A base naming TD is ignored: VAT on TD is Profit Guard's T4 anomaly.
    fees = (PercentFee("VAT", Decimal("0.05"), ("ROOM", "TD")),)
    assert calc.coefficients(fees)["VAT"] == Decimal("0.05")


# ─── Tourism Dirham ───


def test_td_night_index_gaps_and_chain():
    d = [date(2026, 1, i) for i in (1, 2, 3, 5, 6)]
    assert list(calc.td_night_indexes(d).values()) == [1, 2, 3, 1, 2]
    assert calc.td_night_indexes([date(2026, 1, 1)], prior_nights=29)[date(2026, 1, 1)] == 30
    assert calc.td_liable(30, 30) and not calc.td_liable(31, 30) and calc.td_liable(99, None)


def test_remittance_due_date():
    assert calc.remittance_due_date(date(2026, 9, 1)) == date(2026, 10, 15)
    assert calc.remittance_due_date(date(2026, 12, 1)) == date(2027, 1, 15)
    assert calc.remittance_due_date(date(2026, 4, 1), 3) == date(2026, 8, 15)
    assert calc.remittance_due_date(date(2026, 11, 1), 3) == date(2027, 3, 15)
