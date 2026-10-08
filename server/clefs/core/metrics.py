"""
Metrics registry: governed, parameterized SQL (01 spec E3). Ask Clefs's
function-calling schema, the brief's numbers and module dashboards all read
metrics through here, so a number means the same thing everywhere.

    register_metric(Metric(
        key="service.requests", label="Guest requests", description="Requests created",
        unit="count", source="FROM service_requests_v1 r", hotel_column="r.hotel_slug",
        time_column="r.created_at", time_kind="timestamptz", measure="COUNT(*)",
        dimensions=(Dimension("department", "Department", "r.department"),),
        permission="service.view"))

    run_metric(ctx, MetricQuery(metric="service.requests", start=d0, end=d1, grain="day",
                                group_by=["department"], filters={"department": ["Housekeeping"]}))

Safety model (why user text can never reach SQL):
  - The SQL fragments (source, measure, where, dimension sql) are written by
    developers in module code and checked at registration: no ';', no comments,
    and '%' only as '%%' or one of the named parameters below.
  - A query only chooses KEYS (metric, dimensions, filters, grain), each looked
    up in the registry; anything unknown is a 400 before any SQL is built.
  - Every value (hotel, dates, filter values, timezone, grain) is a bound
    parameter, and `<hotel_column> = %(hotel)s` is always part of WHERE.
  - run_metric executes in a READ ONLY transaction with a 5 s statement_timeout
    and returns at most 500 rows.

Time: the [start, end] local dates become UTC instants with core.time, and
buckets use `AT TIME ZONE %(tz)s` (the hotel's zone), never the server's.
"""

from __future__ import annotations

import re
import sys
import threading
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from clefs import db
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.core.time import local_range, to_ms

Unit = Literal["count", "money", "percent", "minutes", "ratio", "nights", "rate"]
TimeKind = Literal["timestamptz", "date", "epoch_ms"]
Grain = Literal["total", "day", "week", "month"]

UNITS = ("count", "money", "percent", "minutes", "ratio", "nights", "rate")
TIME_KINDS = ("timestamptz", "date", "epoch_ms")
MAX_ROWS = 500
STATEMENT_TIMEOUT_MS = 5000
MAX_SPAN_DAYS = 3 * 366 + 1
MAX_FILTER_VALUES = 100
ADDITIVE_UNITS = frozenset({"count", "money", "nights"})
PARAM_NAMES = frozenset({"hotel", "tz", "t0", "t1", "t0_ms", "t1_ms", "d0", "d1"})
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_PCT_RE = re.compile(r"%(%|\(([a-z0-9_]+)\)s)?")


# ─── Definitions ───


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    sql: str  # an expression over the metric's source, e.g. "r.department"
    values_sql: str | None = None  # "SELECT DISTINCT ... WHERE hotel_slug = %(hotel)s" for pickers


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    description: str
    unit: Unit
    source: str  # "FROM staff_requests r LEFT JOIN hotel_rooms hr ON ..."
    hotel_column: str  # "r.hotel_slug"
    time_column: str
    time_kind: TimeKind
    measure: str  # "COUNT(*)", "SUM(x.amount_minor)", ...
    where: str = ""
    dimensions: tuple[Dimension, ...] = ()
    permission: str = ""  # the viewer must hold it ("" = any member)
    currency: Literal["hotel"] | None = None  # money metrics are in the hotel's currency
    money_minor: bool = True  # money measures return minor units (converted for display)
    higher_is_better: bool | None = None
    synonyms: tuple[str, ...] = ()


class MetricQuery(BaseModel):
    metric: str = Field(max_length=120)
    start: date
    end: date
    grain: Grain = "total"
    group_by: list[str] = Field(default_factory=list, max_length=2)
    filters: dict[str, list[str]] = Field(default_factory=dict)
    compare: Literal["prev_period", "prev_year"] | None = None

    @field_validator("filters")
    @classmethod
    def _filters(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(v) > 5:
            raise ValueError("at most 5 filters")
        for key, values in v.items():
            if not values or len(values) > MAX_FILTER_VALUES:
                raise ValueError(f"filter {key!r} needs 1-{MAX_FILTER_VALUES} values")
        return v


_metrics: dict[str, Metric] = {}
_origins: dict[str, str] = {}
_lock = threading.Lock()


def _check_fragment(label: str, sql: str) -> None:
    if not sql:
        return
    if ";" in sql or "--" in sql or "/*" in sql:
        raise ValueError(f"{label}: ';' and SQL comments are not allowed")
    for m in _PCT_RE.finditer(sql):
        if m.group(1) is None or (m.group(2) is not None and m.group(2) not in PARAM_NAMES):
            raise ValueError(f"{label}: '%' must be '%%' or one of %({'|'.join(sorted(PARAM_NAMES))})s")


def register_metric(m: Metric) -> Metric:
    if "." not in m.key:
        raise ValueError(f"metric key {m.key!r} must look like '<module>.<what>'")
    if m.unit not in UNITS:
        raise ValueError(f"unit must be one of {UNITS}")
    if m.time_kind not in TIME_KINDS:
        raise ValueError(f"time_kind must be one of {TIME_KINDS}")
    if not m.source.strip().upper().startswith("FROM "):
        raise ValueError("source must start with FROM")
    for label, frag in (
        ("source", m.source),
        ("hotel_column", m.hotel_column),
        ("time_column", m.time_column),
        ("measure", m.measure),
        ("where", m.where),
    ):
        _check_fragment(f"{m.key}.{label}", frag)
    seen: set[str] = set()
    for d in m.dimensions:
        if not KEY_RE.match(d.key) or d.key in ("bucket", "value", "day", "week", "month"):
            raise ValueError(f"{m.key}: bad dimension key {d.key!r}")
        if d.key in seen:
            raise ValueError(f"{m.key}: duplicate dimension {d.key!r}")
        seen.add(d.key)
        _check_fragment(f"{m.key}.{d.key}", d.sql)
        if d.values_sql:
            _check_fragment(f"{m.key}.{d.key}.values_sql", d.values_sql)
    origin = sys._getframe(1).f_globals.get("__name__", "?")
    with _lock:
        prev = _origins.get(m.key)
        if prev is not None and prev != origin:
            raise ValueError(f"metric {m.key!r} already registered by {prev}")
        _metrics[m.key] = m
        _origins[m.key] = origin
    return m


def unregister_metric(key: str) -> None:
    """Tests only."""
    with _lock:
        _metrics.pop(key, None)
        _origins.pop(key, None)


def registered_metrics() -> list[Metric]:
    with _lock:
        return sorted(_metrics.values(), key=lambda m: m.key)


def metric_origin(key: str) -> str | None:
    with _lock:
        return _origins.get(key)


def get_metric(key: str) -> Metric:
    with _lock:
        m = _metrics.get(key)
    if m is None:
        raise ClefsError(404, "Unknown metric", "unknown_metric")
    return m


def _visible(ctx: Any, m: Metric) -> bool:
    return not m.permission or ctx.can(m.permission)


def describe(m: Metric, hotel: Any = None) -> dict:
    return {
        "key": m.key,
        "label": m.label,
        "description": m.description,
        "unit": m.unit,
        "currency": (hotel.currency if hotel is not None else None) if m.currency == "hotel" else None,
        "dimensions": [{"key": d.key, "label": d.label} for d in m.dimensions],
        "higher_is_better": m.higher_is_better,
        "synonyms": list(m.synonyms),
    }


def list_metrics(ctx: Any) -> list[dict]:
    """Metrics this viewer may query, described for pickers and Ask Clefs."""
    return [describe(m, ctx.hotel) for m in registered_metrics() if _visible(ctx, m)]


# ─── Compile ───


def _bucket_sql(m: Metric) -> str:
    tc = m.time_column
    if m.time_kind == "timestamptz":
        return f"date_trunc(%(grain)s, ({tc}) AT TIME ZONE %(tz)s)::date"
    if m.time_kind == "epoch_ms":
        return f"date_trunc(%(grain)s, to_timestamp(({tc}) / 1000.0) AT TIME ZONE %(tz)s)::date"
    return f"date_trunc(%(grain)s, ({tc})::timestamp)::date"


def _time_predicate(m: Metric) -> str:
    tc = m.time_column
    if m.time_kind == "timestamptz":
        return f"({tc}) >= %(t0)s AND ({tc}) < %(t1)s"
    if m.time_kind == "epoch_ms":
        return f"({tc}) >= %(t0_ms)s AND ({tc}) < %(t1_ms)s"
    return f"({tc}) >= %(d0)s AND ({tc}) <= %(d1)s"


def compile_metric(hotel: Any, q: MetricQuery) -> tuple[str, dict]:
    """(sql, params) for one query. Only registry identifiers are interpolated;
    every value is a parameter; the hotel filter is always present."""
    m = get_metric(q.metric)
    if q.end < q.start:
        raise ClefsError(400, "end is before start", "invalid_range")
    if (q.end - q.start).days > MAX_SPAN_DAYS:
        raise ClefsError(400, "the range can span at most 3 years", "invalid_range")
    dims = {d.key: d for d in m.dimensions}
    for g in q.group_by:
        if g not in dims:
            raise ClefsError(400, f"{q.metric} cannot be grouped by {g!r}", "unknown_dimension")
    if len(set(q.group_by)) != len(q.group_by):
        raise ClefsError(400, "group_by repeats a dimension", "unknown_dimension")
    for f in q.filters:
        if f not in dims:
            raise ClefsError(400, f"{q.metric} cannot be filtered by {f!r}", "unknown_dimension")
    t0, t1 = local_range(hotel, q.start, q.end)
    params: dict[str, Any] = {
        "hotel": hotel.slug,
        "tz": hotel.timezone,
        "t0": t0,
        "t1": t1,
        "t0_ms": to_ms(t0),
        "t1_ms": to_ms(t1),
        "d0": q.start,
        "d1": q.end,
        "grain": q.grain if q.grain != "total" else "day",
    }
    select: list[str] = []
    if q.grain != "total":
        select.append(f"{_bucket_sql(m)} AS bucket")
    for g in q.group_by:
        select.append(f'(({dims[g].sql}))::TEXT AS "g_{g}"')
    n_group = len(select)
    select.append(f"{m.measure} AS value")
    where = [f"({m.hotel_column}) = %(hotel)s", _time_predicate(m)]
    if m.where:
        where.append(f"({m.where})")
    for i, (f, values) in enumerate(sorted(q.filters.items())):
        params[f"f{i}"] = [str(v) for v in values]
        where.append(f"(({dims[f].sql}))::TEXT = ANY(%(f{i})s)")
    sql = f"SELECT {', '.join(select)} {m.source} WHERE {' AND '.join(where)}"
    if n_group:
        positions = ", ".join(str(i) for i in range(1, n_group + 1))
        sql += f" GROUP BY {positions} ORDER BY {positions}"
    sql += f" LIMIT {MAX_ROWS + 1}"
    return sql, params


# ─── Run ───


def _shift(q: MetricQuery) -> tuple[date, date, timedelta | None, int]:
    """The comparison range and how far its buckets move to line up."""
    if q.compare == "prev_year":
        def back(d: date) -> date:
            try:
                return d.replace(year=d.year - 1)
            except ValueError:  # 29 Feb
                return d.replace(year=d.year - 1, day=28)
        return back(q.start), back(q.end), None, 1
    length = (q.end - q.start).days + 1
    return q.start - timedelta(days=length), q.start - timedelta(days=1), timedelta(days=length), 0


def _bucket_floor(d: date, grain: str) -> date:
    """Python twin of date_trunc(grain, ...)::date (ISO weeks start Monday)."""
    if grain == "week":
        return d - timedelta(days=d.weekday())
    if grain == "month":
        return d.replace(day=1)
    return d


def _bucket_next(d: date, grain: str) -> date:
    if grain == "week":
        return d + timedelta(days=7)
    if grain == "month":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d + timedelta(days=1)


def _calendar(start: date, end: date, grain: str) -> list[date]:
    """Every bucket key a [start, end] query can produce, in order."""
    out, b = [], _bucket_floor(start, grain)
    while b <= end:
        out.append(b)
        b = _bucket_next(b, grain)
    return out


def _compare_bucket_map(q: MetricQuery, cstart: date, cend: date) -> dict[date, date] | None:
    """Week and month buckets are date_trunc'd, so shifting a comparison
    bucket by the period length (or a year) lands on a date that is not a
    bucket of the current period. Pair them by position instead: the k-th
    bucket of the comparison range lines up with the k-th of the current
    range, whatever the data holds. None for day buckets, which shift exactly."""
    if q.grain not in ("week", "month"):
        return None
    return dict(zip(_calendar(cstart, cend, q.grain), _calendar(q.start, q.end, q.grain)))


def _value(m: Metric, hotel: Any, v: Any) -> float | int | None:
    if v is None:
        return None
    if m.unit == "money" and m.money_minor:
        # An AVG or ratio in minor units has a fraction: round half up
        # (Canon 2), never truncate toward zero.
        minor = int(Decimal(v).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        amount = money.from_minor(minor, hotel.currency)
        return float(amount) if amount is not None else None
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() and m.unit in ("count", "nights") else float(v)
    return v


def _execute(sql: str, params: dict) -> list[dict]:
    with db.db(readonly=True, statement_timeout_ms=STATEMENT_TIMEOUT_MS) as cur:
        try:
            cur.execute(sql, params)
        except Exception as exc:
            if "statement timeout" in str(exc):
                raise ClefsError(504, "That question took too long to answer", "metric_timeout") from exc
            raise
        return [dict(r) for r in cur.fetchall()]


def _row_key(row: dict, group_by: list[str]) -> tuple:
    return tuple(row.get(f"g_{g}") for g in group_by)


def run_metric(ctx: Any, q: MetricQuery) -> dict:
    """Run one query for the ctx's hotel. 403 without the metric's permission."""
    m = get_metric(q.metric)
    if not _visible(ctx, m):
        raise ClefsError(403, "You cannot see this metric", "forbidden")
    hotel = ctx.hotel
    sql, params = compile_metric(hotel, q)
    raw = _execute(sql, params)
    truncated = len(raw) > MAX_ROWS
    raw = raw[:MAX_ROWS]

    compare_map: dict[tuple, Any] = {}
    compare_total = None
    if q.compare:
        cstart, cend, delta, years = _shift(q)
        cq = q.model_copy(update={"start": cstart, "end": cend})
        csql, cparams = compile_metric(hotel, cq)
        aligned = _compare_bucket_map(q, cstart, cend)
        for r in _execute(csql, cparams)[:MAX_ROWS]:
            b = r.get("bucket")
            if b is not None:
                if aligned is not None:
                    b = aligned.get(b)
                    if b is None:
                        continue  # a comparison bucket with no counterpart
                elif delta is not None:
                    b = b + delta
                elif years:
                    try:
                        b = b.replace(year=b.year + years)
                    except ValueError:
                        b = b.replace(year=b.year + years, day=28)
            compare_map[(b,) + _row_key(r, q.group_by)] = r["value"]
        if q.grain == "total" and not q.group_by:
            compare_total = _value(m, hotel, next(iter(compare_map.values()), None))

    rows = []
    for r in raw:
        b = r.get("bucket")
        key = (b,) + _row_key(r, q.group_by)
        rows.append(
            {
                "bucket": b.isoformat() if b is not None else None,
                "group": {g: r.get(f"g_{g}") for g in q.group_by},
                "value": _value(m, hotel, r["value"]),
                "compare_value": _value(m, hotel, compare_map.get(key)) if q.compare else None,
            }
        )
    total = None
    if q.grain == "total" and not q.group_by:
        total = rows[0]["value"] if rows else None
    elif m.unit in ADDITIVE_UNITS:
        total = sum((r["value"] or 0) for r in rows)
    return {
        "metric": m.key,
        "label": m.label,
        "unit": m.unit,
        "currency": hotel.currency if m.currency == "hotel" else None,
        "grain": q.grain,
        "start": q.start.isoformat(),
        "end": q.end.isoformat(),
        "group_by": list(q.group_by),
        "filters": q.filters,
        "rows": rows,
        "total": total,
        "compare": q.compare,
        "compare_total": compare_total,
        "truncated": truncated,
    }


def dimension_values(ctx: Any, metric_key: str, dimension: str) -> list[str]:
    """Distinct values for a dimension picker (values_sql, read-only)."""
    m = get_metric(metric_key)
    if not _visible(ctx, m):
        raise ClefsError(403, "You cannot see this metric", "forbidden")
    d = next((d for d in m.dimensions if d.key == dimension), None)
    if d is None:
        raise ClefsError(400, f"{metric_key} has no dimension {dimension!r}", "unknown_dimension")
    if not d.values_sql:
        return []
    rows = _execute(f"SELECT * FROM ({d.values_sql}) AS v LIMIT {MAX_ROWS}", {"hotel": ctx.hotel.slug})
    return [str(next(iter(r.values()))) for r in rows if next(iter(r.values())) is not None]


# ─── Core metrics ───

register_metric(
    Metric(
        key="core.signals_raised",
        label="Signals raised",
        description="Items Clefs detected for the owner (first seen in the period)",
        unit="count",
        source="FROM signals s",
        hotel_column="s.hotel_slug",
        time_column="s.first_seen_at",
        time_kind="timestamptz",
        measure="COUNT(*)",
        dimensions=(
            Dimension("section", "Section", "s.section"),
            Dimension("module", "Module", "s.module"),
            Dimension("kind", "Kind", "s.kind"),
        ),
        permission="core.signals.view",
        synonyms=("alerts", "issues found"),
    )
)
register_metric(
    Metric(
        key="core.actions_decided",
        label="Decisions made",
        description="Human decisions on proposed actions (approve, adjust, assign, dispute, ignore)",
        unit="count",
        source="FROM action_events e JOIN actions a ON a.id = e.action_id",
        hotel_column="a.hotel_slug",
        time_column="e.at",
        time_kind="timestamptz",
        measure="COUNT(*)",
        where="e.actor_type = 'user' AND e.event IN ('approve', 'adjust', 'assign', 'dispute', 'ignore')",
        dimensions=(
            Dimension("decision", "Decision", "e.event"),
            Dimension("action_type", "Action type", "a.type"),
            Dimension("module", "Module", "a.module"),
        ),
        permission="core.signals.view",
    )
)
register_metric(
    Metric(
        key="core.verified_value",
        label="Clefs-verified value",
        description="Verified outcomes of Clefs actions that count toward verified profit, net of reversals",
        unit="money",
        source="FROM actions a",
        hotel_column="a.hotel_slug",
        time_column="a.verified_at",
        time_kind="timestamptz",
        measure="COALESCE(SUM(a.outcome_minor), 0)",
        # Only outcomes recorded in the hotel's own currency: minor units of
        # different currencies never add up (record_verification converts
        # other currencies on write when a rate is available).
        where=(
            "a.status = 'verified' AND a.counts_toward_verified_profit "
            "AND a.currency = (SELECT h.currency FROM hotels h WHERE h.slug = a.hotel_slug)"
        ),
        dimensions=(Dimension("action_type", "Action type", "a.type"), Dimension("module", "Module", "a.module")),
        permission="core.signals.view",
        currency="hotel",
        higher_is_better=True,
        synonyms=("verified profit", "money recovered"),
    )
)
