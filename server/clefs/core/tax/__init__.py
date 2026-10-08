"""clefs.core.tax: UAE fees and taxes (Canon: core/tax; 02 spec 2.3; 04 spec D3).

Date-effective fee rules (fee_rules, seeded for Dubai and Abu Dhabi in 0023),
per-hotel overrides (the owner toggle for ANNOUNCED_UNVERIFIED rules), the
hotel's category and stacking convention (hotel_tax_profiles), compose and
decompose for conventions A/B/C, stay composition, Tourism Dirham liability
and its remittance due date. FX rates live in fx_rates (read by
clefs.core.money, with the 3.6725 USD/AED peg seeded).

clefs.core.money.compose/decompose forward here, so their signature
(amount, room_nights, profile) is kept. See README.md for every function.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core.tax.calc import (
    apply_convention,
    coefficients,
    compose,
    decompose,
    factor,
    remittance_due_date,
    td_night_indexes,
)
from clefs.core.tax.models import (
    CONVENTIONS,
    EMIRATE_CODES,
    PROPERTY_CATEGORIES,
    TD_LABEL,
    PercentFee,
    StayLine,
    StayTaxBreakdown,
    TaxBreakdown,
    TaxProfile,
)
from clefs.core.tax.rules import (
    configure_rule,
    derive_category,
    emirate_code,
    get_tax_profile,
    list_fee_rules,
    load_context,
    resolve_profile,
    set_override,
    set_tax_profile,
)
from clefs.core.tax.stay import (
    compose_stay,
    decompose_night,
    profile_in_currency,
    td_liability,
    td_month,
    td_remittance_due_date,
)

# ─── Banner ───


def notices(cur: Any, hotel_slug: str, *, start: date, end: date | None = None) -> list[str]:
    """Banner lines for every ANNOUNCED_UNVERIFIED rule that meets [start,
    end] (default: the next 90 days from `start`), applied or not."""
    stop = end or start + timedelta(days=90)
    return load_context(cur, hotel_slug, start, stop).notices(start, stop)


__all__ = [
    "CONVENTIONS",
    "EMIRATE_CODES",
    "PROPERTY_CATEGORIES",
    "TD_LABEL",
    "PercentFee",
    "StayLine",
    "StayTaxBreakdown",
    "TaxBreakdown",
    "TaxProfile",
    "apply_convention",
    "coefficients",
    "compose",
    "compose_stay",
    "configure_rule",
    "decompose",
    "decompose_night",
    "derive_category",
    "emirate_code",
    "factor",
    "get_tax_profile",
    "list_fee_rules",
    "load_context",
    "notices",
    "profile_in_currency",
    "remittance_due_date",
    "resolve_profile",
    "set_override",
    "set_tax_profile",
    "td_liability",
    "td_month",
    "td_night_indexes",
    "td_remittance_due_date",
]
