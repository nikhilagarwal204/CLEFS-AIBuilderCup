"""USALI summary goldens (04 spec 6.6) and the key table (6.4)."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from clefs.modules.finance import summary, usali

# The 2026-08 golden in minor units (AED fils), signed as the books state them.
AUG_2026 = {
    "rooms.revenue": 42_000_000, "fnb.revenue": 9_500_000, "ood.revenue": 1_200_000, "misc.income": 300_000,
    "rooms.payroll": -7_000_000, "rooms.other.commissions": -5_500_000, "rooms.other": -2_500_000,
    "fnb.cost_of_sales": -3_200_000, "fnb.payroll": -3_000_000, "fnb.other": -800_000,
    "ood.other": -500_000,
    "ag.payroll": -2_500_000, "ag.other": -2_000_000, "its.other": -800_000,
    "sm.payroll": -800_000, "sm.other": -1_000_000, "pom.payroll": -1_000_000, "pom.other": -1_200_000,
    "eww.other": -3_800_000,
    "non_operating.insurance": -600_000, "below_line.depreciation": -3_000_000, "below_line.interest": -1_200_000,
    "excluded.tourism_dirham": 0,
}


def test_gop_golden():
    s = summary.compute(AUG_2026, arn=2480)
    assert s["total_revenue_minor"] == 53_000_000
    assert s["dept_profit_minor"] == 30_500_000
    assert s["undistributed_minor"] == 13_100_000
    assert s["gop_minor"] == 17_400_000
    assert s["gop_margin"] == Decimal("0.3283")
    assert s["goppar_minor"] == 7_016          # 70.16
    assert s["trevpar_minor"] == 21_371        # 213.71
    assert s["revpar_minor"] == 16_935         # 169.35
    assert s["payroll_minor"] == 14_300_000 and s["labour_pct"] == Decimal("0.2698")
    assert s["ebitda_minor"] == 16_800_000 and s["net_profit_minor"] == 12_600_000


def test_zero_arn_and_tor_are_na():
    s = summary.compute({}, arn=0)
    assert s["goppar_minor"] is None and s["gop_margin"] is None and s["labour_pct"] is None
    assert summary.compute(AUG_2026, arn=None)["trevpar_minor"] is None


def test_flow_through_golden_and_na():
    ft, basis = summary.flow_through(17_400_000, 53_000_000,
                                     {"gop_minor": 28_000_000, "total_revenue_minor": 70_000_000, "basis": "yoy"})
    assert ft == Decimal("0.6235") and basis == "yoy"
    # |ΔTOR| under 1% of TOR: n/a
    ft, _ = summary.flow_through(17_400_000, 53_000_000,
                                 {"gop_minor": 17_000_000, "total_revenue_minor": 52_800_000, "basis": "mom"})
    assert ft is None
    assert summary.flow_through(1, 1, None) == (None, None)


def test_booked_net_normalisation_keeps_gop():
    lines = dict(AUG_2026, **{"rooms.revenue": 35_590_000, "rooms.other.commissions": 0})
    before = summary.compute(lines, arn=2480)
    after = summary.compute(summary.normalise_booked_net(lines, 4_410_000), arn=2480)
    assert after["rooms_revenue_minor"] == 40_000_000
    assert after["gop_minor"] == before["gop_minor"]
    assert after["gop_margin"] != before["gop_margin"] and after["trevpar_minor"] > before["trevpar_minor"]


def test_usali_has_exactly_45_keys_each_one_category():
    assert len(usali.KEYS) == 45 == len(set(usali.KEYS))
    cats = {"revenue", "cost_of_sales", "payroll", "other_expense", "undistributed", "mgmt_fee", "non_operating",
            "below_line", "excluded"}
    assert all(usali.LINES[k].category in cats for k in usali.KEYS)
    payroll = {k for k in usali.KEYS if usali.LINES[k].is_payroll}
    assert payroll == {"rooms.payroll", "fnb.payroll", "ood.payroll", "ag.payroll", "its.payroll", "sm.payroll",
                       "pom.payroll", "eww.payroll"}
    sql = (Path(usali.__file__).parent / "migrations" / "1001_finance_mapping.sql").read_text()
    check = sql.split("usali_line            TEXT NOT NULL CHECK (usali_line IN (")[1].split("))")[0]
    assert set(re.findall(r"'([a-z_.]+)'", check)) == set(usali.KEYS)
