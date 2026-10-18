"""
Publish a reviewed month: snapshot the mapping, write fin_monthly_summary,
supersede the month's previous publication, scan for anomalies and raise the
pulse signal (04 spec 6.4-6.8).

Only confirmed mappings are ever read (Ask sees only confirmed, published
data). A booked-net month publishes its normalised view (GOP unchanged).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.audit import audit
from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.finance import anomalies, benchmarks, calc, review, summary

# ─── Summary rows ───

SUMMARY_COLS = ("total_revenue_minor", "rooms_revenue_minor", "dept_profit_minor", "undistributed_minor", "gop_minor",
                "gop_margin", "goppar_minor", "trevpar_minor", "revpar_minor", "payroll_minor", "labour_pct",
                "ebitda_minor", "net_profit_minor", "eww_pct")


def published_summary(cur: Any, hotel_slug: str, month: date) -> dict | None:
    cur.execute("SELECT * FROM fin_monthly_summary WHERE hotel_slug = %s AND period_month = %s",
                (hotel_slug, month))
    r = cur.fetchone()
    return dict(r) if r else None


def flow_base(cur: Any, hotel_slug: str, month: date) -> dict | None:
    """Same month last year, else the prior month (both published)."""
    for basis, m in (("yoy", calc.add_months(month, -12)), ("mom", calc.add_months(month, -1))):
        prev = published_summary(cur, hotel_slug, m)
        if prev and prev.get("gop_minor") is not None:
            return {**prev, "basis": basis}
    return None


def headline(cur: Any, hotel_slug: str, batch: dict, lines: list[dict], checks: dict) -> tuple[dict, dict, dict]:
    """(headline summary, as-reported summary, stat inputs). The headline is
    the normalised view when check 3 flagged the month as booked net."""
    stats = review.stat_inputs(cur, hotel_slug, batch["period_month"])
    by_line = review.confirmed_totals(lines)
    reported = summary.compute(by_line, arn=stats.get("available_rn"))
    if checks.get("booked_net") and stats.get("ota_commission_expected_minor"):
        norm = summary.compute(summary.normalise_booked_net(by_line, int(stats["ota_commission_expected_minor"])),
                               arn=stats.get("available_rn"))
        return {**norm, "normalized": True}, reported, stats
    return {**reported, "normalized": False}, reported, stats


# ─── Publish ───


def publish(cur: Any, hotel_slug: str, batch_id: int, *, actor: Any, actor_id: int | None) -> dict:
    batch = review.refresh(cur, hotel_slug, batch_id)
    if not batch:
        raise ClefsError(404, "import not found", "import_not_found")
    if batch["status"] != "review":
        raise ClefsError(409, f"this import is {batch['status']}: only a reviewed month with passing checks publishes",
                         "not_publishable")
    hotel = get_hotel(cur, hotel_slug)
    month = batch["period_month"]
    lines = review.lines_for_batch(cur, batch)
    checks = batch["checks"] or {}
    h, reported, stats = headline(cur, hotel_slug, batch, lines, checks)
    ft, basis = summary.flow_through(h["gop_minor"], h["total_revenue_minor"], flow_base(cur, hotel_slug, month))
    from clefs.modules.finance import api as fin_api

    vp = fin_api.verified_profit(cur, hotel_slug, start=month, end=calc.month_end(month))
    arn = stats.get("available_rn")
    market = benchmarks.market_index(cur, hotel_slug, month, goppar_minor=h["goppar_minor"], gop_minor=h["gop_minor"],
                                     arn=arn)
    now = clock.now()
    values = {c: h.get(c) for c in SUMMARY_COLS}
    cur.execute(
        f"""INSERT INTO fin_monthly_summary (hotel_slug, period_month, batch_id, currency, normalized,
                {", ".join(SUMMARY_COLS)}, flow_through, flow_through_basis, market_index, verified_profit_minor,
                verified_profit_par_minor, lines, booked_net, arn, published_at)
            VALUES (%s, %s, %s, %s, %s, {", ".join(["%s"] * len(SUMMARY_COLS))}, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, period_month) DO UPDATE SET batch_id = EXCLUDED.batch_id,
                currency = EXCLUDED.currency, normalized = EXCLUDED.normalized,
                {", ".join(f"{c} = EXCLUDED.{c}" for c in SUMMARY_COLS)}, flow_through = EXCLUDED.flow_through,
                flow_through_basis = EXCLUDED.flow_through_basis, market_index = EXCLUDED.market_index,
                verified_profit_minor = EXCLUDED.verified_profit_minor,
                verified_profit_par_minor = EXCLUDED.verified_profit_par_minor, lines = EXCLUDED.lines,
                booked_net = EXCLUDED.booked_net, arn = EXCLUDED.arn, published_at = EXCLUDED.published_at
            RETURNING *""",
        (hotel_slug, month, batch_id, hotel.currency, h["normalized"], *values.values(), ft, basis, Json(market),
         vp["verified_minor"], calc.div_minor(vp["verified_minor"], arn), Json(reported["by_line"]),
         bool(checks.get("booked_net")), arn, now),
    )
    row = dict(cur.fetchone())
    snapshot = {str(ln["account_id"]): ln["version"] for ln in lines}
    cur.execute(
        """UPDATE fin_import_batches SET status = 'superseded', superseded_by = %s
           WHERE hotel_slug = %s AND period_month = %s AND status = 'published' AND id <> %s""",
        (batch_id, hotel_slug, month, batch_id),
    )
    cur.execute(
        """UPDATE fin_import_batches SET status = 'published', published_by = %s, published_at = %s, map_versions = %s
           WHERE id = %s RETURNING *""",
        (actor_id, now, Json(snapshot), batch_id),
    )
    published = dict(cur.fetchone())
    top = anomalies.scan(cur, hotel_slug, month, reported, checks, hotel.currency)
    from clefs.modules.finance import signals as fin_signals

    fin_signals.pl_pulse_ready(cur, hotel_slug, month, row)
    audit(cur, actor, "finance.publish", "fin_import_batch", str(batch_id), hotel_slug=hotel_slug,
          after={"period_month": month.isoformat(), "gop_minor": row["gop_minor"], "normalized": h["normalized"]})
    return {"batch": published, "summary": row, "anomalies": top}
