"""
USALI 12th-edition taxonomy: the closed line-key table (04 spec 6.4).

One table drives everything: the `UsaliLine` Literal (the LLM enum), the
deterministic rules, the summary maths, the `fin.pl_line` metric dimensions
and the DB CHECK in 1001_finance_mapping.sql. A test asserts exactly 45 keys
and that the CHECK lists the same set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

# ─── Schedules (owner-kpis-pnl.md, a secondary source; A9) ───

SCHEDULES: dict[int, str] = {
    1: "Rooms",
    2: "Food and Beverage",
    3: "Other Operated Departments",
    4: "Miscellaneous Income",
    5: "Administrative and General",
    6: "Information and Telecommunications Systems",
    7: "Sales and Marketing",
    8: "Property Operations and Maintenance",
    9: "Energy, Water and Waste",
    10: "Management Fees",
    11: "Non-Operating Income and Expenses",
}

DEPT_LABELS: dict[str, str] = {
    "rooms": "Rooms", "fnb": "Food and Beverage", "ood": "Other Operated Departments", "misc": "Miscellaneous Income",
    "ag": "Administrative and General", "its": "Information and Telecommunications Systems",
    "sm": "Sales and Marketing", "pom": "Property Operations and Maintenance", "eww": "Energy, Water and Waste",
    "mgmt_fee": "Management Fees", "non_operating": "Non-Operating", "below_line": "Below EBITDA",
    "excluded": "Excluded (pass-through and balance sheet)",
}

Category = Literal["revenue", "cost_of_sales", "payroll", "other_expense", "undistributed", "mgmt_fee",
                   "non_operating", "below_line", "excluded"]

# The operated departments (schedules 1-3) carry departmental profit;
# misc income (4) is revenue with no department expenses.
OPERATED = ("rooms", "fnb", "ood")
UNDISTRIBUTED = ("ag", "its", "sm", "pom", "eww")


@dataclass(frozen=True)
class LineSpec:
    key: str
    schedule: int | None
    dept: str
    category: str
    is_payroll: bool
    label: str


# ─── The closed key table ───

UsaliLine = Literal[
    "rooms.revenue", "fnb.revenue", "ood.revenue",
    "rooms.cost_of_sales", "fnb.cost_of_sales", "ood.cost_of_sales",
    "rooms.payroll", "fnb.payroll", "ood.payroll",
    "rooms.other", "fnb.other", "ood.other",
    "rooms.other.commissions", "fnb.other.commissions", "ood.other.commissions",
    "misc.income",
    "ag.payroll", "its.payroll", "sm.payroll", "pom.payroll", "eww.payroll",
    "ag.other", "its.other", "sm.other", "pom.other", "eww.other",
    "sm.paid_search", "sm.display", "sm.social", "sm.loyalty",
    "mgmt_fee.base", "mgmt_fee.incentive",
    "non_operating.rent", "non_operating.property_tax", "non_operating.insurance", "non_operating.other",
    "below_line.depreciation", "below_line.amortization", "below_line.interest", "below_line.income_tax",
    "below_line.owner_drawings",
    "excluded.vat", "excluded.municipality_fee", "excluded.tourism_dirham", "excluded.balance_sheet",
]

_SCHED = {"rooms": 1, "fnb": 2, "ood": 3, "misc": 4, "ag": 5, "its": 6, "sm": 7, "pom": 8, "eww": 9,
          "mgmt_fee": 10, "non_operating": 11}

_LABEL_TAIL = {
    "revenue": "revenue", "cost_of_sales": "cost of sales", "payroll": "payroll", "other": "other expenses",
    "other.commissions": "commissions", "income": "income", "paid_search": "paid search", "display": "display",
    "social": "social", "loyalty": "loyalty", "base": "base fee", "incentive": "incentive fee", "rent": "rent",
    "property_tax": "property tax", "insurance": "insurance", "depreciation": "depreciation",
    "amortization": "amortization", "interest": "interest", "income_tax": "income tax",
    "owner_drawings": "owner drawings", "vat": "VAT", "municipality_fee": "municipality fee",
    "tourism_dirham": "Tourism Dirham", "balance_sheet": "balance sheet",
}


def _category(dept: str, tail: str) -> str:
    if dept in OPERATED:
        if tail == "revenue":
            return "revenue"
        if tail == "cost_of_sales":
            return "cost_of_sales"
        if tail == "payroll":
            return "payroll"
        return "other_expense"
    if dept == "misc":
        return "revenue"
    if dept in UNDISTRIBUTED:
        return "undistributed"
    return dept  # mgmt_fee, non_operating, below_line, excluded


def _spec(key: str) -> LineSpec:
    dept, tail = key.split(".", 1)
    label = f"{DEPT_LABELS[dept].split(' (')[0]}: {_LABEL_TAIL.get(tail, tail)}"
    return LineSpec(key=key, schedule=_SCHED.get(dept), dept=dept, category=_category(dept, tail),
                    is_payroll=tail == "payroll", label=label)


LINES: dict[str, LineSpec] = {k: _spec(k) for k in get_args(UsaliLine)}
KEYS: tuple[str, ...] = tuple(LINES)

PASS_THROUGH_KIND = {"excluded.vat": "vat", "excluded.municipality_fee": "municipality_fee",
                     "excluded.tourism_dirham": "tourism_dirham"}


def spec(key: str) -> LineSpec:
    if key not in LINES:
        raise ValueError(f"unknown USALI line {key!r}")
    return LINES[key]


def is_line(key: str | None) -> bool:
    return key in LINES


def above_gop(key: str) -> bool:
    """Revenue, departmental and undistributed lines: the ones GOP reads."""
    return LINES[key].category in ("revenue", "cost_of_sales", "payroll", "other_expense", "undistributed")


def catalog() -> list[dict]:
    """The key table for the console and the LLM prompt."""
    return [{"key": s.key, "schedule": s.schedule, "dept": s.dept, "category": s.category,
             "is_payroll": s.is_payroll, "label": s.label} for s in LINES.values()]
