"""Value types of clefs.core.tax (Canon: core/tax; 02 spec 2.3; 04 spec D3).

Everything here is a plain frozen dataclass so the pure maths in calc.py can
be tested without a database, and so other lanes can build inputs (StayLine)
without importing any DB code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

# ─── Codes ───

# Percentage fees, in the order they are computed. A fee's base may only name
# components that come before it (ROOM first, VAT last), which keeps every
# component a linear multiple of R and the decomposition closed-form.
PERCENT_ORDER: tuple[str, ...] = ("SC", "MF", "TF", "DEST", "VAT")
COMPONENTS: tuple[str, ...] = ("ROOM",) + PERCENT_ORDER
CONVENTIONS: tuple[str, ...] = ("A", "B", "C")

# hotels.emirate (0003) uses words; fee_rules.emirate (0022) uses codes.
EMIRATE_CODES: dict[str, str] = {
    "dubai": "DXB",
    "abu_dhabi": "AUH",
    "sharjah": "SHJ",
    "ajman": "AJM",
    "rak": "RAK",
    "fujairah": "FUJ",
    "uaq": "UAQ",
}

# Property categories of Dubai's Tourism Dirham schedule (ECR 2/2014,
# Schedule 1). fee_rules.property_category holds one of these, or '*'.
PROPERTY_CATEGORIES: tuple[str, ...] = (
    "hotel_5",
    "hotel_4",
    "hotel_3",
    "hotel_2",
    "hotel_1",
    "apartment_luxury",
    "apartment_deluxe",
    "apartment_standard",
    "guest_house",
    "holiday_home_luxury",
    "holiday_home_standard",
)

TD_LABEL = "Tourism Dirham fee"  # a fee, never "tax" (AR 2/2020 disclosure rule)


# ─── Rates for one date ───


@dataclass(frozen=True)
class PercentFee:
    """One percentage fee after the hotel's convention has been applied to
    its base (B forces MF/TF/DEST to [ROOM]; C forces every base to [ROOM])."""

    code: str
    rate: Decimal
    base: tuple[str, ...]
    rule_id: int | None = None
    rule_status: str | None = None


@dataclass(frozen=True)
class TaxProfile:
    """Everything decompose/compose need for one stay date: the resolved
    percentage fees, the Tourism Dirham rate per unit-night and its rules.
    Built by rules.resolve_profile (DB) or by hand in tests."""

    convention: str = "A"
    currency: str = "AED"
    fees: tuple[PercentFee, ...] = ()
    td_rate: Decimal = Decimal(0)  # major units per unit-night
    td_cap_nights: int | None = None
    td_per_bedroom: bool = False
    td_rule_id: int | None = None
    td_rule_status: str | None = None
    emirate: str | None = None
    property_category: str | None = None
    on: date | None = None
    notices: tuple[str, ...] = ()

    def fee(self, code: str) -> PercentFee | None:
        return next((f for f in self.fees if f.code == code), None)


# ─── Results ───


@dataclass(frozen=True)
class TaxBreakdown:
    """Major-unit Decimals quantized to the currency. room + sc + mf + tf +
    dest + vat + td == gross exactly (decompose puts the rounding residual
    into room; compose derives gross from the rounded parts)."""

    room: Decimal
    sc: Decimal
    mf: Decimal
    tf: Decimal
    dest: Decimal
    vat: Decimal
    td: Decimal
    gross: Decimal
    currency: str
    convention: str
    factor: Decimal

    def minor(self) -> dict[str, int]:
        from clefs.core.money import to_minor

        keys = ("room", "sc", "mf", "tf", "dest", "vat", "td", "gross")
        return {k: to_minor(getattr(self, k), self.currency) for k in keys}

    def as_dict(self) -> dict[str, Any]:
        d = {k: str(getattr(self, k)) for k in ("room", "sc", "mf", "tf", "dest", "vat", "td", "gross")}
        # NUMERIC(18,8) rates leave trailing zeros in the product: 1.23585, not 1.235850000...
        return d | {"factor": str(self.factor.normalize()), "currency": self.currency, "convention": self.convention}


@dataclass(frozen=True)
class StayLine:
    """04 spec D3: one stay date and room type. rate_minor is R per room per
    night. bedrooms overrides room_types.bedrooms (tests, quotes for a room
    type the hotel has not registered yet)."""

    stay_date: date
    room_type_code: str | None
    rooms: int
    rate_minor: int
    bedrooms: int | None = None


@dataclass(frozen=True)
class StayTaxBreakdown:
    """04 spec D3. Amounts are minor units of `currency`, each component
    rounded once over the whole stay. `total` = room + every fee."""

    room: int
    sc: int
    mf: int
    vat: int
    td: int
    td_nights_charged: int
    td_units: int
    currency: str
    convention: str
    tf: int = 0
    dest: int = 0
    total: int = 0
    fee_rules: list[dict] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    td_label: str = TD_LABEL

    def as_dict(self) -> dict[str, Any]:
        return {
            "room": self.room,
            "sc": self.sc,
            "mf": self.mf,
            "tf": self.tf,
            "dest": self.dest,
            "vat": self.vat,
            "td": self.td,
            "total": self.total,
            "td_nights_charged": self.td_nights_charged,
            "td_units": self.td_units,
            "td_label": self.td_label,
            "currency": self.currency,
            "convention": self.convention,
            "fee_rules": list(self.fee_rules),
            "notices": list(self.notices),
        }
