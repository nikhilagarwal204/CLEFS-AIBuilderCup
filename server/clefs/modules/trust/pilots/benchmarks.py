"""
Anonymised benchmarks (03 spec 5.5).

- Only opted-in hotels (trs_benchmark_optins) are ever counted, and a
  cohort needs at least 3 of them with data for a metric and month.
- Below 5 hotels only bands are kept (top, middle, bottom third per hotel)
  and never a median: with n = 3 a median is another hotel's exact value,
  and so is any tercile cut-off. From 5 up the snapshot also stores p50
  and the band cut-offs.
- A hotel only ever sees its own band, n and (from 5) the cohort p50.

Snapshots run monthly (trust.benchmark_snapshot, the 3rd at 05:00 UTC) for
the month before, and on demand for a Clefs admin.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from clefs import jobs
from clefs.audit import audit
from clefs.core import clock, metrics
from clefs.core.errors import ClefsError, bad_request, not_found
from clefs.core.hotels import find_hotel
from clefs.db import Json
from clefs.modules.trust.pilots import store

MIN_HOTELS = 3
MEDIAN_MIN_HOTELS = 5
BANDS = ("top", "middle", "bottom")


class CohortIn(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_.-]+$")
    label: str = Field(min_length=1, max_length=120)
    criteria: dict = Field(default_factory=dict)
    metrics: list[str] = Field(min_length=1, max_length=20)
    active: bool = True


CRITERIA_KEYS = ("country", "emirate", "city", "category", "star_min", "star_max", "rooms_min", "rooms_max")


# ─── Opt-in ───


def is_opted_in(cur: Any, hotel_slug: str) -> bool:
    cur.execute("SELECT opted_in FROM trs_benchmark_optins WHERE hotel_slug = %s", (hotel_slug,))
    row = cur.fetchone()
    return bool(row and row["opted_in"])


def set_opt_in(cur: Any, ctx: Any, hotel_slug: str, opted_in: bool) -> dict:
    before = is_opted_in(cur, hotel_slug)
    user_id = getattr(getattr(ctx, "principal", None), "user_id", None)
    cur.execute(
        "INSERT INTO trs_benchmark_optins (hotel_slug, opted_in, updated_by, updated_at) VALUES (%s, %s, %s, NOW()) "
        "ON CONFLICT (hotel_slug) DO UPDATE SET opted_in = EXCLUDED.opted_in, updated_by = EXCLUDED.updated_by, "
        "updated_at = NOW()",
        (hotel_slug, opted_in, user_id),
    )
    if not opted_in:
        # Leaving removes this hotel's positions at once; the cohort figures
        # it contributed to are recomputed at the next snapshot.
        cur.execute("DELETE FROM trs_benchmark_positions WHERE hotel_slug = %s", (hotel_slug,))
    audit(cur, ctx, "trust.benchmark.opt_in" if opted_in else "trust.benchmark.opt_out", "hotel", hotel_slug,
          before={"opted_in": before}, after={"opted_in": opted_in}, hotel_slug=hotel_slug)
    return {"hotel_slug": hotel_slug, "opted_in": opted_in}


# ─── Cohorts ───


def _check_criteria(criteria: dict) -> dict:
    bad = sorted(set(criteria) - set(CRITERIA_KEYS))
    if bad:
        raise bad_request(f"unknown cohort criteria: {', '.join(bad)}", "invalid_criteria")
    return criteria


def upsert_cohort(cur: Any, ctx: Any, body: CohortIn) -> dict:
    _check_criteria(body.criteria)
    known = {m.key for m in metrics.registered_metrics()}
    unknown = sorted(set(body.metrics) - known)
    if unknown:
        raise bad_request(f"unknown metric(s): {', '.join(unknown)}", "unknown_metric")
    cur.execute(
        "INSERT INTO trs_benchmark_cohorts (key, label, criteria, metrics, active) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (key) DO UPDATE SET label = EXCLUDED.label, criteria = EXCLUDED.criteria, "
        "metrics = EXCLUDED.metrics, active = EXCLUDED.active RETURNING *",
        (body.key, body.label, Json(body.criteria), body.metrics, body.active),
    )
    row = dict(cur.fetchone())
    audit(cur, ctx, "trust.benchmark.cohort_upsert", "trs_benchmark_cohort", str(row["id"]),
          after={"key": row["key"], "criteria": row["criteria"], "metrics": row["metrics"]})
    return row


def list_cohorts(cur: Any, *, active_only: bool = False) -> list[dict]:
    cur.execute("SELECT * FROM trs_benchmark_cohorts WHERE active OR NOT %s ORDER BY key", (active_only,))
    return [dict(r) for r in cur.fetchall()]


def members(cur: Any, cohort: dict) -> list[str]:
    """Opted-in, non-archived hotels that match the cohort's criteria."""
    c = cohort.get("criteria") or {}
    where = ["o.opted_in", "COALESCE(h.status, 'active') <> 'archived'"]
    params: list[Any] = []
    for key, col in (("country", "h.country"), ("emirate", "h.emirate"), ("city", "h.city"), ("category", "h.category")):
        if c.get(key):
            vals = c[key] if isinstance(c[key], list) else [c[key]]
            where.append(f"lower({col}) = ANY(%s)")
            params.append([str(v).lower() for v in vals])
    for key, col, op in (("star_min", "h.star_rating", ">="), ("star_max", "h.star_rating", "<="),
                         ("rooms_min", "h.rooms_count", ">="), ("rooms_max", "h.rooms_count", "<=")):
        if c.get(key) is not None:
            where.append(f"{col} {op} %s")
            params.append(c[key])
    cur.execute(
        f"SELECT h.slug FROM hotels h JOIN trs_benchmark_optins o ON o.hotel_slug = h.slug WHERE {' AND '.join(where)} "
        "ORDER BY h.slug",
        params,
    )
    return [r["slug"] for r in cur.fetchall()]


def month_bounds(period: str) -> tuple[date, date]:
    y, m = (int(x) for x in period.split("-"))
    start = date(y, m, 1)
    nxt = date(y + (m == 12), m % 12 + 1, 1)
    return start, nxt - timedelta(days=1)


def assign_bands(values: dict[str, Decimal], *, higher_is_better: bool = True) -> dict[str, str]:
    """{hotel: top|middle|bottom} by rank; tied values share the better band."""
    ranked = sorted(values.items(), key=lambda kv: kv[1], reverse=higher_is_better)
    n = len(ranked)
    out: dict[str, str] = {}
    first_rank: dict[Decimal, int] = {}
    for i, (slug, v) in enumerate(ranked):
        rank = first_rank.setdefault(v, i)
        out[slug] = BANDS[min(2, rank * 3 // n)]
    return out


def _quantile(sorted_vals: list[Decimal], q: Decimal) -> Decimal:
    # Linear interpolation (the usual "type 7" definition).
    pos = (len(sorted_vals) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def snapshot(cur: Any, cohort: dict, metric_key: str, period: str) -> dict:
    """Compute one (cohort, metric, month). Below 3 hotels with data the
    snapshot is removed rather than published."""
    start, end = month_bounds(period)
    values: dict[str, Decimal] = {}
    for slug in members(cur, cohort):
        hotel = find_hotel(cur, slug)
        if hotel is None:
            continue
        v = store.metric_value(hotel, metric_key, start, end)
        if v is not None:
            values[slug] = v
    n = len(values)
    if n < MIN_HOTELS:
        cur.execute("DELETE FROM trs_benchmark_snapshots WHERE cohort_id = %s AND metric_key = %s AND period = %s",
                    (cohort["id"], metric_key, period))
        return {"cohort": cohort["key"], "metric_key": metric_key, "period": period, "n_hotels": n, "published": False}
    try:
        hib = metrics.get_metric(metric_key).higher_is_better
    except ClefsError:
        hib = None
    higher = hib is not False
    ordered = sorted(values.values())
    p50 = _quantile(ordered, Decimal("0.5")) if n >= MEDIAN_MIN_HOTELS else None
    bands = {}
    if n >= MEDIAN_MIN_HOTELS:
        bands = {"cutoffs": [str(_quantile(ordered, Decimal(1) / 3)), str(_quantile(ordered, Decimal(2) / 3))],
                 "higher_is_better": higher}
    cur.execute(
        """
        INSERT INTO trs_benchmark_snapshots (cohort_id, metric_key, period, n_hotels, p50, bands, computed_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON CONFLICT (cohort_id, metric_key, period) DO UPDATE SET n_hotels = EXCLUDED.n_hotels, p50 = EXCLUDED.p50,
            bands = EXCLUDED.bands, computed_at = NOW()
        RETURNING id
        """,
        (cohort["id"], metric_key, period, n, p50, Json(bands)),
    )
    sid = cur.fetchone()["id"]
    cur.execute("DELETE FROM trs_benchmark_positions WHERE snapshot_id = %s", (sid,))
    for slug, band in assign_bands(values, higher_is_better=higher).items():
        cur.execute("INSERT INTO trs_benchmark_positions (snapshot_id, hotel_slug, band) VALUES (%s, %s, %s)", (sid, slug, band))
    return {"cohort": cohort["key"], "metric_key": metric_key, "period": period, "n_hotels": n, "published": True}


def run_snapshots(cur: Any, period: str) -> list[dict]:
    return [snapshot(cur, c, m, period) for c in list_cohorts(cur, active_only=True) for m in c["metrics"]]


# ─── Hotel view ───


def for_hotel(cur: Any, hotel_slug: str, *, period: str | None = None) -> dict:
    """What one hotel may see: its own band per cohort metric, n, and the
    cohort p50 only from 5 hotels. A hotel that has not opted in sees
    nothing (benchmarks are give-to-get)."""
    opted = is_opted_in(cur, hotel_slug)
    if not opted:
        return {"opted_in": False, "items": []}
    cur.execute(
        """
        SELECT c.key AS cohort_key, c.label AS cohort_label, s.metric_key, s.period, s.n_hotels, s.p50, s.bands,
               s.computed_at, p.band
        FROM trs_benchmark_positions p
        JOIN trs_benchmark_snapshots s ON s.id = p.snapshot_id
        JOIN trs_benchmark_cohorts c ON c.id = s.cohort_id AND c.active
        WHERE p.hotel_slug = %s AND (%s::text IS NULL OR s.period = %s)
        ORDER BY s.period DESC, c.key, s.metric_key
        """,
        (hotel_slug, period, period),
    )
    items = []
    for r in cur.fetchall():
        show_exact = int(r["n_hotels"]) >= MEDIAN_MIN_HOTELS
        try:
            m = metrics.get_metric(r["metric_key"])
            label, unit = m.label, m.unit
        except ClefsError:
            label, unit = r["metric_key"], None
        items.append({
            "cohort_key": r["cohort_key"], "cohort_label": r["cohort_label"], "metric_key": r["metric_key"],
            "metric_label": label, "unit": unit, "period": r["period"], "n_hotels": int(r["n_hotels"]),
            "band": r["band"],
            # Belt and braces: the table refuses a p50 below 5, and the
            # serializer drops it too.
            "p50": str(r["p50"]) if show_exact and r["p50"] is not None else None,
            "cutoffs": (r["bands"] or {}).get("cutoffs") if show_exact else None,
            "bands_only": not show_exact,
            "computed_at": r["computed_at"],
        })
    return {"opted_in": True, "items": items}


def previous_month(today: date) -> str:
    first = today.replace(day=1)
    last = first - timedelta(days=1)
    return f"{last.year:04d}-{last.month:02d}"


@jobs.scheduled("trust.benchmark_snapshot", jobs.monthly_at(3, "05:00", tz="utc"), per_hotel=False, timeout_s=600)
def _snapshot_job(ctx: jobs.JobCtx) -> dict:
    period = previous_month((ctx.scheduled_for or clock.now()).date())
    return {"period": period, "snapshots": run_snapshots(ctx.cur, period)}


def get_cohort(cur: Any, cohort_id: int) -> dict:
    cur.execute("SELECT * FROM trs_benchmark_cohorts WHERE id = %s", (cohort_id,))
    row = cur.fetchone()
    if not row:
        raise not_found("Cohort not found", "cohort_not_found")
    return dict(row)
