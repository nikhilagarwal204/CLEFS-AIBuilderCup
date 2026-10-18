"""
F&B pricing: service charge, municipality fee and VAT for a basket, in
integer minor units, for both menu price displays (03 spec 2.4 place_order).

    inclusive   the menu price is what the guest pays; fees are split OUT of it
                (decompose), so total == subtotal.
    plus_plus   the menu price is net; fees are added ON TOP (compose).

Stacking conventions (02 spec 2.3; Canon: Convention A compound, 1.23585):
    A  SC = s*R, MF = m*(R+SC), VAT = v*(R+SC+MF)
    B  SC = s*R, MF = m*R,      VAT = v*(R+SC+MF)
    C  SC = s*R, MF = m*R,      VAT = v*R
Decomposition rounds R, composes the fees from it and gives the residual
minor unit to R, which reproduces the spec's golden values (AED 490.00:
A 396.49/39.65/30.53/23.33, C 401.65/40.16/28.11/20.08).

Rates come from core/tax when it is installed, else the core fee_rules
table for the hotel's emirate, else the module settings (the Dubai stack).
Fees are computed once on the basket, not per line, so the invoice adds up
the way a POS folio does.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.modules.upsell.settings import UpsellSettings

log = logging.getLogger("clefs.upsell.pricing")

# hotels.emirate (free text, lowercase) -> fee_rules.emirate code.
EMIRATE_CODES = {
    "dubai": "DXB", "dxb": "DXB",
    "abu_dhabi": "AUH", "abu dhabi": "AUH", "abudhabi": "AUH", "auh": "AUH",
    "sharjah": "SHJ", "shj": "SHJ",
    "ajman": "AJM", "ajm": "AJM",
    "ras_al_khaimah": "RAK", "ras al khaimah": "RAK", "rak": "RAK",
    "fujairah": "FUJ", "fuj": "FUJ",
    "umm_al_quwain": "UAQ", "umm al quwain": "UAQ", "uaq": "UAQ",
}


# ─── Rates ───


@dataclass(frozen=True)
class Rates:
    sc: Decimal        # fractions: 0.10 = 10%
    mf: Decimal
    vat: Decimal
    convention: str    # A | B | C
    source: str        # tax | fee_rules | settings

    def as_json(self) -> dict:
        return {"sc": str(self.sc), "mf": str(self.mf), "vat": str(self.vat),
                "convention": self.convention, "source": self.source}


def _pct(p: Decimal) -> Decimal:
    return (Decimal(p) / Decimal(100)).normalize()


def settings_rates(s: UpsellSettings) -> Rates:
    return Rates(_pct(s.service_charge_pct), _pct(s.municipality_fee_pct), _pct(s.vat_pct), s.convention, "settings")


def resolve_rates(cur: Any, hotel: Any, s: UpsellSettings, on: date) -> Rates:
    """The fee rates for an order on `on`. Never raises: pricing must work on
    a hotel whose tax setup is incomplete (the settings are the floor)."""
    fallback = settings_rates(s)
    if s.fee_source == "manual":
        return fallback
    tax = _core_tax_rates(cur, hotel, on, fallback)
    if tax is not None:
        return tax
    rules = _fee_rule_rates(cur, hotel, on, fallback)
    return rules if rules is not None else fallback


def _core_tax_rates(cur: Any, hotel: Any, on: date, fallback: Rates) -> Rates | None:
    """core/tax (the domain lane's package), when installed: its
    resolve_profile(cur, hotel_slug, on) already applies date-effective
    rules, owner overrides and the hotel's convention. A dedicated
    fnb_rates(cur, hotel_slug, on) -> {sc, mf, vat, convention} wins if the
    package ever grows one. Imported lazily (CONTRACTS: core domain schema),
    and any failure falls through to the next source."""
    try:
        tax = importlib.import_module("clefs.core.tax")
    except ImportError:
        return None
    try:
        fnb = getattr(tax, "fnb_rates", None)
        if callable(fnb):
            r = fnb(cur, hotel.slug, on)
            if r:
                return Rates(Decimal(str(r["sc"])), Decimal(str(r["mf"])), Decimal(str(r["vat"])),
                             str(r.get("convention") or fallback.convention), "tax")
        resolve = getattr(tax, "resolve_profile", None)
        if not callable(resolve):
            return None
        cur.execute("SAVEPOINT ups_core_tax")
        try:
            profile = resolve(cur, hotel.slug, on)
            cur.execute("RELEASE SAVEPOINT ups_core_tax")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT ups_core_tax")
            cur.execute("RELEASE SAVEPOINT ups_core_tax")
            raise
    except Exception:  # a tax bug must not stop a guest ordering breakfast
        log.exception("core.tax rates failed for %s", hotel.slug)
        return None
    fees = {f.code: Decimal(f.rate) for f in (getattr(profile, "fees", None) or ())}
    if not fees:
        return None
    # Service charge is hotel policy (Abu Dhabi leaves it owner-configured):
    # no rule means the hotel's own setting. MF, a tourism fee and a
    # destination fee are all percentage levies on the same base, so they
    # share the MF slot. VAT is federal: no rule means the settings rate.
    sc = fees.get("SC", fallback.sc)
    mf = fees.get("MF", Decimal(0)) + fees.get("TF", Decimal(0)) + fees.get("DEST", Decimal(0))
    vat = fees.get("VAT", fallback.vat)
    conv = str(getattr(profile, "convention", None) or fallback.convention).strip()
    return Rates(sc.normalize(), mf.normalize(), vat.normalize(), conv, "tax")


def _fee_rule_rates(cur: Any, hotel: Any, on: date, fallback: Rates) -> Rates | None:
    """PERCENT rules from core fee_rules (0022) for the hotel's emirate, a
    hotel's own OWNER_CONFIGURED row winning over the emirate row, the newest
    effective rule winning per fee. An ANNOUNCED_UNVERIFIED rule (the 2026
    Dubai relief) applies only with the owner's override apply=TRUE, and an
    override apply=FALSE switches a rule off (Canon: date-effective rules and
    an owner toggle). Runs in a savepoint: the tables are another lane's, and
    a failed probe must not abort the caller's order."""
    code = EMIRATE_CODES.get((getattr(hotel, "emirate", None) or "").strip().lower())
    if code is None:
        return None
    cur.execute("SELECT to_regclass('public.fee_rules') IS NOT NULL AS ok")
    if not cur.fetchone()["ok"]:
        return None
    cur.execute("SAVEPOINT ups_fee_rules")
    try:
        cur.execute(
            """
            SELECT r.fee_code, r.rate, r.hotel_slug FROM fee_rules r
            LEFT JOIN hotel_tax_overrides o ON o.fee_rule_id = r.id AND o.hotel_slug = %(h)s
            WHERE r.calc_mode = 'PERCENT' AND r.fee_code IN ('SC', 'MF', 'TF', 'VAT')
              AND r.emirate = %(e)s AND (r.hotel_slug IS NULL OR r.hotel_slug = %(h)s)
              AND COALESCE(o.effective_from, r.effective_from) <= %(on)s
              AND (COALESCE(o.effective_to, r.effective_to) IS NULL OR COALESCE(o.effective_to, r.effective_to) >= %(on)s)
              AND CASE WHEN r.rule_status = 'ANNOUNCED_UNVERIFIED' THEN COALESCE(o.apply, FALSE)
                       ELSE COALESCE(o.apply, TRUE) END
            ORDER BY (r.hotel_slug IS NULL), COALESCE(o.effective_from, r.effective_from) DESC
            """,
            {"e": code, "h": hotel.slug, "on": on},
        )
        rows = cur.fetchall()
        cur.execute(
            """
            SELECT convention FROM hotel_tax_profiles
            WHERE hotel_slug = %s AND effective_from <= %s ORDER BY effective_from DESC LIMIT 1
            """,
            (hotel.slug, on),
        )
        prof = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT ups_fee_rules")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT ups_fee_rules")
        cur.execute("RELEASE SAVEPOINT ups_fee_rules")
        return None
    if not rows:
        return None
    first: dict[str, Decimal] = {}
    for r in rows:  # hotel rows first, then the newest emirate row per code
        first.setdefault(r["fee_code"], Decimal(r["rate"]))
    # Service charge is hotel policy (Abu Dhabi leaves it OWNER_CONFIGURED):
    # no rule means the hotel's own setting. MF and a tourism fee (TF) are
    # both percentage levies on the same base, so they share the MF slot.
    sc = first.get("SC", fallback.sc)
    mf = first.get("MF", Decimal(0)) + first.get("TF", Decimal(0))
    vat = first.get("VAT", fallback.vat)
    conv = (prof or {}).get("convention") or fallback.convention
    return Rates(sc.normalize(), mf.normalize(), vat.normalize(), str(conv).strip(), "fee_rules")


# ─── Compose / decompose (integer minor units) ───


def _r(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def compose(net_minor: int, rates: Rates) -> tuple[int, int, int]:
    """Net -> (service charge, municipality fee, VAT), each rounded half up."""
    n = Decimal(net_minor)
    sc = _r(n * rates.sc)
    if rates.convention == "A":
        mf = _r((n + sc) * rates.mf)
    else:
        mf = _r(n * rates.mf)
    if rates.convention == "C":
        vat = _r(n * rates.vat)
    else:
        vat = _r((n + sc + mf) * rates.vat)
    return sc, mf, vat


def factor(rates: Rates) -> Decimal:
    s, m, v = rates.sc, rates.mf, rates.vat
    if rates.convention == "A":
        return (1 + s) * (1 + m) * (1 + v)
    if rates.convention == "B":
        return (1 + s + m) * (1 + v)
    return 1 + s + m + v


def decompose(gross_minor: int, rates: Rates) -> tuple[int, int, int, int]:
    """Gross -> (net, SC, MF, VAT) summing exactly to gross (residual to net)."""
    net = _r(Decimal(gross_minor) / factor(rates))
    sc, mf, vat = compose(net, rates)
    net += gross_minor - (net + sc + mf + vat)
    return net, sc, mf, vat
