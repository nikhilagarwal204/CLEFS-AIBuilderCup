"""
Market context and the pilot baseline (04 spec 6.7).

GOPPAR is context only: the hotel's same-month year-on-year change set
against the market's, from admin-entered public figures (each with its
source_url). Seasonality-matched: month against month; when the market
figure is YTD only, the hotel's YTD is used and the chip says "YTD".
Relative = (1 + hotel_yoy) / (1 + market_yoy) - 1. No target, tier or
milestone exists anywhere in this module.

The baseline stores up to 12 trailing published months with the confirmed
mapping snapshot in fin_baselines, which a trigger keeps immutable.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.db import Json
from clefs.modules.finance import calc, settings as fin_settings

CAVEAT = "Market figures from public HotStats releases, mostly branded hotels; indicative; not a Clefs outcome."
METRIC = "goppar_yoy"   # value = percent change, e.g. -47.8


# ─── Benchmarks (clefs_admin write) ───


def add_benchmark(cur: Any, *, region: str, metric: str, period: str, period_kind: str, value: Decimal,
                  source_url: str, publisher: str | None, entered_by: int | None) -> dict:
    if not source_url.startswith(("https://", "http://")):
        raise ClefsError(400, "a benchmark needs its public source_url", "source_url_required")
    if period_kind not in ("month", "ytd"):
        raise ClefsError(400, "period_kind is month or ytd", "invalid_period_kind")
    calc.parse_month(period)  # 'YYYY-MM'; ValueError -> 422 by the router
    cur.execute(
        """INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url, publisher,
               entered_by, entered_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING *""",
        (region.strip(), metric, period[:7], period_kind, value, source_url, publisher, entered_by),
    )
    return dict(cur.fetchone())


def list_benchmarks(cur: Any, *, region: str | None = None) -> list[dict]:
    cur.execute("""SELECT * FROM fin_market_benchmarks WHERE (%s::text IS NULL OR lower(region) = lower(%s))
                   ORDER BY period DESC, id DESC LIMIT 200""", (region, region))
    return [dict(r) for r in cur.fetchall()]


def _regions(cur: Any, hotel_slug: str) -> list[str]:
    cfg = fin_settings.load(cur, hotel_slug)
    h = get_hotel(cur, hotel_slug)
    out = [cfg.market_region, h.emirate, h.country, "UAE" if (h.country or "").upper() == "AE" else None]
    return [r.lower() for r in out if r]


def _ytd(cur: Any, hotel_slug: str, month: date, current: dict | None) -> Decimal | None:
    """YTD GOPPAR (GOP / ARN) from published months Jan..month; `current`
    stands in for `month` itself while it is being published."""
    cur.execute(
        """SELECT period_month, gop_minor, arn FROM fin_monthly_summary
           WHERE hotel_slug = %s AND period_month BETWEEN %s AND %s""",
        (hotel_slug, month.replace(month=1), month),
    )
    rows = {r["period_month"]: r for r in cur.fetchall()}
    if current is not None:
        rows[month] = {"gop_minor": current.get("gop_minor"), "arn": current.get("arn")}
    if len(rows) < month.month or any(r["gop_minor"] is None or not r["arn"] for r in rows.values()):
        return None
    return calc.ratio(sum(int(r["gop_minor"]) for r in rows.values()), sum(int(r["arn"]) for r in rows.values()), 6)


def market_index(cur: Any, hotel_slug: str, month: date, *, goppar_minor: int | None,
                 gop_minor: int | None = None, arn: int | None = None) -> dict:
    regions = _regions(cur, hotel_slug)
    cur.execute(
        """SELECT * FROM fin_market_benchmarks WHERE lower(region) = ANY(%s) AND metric = %s AND period = %s
           ORDER BY (period_kind = 'month') DESC, entered_at DESC LIMIT 1""",
        (regions, METRIC, month.strftime("%Y-%m")),
    )
    bm = cur.fetchone()
    if bm is None:
        return {"available": False, "reason": "no market figure for this month", "caveat": CAVEAT}
    market_yoy = Decimal(str(bm["value"])) / 100
    if bm["period_kind"] == "month":
        cur.execute("SELECT goppar_minor FROM fin_monthly_summary WHERE hotel_slug = %s AND period_month = %s",
                    (hotel_slug, calc.add_months(month, -12)))
        ly = cur.fetchone()
        this = goppar_minor
        last = int(ly["goppar_minor"]) if ly and ly["goppar_minor"] is not None else None
    else:
        this = _ytd(cur, hotel_slug, month, {"gop_minor": gop_minor, "arn": arn} if gop_minor is not None else None)
        last = _ytd(cur, hotel_slug, calc.add_months(month, -12), None)
    # WHY a base <= 0 is n/a and not abs(): (this - last) / last flips sign on
    # a loss-making base (-10 -> +10 reads -200%), and the relative index then
    # shows a hotel that improved as far behind the market. A percentage off a
    # loss or a zero has no honest reading, so the chip says so instead.
    if last is not None and last <= 0 and this is not None:
        return {"available": False, "reason": "last year's GOPPAR for the same period was not positive, so a "
                "year-on-year change cannot be read", "caveat": CAVEAT, "market_yoy": str(market_yoy),
                "source_url": bm["source_url"]}
    hotel_yoy = calc.ratio(this - last, last, 6) if this is not None and last else None
    if hotel_yoy is None or market_yoy == -1:
        return {"available": False, "reason": "no hotel figure for the same period last year", "caveat": CAVEAT,
                "market_yoy": str(market_yoy), "source_url": bm["source_url"]}
    rel = ((1 + hotel_yoy) / (1 + market_yoy) - 1).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    return {"available": True, "chip": "YTD" if bm["period_kind"] == "ytd" else "month",
            "period_kind": bm["period_kind"], "period": bm["period"], "region": bm["region"],
            "hotel_yoy": str(hotel_yoy.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
            "market_yoy": str(market_yoy), "relative": str(rel), "source_url": bm["source_url"],
            "publisher": bm["publisher"], "caveat": CAVEAT}


# ─── Baseline (immutable) ───

BASELINE_METRICS = ("total_revenue_minor", "rooms_revenue_minor", "gop_minor", "gop_margin", "goppar_minor",
                    "trevpar_minor", "revpar_minor", "payroll_minor", "labour_pct", "ebitda_minor", "net_profit_minor",
                    "arn", "normalized")


def register_baseline(cur: Any, hotel_slug: str, *, actor_id: int | None, end_month: date | None = None) -> dict:
    """Store the 12 trailing published months ending at `end_month` (default:
    the latest published) with their mapping snapshots."""
    hotel = get_hotel(cur, hotel_slug)
    if end_month is None:
        cur.execute("SELECT MAX(period_month) AS m FROM fin_monthly_summary WHERE hotel_slug = %s", (hotel_slug,))
        end_month = (cur.fetchone() or {}).get("m")
    if end_month is None:
        raise ClefsError(409, "publish at least one month before registering a baseline", "no_published_months")
    start = calc.add_months(end_month, -11)
    cur.execute(
        """SELECT s.*, b.map_versions FROM fin_monthly_summary s LEFT JOIN fin_import_batches b ON b.id = s.batch_id
           WHERE s.hotel_slug = %s AND s.period_month BETWEEN %s AND %s ORDER BY s.period_month""",
        (hotel_slug, start, end_month),
    )
    rows = cur.fetchall()
    if not rows:
        raise ClefsError(409, "no published month in the baseline window", "no_published_months")
    metrics = {r["period_month"].isoformat(): {k: calc.dec_str(r[k]) if not isinstance(r[k], (int, bool, type(None)))
                                               else r[k] for k in BASELINE_METRICS} for r in rows}
    snapshot = {r["period_month"].isoformat(): r["map_versions"] or {} for r in rows}
    cur.execute(
        """INSERT INTO fin_baselines (hotel_slug, registered_at, period_start, period_end, map_version_snapshot,
               metrics, currency, registered_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (hotel_slug, clock.now(), start, end_month, Json(snapshot), Json(metrics), hotel.currency, actor_id),
    )
    return dict(cur.fetchone())


def get_baseline(cur: Any, hotel_slug: str) -> dict | None:
    """The first registered baseline (the pilot's before-picture); later
    registrations are kept but never replace it."""
    cur.execute("SELECT * FROM fin_baselines WHERE hotel_slug = %s ORDER BY registered_at LIMIT 1", (hotel_slug,))
    r = cur.fetchone()
    return dict(r) if r else None
