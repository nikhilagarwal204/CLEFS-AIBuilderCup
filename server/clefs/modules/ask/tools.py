"""
Tool schemas generated from the viewer's catalog (spec 7), and the validator
that turns the model's arguments into a governed query.

The schemas are strict (every property required, no extras, nullable via a
null type) and their enums are this hotel's metrics and dimensions for this
role only. The validator re-checks everything anyway: the model's output is
untrusted input, so every key is looked up again, every filter value is
matched to the distinct-values cache and every period is resolved on the
server. Nothing the model writes is ever interpolated into SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from clefs.modules.ask import periods, values
from clefs.modules.ask.catalog import derived
from clefs.modules.ask.constants import (
    COMPARE_MAP,
    COMPARE_TO,
    DEFAULT_LIMIT,
    FILTER_OPS,
    GRAINS,
    MAX_DIMENSIONS,
    MAX_FILTER_VALUES,
    MAX_FILTERS,
    MAX_LIMIT,
    PERIOD_PRESETS,
    PII_DIMENSIONS,
    SORTS,
)

TOOL_NAMES = ("query_metric", "list_metrics", "describe_metric", "answer_unsupported")


class ToolArgError(ValueError):
    """Arguments we refuse; the message goes back to the model verbatim."""


@dataclass
class QuerySpec:
    metric: str
    dimensions: list[str]
    filters: dict[str, list[str]]
    period: periods.Period
    grain: str
    compare: str | None
    sort: str
    limit: int
    args: dict = field(default_factory=dict)  # normalized arguments (what a saved view stores)


# ─── Schemas ───


def _fn(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


def _enum(options: list[str]) -> dict:
    # An empty enum is invalid JSON Schema: a hotel with no dimensions gets a
    # plain string the validator then refuses.
    return {"type": "string", "enum": options} if options else {"type": "string"}


def build_tools(entries: list[dict]) -> list[dict]:
    """The four tools, enums injected from this viewer's catalog."""
    keys = [e["key"] for e in entries]
    dims = sorted({d["key"] for e in entries for d in e["dimensions"]})
    period = {
        "type": "object",
        "description": "Use a preset when one fits; otherwise explicit hotel-local dates (YYYY-MM-DD).",
        "properties": {
            "preset": {"type": ["string", "null"], "enum": [*PERIOD_PRESETS, None]},
            "from": {"type": ["string", "null"]},
            "to": {"type": ["string", "null"]},
        },
        "required": ["preset", "from", "to"],
        "additionalProperties": False,
    }
    flt = {
        "type": "object",
        "properties": {
            "field": _enum(dims),
            "op": {"type": "string", "enum": list(FILTER_OPS)},
            "value": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["field", "op", "value"],
        "additionalProperties": False,
    }
    query = _fn(
        "query_metric",
        "Run one governed metric query for this hotel. Break down by at most 2 dimensions.",
        {
            "metric": _enum(keys),
            "dimensions": {"type": "array", "items": _enum(dims), "description": "0-2 dimension keys"},
            "filters": {"type": "array", "items": flt, "description": f"at most {MAX_FILTERS}"},
            "period": period,
            "grain": {"type": "string", "enum": list(GRAINS), "description": "total, or a time series"},
            "compare_to": {"type": "string", "enum": list(COMPARE_TO)},
            "sort": {"type": "string", "enum": list(SORTS)},
            "limit": {"type": "integer", "description": f"rows to return, 1-{MAX_LIMIT}"},
        },
    )
    return [
        query,
        _fn("list_metrics", "List the metrics this user may ask about.", {}),
        _fn("describe_metric", "Describe one metric: meaning, unit, dimensions and known values.",
            {"key": _enum(keys)}),
        _fn(
            "answer_unsupported",
            "Use when no metric answers the question: guest-level personal data, forecasts of others, "
            "opinions, or anything outside the list.",
            {"reason": {"type": "string"}, "suggestions": {"type": "array", "items": {"type": "string"}}},
        ),
    ]


# ─── Validation ───


def _values_metric(metric: str) -> str:
    """Derived ratios take their filter values from the numerator."""
    d = derived(metric)
    return d.num if d is not None else metric


def _filters(ctx: Any, metric: str, dim_keys: set[str], raw: Any) -> tuple[dict[str, list[str]], list[dict]]:
    if raw in (None, []):
        return {}, []
    if not isinstance(raw, list):
        raise ToolArgError("filters must be a list of {field, op, value}")
    if len(raw) > MAX_FILTERS:
        raise ToolArgError(f"at most {MAX_FILTERS} filters")
    out: dict[str, list[str]] = {}
    echo: list[dict] = []
    for f in raw:
        if not isinstance(f, dict):
            raise ToolArgError("each filter is {field, op, value}")
        fld, op = f.get("field"), f.get("op") or "eq"
        vals = f.get("value")
        vals = [vals] if isinstance(vals, (str, int, float)) else list(vals or [])
        vals = [str(v).strip() for v in vals if str(v).strip()]
        if fld in PII_DIMENSIONS:
            raise ToolArgError("guest-level fields cannot be filtered on")
        if fld not in dim_keys:
            raise ToolArgError(f"{metric} cannot be filtered by {fld!r}; its dimensions are {sorted(dim_keys)}")
        if op not in FILTER_OPS:
            raise ToolArgError(f"op must be one of {list(FILTER_OPS)}")
        if not vals or len(vals) > MAX_FILTER_VALUES:
            raise ToolArgError(f"filter {fld!r} needs 1-{MAX_FILTER_VALUES} values")
        if op == "eq" and len(vals) != 1:
            raise ToolArgError("op eq takes exactly one value; use in for several")
        known = values.allowed(ctx, _values_metric(metric), fld)
        if op in ("gte", "lte"):
            if known is None:
                raise ToolArgError(f"{fld!r} has no known values to compare against; use eq or in")
            matched = values.compare(known, op, vals[0])
            if not matched:
                raise ToolArgError(f"no {fld!r} value is {op} {vals[0]!r}")
        elif known is None:
            matched = vals
        else:
            matched = []
            for v in vals:
                m = values.match(v, known)
                if m is None:
                    raise ToolArgError(f"{v!r} is not a known {fld!r} value; known values: {known[:15]}")
                matched.append(m)
        prev = out.get(fld)
        out[fld] = sorted(set(matched) & set(prev)) if prev is not None else sorted(set(matched))
        if not out[fld]:
            raise ToolArgError(f"the filters on {fld!r} exclude every value")
        echo.append({"field": fld, "op": op, "value": vals})
    return out, echo


def validate_query(ctx: Any, args: dict, catalog: dict[str, dict]) -> QuerySpec:
    """query_metric arguments -> QuerySpec, or ToolArgError for the model."""
    if not isinstance(args, dict):
        raise ToolArgError("arguments must be an object")
    metric = args.get("metric")
    entry = catalog.get(metric) if isinstance(metric, str) else None
    if entry is None:
        raise ToolArgError(f"unknown metric {metric!r}; call list_metrics for the allowed keys")
    dim_keys = {d["key"] for d in entry["dimensions"]}
    dims = [d for d in (args.get("dimensions") or []) if d not in (None, "", "none")]
    if len(dims) > MAX_DIMENSIONS:
        raise ToolArgError(f"at most {MAX_DIMENSIONS} dimensions")
    if len(set(dims)) != len(dims):
        raise ToolArgError("a dimension is repeated")
    for d in dims:
        if d in PII_DIMENSIONS:
            raise ToolArgError("guest-level fields cannot be shown")
        if d not in dim_keys:
            raise ToolArgError(f"{metric} cannot be broken down by {d!r}; its dimensions are {sorted(dim_keys)}")
    filters, filter_echo = _filters(ctx, metric, dim_keys, args.get("filters"))
    try:
        period = periods.resolve(args.get("period"), ctx.hotel)
    except periods.PeriodError as exc:
        raise ToolArgError(str(exc)) from exc
    grain = args.get("grain") or "total"
    if grain not in GRAINS:
        raise ToolArgError(f"grain must be one of {list(GRAINS)}")
    compare_to = args.get("compare_to") or "none"
    if compare_to not in COMPARE_MAP:
        raise ToolArgError(f"compare_to must be one of {list(COMPARE_TO)}")
    sort = args.get("sort") or ("time_asc" if grain != "total" else "value_desc")
    if sort not in SORTS:
        raise ToolArgError(f"sort must be one of {list(SORTS)}")
    try:
        limit = int(args.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    limit = max(1, min(MAX_LIMIT, limit))
    normalized = {
        "metric": metric,
        "dimensions": dims,
        "filters": filter_echo,
        "period": {"preset": period.preset, "from": None if period.preset else period.start.isoformat(),
                   "to": None if period.preset else period.end.isoformat()},
        "grain": grain,
        "compare_to": compare_to,
        "sort": sort,
        "limit": limit,
    }
    return QuerySpec(metric=metric, dimensions=dims, filters=filters, period=period, grain=grain,
                     compare=COMPARE_MAP[compare_to], sort=sort, limit=limit, args=normalized)
