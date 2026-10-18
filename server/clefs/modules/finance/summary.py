"""
The USALI summary of one month: GOP, margin, GOPPAR, TRevPAR, RevPAR,
labour %, EBITDA, net profit (04 spec 6.6).

Pure arithmetic over signed line amounts (income positive, expenses
negative) keyed by USALI line, so the same function serves the review screen,
publish, the booked-net normalised view and the baseline. Ratios with a zero
or missing denominator are None ("n/a").
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from clefs.modules.finance import calc, usali

# ─── Line totals ───


def totals_by_line(lines: list[dict]) -> dict[str, int]:
    """{usali_line: signed minor} from [{usali_line, amount_minor}]; lines
    with no mapping are left out (reconciliation reports them)."""
    out: dict[str, int] = defaultdict(int)
    for ln in lines:
        key = ln.get("usali_line")
        if key and usali.is_line(key):
            out[key] += int(ln["amount_minor"])
    return dict(out)


def normalise_booked_net(by_line: dict[str, int], expected_commission_minor: int) -> dict[str, int]:
    """Booked-net view (check 3): rooms revenue grossed up by the expected OTA
    commission and the same amount added as a rooms commission expense. GOP
    is unchanged; margin, TRevPAR and RevPAR move."""
    out = dict(by_line)
    out["rooms.revenue"] = out.get("rooms.revenue", 0) + expected_commission_minor
    out["rooms.other.commissions"] = out.get("rooms.other.commissions", 0) - expected_commission_minor
    return out


# ─── Summary ───


def compute(by_line: dict[str, int], *, arn: int | None) -> dict[str, Any]:
    """Every headline figure from signed line totals. Expenses are reported
    as positive costs; revenue as positive income."""
    def rev(key: str) -> int:
        return by_line.get(key, 0)

    def cost(pred) -> int:
        return -sum(v for k, v in by_line.items() if pred(usali.LINES[k]))

    revenue = {d: rev(f"{d}.revenue") for d in usali.OPERATED}
    revenue["misc"] = rev("misc.income")
    tor = sum(revenue.values())
    dept = {}
    for d in usali.OPERATED:
        exp = cost(lambda s, d=d: s.dept == d and s.category != "revenue")
        dept[d] = {"revenue_minor": revenue[d], "expenses_minor": exp, "profit_minor": revenue[d] - exp,
                   "payroll_minor": cost(lambda s, d=d: s.dept == d and s.is_payroll),
                   "cost_of_sales_minor": cost(lambda s, d=d: s.dept == d and s.category == "cost_of_sales")}
    dept_profit = sum(v["profit_minor"] for v in dept.values()) + revenue["misc"]
    undist = {d: {"expenses_minor": cost(lambda s, d=d: s.dept == d),
                  "payroll_minor": cost(lambda s, d=d: s.dept == d and s.is_payroll)} for d in usali.UNDISTRIBUTED}
    undistributed = sum(v["expenses_minor"] for v in undist.values())
    gop = dept_profit - undistributed
    mgmt = cost(lambda s: s.category == "mgmt_fee")
    non_op = cost(lambda s: s.category == "non_operating")
    ebitda = gop - mgmt - non_op
    below = cost(lambda s: s.category == "below_line")
    payroll = cost(lambda s: s.is_payroll)
    eww = undist["eww"]["expenses_minor"]
    return {
        "revenue": revenue, "total_revenue_minor": tor, "rooms_revenue_minor": revenue["rooms"],
        "departments": dept, "dept_profit_minor": dept_profit,
        "undistributed": undist, "undistributed_minor": undistributed,
        "gop_minor": gop, "gop_margin": calc.ratio(gop, tor if tor > 0 else None),
        "goppar_minor": calc.div_minor(gop, arn), "trevpar_minor": calc.div_minor(tor, arn),
        "revpar_minor": calc.div_minor(revenue["rooms"], arn),
        "payroll_minor": payroll, "labour_pct": calc.ratio(payroll, tor if tor > 0 else None),
        "eww_minor": eww, "eww_pct": calc.ratio(eww, tor if tor > 0 else None),
        "mgmt_fee_minor": mgmt, "non_operating_minor": non_op, "ebitda_minor": ebitda,
        "below_line_minor": below, "net_profit_minor": ebitda - below,
        "excluded_minor": sum(v for k, v in by_line.items() if usali.LINES[k].category == "excluded"),
        "arn": arn, "by_line": dict(by_line),
    }


def flow_through(cur_gop: int, cur_tor: int, base: dict | None) -> tuple[Decimal | None, str | None]:
    """ΔGOP / ΔTOR against `base` ({gop_minor, total_revenue_minor, basis});
    n/a when there is no base or |ΔTOR| < 1% of TOR."""
    if not base or base.get("gop_minor") is None or base.get("total_revenue_minor") is None:
        return None, None
    d_tor = cur_tor - int(base["total_revenue_minor"])
    d_gop = cur_gop - int(base["gop_minor"])
    if cur_tor == 0 or abs(d_tor) * 100 < abs(cur_tor):
        return None, base.get("basis")
    return calc.ratio(d_gop, d_tor), base.get("basis")


def money_view(s: dict, currency: str) -> dict:
    """Decimal strings beside every *_minor figure, percent strings beside ratios."""
    out: dict[str, Any] = {}
    for k, v in s.items():
        if k.endswith("_minor"):
            out[k] = v
            out[k.removesuffix("_minor")] = calc.money_or_none(v, currency)
        elif isinstance(v, Decimal):
            out[k] = str(v)
            out[f"{k}_display"] = calc.pct_str(v)
        elif isinstance(v, dict):
            out[k] = {kk: (money_view(vv, currency) if isinstance(vv, dict)
                           else calc.money_or_none(vv, currency) if isinstance(vv, int) else vv)
                      for kk, vv in v.items()}
        else:
            out[k] = v
    return out
