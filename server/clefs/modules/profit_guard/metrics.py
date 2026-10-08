"""Profit Guard metrics (02 spec 3.9), registered at import (jobs.py
imports this file). Money is minor units in the hotel's currency; rows in
another currency are left out rather than mislabelled (findings) or
converted with the rate stored on the line (statement commission).

Findings metrics never count internal-only rows (Inquiry Guard's OTA
leaks, Canon 19), STRUCTURAL commission (it is a contract term, shown in
net ADR) or COMPLIANCE items (never in money totals). Recovered money is
the ledger's credit events, so it is dated when the credit landed.
"""

from __future__ import annotations

from clefs.core.metrics import Dimension, Metric, register_metric

PERM = "profit.view"
HOTEL_CCY = "f.currency = (SELECT h.currency FROM hotels h WHERE h.slug = f.hotel_slug)"
# T5 (a duplicate Tourism Dirham) is RECOVERABLE for the guest, not OTA leakage: tax checks stay out.
TAX_CHECKS = "f.detector NOT IN ('T1', 'T2', 'T3', 'T4', 'T5', 'T6', 'T7', 'TX1')"
MONEY_FINDINGS = f"NOT f.internal_only AND f.class IN ('RECOVERABLE', 'PREVENTABLE') AND {TAX_CHECKS} AND {HOTEL_CCY}"

_F = "FROM pfg_findings f"
_F_DIMS = (
    Dimension("channel", "Channel", "f.ota_code",
              "SELECT DISTINCT ota_code FROM pfg_findings WHERE hotel_slug = %(hotel)s AND ota_code IS NOT NULL"),
    Dimension("detector", "Leak check", "f.detector",
              "SELECT DISTINCT detector FROM pfg_findings WHERE hotel_slug = %(hotel)s"),
    Dimension("class", "Class", "f.class"),
    Dimension("status", "Status", "f.status"),
)
_E = "FROM pfg_finding_events e JOIN pfg_findings f ON f.id = e.finding_id"
_L = "FROM pfg_ota_statement_lines l JOIN pfg_ota_statements s ON s.id = l.statement_id"
_L_DIMS = (
    Dimension("channel", "Channel", "l.ota_code",
              "SELECT DISTINCT ota_code FROM pfg_ota_statement_lines WHERE hotel_slug = %(hotel)s"),
)
_CE = "FROM pfg_channel_economics c"
_CE_DIMS = (
    Dimension("channel", "Channel", "c.channel",
              "SELECT DISTINCT channel FROM pfg_channel_economics WHERE hotel_slug = %(hotel)s"),
)
_V = "FROM pfg_virtual_cards v"
_TD = "FROM pfg_td_ledger t"


def _reg(key: str, label: str, description: str, *, source: str, hotel_column: str, time_column: str,
         time_kind: str, measure: str, unit: str = "money", **kw) -> None:
    register_metric(Metric(
        key=key, label=label, description=description, unit=unit, source=source, hotel_column=hotel_column,
        time_column=time_column, time_kind=time_kind, measure=measure, permission=PERM,
        currency="hotel" if unit == "money" else None, **kw,
    ))


# ─── Leakage and recovery ───

_reg("pg.leakage_detected_aed", "OTA leakage found",
     "Recoverable and preventable OTA leakage found (dismissed findings left out), by the day it was found",
     source=_F, hotel_column="f.hotel_slug", time_column="f.created_at", time_kind="timestamptz",
     measure="SUM(f.amount_minor)", where=f"{MONEY_FINDINGS} AND f.status <> 'DISMISSED'", dimensions=_F_DIMS,
     higher_is_better=False, synonyms=("ota leakage", "leakage found", "money found"))
_reg("pg.leakage_recovered_aed", "OTA leakage recovered",
     "Credits matched on OTA credit notes, statements and payouts (or recorded by a person), by credit date",
     source=_E, hotel_column="e.hotel_slug", time_column="e.at", time_kind="timestamptz",
     measure="SUM(e.amount_minor)", where=f"e.channel = 'credit' AND {MONEY_FINDINGS}",
     dimensions=tuple(d for d in _F_DIMS if d.key != "status"), higher_is_better=True,
     synonyms=("recovered", "money recovered", "credits"))
_reg("pg.recovery_rate", "Recovery rate",
     "Recovered as a share of recoverable leakage found in the period (dismissed findings left out)",
     source=_F, hotel_column="f.hotel_slug", time_column="f.created_at", time_kind="timestamptz",
     measure="100.0 * SUM(f.recovered_minor) / NULLIF(SUM(f.amount_minor), 0)", unit="percent",
     where=f"{MONEY_FINDINGS} AND f.class = 'RECOVERABLE' AND f.status <> 'DISMISSED'",
     dimensions=tuple(d for d in _F_DIMS if d.key in ("channel", "detector")), higher_is_better=True)
_reg("pg.open_findings", "Open findings", "Findings still detected, accepted or disputed, by the day found",
     source=_F, hotel_column="f.hotel_slug", time_column="f.created_at", time_kind="timestamptz",
     measure="COUNT(*)", unit="count",
     where="NOT f.internal_only AND f.class <> 'STRUCTURAL' AND f.status IN ('DETECTED', 'ACCEPTED', 'DISPUTED')",
     dimensions=_F_DIMS, higher_is_better=False)

# ─── Commission ───

_LINES = "s.doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT', 'CREDIT_NOTE', 'DEBIT_NOTE') AND l.check_in IS NOT NULL"
_reg("pg.commission_paid_aed", "OTA commission invoiced",
     "Commission on OTA invoices net of credit notes, converted at the line's stored rate, by check-in date",
     source=_L, hotel_column="l.hotel_slug", time_column="l.check_in", time_kind="date",
     measure="SUM(ROUND(l.commission_minor * COALESCE(l.fx_rate, 1)))", where=_LINES, dimensions=_L_DIMS,
     higher_is_better=False, synonyms=("commission", "ota commission"))
_reg("pg.effective_commission_pct", "Effective commission rate",
     "Commission as a share of the commissionable amount on OTA invoices, by check-in date",
     source=_L, hotel_column="l.hotel_slug", time_column="l.check_in", time_kind="date",
     measure="100.0 * SUM(l.commission_minor) / NULLIF(SUM(l.commissionable_minor), 0)", unit="percent",
     where=_LINES, dimensions=_L_DIMS, higher_is_better=False)

# ─── Net ADR by channel (the monthly rollup) ───

_reg("pg.commission_on_tax_aed", "Commission paid on taxes",
     "Commission landing on municipality fee, VAT or Tourism Dirham (Agoda structural, Booking.com fee setup)",
     source=_CE, hotel_column="c.hotel_slug", time_column="c.month", time_kind="date",
     measure="SUM(c.commission_on_tax_minor)", dimensions=_CE_DIMS, higher_is_better=False)
_reg("pg.net_adr_aed", "Net ADR", "Room revenue after commission, payment, FX and cancellation costs per room night",
     source=_CE, hotel_column="c.hotel_slug", time_column="c.month", time_kind="date",
     measure="SUM(c.net_minor)::numeric / NULLIF(SUM(c.room_nights), 0)", dimensions=_CE_DIMS,
     higher_is_better=True, synonyms=("net adr", "net rate"))
_reg("pg.gross_adr_aed", "Gross ADR", "What guests paid, taxes and fees included, per room night",
     source=_CE, hotel_column="c.hotel_slug", time_column="c.month", time_kind="date",
     measure="SUM(c.gross_minor)::numeric / NULLIF(SUM(c.room_nights), 0)", dimensions=_CE_DIMS,
     higher_is_better=True)
_reg("pg.net_retention_pct", "Net retention", "Net as a share of what guests paid",
     source=_CE, hotel_column="c.hotel_slug", time_column="c.month", time_kind="date",
     measure="100.0 * SUM(c.net_minor) / NULLIF(SUM(c.gross_minor), 0)", unit="percent", dimensions=_CE_DIMS,
     higher_is_better=True)

# ─── Virtual cards ───

_reg("pg.vcc_uncollected_aed", "Virtual card money not collected",
     "Balance left on OTA virtual cards that can still be charged, by charge deadline",
     source=_V, hotel_column="v.hotel_slug", time_column="v.charge_deadline", time_kind="date",
     measure="SUM(v.current_balance_minor)",
     where="v.status IN ('AVAILABLE', 'FUNDED', 'PARTIALLY_CHARGED') AND v.current_balance_minor > 0",
     dimensions=(Dimension("channel", "Channel", "v.ota_code"),), higher_is_better=False)

# ─── Tourism Dirham ───

_reg("tax.td_collected_aed", "Tourism Dirham collected", "Tourism Dirham posted on folios, by collection month",
     source=_TD, hotel_column="t.hotel_slug", time_column="t.month", time_kind="date",
     measure="SUM(t.td_collected_minor)", synonyms=("tourism dirham", "td collected"))
_reg("tax.td_remitted_aed", "Tourism Dirham remitted", "Tourism Dirham paid to DET, by collection month",
     source=_TD, hotel_column="t.hotel_slug", time_column="t.month", time_kind="date",
     measure="SUM(t.td_remitted_minor)")
