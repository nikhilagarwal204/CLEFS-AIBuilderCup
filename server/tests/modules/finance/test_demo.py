"""Finance demo data (04 spec 7.4): only the planted anomalies fire; the goldens read fin_stat_inputs."""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from clefs.core import signals
from clefs.modules.finance import api, demo


def test_demo_seed_publishes_13_months_with_only_planted_findings(cur, hotel_uae, neighbours):
    plants = demo.seed_demo(cur, hotel_uae.slug, random.Random(20261001), date(2026, 9, 28))["finance"]
    assert len(plants["published_months"]) == 13
    aug = api.monthly_summary(cur, hotel_uae.slug, month=date(2026, 8, 1))
    assert aug["gop"] == "174000.00" and aug["gop_margin_pct"] == "32.83" and aug["goppar"] == "70.16"
    assert aug["trevpar"] == "213.71" and aug["revpar"] == "169.35" and aug["labour_pct"] == "26.98"
    assert aug["flow_through_pct"] == "62.35" and aug["flow_through_basis"] == "yoy"
    assert aug["net_profit"] == "126000.00"
    base = api.monthly_summary(cur, hotel_uae.slug, month=date(2025, 8, 1))
    assert base["total_revenue"] == "700000.00" and base["gop"] == "280000.00"
    cur.execute("SELECT DISTINCT period_month, kind FROM fin_anomalies WHERE hotel_slug = %s", (hotel_uae.slug,))
    found = {(r["period_month"], r["kind"]) for r in cur.fetchall()}
    assert {m for m, _k in found} <= {date(2026, 6, 1), date(2026, 7, 1)}, found
    assert (date(2026, 6, 1), "labour_ratio") in found and (date(2026, 7, 1), "booked_net") in found
    cur.execute("SELECT period_month FROM fin_monthly_summary WHERE hotel_slug = %s AND booked_net", (hotel_uae.slug,))
    assert [r["period_month"] for r in cur.fetchall()] == [date(2026, 7, 1)]
    jul = api.monthly_summary(cur, hotel_uae.slug, month=date(2026, 7, 1))
    assert jul["normalized"] and jul["rooms_revenue"] == "400000.00"
    # labour in the generated months stays inside [25.5%, 27.5%] with some spread
    cur.execute("""SELECT labour_pct FROM fin_monthly_summary WHERE hotel_slug = %s
                   AND period_month NOT IN ('2026-06-01', '2026-07-01')""", (hotel_uae.slug,))  # July is normalised
    lab = [Decimal(r["labour_pct"]) for r in cur.fetchall()]
    assert all(Decimal("0.255") <= v <= Decimal("0.275") for v in lab) and max(lab) - min(lab) >= Decimal("0.003")
    kinds = {s["kind"] for s in signals.list_signals(cur, hotel_uae.slug, modules=["finance"], limit=200)}
    assert {"fin.anomaly", "fin.booked_net", "fin.pl_pulse_ready"} <= kinds and "fin.mapping_needed" not in kinds
    assert plants["baseline_registered_at"]
