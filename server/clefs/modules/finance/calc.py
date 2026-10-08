"""
Arithmetic shared by the pulse and the P&L summary.

Every ratio whose denominator is 0 or None is None ("n/a", 04 spec 6.1):
no division error, and no fake zero that a trend line would plot.
Minor-unit ratios round half up (Canon 2, as core.money does).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import money


def div_minor(num: int | None, den: int | float | Decimal | None) -> int | None:
    """num / den rounded half up to a whole minor unit, or None."""
    if num is None or den is None or Decimal(str(den)) == 0:
        return None
    q = Decimal(int(num)) / Decimal(str(den))
    return int(q.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def ratio(num: int | Decimal | None, den: int | Decimal | None, places: int = 4) -> Decimal | None:
    """num / den as a Decimal with `places` decimals (0.3283 = 32.83%), or None."""
    if num is None or den is None or Decimal(str(den)) == 0:
        return None
    q = Decimal(str(num)) / Decimal(str(den))
    return q.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def pct_str(r: Decimal | None, places: int = 2) -> str | None:
    """0.3283 -> "32.83" (percent points as a string), None stays None."""
    if r is None:
        return None
    return str((Decimal(r) * 100).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def money_or_none(minor: int | None, currency: str) -> str | None:
    return None if minor is None else money.money_str(int(minor), currency)


def dec_str(v: Any) -> str | None:
    return None if v is None else str(v)


# ─── Periods ───


def month_start(d: date) -> date:
    return d.replace(day=1)


def next_month(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def prev_month(d: date) -> date:
    return (d.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_end(d: date) -> date:
    return next_month(d) - timedelta(days=1)


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def iso_week_start(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def parse_month(s: str) -> date:
    """'2026-08' or '2026-08-01' -> date(2026, 8, 1); ValueError otherwise."""
    s = (s or "").strip()
    if len(s) == 7:
        s += "-01"
    return date.fromisoformat(s).replace(day=1)


# ─── JSONB ───


def _json_default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, (date,)):
        return o.isoformat()
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def jsonb(v: Any) -> Any:
    """psycopg2 Json that writes Decimals as strings (never floats) and dates as ISO."""
    import json

    from clefs.db import Json

    return Json(v, dumps=lambda o: json.dumps(o, default=_json_default))
