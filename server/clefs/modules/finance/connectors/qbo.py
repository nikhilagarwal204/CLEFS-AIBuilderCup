"""
QuickBooks Online Reports connector (core.oauth provider `qbo`, 04 spec 6.3).

GET /v3/company/{realmId}/reports/ProfitAndLoss?start_date&end_date
&accounting_method=Accrual and reports/TrialBalance. Endpoint and parameters
are UNVERIFIED (04 spec 7.7). UAE hotels only (QBO is discontinued in
India); report reads are metered, so the sync reads once a month.

The report is nested Sections (group Income, COGS, Expenses, OtherIncome,
OtherExpenses, plus computed GrossProfit / NetOperatingIncome / NetIncome)
with Data rows whose first ColData carries the account id.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from clefs.core import oauth
from clefs.modules.finance import calc
from clefs.modules.finance.connectors.common import Report, get_json, to_minor
from clefs.settings import get_settings

PROVIDER = "qbo"
HOSTS = {"sandbox": "https://sandbox-quickbooks.api.intuit.com", "production": "https://quickbooks.api.intuit.com"}
INCOME_GROUPS = {"income", "otherincome"}
EXPENSE_GROUPS = {"cogs", "expenses", "otherexpenses"}


def base_url() -> str:
    return HOSTS.get(str(getattr(get_settings(), "QBO_ENV", "sandbox") or "sandbox"), HOSTS["sandbox"])


def _walk(rows: list[dict], group: str | None, sign: int, out: Report, currency: str) -> None:
    for row in rows or []:
        g = (row.get("group") or "").lower() or group
        if row.get("type") == "Section":
            if g == "netincome":
                cols = (row.get("Summary") or {}).get("ColData") or []
                if len(cols) >= 2:
                    out.net_profit_minor = to_minor(cols[1].get("value"), currency)
                continue
            s = 1 if g in INCOME_GROUPS else -1 if g in EXPENSE_GROUPS else sign
            _walk(((row.get("Rows") or {}).get("Row")) or [], g, s, out, currency)
            continue
        cols = row.get("ColData") or []
        if row.get("type") != "Data" or len(cols) < 2 or group not in INCOME_GROUPS | EXPENSE_GROUPS:
            continue
        amount = to_minor(cols[1].get("value"), currency)
        if amount is None:
            continue
        out.lines.append({"code": cols[0].get("id"), "name": str(cols[0].get("value") or "").strip(),
                          "group_raw": group, "type_raw": "income" if group in INCOME_GROUPS else "expense",
                          "amount_minor": sign * amount, "row_ref": {"group": group}})


def parse_pl(payload: dict, currency: str) -> Report:
    header = payload.get("Header") or {}
    out = Report(meta={"start": header.get("StartPeriod"), "end": header.get("EndPeriod"),
                       "report_currency": header.get("Currency")})
    _walk(((payload.get("Rows") or {}).get("Row")) or [], None, -1, out, currency)
    return out


def parse_tb(payload: dict, currency: str) -> Report:
    out = Report(meta={"end": (payload.get("Header") or {}).get("EndPeriod")})

    def walk(rows: list[dict]) -> None:
        for row in rows or []:
            if row.get("type") == "Section":
                walk(((row.get("Rows") or {}).get("Row")) or [])
                continue
            cols = row.get("ColData") or []
            if row.get("type") != "Data" or len(cols) < 3:
                continue
            dr = to_minor(cols[1].get("value"), currency) or 0
            cr = to_minor(cols[2].get("value"), currency) or 0
            out.debit_minor += dr
            out.credit_minor += cr
            out.lines.append({"code": cols[0].get("id"), "name": str(cols[0].get("value") or "").strip(),
                              "group_raw": None, "type_raw": None, "amount_minor": cr - dr, "row_ref": {}})

    walk(((payload.get("Rows") or {}).get("Row")) or [])
    return out


def _realm(cur: Any, hotel_slug: str) -> str:
    conn = oauth.get_connection(cur, hotel_slug, PROVIDER) or {}
    if not conn.get("account_ref"):
        raise oauth.NotConnected("QuickBooks gave no company (realmId): reconnect", provider=PROVIDER)
    return conn["account_ref"]


def fetch_pl(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    token = oauth.get_access_token(cur, hotel_slug, PROVIDER)
    realm = _realm(cur, hotel_slug)
    payload = get_json(f"{base_url()}/v3/company/{realm}/reports/ProfitAndLoss", token=token, provider=PROVIDER,
                       params={"start_date": month.isoformat(), "end_date": calc.month_end(month).isoformat(),
                               "accounting_method": "Accrual"})
    rep = parse_pl(payload, currency)
    rep.meta.update({"tenant_ref": realm, "basis": "accrual"})
    return rep


def fetch_tb(cur: Any, hotel_slug: str, month: date, currency: str) -> Report:
    token = oauth.get_access_token(cur, hotel_slug, PROVIDER)
    realm = _realm(cur, hotel_slug)
    payload = get_json(f"{base_url()}/v3/company/{realm}/reports/TrialBalance", token=token, provider=PROVIDER,
                       params={"start_date": month.isoformat(), "end_date": calc.month_end(month).isoformat(),
                               "accounting_method": "Accrual"})
    rep = parse_tb(payload, currency)
    rep.meta.update({"tenant_ref": realm})
    return rep
