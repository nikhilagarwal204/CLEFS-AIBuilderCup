"""
Result -> {table, chart_spec, trace} (spec 7 output). Plain data only; the
console formats money and dates with the hotel's locale (app/lib/format.ts)
and the WhatsApp reply formats them here (whatsapp.py).
"""

from __future__ import annotations

import re
from typing import Any

from clefs.modules.ask.catalog import derived
from clefs.modules.ask.tools import QuerySpec

MAX_SERIES = 6
# Defence in depth for "no guest PII through Ask": even if a module ever
# groups by something guest-shaped, an email or phone never leaves in a cell.
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def safe_label(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v)
    if _EMAIL_RE.search(s) or _PHONE_RE.fullmatch(s.strip()):
        return "[hidden]"
    return s


def change_pct(value: Any, compare: Any) -> float | None:
    if value is None or compare in (None, 0):
        return None
    return round((float(value) - float(compare)) / abs(float(compare)) * 100, 1)


# ─── Table ───


def table(res: dict, spec: QuerySpec, entry: dict) -> dict:
    dim_labels = {d["key"]: d["label"] for d in entry["dimensions"]}
    cols: list[dict] = []
    if spec.grain != "total":
        cols.append({"key": "bucket", "label": spec.grain.capitalize(), "kind": "time"})
    for d in spec.dimensions:
        cols.append({"key": d, "label": dim_labels.get(d, d), "kind": "dimension"})
    unit, cur = res.get("unit"), res.get("currency")
    cols.append({"key": "value", "label": res.get("label") or entry["label"], "kind": "value", "unit": unit,
                 "currency": cur})
    if spec.compare:
        clabel = "Previous period" if spec.compare == "prev_period" else "Same period last year"
        cols.append({"key": "compare_value", "label": clabel, "kind": "compare", "unit": unit, "currency": cur})
        cols.append({"key": "change_pct", "label": "Change", "kind": "change", "unit": "percent"})
    rows = []
    for r in res["rows"]:
        row: list[Any] = []
        if spec.grain != "total":
            row.append(r["bucket"])
        row += [safe_label(r["group"].get(d)) for d in spec.dimensions]
        row.append(r["value"])
        if spec.compare:
            row += [r["compare_value"], change_pct(r["value"], r["compare_value"])]
        rows.append(row)
    return {"columns": cols, "rows": rows, "total": res.get("total"), "compare_total": res.get("compare_total"),
            "row_count": res.get("row_count", len(rows)), "limited": bool(res.get("limited")),
            "truncated": bool(res.get("truncated"))}


# ─── Chart ───


def _series_key(i: int) -> str:
    return f"s{i}"


def chart_spec(res: dict, spec: QuerySpec) -> dict:
    unit, cur = res.get("unit"), res.get("currency")
    base = {"unit": unit, "currency": cur, "label": res.get("label")}
    rows = res["rows"]
    if spec.grain == "total" and not spec.dimensions:
        v = res.get("total") if res.get("total") is not None else (rows[0]["value"] if rows else None)
        cv = res.get("compare_total") if res.get("compare_total") is not None else (
            rows[0]["compare_value"] if rows else None)
        return {**base, "type": "kpi", "x": None, "series": [{"key": "value", "label": res.get("label")}],
                "data": [{"value": v, "compare_value": cv, "change_pct": change_pct(v, cv)}]}
    if spec.grain != "total":
        # Time series: one line per group value (first dimension), else one
        # line plus its comparison.
        if spec.dimensions:
            d = spec.dimensions[0]
            groups: list[str] = []
            for r in rows:
                g = safe_label(r["group"].get(d)) or "(none)"
                if g not in groups and len(groups) < MAX_SERIES:
                    groups.append(g)
            series = [{"key": _series_key(i), "label": g} for i, g in enumerate(groups)]
            by_x: dict[str, dict] = {}
            for r in rows:
                g = safe_label(r["group"].get(d)) or "(none)"
                if g in groups:
                    by_x.setdefault(r["bucket"], {"x": r["bucket"]})[_series_key(groups.index(g))] = r["value"]
            return {**base, "type": "line", "x": "bucket", "series": series,
                    "data": [by_x[k] for k in sorted(by_x)]}
        series = [{"key": "value", "label": res.get("label")}]
        if spec.compare:
            series.append({"key": "compare_value", "label": "Comparison"})
        data = [{"x": r["bucket"], "value": r["value"], "compare_value": r["compare_value"]} for r in rows]
        return {**base, "type": "line", "x": "bucket", "series": series, "data": data}
    # Breakdown by category.
    d = spec.dimensions[0]
    labels = [safe_label(r["group"].get(d)) or "(none)" for r in rows]
    long_labels = any(len(lab) > 10 for lab in labels)  # column ticks fit about 10 characters
    kind = "hbar" if (len(rows) > 6 or long_labels) else "column"
    series = [{"key": "value", "label": res.get("label")}]
    if spec.compare:
        series.append({"key": "compare_value", "label": "Comparison"})
    data = [{"x": lab, "value": r["value"], "compare_value": r["compare_value"]} for lab, r in zip(labels, rows)]
    return {**base, "type": kind, "x": d, "series": series, "data": data}


# ─── Trace ───


def trace(res: dict, spec: QuerySpec, entry: dict) -> dict:
    d = derived(spec.metric)
    return {
        "metric": spec.metric,
        "metric_label": entry["label"],
        "description": entry.get("description"),
        "dims": list(spec.dimensions),
        "filters": [{"field": k, "values": v} for k, v in sorted(spec.filters.items())],
        "period": spec.period.to_api(),
        "grain": spec.grain,
        "compare": spec.compare,
        "sort": spec.sort,
        "limit": spec.limit,
        "unit": res.get("unit"),
        "currency": res.get("currency"),
        "row_count": res.get("row_count"),
        "truncated": bool(res.get("truncated")),
        "derived": {"numerator": d.num, "denominator": d.den, "scale": d.scale} if d else None,
        "source": "clefs.core.metrics",
    }


def result_block(res: dict, spec: QuerySpec, entry: dict) -> dict:
    return {"table": table(res, spec, entry), "chart_spec": chart_spec(res, spec), "trace": trace(res, spec, entry)}
