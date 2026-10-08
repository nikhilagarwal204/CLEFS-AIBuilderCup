"""Pure tax maths: stacking conventions A/B/C, compose, decompose, the
Tourism Dirham night index and the remittance due date. No database here.

Why a coefficient model: every percentage fee is a rate times a sum of
components that come before it, so each component is k_c x R for a constant
k_c. The gross-to-R factor is then sum(k) and decomposition is one division
(uae-taxes-fees.md section 4: Dubai A 1.23585, B 1.2285, C 1.22).

Rounding: each fee is rounded once (ROUND_HALF_UP, to the currency's minor
units) from the exact value; decompose gives the residual to R so the parts
always sum to the gross (golden C: R 401.65, not 401.64).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Iterable

from clefs.core.money import D, quantize
from clefs.core.tax.models import PERCENT_ORDER, PercentFee, TaxBreakdown, TaxProfile

# ─── Conventions ───


def apply_convention(fees: Iterable[PercentFee], convention: str) -> tuple[PercentFee, ...]:
    """The hotel's convention overrides each rule's base[] (02 spec 2.3):
    A keeps the rule bases (compound), B puts MF/TF/DEST on [ROOM] only, and C
    puts every fee, VAT included, on [ROOM] (additive)."""
    conv = (convention or "A").upper()
    if conv not in ("A", "B", "C"):
        raise ValueError(f"unknown stacking convention {convention!r}")
    out: list[PercentFee] = []
    for f in fees:
        base = tuple(f.base) or ("ROOM",)
        if conv == "C" or (conv == "B" and f.code in ("MF", "TF", "DEST")):
            base = ("ROOM",)
        out.append(PercentFee(f.code, D(f.rate), base, f.rule_id, f.rule_status))
    return tuple(out)


def coefficients(fees: Iterable[PercentFee]) -> dict[str, Decimal]:
    """k_c per component (ROOM = 1). A base naming a later component (or TD,
    which is outside every percentage base) is ignored: VAT on TD is the T4
    anomaly Profit Guard detects, never something core computes."""
    by_code = {f.code: f for f in fees}
    k: dict[str, Decimal] = {"ROOM": Decimal(1)}
    for code in PERCENT_ORDER:
        fee = by_code.get(code)
        if fee is None:
            k[code] = Decimal(0)
            continue
        k[code] = D(fee.rate) * sum((k[b] for b in fee.base if b in k), Decimal(0))
    return k


def factor(profile: TaxProfile) -> Decimal:
    """Gross ex-TD per 1 of R under the profile's convention."""
    return sum(coefficients(apply_convention(profile.fees, profile.convention)).values(), Decimal(0))


# ─── Compose / decompose ───


def _breakdown(room: Decimal, parts: dict[str, Decimal], td: Decimal, profile: TaxProfile, f: Decimal) -> TaxBreakdown:
    gross = room + sum(parts.values(), Decimal(0)) + td
    return TaxBreakdown(
        room=room,
        sc=parts["SC"],
        mf=parts["MF"],
        tf=parts["TF"],
        dest=parts["DEST"],
        vat=parts["VAT"],
        td=td,
        gross=gross,
        currency=profile.currency,
        convention=profile.convention,
        factor=f,
    )


def td_amount(profile: TaxProfile, unit_nights: int) -> Decimal:
    """TD for `unit_nights` liable unit-nights (the caller applies the cap
    and the stayed-nights rule; see td_night_indexes)."""
    return quantize(D(profile.td_rate) * int(unit_nights or 0), profile.currency)


def split_room(room_exact: Any, profile: TaxProfile) -> tuple[dict[str, Decimal], Decimal]:
    """Exact (unrounded) fee amounts for an exact R, plus the factor."""
    k = coefficients(apply_convention(profile.fees, profile.convention))
    r = D(room_exact)
    return {c: k[c] * r for c in PERCENT_ORDER}, sum(k.values(), Decimal(0))


def compose(net_room: Any, room_nights: int, profile: TaxProfile) -> TaxBreakdown:
    """Net room revenue R (major units) -> the full guest price. room_nights
    is the count of TD-liable unit-nights (0 for none)."""
    ccy = profile.currency
    room = quantize(D(net_room), ccy)
    exact, f = split_room(room, profile)
    parts = {c: quantize(v, ccy) for c, v in exact.items()}
    return _breakdown(room, parts, td_amount(profile, room_nights), profile, f)


def decompose(gross: Any, room_nights: int, profile: TaxProfile) -> TaxBreakdown:
    """Guest-paid gross (major units, TD included) -> R and each fee. TD is
    stripped first because it is fixed and outside VAT; the rest is divided
    by the convention's factor; fees round once and R takes the residual."""
    ccy = profile.currency
    g = quantize(D(gross), ccy)
    td = td_amount(profile, room_nights)
    base = g - td
    k = coefficients(apply_convention(profile.fees, profile.convention))
    f = sum(k.values(), Decimal(0))
    room_exact = base / f
    parts = {c: quantize(k[c] * room_exact, ccy) for c in PERCENT_ORDER}
    room = base - sum(parts.values(), Decimal(0))
    return _breakdown(room, parts, td, profile, f)


# ─── Tourism Dirham ───


def td_night_indexes(dates: Iterable[date], *, prior_nights: int = 0) -> dict[date, int]:
    """1-based consecutive-night index per date. A gap of a day or more
    restarts the count (AR 2/2020: the cap is 30 *consecutive* nights);
    prior_nights continues a chain whose earlier nights are not in `dates`
    (a back-to-back reservation for the same guest and room)."""
    out: dict[date, int] = {}
    prev: date | None = None
    idx = int(prior_nights or 0)
    for d in sorted(set(dates)):
        if prev is not None and (d - prev).days != 1:
            idx = 0
        idx += 1
        out[d] = idx
        prev = d
    return out


def td_liable(index: int, cap: int | None) -> bool:
    return cap is None or index <= cap


def remittance_due_date(month: date, deferral_months: int = 0) -> date:
    """TD collected in `month` is due by the 15th of the following month
    (AR 2/2020: "before the 16th"), plus any applied deferral months."""
    total = month.year * 12 + (month.month - 1) + 1 + int(deferral_months or 0)
    return date(total // 12, total % 12 + 1, 15)
