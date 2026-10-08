"""
Deterministic answers built from results alone: the text of a saved view
(re-run without the LLM), the table-only fallback when the model's prose
fails grounding twice, and the WhatsApp table lines. Every number here is
copied from a result, so these texts are grounded by construction.
"""

from __future__ import annotations

from typing import Any

from clefs.core import money
from clefs.modules.ask.present import change_pct, safe_label
from clefs.modules.ask.tools import QuerySpec


def fmt_value(v: Any, unit: str | None, currency: str | None, locale: str | None = None) -> str:
    if v is None:
        return "n/a"
    f = float(v)
    if unit == "money" and currency:
        try:
            return money.fmt_money(round(f, 2), currency, locale or "en-AE")
        except Exception:  # an odd currency code must not sink the answer
            return f"{currency} {f:,.2f}"
    if unit == "percent":
        return f"{f:,.1f}%"
    if unit == "minutes":
        return f"{f:,.0f} min"
    if unit in ("count", "nights"):
        return f"{f:,.0f}" if f == int(f) else f"{f:,.2f}"
    return f"{f:,.2f}"


def _change_words(v: Any, c: Any, compare: str | None) -> str:
    pct = change_pct(v, c)
    if pct is None:
        return ""
    against = "the previous period" if compare == "prev_period" else "the same period last year"
    if pct == 0:
        return f", flat on {against}"
    return f", {'up' if pct > 0 else 'down'} {abs(pct):,.1f}% on {against}"


def summarize(res: dict, spec: QuerySpec, *, locale: str | None = None) -> str:
    """One or two plain sentences stating what the result holds."""
    label = res.get("label") or spec.metric
    unit, cur = res.get("unit"), res.get("currency")
    period = spec.period.to_api()["label"]
    rows = res.get("rows") or []
    if not rows and res.get("total") in (None, 0):
        return f"{label}, {period}: no data recorded for this period."
    if spec.grain == "total" and not spec.dimensions:
        v = res.get("total") if res.get("total") is not None else rows[0]["value"]
        c = res.get("compare_total") if res.get("compare_total") is not None else (
            rows[0]["compare_value"] if rows else None)
        return f"{label}, {period}: {fmt_value(v, unit, cur, locale)}{_change_words(v, c, spec.compare)}."
    head = f"{label}, {period}"
    if res.get("total") is not None:
        head += f": {fmt_value(res['total'], unit, cur, locale)} in total"
    if spec.dimensions and rows:
        top = rows[0]
        name = " / ".join(safe_label(top["group"].get(d)) or "(none)" for d in spec.dimensions)
        lead = "Highest" if spec.sort == "value_desc" else "First"
        return f"{head}. {lead}: {name} at {fmt_value(top['value'], unit, cur, locale)}."
    return f"{head}. See the table for each {spec.grain}."


def lines(res: dict, spec: QuerySpec, *, max_rows: int, locale: str | None = None) -> list[str]:
    """Aligned "label  value" lines (WhatsApp renders them in a code block)."""
    unit, cur = res.get("unit"), res.get("currency")
    items: list[tuple[str, str]] = []
    for r in (res.get("rows") or [])[:max_rows]:
        parts = []
        if spec.grain != "total" and r.get("bucket"):
            parts.append(str(r["bucket"]))
        parts += [safe_label(r["group"].get(d)) or "(none)" for d in spec.dimensions]
        name = " / ".join(parts) or (res.get("label") or "Total")
        val = fmt_value(r["value"], unit, cur, locale)
        if spec.compare:
            pct = change_pct(r["value"], r["compare_value"])
            if pct is not None:
                val += f" ({pct:+.1f}%)"
        items.append((name[:28], val))
    width = max((len(n) for n, _ in items), default=0)
    return [f"{n.ljust(width)}  {v}" for n, v in items]
