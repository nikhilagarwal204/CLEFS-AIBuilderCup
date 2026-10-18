"""Raw cell -> canonical value, per field type and the mapping's transforms
(02 spec 2.2 step 4: date_fmt, decimal_sep, sign_flip, pct_to_ratio).

Canonical values are JSON-safe: dates are ISO strings, instants ISO strings
(naive ones are hotel-local; core/stays reads them that way), money is an
int in minor units of the row's currency, ratios are Decimal strings.
Every function raises ValueError with a message a person can act on; the
caller turns it into an ERROR row, never a failed batch.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from dateutil import parser as dateparser

from clefs.core import money

# Excel's day zero (1900 system, with its phantom 29 Feb 1900).
EXCEL_EPOCH = datetime(1899, 12, 30)
# Serial numbers from 1990 to 2100: a bare int in a date column in this
# range is an Excel serial that lost its format on the way to CSV.
EXCEL_SERIAL_RANGE = (32874, 73051)
CURRENCY_SYMBOLS = {
    "د.إ": "AED", "dhs": "AED", "dh": "AED", "aed": "AED", "$": "USD", "us$": "USD", "usd": "USD",
    "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP", "₹": "INR", "rs": "INR", "inr": "INR",
    "sar": "SAR", "ر.س": "SAR", "omr": "OMR", "qar": "QAR", "bhd": "BHD", "kwd": "KWD",
}
KNOWN_CODES = frozenset(set(CURRENCY_SYMBOLS.values()) | set(money.MINOR_UNITS) | {"CHF", "CNY", "JPY", "SGD", "THB"})
_TOKEN_FMT =(("YYYY", "%Y"), ("YY", "%y"), ("MMM", "%b"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("mm", "%M"))


# ─── Numbers ───


# A currency token around a number: letters or symbols ("AED", "US$", "€",
# "د.إ"), each chunk optionally closed by an abbreviation dot ("Dhs.",
# "Rs."). The class leaves out digits, separators, signs and parentheses, so
# stripping it never eats part of the number: before this, the dot of
# "Dhs. 1,200" stayed in the body and read as a European decimal comma
# (1.200, a thousandfold understatement), and the "-" of "AED -30.00"
# was dropped with the letters (a refund imported as revenue).
_CCY_LEAD = re.compile(r"^(?:[^\d\s()+\-−.,]+\.?\s*)+")
_CCY_TRAIL = re.compile(r"(?:\s*[^\d\s()+\-−.,%]+\.?)+$")
# Grouping marks that may sit inside a number: spaces (plain, no-break,
# narrow no-break) and the Swiss apostrophes.
_GROUPING = re.compile(r"[\s'’  ]")


def _strip_currency(s: str) -> str:
    return _CCY_TRAIL.sub("", _CCY_LEAD.sub("", s)).strip()


def _sign_and_body(s: str) -> tuple[bool, str]:
    """(negative, bare number) for a money-ish cell. The sign may sit outside
    or inside the currency ("-AED 30", "AED -30", "$-50", "(AED 12.00)",
    "AED (12.00)", "30.00-"); a "%" stays for parse_ratio to see."""
    neg = False
    s = _strip_currency(s)
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, _strip_currency(s[1:-1])
    if s[:1] in ("-", "−"):
        neg, s = True, _strip_currency(s[1:])
    elif s[:1] == "+":
        s = _strip_currency(s[1:])
    if s.endswith("-") and not neg:
        neg, s = True, s[:-1].strip()
    return neg, _GROUPING.sub("", s).rstrip("%")


def parse_decimal(v: Any, *, decimal_sep: str | None = None) -> Decimal:
    """"1,234.50", "1.234,50", "(12.00)", "AED 30", "Dhs. 1,200", "AED -30",
    "$-50", "-5", "5-" and Excel floats. With no decimal_sep, a lone comma
    followed by exactly three digits is a thousands separator, otherwise a
    decimal one. Anything other than digits and separators left after the
    currency and sign are gone is refused rather than silently dropped."""
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, (int, Decimal)):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(repr(v))
    s = str(v).strip()
    if not s:
        raise ValueError("empty")
    neg, body = _sign_and_body(s)
    if not re.search(r"\d", body) or re.search(r"[^\d.,]", body):
        raise ValueError(f"{s!r} is not a number")
    if decimal_sep == ",":
        body = body.replace(".", "").replace(",", ".")
    elif decimal_sep == ".":
        body = body.replace(",", "")
    elif "," in body and "." in body:
        body = body.replace(",", "") if body.rfind(".") > body.rfind(",") else body.replace(".", "").replace(",", ".")
    elif "," in body:
        body = body.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", body) else body.replace(",", ".")
    try:
        d = Decimal(body)
    except InvalidOperation as exc:
        raise ValueError(f"{s!r} is not a number") from exc
    return -d if neg else d


def currency_in(v: Any) -> str | None:
    """A currency named inside a money cell ("AED 1,200", "$50")."""
    if not isinstance(v, str):
        return None
    m = re.search(r"(?<![A-Za-z])([A-Z]{3})(?![A-Za-z])", v)
    if m and m.group(1) in KNOWN_CODES:
        return m.group(1)
    low = v.lower()
    return next((c for sym, c in CURRENCY_SYMBOLS.items() if sym in low), None)


def parse_currency(v: Any) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    code = CURRENCY_SYMBOLS.get(s.lower())
    if code is None:
        raise ValueError(f"{s!r} is not a currency code")
    return code


def parse_money(v: Any, currency: str, *, decimal_sep: str | None = None, sign_flip: bool = False) -> int:
    d = parse_decimal(v, decimal_sep=decimal_sep)
    if sign_flip:
        d = -d
    return money.to_minor(d, currency)


def parse_ratio(v: Any, *, pct_to_ratio: bool | None = None, decimal_sep: str | None = None) -> str:
    """A percentage as a ratio string ("15%" and "15" -> "0.15"). With no
    transform, a value above 1 or carrying "%" is a percentage; a transform
    of True divides even small values ("0.5" meaning half a percent), False
    keeps the value as a ratio."""
    d = parse_decimal(v, decimal_sep=decimal_sep)
    is_pct = pct_to_ratio if pct_to_ratio is not None else ("%" in str(v) or abs(d) > 1)
    if is_pct:
        d = d / 100
    return str(d.normalize()) if d != 0 else "0"


# ─── Dates ───


def to_strftime(fmt: str) -> str:
    """"DD/MM/YYYY" style tokens -> strftime; a strftime string passes."""
    if "%" in fmt:
        return fmt
    out = fmt
    for tok, rep in _TOKEN_FMT:
        out = out.replace(tok, rep)
    return out


def _dayfirst(date_fmt: str | None) -> bool:
    """UAE and Indian exports write day first; "monthfirst" (or an MM/DD
    format) says otherwise."""
    if not date_fmt:
        return True
    f = date_fmt.lower()
    if f == "monthfirst" or f.startswith(("mm/dd", "mm-dd", "%m/%d", "%m-%d", "mmm")):
        return False
    return True


def parse_datetime(v: Any, *, date_fmt: str | None = None) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, (int, float, Decimal)) and not isinstance(v, bool):
        n = float(v)
        if EXCEL_SERIAL_RANGE[0] <= n <= EXCEL_SERIAL_RANGE[1]:
            return EXCEL_EPOCH + timedelta(days=n)
        raise ValueError(f"{v!r} is not a date")
    s = str(v).strip()
    if not s:
        raise ValueError("empty")
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        return parse_datetime(float(s))
    if date_fmt and date_fmt.lower() not in ("dayfirst", "monthfirst"):
        try:
            return datetime.strptime(s, to_strftime(date_fmt))
        except ValueError:
            pass  # a cell in another layout still gets the loose reader
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}([ T].*)?", s):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            pass
    try:
        return dateparser.parse(s, dayfirst=_dayfirst(date_fmt))
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{s!r} is not a date") from exc


def parse_date(v: Any, *, date_fmt: str | None = None) -> date:
    return parse_datetime(v, date_fmt=date_fmt).date()


def parse_month(v: Any, *, date_fmt: str | None = None) -> date:
    """The first of the month: "2025-10", "Oct 2025", "October-25", a date."""
    if isinstance(v, str):
        s = v.strip()
        m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", s)
        if m:
            return date(int(m.group(1)), int(m.group(2)), 1)
        m = re.fullmatch(r"([A-Za-z]{3,9})[-\s/']*(\d{2}|\d{4})", s)
        if m:
            year = int(m.group(2)) + (2000 if len(m.group(2)) == 2 else 0)
            d = dateparser.parse(f"1 {m.group(1)} {year}", dayfirst=True)
            return date(d.year, d.month, 1)
    d = parse_date(v, date_fmt=date_fmt)
    return date(d.year, d.month, 1)


# ─── Per-type coercion ───


def parse_int(v: Any, *, decimal_sep: str | None = None) -> int:
    d = parse_decimal(v, decimal_sep=decimal_sep)
    if d != d.to_integral_value():
        raise ValueError(f"{v!r} is not a whole number")
    return int(d)


def as_text(v: Any) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))  # Excel stores reservation numbers as floats
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return str(v).strip()


def coerce(value: Any, typ: str, *, currency: str, transforms: dict | None = None,
           status_map: dict[str, str] | None = None) -> tuple[Any, str | None]:
    """(canonical value, warning or None) for one cell. None and blank cells
    give None. Raises ValueError when the value cannot be read at all."""
    t = transforms or {}
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, None
    if typ == "text":
        return as_text(value), None
    if typ == "contact":
        return as_text(value), None
    if typ == "date":
        return parse_date(value, date_fmt=t.get("date_fmt")).isoformat(), None
    if typ == "datetime":
        return parse_datetime(value, date_fmt=t.get("date_fmt")).isoformat(), None
    if typ == "month":
        return parse_month(value, date_fmt=t.get("date_fmt")).isoformat(), None
    if typ == "money":
        return parse_money(value, currency, decimal_sep=t.get("decimal_sep"), sign_flip=bool(t.get("sign_flip"))), None
    if typ == "int":
        return parse_int(value, decimal_sep=t.get("decimal_sep")), None
    if typ == "number":
        d = parse_decimal(value, decimal_sep=t.get("decimal_sep"))
        return str(-d if t.get("sign_flip") else d), None
    if typ == "pct":
        return parse_ratio(value, pct_to_ratio=t.get("pct_to_ratio"), decimal_sep=t.get("decimal_sep")), None
    if typ == "currency":
        return parse_currency(value), None
    if typ == "status":
        raw = as_text(value)
        key = re.sub(r"\s+", " ", raw.lower().replace("-", " ").replace("_", " ")).strip()
        smap = {re.sub(r"[-_]", " ", k): v for k, v in (status_map or {}).items()}
        if key in smap:
            return smap[key], None
        if raw.upper() in set(smap.values()):
            return raw.upper(), None
        return raw.upper(), f"unknown status {raw!r}"
    raise ValueError(f"unknown field type {typ!r}")
