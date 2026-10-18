"""Finance test helpers: occupancy days, stubbed neighbours, statement lines."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from clefs.modules.finance import module as _finance_module  # noqa: F401  (registers handlers, jobs, metrics)
from clefs.modules.profit_guard import api as profit_guard_api
from clefs.modules.service import api as service_api


@pytest.fixture
def occupancy(cur):
    """occupancy(slug, start, days, available, occupied, revenue_minor_per_day=0)."""

    def _put(slug: str, start: date, days: int, available: int, occupied: int, rev: int = 0) -> None:
        for i in range(days):
            cur.execute(
                """INSERT INTO occupancy_daily (hotel_slug, date, rooms_available, rooms_occupied,
                       room_revenue_minor, currency, source)
                   VALUES (%s, %s, %s, %s, %s, 'AED', 'MANUAL')
                   ON CONFLICT (hotel_slug, date) DO UPDATE SET rooms_available = EXCLUDED.rooms_available,
                       rooms_occupied = EXCLUDED.rooms_occupied, room_revenue_minor = EXCLUDED.room_revenue_minor""",
                (slug, start + timedelta(days=i), available, occupied, rev),
            )

    return _put


@pytest.fixture
def neighbours(monkeypatch):
    """Stub Profit Guard and Service answers: set .dist, .cxl, .sc, .recovery, .chan."""

    class N:
        dist: list = []
        cxl: list = []
        sc = True
        recovery = 0
        chan: list = []

    n = N()
    monkeypatch.setattr(profit_guard_api, "distribution_cost_by_night", lambda cur, slug, *, start, end: n.dist)
    monkeypatch.setattr(profit_guard_api, "cancellation_cost_by_date", lambda cur, slug, *, start, end: n.cxl)
    monkeypatch.setattr(profit_guard_api, "treat_sc_as_revenue", lambda cur, slug: n.sc)
    monkeypatch.setattr(profit_guard_api, "channel_economics", lambda cur, slug, *, start, end: n.chan)
    monkeypatch.setattr(service_api, "recovery_cost", lambda cur, slug, *, start, end: n.recovery)
    return n


def dist_row(day: date, channel: str, is_ota: bool, commission: int, pay: int = 0, be: int = 0) -> dict:
    return {"stay_date": day, "channel_code": channel, "is_ota": is_ota, "commission_minor": commission,
            "payment_fee_minor": pay, "booking_engine_fee_minor": be, "currency": "AED"}


# ─── Statements ───

# The 2026-08 golden (04 spec 6.6) as an uploaded CSV with mixed EN/AR names.
# code -> (name, amount, USALI line a person confirms)
AUG_ROWS = [
    ("", "Income", None, None),
    ("4000", "Room Revenue / إيرادات الغرف", 420000, "rooms.revenue"),
    ("4100", "F&B Revenue", 95000, "fnb.revenue"),
    ("4200", "Spa Revenue", 12000, "ood.revenue"),
    ("4300", "Miscellaneous Income", 3000, "misc.income"),
    ("4400", "Tourism Dirham Collected", 14880, "excluded.tourism_dirham"),
    ("", "Cost of Sales", None, None),
    ("5000", "F&B Cost of Sales", 32000, "fnb.cost_of_sales"),
    ("", "Operating Expenses", None, None),
    ("6000", "رواتب الغرف", 70000, "rooms.payroll"),
    ("6010", "OTA Commissions", 55000, "rooms.other.commissions"),
    ("6020", "Rooms Other Expenses", 25000, "rooms.other"),
    ("6100", "F&B Payroll", 30000, "fnb.payroll"),
    ("6110", "F&B Other Expenses", 8000, "fnb.other"),
    ("6200", "Spa Expenses", 5000, "ood.other"),
    ("6300", "A&G Payroll", 25000, "ag.payroll"),
    ("6310", "A&G Other", 20000, "ag.other"),
    ("6400", "IT Systems", 8000, "its.other"),
    ("6500", "Sales Payroll", 8000, "sm.payroll"),
    ("6510", "Marketing", 10000, "sm.other"),
    ("6600", "Maintenance Payroll", 10000, "pom.payroll"),
    ("6610", "Maintenance Other", 12000, "pom.other"),
    ("6700", "DEWA Electricity", 38000, "eww.other"),
    ("6800", "Insurance", 6000, "non_operating.insurance"),
    ("6900", "Depreciation", 30000, "below_line.depreciation"),
    ("6910", "Interest on loan", 12000, "below_line.interest"),
    ("6920", "Tourism Dirham Remitted", 14880, "excluded.tourism_dirham"),
    ("", "Net Profit", 126000, None),
]
AUG_LINES = {code: line for code, _n, _a, line in AUG_ROWS if line}


def statement_csv(rows=AUG_ROWS, overrides: dict | None = None) -> bytes:
    out = ["Account Code,Account,Amount"]
    for code, name, amount, _line in rows:
        amount = (overrides or {}).get(code, amount)
        out.append(f"{code},\"{name}\",{'' if amount is None else amount}")
    return ("\n".join(out) + "\n").encode("utf-8")


@pytest.fixture
def upload_statement(cur):
    """upload_statement(slug, data, month, kind='PL_STATEMENT', filename=...) -> core commit result."""
    from clefs import files
    from clefs.core import ingest

    def _up(slug: str, data: bytes, month: date, kind: str = "PL_STATEMENT", filename: str = "pl.csv") -> dict:
        ref = files.put_file(cur, slug, data, filename=filename, content_type="text/csv",
                             purpose="finance.statement", source="upload")
        b = ingest.create_batch(cur, slug, file_id=ref.id, kind=kind,
                                source_code="pl_statement" if kind == "PL_STATEMENT" else "trial_balance",
                                period_month=month)
        assert b["status"] == "REVIEW", (b["status"], b.get("errors"))
        return ingest.commit_batch(cur, slug, b["id"])

    return _up


def confirm_all(cur, slug: str, batch_id: int, lines_by_code: dict, *, reasons: dict | None = None) -> None:
    from clefs.modules.finance import mapping

    cur.execute("""SELECT DISTINCT a.id, a.code FROM fin_pl_lines l JOIN fin_coa_accounts a ON a.id = l.account_id
                   WHERE l.batch_id = %s""", (batch_id,))
    for r in cur.fetchall():
        mapping.confirm_one(cur, slug, r["id"], actor_id=None, usali_line=lines_by_code[r["code"]],
                            override_reason=(reasons or {}).get(r["code"]))
