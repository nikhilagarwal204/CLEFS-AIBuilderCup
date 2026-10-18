"""Clefs-verified profit, market index, baseline immutability, metrics (04 spec 6.7, 6.8)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

import psycopg2
import pytest

from clefs.core import actions
from clefs.modules.finance import api, benchmarks, review

AUG = date(2026, 8, 1)


def _verified(cur, slug, minor: int, *, reverse: bool = False) -> int:
    a = actions.create_action(cur, slug, type="core.task", title="(DEMO) outcome", actor=None)
    actions.decide(cur, a["id"], "approve", actor=None, hotel_slug=slug)
    actions.mark_done(cur, a["id"], actor=None)
    actions.record_verification(cur, a["id"], verified=True, outcome_minor=minor, currency="AED",
                                counts_toward_verified_profit=True)
    if reverse:
        actions.reverse_verification(cur, a["id"], reason="refunded")
    return a["id"]


def test_verified_profit_per_available_room_nets_reversals(cur, hotel_uae, clock, neighbours):
    clock.freeze(datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc))
    for minor in (302_000, 121_000, 73_000):      # Profit Guard, inquiries, amenities
        _verified(cur, hotel_uae.slug, minor)
    _verified(cur, hotel_uae.slug, 100_000, reverse=True)
    review.set_overrides(cur, hotel_uae.slug, AUG, {"available_rn": 2480}, "AED")
    vp = api.verified_profit(cur, hotel_uae.slug, start=AUG, end=date(2026, 8, 31))
    assert vp["verified_minor"] == 496_000 and vp["verified_value"] == "4960.00"
    assert vp["available_rn"] == 2480 and vp["verified_par"] == "2.00"
    # without occupancy the per-room figure is n/a, not an error
    vp = api.verified_profit(cur, hotel_uae.slug, start=date(2026, 8, 3), end=date(2026, 8, 9))
    assert vp["verified_par"] is None


def _summary(cur, slug, month: date, gop: int, arn: int = 2480, goppar: int | None = None) -> None:
    cur.execute(
        """INSERT INTO fin_monthly_summary (hotel_slug, period_month, currency, total_revenue_minor, gop_minor,
               goppar_minor, arn, lines, published_at) VALUES (%s, %s, 'AED', %s, %s, %s, %s, %s, now())""",
        (slug, month, gop * 3, gop, goppar if goppar is not None else gop // arn, arn, json.dumps({})),
    )


def test_market_index_same_month_and_ytd(cur, hotel_uae):
    slug = hotel_uae.slug
    _summary(cur, slug, date(2025, 8, 1), 28_000_000)          # GOPPAR 112.90
    cur.execute("""INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url)
                   VALUES ('UAE', 'goppar_yoy', '2026-08', 'month', -40.0, 'https://example.org/a')""")
    mi = benchmarks.market_index(cur, slug, AUG, goppar_minor=7_016)
    assert mi["available"] and mi["chip"] == "month" and mi["caveat"].startswith("Market figures")
    hotel_yoy = Decimal(mi["hotel_yoy"])
    assert Decimal(mi["relative"]) == ((1 + hotel_yoy) / Decimal("0.6") - 1).quantize(Decimal("0.0001"))
    # YTD only: the hotel's YTD is used and the chip says YTD
    cur.execute("""INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url)
                   VALUES ('UAE', 'goppar_yoy', '2026-07', 'ytd', -47.8, 'https://example.org/b')""")
    for m in range(1, 8):
        _summary(cur, slug, date(2025, m, 1), 20_000_000)
        if m < 7:
            _summary(cur, slug, date(2026, m, 1), 10_000_000)
    mi = benchmarks.market_index(cur, slug, date(2026, 7, 1), goppar_minor=4_032, gop_minor=10_000_000, arn=2480)
    assert mi["available"] and mi["chip"] == "YTD" and mi["hotel_yoy"] == "-0.5000"
    assert benchmarks.market_index(cur, slug, date(2026, 5, 1), goppar_minor=1)["available"] is False


def test_market_index_is_na_on_a_loss_making_base(cur, hotel_uae):
    """Regression: (now - ly) / ly flipped sign when last year's GOPPAR was
    negative (-10 -> +10 read -200%), so an improving hotel showed as far
    behind the market. A base <= 0 now makes the chip n/a, month and YTD."""
    slug = hotel_uae.slug
    _summary(cur, slug, date(2025, 8, 1), -24_800, goppar=-10)
    cur.execute("""INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url)
                   VALUES ('UAE', 'goppar_yoy', '2026-08', 'month', -10.0, 'https://example.org/a')""")
    mi = benchmarks.market_index(cur, slug, AUG, goppar_minor=10)
    assert mi["available"] is False and "not positive" in mi["reason"] and "relative" not in mi
    assert mi["source_url"] == "https://example.org/a"
    # YTD: a loss-making last-year YTD is n/a too
    cur.execute("""INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url)
                   VALUES ('UAE', 'goppar_yoy', '2026-02', 'ytd', 5.0, 'https://example.org/b')""")
    _summary(cur, slug, date(2025, 1, 1), -1_000_000)
    _summary(cur, slug, date(2025, 2, 1), 500_000)
    _summary(cur, slug, date(2026, 1, 1), 1_000_000)
    mi = benchmarks.market_index(cur, slug, date(2026, 2, 1), goppar_minor=200, gop_minor=1_000_000, arn=2480)
    assert mi["available"] is False and "not positive" in mi["reason"]
    # a positive base still computes
    _summary(cur, slug, date(2025, 9, 1), 24_800, goppar=10)
    cur.execute("""INSERT INTO fin_market_benchmarks (region, metric, period, period_kind, value, source_url)
                   VALUES ('UAE', 'goppar_yoy', '2026-09', 'month', 0.0, 'https://example.org/c')""")
    mi = benchmarks.market_index(cur, slug, date(2026, 9, 1), goppar_minor=15)
    assert mi["available"] and mi["hotel_yoy"] == "0.5000" and mi["relative"] == "0.5000"


def test_baseline_is_immutable(cur, hotel_uae):
    slug = hotel_uae.slug
    for m in range(1, 9):
        _summary(cur, slug, date(2026, m, 1), 10_000_000 + m)
    b = benchmarks.register_baseline(cur, slug, actor_id=None)
    assert b["period_start"] == date(2025, 9, 1) and b["period_end"] == AUG and len(b["metrics"]) == 8
    for sql in ("UPDATE fin_baselines SET currency = 'USD' WHERE hotel_slug = %s",
                "DELETE FROM fin_baselines WHERE hotel_slug = %s"):
        cur.execute("SAVEPOINT imm")
        with pytest.raises(psycopg2.errors.InsufficientPrivilege):
            cur.execute(sql, (slug,))
        cur.execute("ROLLBACK TO SAVEPOINT imm")
    assert benchmarks.get_baseline(cur, slug)["currency"] == "AED"


def test_metrics_for_ask_read_published_data(cur, hotel_uae, client_as):
    slug = hotel_uae.slug
    cur.execute(
        """INSERT INTO fin_monthly_summary (hotel_slug, period_month, currency, total_revenue_minor, gop_minor,
               payroll_minor, arn, lines, published_at)
           VALUES (%s, '2026-08-01', 'AED', 53000000, 17400000, 14300000, 2480, %s, now())""",
        (slug, json.dumps({"rooms.revenue": 42_000_000, "rooms.payroll": -7_000_000, "fnb.revenue": 9_500_000})),
    )
    c = client_as("finance")
    q = {"start": "2026-08-01", "end": "2026-08-31", "grain": "total"}
    assert c.post(f"/hotels/{slug}/metrics/query", json={**q, "metric": "fin.gop"}).json()["total"] == 174000
    assert c.post(f"/hotels/{slug}/metrics/query", json={**q, "metric": "fin.goppar"}).json()["total"] == 70.16
    r = c.post(f"/hotels/{slug}/metrics/query", json={**q, "metric": "fin.pl_line", "group_by": ["usali_line"]}).json()
    rows = {row["group"]["usali_line"]: row["value"] for row in r["rows"]}
    assert rows["rooms.payroll"] == 70000 and rows["rooms.revenue"] == 420000
    assert c.post(f"/hotels/{slug}/metrics/query", json={**q, "metric": "fin.labour_pct"}).json()["total"] == \
        pytest.approx(26.98, abs=0.01)
    listed = {m["key"] for m in c.get(f"/hotels/{slug}/metrics").json()}
    assert {"fin.nrevpar", "fin.ota_cost_por", "fin.cancellation_leakage", "fin.recovery_cost", "fin.gop",
            "fin.gop_margin_pct", "fin.goppar", "fin.trevpar", "fin.revpar_pl", "fin.flow_through_pct",
            "fin.labour_pct", "fin.dept_profit", "fin.pl_line", "fin.verified_profit",
            "fin.verified_profit_par"} <= listed
    rev = {m["key"] for m in client_as("revenue").get(f"/hotels/{slug}/metrics").json()}
    assert "fin.nrevpar" in rev and "fin.gop" not in rev
