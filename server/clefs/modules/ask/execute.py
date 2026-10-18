"""
Run a validated QuerySpec through the metrics registry (never raw SQL) and
shape the result into what every surface shows: a table, a chart spec and
the trace behind "How this was computed".

Derived ratios (catalog.DERIVED) run their numerator and denominator as two
registry queries with the same keys and divide row by row, so a ratio
inherits every guarantee of the registry (bound values, hotel filter,
read-only, timeout, row cap).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from clefs.core import metrics
from clefs.core.metrics import MetricQuery
from clefs.modules.ask.catalog import Derived, derived
from clefs.modules.ask.tools import QuerySpec

# ─── Registry calls ───


def _mq(spec: QuerySpec, metric: str) -> MetricQuery:
    return MetricQuery(
        metric=metric, start=spec.period.start, end=spec.period.end, grain=spec.grain,
        group_by=list(spec.dimensions), filters=dict(spec.filters), compare=spec.compare,
    )


def _bucket_days(bucket: str | None, grain: str, start: date, end: date) -> int:
    """Days of the [start, end] range inside one bucket (capacity ratios)."""
    if grain == "total" or bucket is None:
        return (end - start).days + 1
    b0 = date.fromisoformat(bucket)
    if grain == "day":
        b1 = b0
    elif grain == "week":
        b1 = b0 + timedelta(days=6)
    else:
        b1 = (b0.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    lo, hi = max(b0, start), min(b1, end)
    return max(0, (hi - lo).days + 1)


def _div(a: Any, b: Any, scale: float) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round(float(a) / float(b) * scale, 2)


def _run_derived(ctx: Any, d: Derived, spec: QuerySpec) -> dict:
    num = metrics.run_metric(ctx, _mq(spec, d.num))
    rooms = int(getattr(ctx.hotel, "rooms_count", 0) or 0)
    den_rows: dict[tuple, dict] = {}
    if d.den != "capacity":
        den = metrics.run_metric(ctx, _mq(spec, d.den))
        den_rows = {(r["bucket"], tuple(sorted(r["group"].items()))): r for r in den["rows"]}
    rows = []
    tot_num = tot_den = 0.0
    for r in num["rows"]:
        k = (r["bucket"], tuple(sorted(r["group"].items())))
        if d.den == "capacity":
            cap = rooms * _bucket_days(r["bucket"], spec.grain, spec.period.start, spec.period.end)
            dv, dc = cap, cap
        else:
            dr = den_rows.get(k) or {}
            dv, dc = dr.get("value"), dr.get("compare_value")
        rows.append({
            "bucket": r["bucket"], "group": r["group"],
            "value": _div(r["value"], dv, d.scale), "compare_value": _div(r["compare_value"], dc, d.scale),
        })
        tot_num += float(r["value"] or 0)
        tot_den += float(dv or 0)
    total = rows[0]["value"] if (spec.grain == "total" and not spec.dimensions and rows) else None
    if total is None and not spec.dimensions:
        total = _div(tot_num, tot_den, d.scale)
    compare_total = rows[0]["compare_value"] if (spec.grain == "total" and not spec.dimensions and rows) else None
    return {
        **num, "metric": d.key, "label": d.label, "unit": d.unit,
        "currency": ctx.hotel.currency if d.currency == "hotel" else None,
        "rows": rows, "total": total, "compare_total": compare_total,
    }


def run(ctx: Any, spec: QuerySpec) -> dict:
    """The registry's result shape (core.metrics.run_metric) for one spec."""
    d = derived(spec.metric)
    res = _run_derived(ctx, d, spec) if d is not None else metrics.run_metric(ctx, _mq(spec, spec.metric))
    return _sort_limit(res, spec)


def _sort_limit(res: dict, spec: QuerySpec) -> dict:
    rows = list(res["rows"])
    if spec.sort == "value_desc":
        rows.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0)))
    elif spec.sort == "value_asc":
        rows.sort(key=lambda r: (r["value"] is None, r["value"] or 0))
    elif spec.sort == "label_asc":
        rows.sort(key=lambda r: tuple(str(v) for v in r["group"].values()))
    else:
        rows.sort(key=lambda r: (r["bucket"] or "", tuple(str(v) for v in r["group"].values())))
    res = dict(res)
    res["row_count"] = len(rows)
    res["rows"] = rows[: spec.limit]
    res["limited"] = len(rows) > spec.limit
    return res
