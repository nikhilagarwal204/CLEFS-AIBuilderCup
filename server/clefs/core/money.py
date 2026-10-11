"""
Money (Canon 2): BIGINT minor units + CHAR(3) currency in storage, Decimal
strings in APIs, Decimal arithmetic with ROUND_HALF_UP everywhere.

    amount = D("1234.5")                        # never float arithmetic
    fils = to_minor(amount, "AED")              # 123450
    money_str(123450, "AED")                    # "1234.50" (API field value)
    fmt_money(D("1234.5"), "AED", "en-AE")      # "AED 1,234.50" (human text)
    convert(cur, D("100"), "USD", "AED", date(2026, 10, 1))   # Decimal("367.25")

Why minor units: floats cannot represent 0.10 exactly, and NUMERIC(14,2)
columns tempt code into doing arithmetic in SQL with implicit casts. Integers
in fils add up exactly, and quantize() is the one rounding rule.

FX: the fx_rates table belongs to core/tax (the domain lane, 0020-0099). Until
it exists, or when it has no rate for the date, convert() falls back to the
fixed AED/USD peg (3.6725, Central Bank of the UAE) and refuses anything else.

Tax compose/decompose (conventions A/B/C, the Dubai fee stack) is owned by
core/tax; compose()/decompose() below delegate to it when it is installed.
"""

from __future__ import annotations

import importlib
import re
from datetime import date
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from clefs.core.errors import ClefsError, NotConfigured

# ISO 4217 minor units for currencies Clefs hotels and their OTAs use. Anything
# else defaults to 2 (true for nearly every currency). The console's money
# helpers pin the same table (client lib/format.ts MINOR_UNITS), so an amount
# is stored and shown in the same unit whatever the browser's Intl data says.
MINOR_UNITS: dict[str, int] = {
    "AED": 2, "USD": 2, "EUR": 2, "GBP": 2, "INR": 2, "SAR": 2, "QAR": 2, "CNY": 2,
    "RUB": 2, "CHF": 2, "AUD": 2, "CAD": 2, "SGD": 2, "THB": 2, "EGP": 2, "PKR": 2,
    "HKD": 2, "TWD": 2, "IDR": 2, "PHP": 2,
    "OMR": 3, "KWD": 3, "BHD": 3, "JOD": 3,
    "JPY": 0, "KRW": 0, "VND": 0,
}

# Fixed pegs: units of `quote` per 1 `base`. The AED has been pegged to the USD
# at 3.6725 since 1997, so AED<->USD never depends on a rates feed.
AED_USD_PEG = Decimal("3.6725")
PEGS: dict[tuple[str, str], Decimal] = {("USD", "AED"): AED_USD_PEG}

CCY_RE = re.compile(r"^[A-Z]{3}$")
# Symbols are ambiguous across the Gulf (several dirhams and riyals), so text
# uses the ISO code, the way hotel folios and OTA statements do.
_INDIAN_LOCALES = ("en-IN", "hi-IN", "hi")


def D(x: Any) -> Decimal:
    """Decimal from int, str, Decimal or float (floats via str, so 0.1 stays 0.1)."""
    if isinstance(x, Decimal):
        return x
    if isinstance(x, bool) or x is None:
        raise ValueError(f"not an amount: {x!r}")
    if isinstance(x, int):
        return Decimal(x)
    if isinstance(x, float):
        return Decimal(repr(x))
    try:
        return Decimal(str(x).strip().replace(",", ""))
    except InvalidOperation as exc:
        raise ValueError(f"not an amount: {x!r}") from exc


def ccy(code: str | None) -> str:
    """Normalized ISO currency code ('aed ' -> 'AED'); 400 on garbage."""
    c = (code or "").strip().upper()
    if not CCY_RE.match(c):
        raise ClefsError(400, f"currency must be an ISO 4217 code, got {code!r}", "invalid_currency")
    return c


def minor_units(currency: str) -> int:
    return MINOR_UNITS.get(ccy(currency), 2)


def quantize(a: Any, currency: str) -> Decimal:
    """Round to the currency's minor unit, half up (the only rounding rule)."""
    exp = Decimal(1).scaleb(-minor_units(currency))
    return D(a).quantize(exp, rounding=ROUND_HALF_UP)


def to_minor(a: Any, currency: str) -> int:
    """Decimal major units -> integer minor units (AED 12.345 -> 1235 fils)."""
    return int(quantize(a, currency).scaleb(minor_units(currency)))


def from_minor(minor: int | None, currency: str) -> Decimal | None:
    if minor is None:
        return None
    return Decimal(int(minor)).scaleb(-minor_units(currency)).quantize(
        Decimal(1).scaleb(-minor_units(currency))
    )


def money_str(minor: int | None, currency: str | None) -> str | None:
    """The API representation: a Decimal string in major units, or None."""
    if minor is None or not currency:
        return None
    return str(from_minor(minor, currency))


def _group(digits: str, indian: bool) -> str:
    if len(digits) <= 3:
        return digits
    if not indian:
        return f"{int(digits):,}"
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def fmt_money(a: Any, currency: str, locale: str | None = "en-AE", *, minor: bool = False) -> str:
    """Human text: "AED 1,234.50", "-AED 12.00", Indian grouping for en-IN
    ("INR 1,23,456.00"). `minor=True` means `a` is in minor units."""
    code = ccy(currency)
    amount = from_minor(int(a), code) if minor else quantize(a, code)
    assert amount is not None
    sign = "-" if amount < 0 else ""
    text = f"{abs(amount):f}"
    whole, _, frac = text.partition(".")
    indian = (locale or "").startswith(_INDIAN_LOCALES)
    body = _group(whole, indian) + (f".{frac}" if frac else "")
    return f"{sign}{code} {body}"


def allocate(total_minor: int, weights: list[Any]) -> list[int]:
    """Split an integer amount by weights so the parts add up exactly (largest
    remainder; ties go to the earliest part). Used when one gross amount is
    spread over nights or lines."""
    if not weights:
        return []
    ws = [D(w) for w in weights]
    s = sum(ws)
    if s == 0:
        raise ValueError("weights sum to zero")
    raw = [Decimal(total_minor) * w / s for w in ws]
    floors = [int(r.to_integral_value(rounding=ROUND_FLOOR)) for r in raw]
    left = total_minor - sum(floors)
    order = sorted(range(len(raw)), key=lambda i: (-(raw[i] - floors[i]), i))
    for i in order[: abs(left)]:
        floors[i] += 1 if left > 0 else -1
    return floors


# ─── FX ───


def _fx_table_rate(cur: Any, base: str, quote: str, on: date) -> Decimal | None:
    """Latest fx_rates row on or before `on`, looking both directions. Runs in
    a SAVEPOINT: the table is owned by another lane, and a failed probe must
    never abort the caller's transaction."""
    cur.execute("SELECT to_regclass('public.fx_rates') IS NOT NULL AS present")
    row = cur.fetchone()
    if not row or not row["present"]:
        return None
    cur.execute("SAVEPOINT clefs_fx_probe")
    try:
        cur.execute(
            """
            SELECT base, quote, rate FROM fx_rates
            WHERE ((base = %(b)s AND quote = %(q)s) OR (base = %(q)s AND quote = %(b)s)) AND date <= %(on)s
            ORDER BY date DESC LIMIT 1
            """,
            {"b": base, "q": quote, "on": on},
        )
        hit = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT clefs_fx_probe")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT clefs_fx_probe")
        cur.execute("RELEASE SAVEPOINT clefs_fx_probe")
        return None
    if hit is None:
        return None
    rate = D(hit["rate"])
    if rate == 0:
        return None
    return rate if hit["base"].strip() == base else Decimal(1) / rate


def fx_rate(cur: Any, base: str, quote: str, on: date) -> Decimal | None:
    """Units of `quote` per 1 `base` on `on`: the fx_rates table, else a peg."""
    b, q = ccy(base), ccy(quote)
    if b == q:
        return Decimal(1)
    if cur is not None:
        rate = _fx_table_rate(cur, b, q, on)
        if rate is not None:
            return rate
    if (b, q) in PEGS:
        return PEGS[(b, q)]
    if (q, b) in PEGS:
        return Decimal(1) / PEGS[(q, b)]
    return None


def convert(cur: Any, a: Any, src: str, dst: str, on: date) -> Decimal:
    """Convert and quantize to `dst`. 409 fx_unavailable when no rate exists."""
    rate = fx_rate(cur, src, dst, on)
    if rate is None:
        raise ClefsError(409, f"No {ccy(src)}->{ccy(dst)} rate for {on.isoformat()}", "fx_unavailable")
    return quantize(D(a) * rate, dst)


def convert_minor(cur: Any, minor: int, src: str, dst: str, on: date) -> int:
    converted = convert(cur, from_minor(minor, src), src, dst, on)
    return to_minor(converted, dst)


# ─── Tax (owned by core/tax; delegated) ───
# clefs.core.tax implements date-effective fee rules and compose/decompose
# for stacking conventions A/B/C with the golden values (AED 500 gross,
# 3-star: A = 396.49 / 39.65 / 30.53 / 23.33); see core/tax/README.md. These
# two functions are the stable entry points the foundation spec (E7)
# promised; they forward to clefs.core.tax so callers never change imports.


def _tax() -> Any:
    try:
        return importlib.import_module("clefs.core.tax")
    except ModuleNotFoundError as exc:
        raise NotConfigured("Tax composition (core/tax)") from exc


def compose(net_room: Any, room_nights: int, profile: Any) -> Any:
    """Net room revenue -> TaxBreakdown (delegates to core.tax.compose)."""
    fn = getattr(_tax(), "compose", None)
    if fn is None:
        raise NotConfigured("Tax composition (core/tax)")
    return fn(net_room, room_nights, profile)


def decompose(gross: Any, room_nights: int, profile: Any) -> Any:
    """Gross guest-paid -> TaxBreakdown (delegates to core.tax.decompose)."""
    fn = getattr(_tax(), "decompose", None)
    if fn is None:
        raise NotConfigured("Tax decomposition (core/tax)")
    return fn(gross, room_nights, profile)
