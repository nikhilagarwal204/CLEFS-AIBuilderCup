"""Anomalies, booked net, top-3 signals and the investigate action (04 spec 6.5-6.8)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from clefs.core import signals
from clefs.modules.finance import anomalies, publish, review, summary
from tests.modules.finance.conftest import AUG_LINES, AUG_ROWS, confirm_all, statement_csv

JUN, JUL = date(2026, 6, 1), date(2026, 7, 1)
LABOUR_HISTORY = ("0.2610", "0.2655", "0.2700", "0.2580", "0.2690", "0.2630")   # Dec 2025 - May 2026


def _history(cur, slug):
    for i, pct in enumerate(LABOUR_HISTORY):
        m = date(2025, 12, 1) if i == 0 else date(2026, i, 1)
        tor = 50_000_000
        lines = {"rooms.revenue": tor, "rooms.payroll": -int(tor * Decimal(pct)), "rooms.other": -2_500_000,
                 "eww.other": -3_600_000}
        cur.execute(
            """INSERT INTO fin_monthly_summary (hotel_slug, period_month, currency, total_revenue_minor, gop_minor,
                   payroll_minor, labour_pct, eww_pct, lines, arn, published_at)
               VALUES (%s, %s, 'AED', %s, 15000000, %s, %s, 0.0720, %s, 2400, now())""",
            (slug, m, tor, int(tor * Decimal(pct)), Decimal(pct), __import__("json").dumps(lines)),
        )


def test_labour_anomaly_june_by_zscore(cur, hotel_uae, neighbours):
    _history(cur, hotel_uae.slug)
    # June: TOR 450,000 with payroll 142,000 (31.56%)
    by_line = {"rooms.revenue": 45_000_000, "rooms.payroll": -8_000_000, "ag.payroll": -6_200_000,
               "rooms.other": -2_250_000, "eww.other": -3_240_000}
    s = summary.compute(by_line, arn=2400)
    assert s["labour_pct"] == Decimal("0.3156")
    found = anomalies.ratio_findings(cur, hotel_uae.slug, JUN, s)
    kinds = {f["kind"]: f for f in found}
    assert set(kinds) == {"labour_ratio"}
    f = kinds["labour_ratio"]
    assert f["zscore"] >= Decimal("2.5") and (f["value"] - f["expected"]) * 100 >= 4
    assert f["delta_minor"] > 0 and f["confidence"] > 0.5
    # a normal month (26.5%) raises nothing
    ok = summary.compute({"rooms.revenue": 45_000_000, "rooms.payroll": -11_925_000, "rooms.other": -2_250_000,
                          "eww.other": -3_240_000}, arn=2400)
    assert anomalies.ratio_findings(cur, hotel_uae.slug, JUN, ok) == []


def test_scan_raises_top_signals_and_action(cur, hotel_uae, neighbours, client_as):
    _history(cur, hotel_uae.slug)
    by_line = {"rooms.revenue": 45_000_000, "rooms.payroll": -8_000_000, "ag.payroll": -6_200_000,
               "rooms.other": -2_250_000, "eww.other": -3_240_000}
    s = summary.compute(by_line, arn=2400)
    top = anomalies.scan(cur, hotel_uae.slug, JUN, s, {"items": []}, "AED")
    assert [a["kind"] for a in top] == ["labour_ratio"]
    sig = signals.list_signals(cur, hotel_uae.slug, modules=["finance"])
    assert [x["kind"] for x in sig] == ["fin.anomaly"] and sig[0]["recommended_action"]["type"] == \
        "finance.investigate_anomaly"
    r = client_as("gm").post(f"/hotels/{hotel_uae.slug}/finance/anomalies/{top[0]['id']}/action")
    assert r.status_code == 201, r.text
    act = r.json()["action"]
    assert act["type"] == "finance.investigate_anomaly" and act["status"] == "proposed"
    # idempotent per anomaly
    again = client_as("gm").post(f"/hotels/{hotel_uae.slug}/finance/anomalies/{top[0]['id']}/action").json()
    assert again["action"]["id"] == act["id"]
    assert client_as("revenue").post(f"/hotels/{hotel_uae.slug}/finance/anomalies/{top[0]['id']}/action"
                                     ).status_code == 403


def test_booked_net_july_golden(cur, hotel_uae, upload_statement, neighbours):
    slug = hotel_uae.slug
    rows = [r if r[1] != "Net Profit" else ("", "Net Profit", 116900, None) for r in AUG_ROWS]
    upload_statement(slug, statement_csv(rows, overrides={"4000": 355900, "6010": 0}), JUL)
    cur.execute("SELECT id FROM fin_import_batches WHERE hotel_slug = %s", (slug,))
    bid = cur.fetchone()["id"]
    confirm_all(cur, slug, bid, AUG_LINES)
    review.set_overrides(cur, slug, JUL, {"available_rn": 2480, "rooms_rev_pms_minor": 40_000_000,
                                          "ota_share": "0.70", "ota_commission_expected_minor": 4_410_000}, "AED")
    b = review.refresh(cur, slug, bid)
    items = {i["key"]: i for i in b["checks"]["items"]}
    assert b["status"] == "review" and b["checks"]["booked_net"] is True
    assert items["booked_net"]["data"]["rooms_diff_pct"] == "-0.1103"
    assert items["booked_net"]["data"]["normalized_rooms_minor"] == 40_000_000
    out = publish.publish(cur, slug, bid, actor=None, actor_id=None)
    s = out["summary"]
    assert s["normalized"] and s["booked_net"] and s["rooms_revenue_minor"] == 40_000_000
    reported = s["lines"]
    gop_reported = summary.compute({k: int(v) for k, v in reported.items()}, arn=2480)["gop_minor"]
    assert s["gop_minor"] == gop_reported                     # GOP unchanged
    kinds = {x["kind"] for x in signals.list_signals(cur, slug, modules=["finance"])}
    assert {"fin.booked_net", "fin.pl_pulse_ready"} <= kinds
