"""
Xero Reports connector (core.oauth provider `xero`, 04 spec 6.3).

- GET https://api.xero.com/connections returns the tenant (stored as the
  connection's account_ref and fin_connections.tenant_ref).
- GET .../Reports/ProfitAndLoss?fromDate&toDate&standardLayout=true and
  .../Reports/TrialBalance?date, with the header xero-tenant-id.
  `standardLayout` is UNVERIFIED (04 spec 7.7).
- Rows are parsed as Section / Row / SummaryRow; account ids come from the
  cell Attributes (id "account"). The Journals endpoint is never used (it
  needs the Advanced tier).

Xero ToS: data from here is mapped for this hotel only (data_origin 'xero'
never reaches fin_mapping_memory or another hotel's examples).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from clefs.core import oauth
from clefs.modules.finance import calc
from clefs.modules.finance.connectors.common import Report, get_json, section_sign, to_minor

API = "https://api.xero.com"
PROVIDER = "xero"
_CODE_IN_NAME = re.compile(r"^(?P<name>.*?)\s*\((?P<code>[\w.-]+)\)\s*$")


def tenant_id(cur: Any, hotel_slug: str, token: str) -> str:
    conn = oauth.get_connection(cur, hotel_slug, PROVIDER) or {}
    if conn.get("account_ref"):
        return conn["account_ref"]
    rows = get_json(f"{API}/connections", token=token, provider=PROVIDER)
    tenants = [r for r in rows or [] if (r.get("tenantType") or "ORGANISATION") == "ORGANISATION"]
    if not tenants:
        raise oauth.NotConnected("the Xero connection has no organisation", provider=PROVIDER)
    tid = tenants[0]["tenantId"]
    oauth.set_account_ref(cur, hotel_slug, PROVIDER, tid, meta={"tenant_name": tenants[0].get("tenantName")})
    return tid


def _cells(row: dict) -> list[dict]:
    return row.get("Cells") or []


def _account_id(cell: dict) -> str | None:
    for a in cell.get("Attributes") or []:
        if a.get("Id") == "account":
            return a.get("Value")
    return None


def parse_pl(payload: dict, currency: str) -> Report:
    """A Xero ProfitAndLoss report for one period -> Report. The first value
    column is the period; expense sections are negated (income positive)."""
    rep = (payload.get("Reports") or [{}])[0]
    out = Report(meta={"report_id": rep.get("ReportID"), "report_date": rep.get("ReportDate"),
                       "titles": rep.get("ReportTitles")})
    for section in rep.get("Rows") or []:
        if section.get("RowType") != "Section":
            continue
        title = section.get("Title") or ""
        kind, sign = section_sign(title)
        for row in section.get("Rows") or []:
            cells = _cells(row)
            if len(cells) < 2:
                continue
            label = str(cells[0].get("Value") or "").strip()
            amount = to_minor(cells[1].get("Value"), currency)
            if row.get("RowType") == "Row" and not title and re.match(r"(?i)^net (profit|loss|income)", label):
                out.net_profit_minor = amount
                continue
            if row.get("RowType") != "Row" or amount is None or not title:
                continue  # SummaryRow totals and computed lines (Gross Profit)
            out.lines.append({"code": _account_id(cells[0]) or _account_id(cells[1]), "name": label,
                              "group_raw": title, "type_raw": kind, "amount_minor": sign * amount,
                              "row_ref": {"section": title}})
    return out


def parse_tb(payload: dict, currency: str) -> Report:
    """A Xero TrialBalance: [Account, Debit, Credit, YTD Debit, YTD Credit];
    the month's Debit and Credit columns are read."""
    rep = (payload.get("Reports") or [{}])[0]
    out = Report(meta={"report_id": rep.get("ReportID"), "report_date": rep.get("ReportDate")})
    for section in rep.get("Rows") or []:
        if section.get("RowType") != "Section":
            continue
        for row in section.get("Rows") or []:
            cells = _cells(row)
            if row.get("RowType") != "Row" or len(cells) < 3:
                continue
            raw = str(cells[0].get("Value") or "").strip()
            m = _CODE_IN_NAME.match(raw)
            dr = to_minor(cells[1].get("Value"), currency) or 0
            cr = to_minor(cells[2].get("Value"), currency) or 0
            out.debit_minor += dr
            out.credit_minor += cr
            out.lines.append({"code": _account_id(cells[0]) or (m.group("code") if m else None),
                              "name": m.group("name") if m else raw, "group_raw": section.get("Title"),
                              "type_raw": section.get("Title"), "amount_minor": cr - dr,
                              "row_ref": {"section": section.get("Title")}})
    return out


def fetch_pl(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    token = oauth.get_access_token(cur, hotel_slug, PROVIDER)
    tid = tenant_id(cur, hotel_slug, token)
    payload = get_json(f"{API}/api.xro/2.0/Reports/ProfitAndLoss", token=token, provider=PROVIDER,
                       params={"fromDate": month.isoformat(), "toDate": calc.month_end(month).isoformat(),
                               "standardLayout": "true"}, headers={"xero-tenant-id": tid})
    rep = parse_pl(payload, currency)
    rep.meta.update({"tenant_ref": tid, "basis": "accrual"})
    return rep


def fetch_tb(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    token = oauth.get_access_token(cur, hotel_slug, PROVIDER)
    tid = tenant_id(cur, hotel_slug, token)
    payload = get_json(f"{API}/api.xro/2.0/Reports/TrialBalance", token=token, provider=PROVIDER,
                       params={"date": calc.month_end(month).isoformat()}, headers={"xero-tenant-id": tid})
    rep = parse_tb(payload, currency)
    rep.meta.update({"tenant_ref": tid})
    return rep
