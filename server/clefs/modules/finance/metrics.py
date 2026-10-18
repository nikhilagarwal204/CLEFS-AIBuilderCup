"""
Finance metrics for Ask Clefs and the console (04 spec 6.8), registered at
import (module.py imports this).

Weekly metrics read fin_weekly (the pulse). P&L metrics read ONLY
fin_monthly_summary, which holds published months computed from confirmed
mappings, so Ask never sees an unconfirmed mapping. Money metrics use the
hotel's currency (currency='hotel'); no key carries a currency (legacy
tenants use INR). Ratios skip weeks and months whose denominator is 0.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

# ─── Weekly pulse ───

_W = dict(source="FROM fin_weekly w", hotel_column="w.hotel_slug", time_column="w.week_start", time_kind="date",
          permission="finance.weekly.view")
_HAS_DIST = "w.distribution_cost_minor IS NOT NULL AND w.available_rn > 0"

register_metric(Metric(
    key="fin.nrevpar", label="NRevPAR", unit="money", currency="hotel", higher_is_better=True,
    description="Net RevPAR: rooms revenue minus distribution cost (commission and fees), per available room",
    measure=(f"SUM(w.rooms_revenue_minor - w.distribution_cost_minor) FILTER (WHERE {_HAS_DIST})::numeric"
             f" / NULLIF(SUM(w.available_rn) FILTER (WHERE {_HAS_DIST}), 0)"),
    synonyms=("net revpar", "net revenue per available room"), **_W,
))
register_metric(Metric(
    key="fin.ota_cost_por", label="OTA cost per occupied room", unit="money", currency="hotel",
    higher_is_better=False,
    description="OTA commission and fees divided by occupied room-nights across all channels",
    measure=("SUM((w.detail->>'ota_cost_minor')::bigint) FILTER (WHERE w.occupied_rn > 0)::numeric"
             " / NULLIF(SUM(w.occupied_rn) FILTER (WHERE w.detail->>'ota_cost_minor' IS NOT NULL), 0)"),
    synonyms=("ota cost per room", "commission per occupied room", "distribution cost per room"), **_W,
))
register_metric(Metric(
    key="fin.cancellation_leakage", label="Cancellation leakage", unit="money", currency="hotel",
    higher_is_better=False, description="What cancellations and no-shows cost (commission invoiced on them)",
    measure="SUM(w.cancellation_leakage_minor)", synonyms=("cancellation cost",), **_W,
))
register_metric(Metric(
    key="fin.recovery_cost", label="Service recovery cost", unit="money", currency="hotel", higher_is_better=False,
    description="Service-recovery gestures given to guests", measure="SUM(w.recovery_cost_minor)",
    synonyms=("recovery gestures", "compensation cost"), **_W,
))

# ─── Monthly P&L (published, confirmed data only) ───

_M = dict(source="FROM fin_monthly_summary s", hotel_column="s.hotel_slug", time_column="s.period_month",
          time_kind="date", permission="finance.pl.view")

for key, label, unit, measure, better, desc, syn in (
    ("fin.gop", "GOP", "money", "SUM(s.gop_minor)", True, "Gross operating profit (USALI)",
     ("gross operating profit", "operating profit")),
    ("fin.gop_margin_pct", "GOP margin", "percent", "100.0 * SUM(s.gop_minor) / NULLIF(SUM(s.total_revenue_minor), 0)",
     True, "GOP as a share of total operating revenue", ("gop margin", "operating margin")),
    ("fin.goppar", "GOPPAR", "money", "SUM(s.gop_minor)::numeric / NULLIF(SUM(s.arn) FILTER (WHERE s.arn > 0), 0)",
     True, "GOP per available room", ("gop per available room",)),
    ("fin.trevpar", "TRevPAR", "money",
     "SUM(s.total_revenue_minor)::numeric / NULLIF(SUM(s.arn) FILTER (WHERE s.arn > 0), 0)", True,
     "Total operating revenue per available room", ("total revenue per available room",)),
    ("fin.revpar_pl", "RevPAR (P&L)", "money",
     "SUM(s.rooms_revenue_minor)::numeric / NULLIF(SUM(s.arn) FILTER (WHERE s.arn > 0), 0)", True,
     "P&L rooms revenue per available room", ("revpar from the p&l",)),
    ("fin.flow_through_pct", "Flow-through", "percent", "100.0 * AVG(s.flow_through)", True,
     "Change in GOP over change in revenue against the same month last year (else the prior month)",
     ("flow through",)),
    ("fin.labour_pct", "Labour cost %", "percent",
     "100.0 * SUM(s.payroll_minor) / NULLIF(SUM(s.total_revenue_minor), 0)", False,
     "All payroll as a share of total operating revenue", ("payroll percentage", "labour cost", "staff cost")),
):
    register_metric(Metric(key=key, label=label, unit=unit, measure=measure, higher_is_better=better, description=desc,
                           synonyms=syn, currency="hotel" if unit == "money" else None, **_M))

_LINES = ("FROM fin_monthly_summary s CROSS JOIN LATERAL jsonb_each_text(COALESCE(s.lines, '{}'::jsonb)) AS l(k, v)")
_IS_REV = "(l.k LIKE '%%.revenue' OR l.k = 'misc.income')"
_DEPT = Dimension("department", "Department", "split_part(l.k, '.', 1)")

register_metric(Metric(
    key="fin.dept_profit", label="Departmental profit", unit="money", currency="hotel", higher_is_better=True,
    description="Revenue minus departmental expenses per operated department (rooms, F&B, other operated, misc)",
    source=_LINES, hotel_column="s.hotel_slug", time_column="s.period_month", time_kind="date",
    measure="SUM(l.v::bigint)", where="split_part(l.k, '.', 1) IN ('rooms', 'fnb', 'ood', 'misc')",
    dimensions=(_DEPT,), permission="finance.pl.view", synonyms=("department profit",),
))
register_metric(Metric(
    key="fin.pl_line", label="P&L line", unit="money", currency="hotel",
    description="A USALI line as published: revenue as income, every other line as a cost",
    source=_LINES, hotel_column="s.hotel_slug", time_column="s.period_month", time_kind="date",
    measure=f"SUM(CASE WHEN {_IS_REV} THEN l.v::bigint ELSE -(l.v::bigint) END)",
    dimensions=(Dimension("usali_line", "USALI line", "l.k"), _DEPT), permission="finance.pl.view",
    synonyms=("p&l line", "expense line", "cost line"),
))

# ─── Clefs-verified profit ───

register_metric(Metric(
    key="fin.verified_profit", label="Clefs-verified profit", unit="money", currency="hotel", higher_is_better=True,
    description="Verified action outcomes that count toward verified profit, net of reversals, by verified date",
    source="FROM actions a JOIN hotels h ON h.slug = a.hotel_slug", hotel_column="a.hotel_slug",
    time_column="a.verified_at", time_kind="timestamptz", measure="SUM(a.outcome_minor)",
    where="a.status = 'verified' AND a.counts_toward_verified_profit AND a.currency = h.currency",
    dimensions=(Dimension("module", "Module", "a.module"),), permission="finance.view",
    synonyms=("verified profit", "clefs profit"),
))
register_metric(Metric(
    key="fin.verified_profit_par", label="Clefs-verified profit per available room", unit="money", currency="hotel",
    higher_is_better=True, description="Clefs-verified profit of published months per available room",
    measure="SUM(s.verified_profit_minor)::numeric / NULLIF(SUM(s.arn) FILTER (WHERE s.arn > 0), 0)",
    synonyms=("verified profit per room",), **{**_M, "permission": "finance.view"},
))
