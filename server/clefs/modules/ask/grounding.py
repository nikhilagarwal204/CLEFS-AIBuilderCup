"""
Numeric grounding (spec 7): every number in an answer must come from the
tool results, after formatting and within rounding tolerance. A model that
invents, miscopies or "estimates" a figure fails here, gets one regeneration
with the offending numbers named, and then the owner gets the table alone.

What counts as grounded, for each result: every row value and comparison,
the totals, the change percentages and differences between them, shares of
an additive total, the row count and limit; plus the numbers that name the
period (years, days, the period length) and numbers the owner typed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from clefs.core.metrics import ADDITIVE_UNITS

_NUM_RE = re.compile(
    r"(?<![\w.])(?P<sign>[-+])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<scale>k|K|m|M|bn|BN|million|thousand|billion)\b)?(?P<pct>\s?%)?"
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_SCALES = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
REL_TOL = 0.005  # half a percent: "AED 1.2M" for 1,204,300 still reads as the same number


@dataclass
class Number:
    text: str
    value: float
    unit: float  # the precision the text implies (1, 0.1, 100 for "12.3k", ...)
    pct: bool = False


@dataclass
class Report:
    ok: bool
    numbers: list[Number] = field(default_factory=list)
    ungrounded: list[str] = field(default_factory=list)


# ─── Extraction ───


def extract(text: str) -> list[Number]:
    """Every number written in `text`, with the precision its format implies."""
    out: list[Number] = []
    for m in _NUM_RE.finditer(text or ""):
        raw = m.group("num")
        digits = raw.replace(",", "")
        decimals = len(digits.split(".")[1]) if "." in digits else 0
        scale = _SCALES.get((m.group("scale") or "").lower(), 1.0)
        value = float(digits) * scale
        unit = (10 ** -decimals) * scale
        out.append(Number(text=m.group(0).strip(), value=value, unit=unit, pct=bool(m.group("pct"))))
    return out


# ─── Allowed values ───


def _add(bag: set[float], v: Any) -> None:
    if v is None or isinstance(v, bool):
        return
    try:
        f = float(v)
    except (TypeError, ValueError):
        return
    bag.add(abs(round(f, 6)))


def _date_parts(bag: set[float], d: date) -> None:
    for v in (d.year, d.month, d.day, d.year % 100):
        _add(bag, v)


def allowed(results: Iterable[dict], *, question: str = "", extra: Iterable[Any] = ()) -> set[float]:
    """The numbers an answer may cite, from registry results (execute.run)."""
    bag: set[float] = set()
    for res in results:
        unit = res.get("unit")
        rows = res.get("rows") or []
        total = res.get("total")
        for key in ("total", "compare_total", "row_count", "limit"):
            _add(bag, res.get(key))
        _add(bag, len(rows))
        vals = []
        for r in rows:
            v, c = r.get("value"), r.get("compare_value")
            _add(bag, v)
            _add(bag, c)
            if v is not None:
                vals.append(float(v))
            if v is not None and c not in (None, 0):
                _add(bag, float(v) - float(c))
                _add(bag, (float(v) - float(c)) / abs(float(c)) * 100)
                _add(bag, float(v) / float(c))
            if unit == "ratio" and v is not None:
                _add(bag, float(v) * 100)
            if total not in (None, 0) and v is not None and unit in ADDITIVE_UNITS:
                _add(bag, float(v) / float(total) * 100)
            b = r.get("bucket")
            if b:
                _date_parts(bag, date.fromisoformat(b[:10]))
            for g in (r.get("group") or {}).values():
                _add(bag, g)  # a numeric label ("floor 5") is quoted, not computed
        if vals:
            _add(bag, sum(vals))
            if len(vals) >= 2:
                s = sorted(vals, reverse=True)
                _add(bag, s[0] - s[1])  # "X led Y by ..."
        t, ct = res.get("total"), res.get("compare_total")
        if t is not None and ct not in (None, 0):
            _add(bag, float(t) - float(ct))
            _add(bag, (float(t) - float(ct)) / abs(float(ct)) * 100)
        for k in ("start", "end"):
            if res.get(k):
                _date_parts(bag, date.fromisoformat(str(res[k])[:10]))
        if res.get("start") and res.get("end"):
            span = (date.fromisoformat(res["end"][:10]) - date.fromisoformat(res["start"][:10])).days + 1
            _add(bag, span)
    for n in extract(question):
        _add(bag, n.value)
    for v in extra:
        _add(bag, v)
    return bag


# ─── Check ───


def _matches(n: Number, bag: set[float]) -> bool:
    x = abs(n.value)
    tol_abs = n.unit / 2 + 1e-9
    for a in bag:
        if abs(x - a) <= tol_abs or (a and abs(x - a) <= REL_TOL * a):
            return True
    return False


def check(text: str, bag: set[float]) -> Report:
    text = _ISO_DATE_RE.sub(lambda m: f"{int(m.group(3))} {int(m.group(2))} {m.group(1)}", text or "")
    nums = extract(text)
    bad = [n.text for n in nums if not _matches(n, bag)]
    return Report(ok=not bad, numbers=nums, ungrounded=bad)
