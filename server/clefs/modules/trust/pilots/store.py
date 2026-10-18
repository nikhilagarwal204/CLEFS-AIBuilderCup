"""
Pilots (03 spec 5.5): scope, dates, success criteria, baseline and the
conversion price. Criteria are evaluated through clefs.core.metrics, so
every figure on the scorecard and the report is a metric query the reader
can re-run.

Comparators:
  gte, lte, gt, lt   the pilot-period value against `target`
  improve_pct        (value - baseline) / baseline * 100 >= target
  reduce_pct         (baseline - value) / baseline * 100 >= target
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field, field_validator

from clefs.audit import audit
from clefs.core import metrics, money
from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.hotels import Hotel, get_hotel
from clefs.db import Json

COMPARATORS = ("gte", "lte", "gt", "lt", "improve_pct", "reduce_pct")
BASELINE_COMPARATORS = frozenset({"improve_pct", "reduce_pct"})
STATUSES = ("draft", "active", "completed", "converted", "cancelled")
TRANSITIONS = {
    "draft": {"active", "cancelled"},
    "active": {"completed", "cancelled"},
    "completed": {"converted", "cancelled"},
    "converted": set(),
    "cancelled": set(),
}
DEFAULT_ON_TRACK = Decimal("0.7")

_COLS = ("id, hotel_slug, name, modules, start_date, end_date, status, success_criteria, baseline_start, baseline_end, "
         "baseline_metrics, conversion_price_minor, currency, terms, outcome, created_by, created_at, updated_at")


# ─── Models ───


class Criterion(BaseModel):
    metric_key: str = Field(min_length=1, max_length=120)
    comparator: str
    target: Decimal
    weight: Decimal = Field(default=Decimal(1), gt=0, le=100)
    label: str | None = Field(default=None, max_length=200)

    @field_validator("comparator")
    @classmethod
    def _cmp(cls, v: str) -> str:
        if v not in COMPARATORS:
            raise ValueError(f"comparator must be one of {COMPARATORS}")
        return v


class PilotIn(BaseModel):
    name: str = Field(default="Pilot", min_length=1, max_length=120)
    modules: list[str] = Field(default_factory=list, max_length=20)
    start_date: date
    end_date: date
    success_criteria: list[Criterion] = Field(default_factory=list, max_length=20)
    baseline_start: date | None = None
    baseline_end: date | None = None
    conversion_price: Decimal | None = Field(default=None, ge=0)   # major units, hotel currency
    terms: dict = Field(default_factory=dict)


class PilotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    modules: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    success_criteria: list[Criterion] | None = None
    baseline_start: date | None = None
    baseline_end: date | None = None
    conversion_price: Decimal | None = Field(default=None, ge=0)
    terms: dict | None = None


# ─── Metric reads ───


def system_ctx(hotel: Hotel) -> Any:
    """A ctx for metrics.run_metric from a job: the pilot's criteria were
    chosen by an owner or GM, so the job reads them with every permission."""
    return SimpleNamespace(hotel=hotel, can=lambda *perms: True)


def metric_value(hotel: Hotel, metric_key: str, start: date, end: date) -> Decimal | None:
    """The metric's total over hotel-local [start, end], or None (no data,
    unknown metric, timeout): a criterion without data is 'no_data', never 0."""
    try:
        out = metrics.run_metric(system_ctx(hotel), metrics.MetricQuery(metric=metric_key, start=start, end=end))
    except ClefsError:
        return None
    total = out.get("total")
    if total is None:
        return None
    try:
        value = Decimal(str(total))
    except InvalidOperation:
        return None
    # Money comes back as a float in major units: pin it to the currency's
    # minor units so "6000.0" and "6000.00" never disagree on a report.
    if out.get("unit") == "money" and out.get("currency"):
        return money.quantize(value, out["currency"])
    return value


def _validate_metrics(criteria: list[Criterion]) -> None:
    known = {m.key for m in metrics.registered_metrics()}
    unknown = sorted({c.metric_key for c in criteria} - known)
    if unknown:
        raise bad_request(f"unknown metric(s): {', '.join(unknown)}", "unknown_metric")


# ─── Criteria ───


def judge_criterion(c: dict, value: Decimal | None, baseline: Decimal | None) -> dict:
    """{value, baseline, target, change_pct, met, status: met|not_met|no_data}."""
    target = Decimal(str(c["target"]))
    out: dict[str, Any] = {"metric_key": c["metric_key"], "label": c.get("label") or c["metric_key"],
                           "comparator": c["comparator"], "target": str(target), "weight": str(c.get("weight", 1)),
                           "value": None if value is None else str(value),
                           "baseline": None if baseline is None else str(baseline), "change_pct": None}
    if value is None:
        return {**out, "met": False, "status": "no_data"}
    cmp = c["comparator"]
    if cmp in BASELINE_COMPARATORS:
        if baseline is None or baseline == 0:
            return {**out, "met": False, "status": "no_data"}
        change = (value - baseline) / abs(baseline) * 100
        out["change_pct"] = str(change.quantize(Decimal("0.1")))
        met = change >= target if cmp == "improve_pct" else -change >= target
    else:
        met = {"gte": value >= target, "lte": value <= target, "gt": value > target, "lt": value < target}[cmp]
    return {**out, "met": bool(met), "status": "met" if met else "not_met"}


def score(results: list[dict]) -> Decimal | None:
    """Weighted share of met criteria among those with data."""
    judged = [r for r in results if r["status"] != "no_data"]
    total = sum(Decimal(r["weight"]) for r in judged)
    if not judged or total == 0:
        return None
    return (sum(Decimal(r["weight"]) for r in judged if r["met"]) / total).quantize(Decimal("0.0001"))


def evaluate(hotel: Hotel, pilot: dict, *, until: date | None = None) -> dict:
    """Every criterion over [start_date, min(until, end_date)] against the
    stored baseline. {period_start, period_end, criteria[], score, all_met}."""
    end = min(until or pilot["end_date"], pilot["end_date"])
    start = pilot["start_date"]
    base = pilot.get("baseline_metrics") or {}
    results = []
    for c in pilot.get("success_criteria") or []:
        value = metric_value(hotel, c["metric_key"], start, end) if end >= start else None
        b = base.get(c["metric_key"])
        results.append(judge_criterion(c, value, Decimal(str(b)) if b is not None else None))
    s = score(results)
    return {"period_start": start.isoformat(), "period_end": end.isoformat(), "criteria": results,
            "score": None if s is None else str(s),
            "all_met": bool(results) and all(r["met"] for r in results)}


# ─── Store ───


def _out(row: dict) -> dict:
    out = dict(row)
    minor = row.get("conversion_price_minor")
    out["conversion_price"] = None if minor is None else money.money_str(int(minor), row["currency"])
    return out


def get_pilot(cur: Any, hotel_slug: str, pilot_id: int) -> dict:
    cur.execute(f"SELECT {_COLS} FROM trs_pilots WHERE id = %s AND hotel_slug = %s", (pilot_id, hotel_slug))
    row = cur.fetchone()
    if not row:
        raise not_found("Pilot not found", "pilot_not_found")
    return _out(dict(row))


def list_pilots(cur: Any, hotel_slug: str) -> list[dict]:
    cur.execute(f"SELECT {_COLS} FROM trs_pilots WHERE hotel_slug = %s ORDER BY start_date DESC, id DESC", (hotel_slug,))
    return [_out(dict(r)) for r in cur.fetchall()]


def capture_baseline(hotel: Hotel, criteria: list[dict], start: date | None, end: date | None) -> dict:
    """{metric_key: value as str} over the baseline period (skipped metrics
    are absent, and their baseline comparators read no_data)."""
    if not start or not end:
        return {}
    out = {}
    for key in sorted({c["metric_key"] for c in criteria}):
        v = metric_value(hotel, key, start, end)
        if v is not None:
            out[key] = str(v)
    return out


def _check_dates(start: date, end: date, b0: date | None, b1: date | None) -> None:
    if end < start:
        raise bad_request("end_date is before start_date", "invalid_dates")
    if (b0 is None) != (b1 is None):
        raise bad_request("give both baseline_start and baseline_end, or neither", "invalid_baseline")
    if b0 and b1 and (b1 < b0 or b1 >= start):
        raise bad_request("the baseline must end before the pilot starts", "invalid_baseline")


def create_pilot(cur: Any, ctx: Any, hotel_slug: str, body: PilotIn) -> dict:
    hotel = get_hotel(cur, hotel_slug)
    _check_dates(body.start_date, body.end_date, body.baseline_start, body.baseline_end)
    _validate_metrics(body.success_criteria)
    criteria = [c.model_dump(mode="json") for c in body.success_criteria]
    baseline = capture_baseline(hotel, criteria, body.baseline_start, body.baseline_end)
    price = None if body.conversion_price is None else money.to_minor(body.conversion_price, hotel.currency)
    cur.execute(
        f"""
        INSERT INTO trs_pilots (hotel_slug, name, modules, start_date, end_date, success_criteria, baseline_start,
                                baseline_end, baseline_metrics, conversion_price_minor, currency, terms, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING {_COLS}
        """,
        (hotel_slug, body.name, body.modules, body.start_date, body.end_date, Json(criteria), body.baseline_start,
         body.baseline_end, Json(baseline), price, hotel.currency, Json(body.terms),
         getattr(getattr(ctx, "principal", None), "user_id", None)),
    )
    row = dict(cur.fetchone())
    audit(cur, ctx, "trust.pilot.create", "trs_pilot", str(row["id"]),
          after={"name": row["name"], "modules": row["modules"], "start_date": str(row["start_date"]),
                 "end_date": str(row["end_date"]), "conversion_price_minor": price}, hotel_slug=hotel_slug)
    return _out(row)


def update_pilot(cur: Any, ctx: Any, hotel_slug: str, pilot_id: int, patch: PilotPatch) -> dict:
    """Edit a draft or active pilot. Changing the criteria or the baseline
    period re-captures the baseline; a completed pilot is frozen (409)."""
    before = get_pilot(cur, hotel_slug, pilot_id)
    if before["status"] not in ("draft", "active"):
        raise ClefsError(409, "Only a draft or active pilot can be edited", "pilot_frozen")
    hotel = get_hotel(cur, hotel_slug)
    data = patch.model_dump(exclude_unset=True)
    start = data.get("start_date", before["start_date"])
    end = data.get("end_date", before["end_date"])
    b0 = data.get("baseline_start", before["baseline_start"])
    b1 = data.get("baseline_end", before["baseline_end"])
    _check_dates(start, end, b0, b1)
    criteria = before["success_criteria"]
    if patch.success_criteria is not None:
        _validate_metrics(patch.success_criteria)
        criteria = [c.model_dump(mode="json") for c in patch.success_criteria]
    baseline = before["baseline_metrics"]
    if any(k in data for k in ("success_criteria", "baseline_start", "baseline_end")):
        baseline = capture_baseline(hotel, criteria, b0, b1)
    price = before["conversion_price_minor"]
    if "conversion_price" in data:
        price = None if patch.conversion_price is None else money.to_minor(patch.conversion_price, hotel.currency)
    cur.execute(
        f"""
        UPDATE trs_pilots SET name = %s, modules = %s, start_date = %s, end_date = %s, success_criteria = %s,
               baseline_start = %s, baseline_end = %s, baseline_metrics = %s, conversion_price_minor = %s,
               terms = %s, updated_at = NOW()
        WHERE id = %s AND hotel_slug = %s RETURNING {_COLS}
        """,
        (data.get("name", before["name"]), data.get("modules", before["modules"]), start, end, Json(criteria), b0, b1,
         Json(baseline), price, Json(data.get("terms", before["terms"])), pilot_id, hotel_slug),
    )
    row = dict(cur.fetchone())
    audit(cur, ctx, "trust.pilot.update", "trs_pilot", str(pilot_id), before={k: str(before.get(k)) for k in data},
          after={k: str(row.get(k)) for k in data if k in row}, hotel_slug=hotel_slug)
    return _out(row)


def set_status(cur: Any, ctx: Any, hotel_slug: str, pilot_id: int, status: str) -> dict:
    before = get_pilot(cur, hotel_slug, pilot_id)
    if status not in TRANSITIONS.get(before["status"], set()):
        raise ClefsError(409, f"A {before['status']} pilot cannot become {status}", "illegal_transition")
    outcome = before["outcome"]
    if status == "completed":
        # The final evaluation is frozen with the pilot, so the report does
        # not drift when later data arrives.
        outcome = evaluate(get_hotel(cur, hotel_slug), before)
    cur.execute(f"UPDATE trs_pilots SET status = %s, outcome = %s, updated_at = NOW() WHERE id = %s RETURNING {_COLS}",
                (status, Json(outcome), pilot_id))
    row = dict(cur.fetchone())
    audit(cur, ctx, f"trust.pilot.{status}", "trs_pilot", str(pilot_id), before={"status": before["status"]},
          after={"status": status}, hotel_slug=hotel_slug)
    return _out(row)
