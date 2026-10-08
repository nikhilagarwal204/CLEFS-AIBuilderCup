"""
Finance demo data (04 spec 7.4): 13 months of P&L (2025-08 .. 2026-08) as one
multi-month CSV with mixed EN/AR account names, imported through core ingest,
mapped, checked and published in order, then a registered baseline.

- 2026-08 is the golden month (6.6): TOR 530,000, GOP 174,000, ARN 2,480.
- 2025-08 has TOR 700,000 and GOP 280,000 (the flow-through base).
- 2026-06 plants the labour anomaly (TOR 450,000, payroll 142,000).
- 2026-07 plants booked net (rooms 355,900, commission line 0; PMS 400,000,
  OTA share 70%, expected commission 44,100).
- Other months are generated under constraints: labour in [25.5%, 27.5%]
  (spread >= 0.3 pp) and every other line within 1 pp of its Aug 2026 share,
  so only the planted anomalies fire. Statistical inputs are seeded as
  overrides for every month (the goldens read fin_stat_inputs, not stays).

No wall clock is read; every name carries "(DEMO)" where a name is shown.
"""

from __future__ import annotations

import random
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.modules.finance import calc

# ─── The chart of accounts (code, name, section, USALI line, Aug 2026 amount) ───

ACCOUNTS: tuple[tuple[str, str, str, str, int], ...] = (
    ("4000", "Room Revenue / إيرادات الغرف", "income", "rooms.revenue", 420_000),
    ("4100", "F&B Revenue / إيرادات الأغذية والمشروبات", "income", "fnb.revenue", 95_000),
    ("4200", "Spa and Laundry Revenue", "income", "ood.revenue", 12_000),
    ("4300", "Miscellaneous Income", "income", "misc.income", 3_000),
    ("4400", "Tourism Dirham Collected", "income", "excluded.tourism_dirham", 14_880),
    ("4410", "Municipality Fee Collected", "income", "excluded.municipality_fee", 26_000),
    ("5000", "F&B Cost of Sales", "cost_of_sales", "fnb.cost_of_sales", 32_000),
    ("6000", "رواتب الغرف", "expense", "rooms.payroll", 70_000),
    ("6010", "OTA Commissions / عمولات", "expense", "rooms.other.commissions", 55_000),
    ("6020", "Rooms Guest Supplies", "expense", "rooms.other", 25_000),
    ("6100", "F&B Salaries", "expense", "fnb.payroll", 30_000),
    ("6110", "F&B Other Expenses", "expense", "fnb.other", 8_000),
    ("6200", "Spa and Laundry Expenses", "expense", "ood.other", 5_000),
    ("6300", "Admin Salaries", "expense", "ag.payroll", 25_000),
    ("6310", "Admin and General", "expense", "ag.other", 20_000),
    ("6400", "IT Systems and Licences", "expense", "its.other", 8_000),
    ("6500", "Sales Salaries", "expense", "sm.payroll", 8_000),
    ("6510", "Marketing and Advertising", "expense", "sm.other", 10_000),
    ("6600", "Maintenance Salaries", "expense", "pom.payroll", 10_000),
    ("6610", "Repairs and Maintenance", "expense", "pom.other", 12_000),
    ("6700", "DEWA Electricity", "expense", "eww.other", 38_000),
    ("6800", "Property Insurance", "expense", "non_operating.insurance", 6_000),
    ("6900", "Depreciation", "expense", "below_line.depreciation", 30_000),
    ("6910", "Interest on Bank Loan", "expense", "below_line.interest", 12_000),
    ("6920", "Tourism Dirham Remitted", "expense", "excluded.tourism_dirham", 14_880),
    ("6930", "رسوم البلدية", "expense", "excluded.municipality_fee", 26_000),
)
LINE_OF = {code: line for code, _n, _s, line, _a in ACCOUNTS}
REVENUE = ("4000", "4100", "4200", "4300")
PAYROLL = ("6000", "6100", "6300", "6500", "6600")
OPEX = ("5000", "6010", "6020", "6110", "6200", "6310", "6400", "6510", "6610", "6700")   # non-payroll, above GOP
BELOW = ("6800", "6900", "6910")
PASS = (("4400", "6920"), ("4410", "6930"))
TOR_AUG = 530_000

FIRST, LAST = date(2025, 8, 1), date(2026, 8, 1)
JUN, JUL, AUG25 = date(2026, 6, 1), date(2026, 7, 1), date(2025, 8, 1)


def _share(code: str) -> Decimal:
    return Decimal(dict((c, a) for c, _n, _s, _l, a in ACCOUNTS)[code]) / TOR_AUG


def _round(x: Decimal) -> int:
    return int(x.quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ─── Month generation ───


def _months() -> list[date]:
    out, m = [], FIRST
    while m <= LAST:
        out.append(m)
        m = calc.next_month(m)
    return out


def month_amounts(month: date, rng: random.Random, labour: Decimal) -> dict[str, int]:
    """Positive AED amounts per account code for one month (sign comes from
    the CSV section)."""
    base = {c: a for c, _n, _s, _l, a in ACCOUNTS}
    if month == LAST:
        return dict(base)
    amounts: dict[str, int] = {}
    if month == JUL:
        amounts["4000"] = 355_900
        guess = Decimal(355_900) / _share("4000")
        for c in REVENUE[1:]:
            amounts[c] = _round(guess * (_share(c) + Decimal(str(rng.uniform(-0.003, 0.003)))))
        tor = sum(amounts[c] for c in REVENUE)
    else:
        tor = {AUG25: 700_000, JUN: 450_000}.get(month) or rng.randint(48, 69) * 10_000
        for c in REVENUE[1:]:
            amounts[c] = _round(tor * (_share(c) + Decimal(str(rng.uniform(-0.003, 0.003)))))
        amounts["4000"] = tor - sum(amounts[c] for c in REVENUE[1:])
    payroll = 142_000 if month == JUN else _round(tor * labour)
    pay_base = sum(base[c] for c in PAYROLL)
    for c in PAYROLL[1:]:
        amounts[c] = _round(Decimal(payroll) * base[c] / pay_base)
    amounts[PAYROLL[0]] = payroll - sum(amounts[c] for c in PAYROLL[1:])
    shift = Decimal("-0.00579") if month == AUG25 else Decimal(0)
    for c in OPEX:
        jitter = Decimal(0) if month == AUG25 else Decimal(str(rng.uniform(-0.006, 0.006)))
        amounts[c] = max(0, _round(tor * (_share(c) + shift + jitter)))
    if month == AUG25:
        # GOP exactly 280,000: the last opex line absorbs the rounding.
        amounts[OPEX[-1]] = tor - payroll - 280_000 - sum(amounts[c] for c in OPEX[:-1])
    if month == JUL:
        amounts["6010"] = 0          # booked net: no commission line
    for c in BELOW:
        amounts[c] = base[c]
    for inc, exp in PASS:
        amounts[inc] = amounts[exp] = _round(Decimal(base[inc]) * Decimal(amounts["4000"]) / base["4000"])
    return amounts


def labour_path(rng: random.Random, n: int) -> list[Decimal]:
    """Labour % for the generated months: in [25.5%, 27.5%], not all equal."""
    vals = [Decimal(str(round(rng.uniform(0.2555, 0.2745), 4))) for _ in range(n)]
    vals[0], vals[-1] = Decimal("0.2560"), Decimal("0.2720")     # the spread is at least 0.3 pp
    return vals


def statement_csv(table: dict[date, dict[str, int]]) -> bytes:
    months = sorted(table)
    head = ["Account Code", "Account"] + [m.strftime("%b %Y") for m in months]
    out = [",".join(head)]
    sections = (("income", "Income"), ("cost_of_sales", "Cost of Sales"), ("expense", "Operating Expenses"))
    for key, title in sections:
        out.append(",".join(["", title] + [""] * len(months)))
        for code, name, sec, _line, _a in ACCOUNTS:
            if sec == key:
                out.append(",".join([code, f'"{name}"'] + [str(table[m][code]) for m in months]))
    net = []
    for m in months:
        t = table[m]
        net.append(str(sum(t[c] if s == "income" else -t[c] for c, _n, s, _l, _a in ACCOUNTS)))
    out.append(",".join(["", "Net Profit"] + net))
    return ("\n".join(out) + "\n").encode("utf-8")


# ─── Seeding ───


def _stat_overrides(month: date, a: dict[str, int], rng: random.Random) -> dict:
    """ARN 80 rooms x days; PMS rooms revenue within 1% of the books; OTA share
    about 55% with a commission line well above a quarter of the expected
    commission (so check 3 stays quiet), except the planted July."""
    days = (calc.next_month(month) - month).days
    if month == JUL:
        return {"available_rn": 80 * days, "rooms_rev_pms_minor": 40_000_000, "ota_share": "0.70",
                "ota_commission_expected_minor": 4_410_000}
    if month == LAST:
        pms = 424_100
    else:
        pms = _round(Decimal(a["4000"]) * (1 + Decimal(str(round(rng.uniform(-0.009, 0.009), 4)))))
    return {"available_rn": 80 * days, "rooms_rev_pms_minor": pms * 100, "ota_share": "0.55",
            "ota_commission_expected_minor": _round(Decimal(a["4000"]) * Decimal("0.0825")) * 100}


def seed_demo(cur: Any, hotel_slug: str, rng: random.Random, anchor: date) -> dict:
    """Import, map, check and publish 13 months, then register the baseline.
    Returns the plants for expected_plants.json."""
    from clefs import files
    from clefs.core import ingest
    from clefs.core.hotels import get_hotel
    from clefs.modules.finance import mapping, publish, review

    hotel = get_hotel(cur, hotel_slug)
    months = [m for m in _months() if m < anchor]
    labour = labour_path(rng, len(months))
    table = {m: month_amounts(m, rng, labour[i]) for i, m in enumerate(months)}
    ref = files.put_file(cur, hotel_slug, statement_csv(table), filename="pl-2025-08-to-2026-08 (DEMO).csv",
                         content_type="text/csv", purpose="finance.statement", source="import")
    core = ingest.create_batch(cur, hotel_slug, file_id=ref.id, kind="PL_STATEMENT", source_code="pl_statement",
                               via="DEMO", use_ai=False)
    if core["status"] != "COMMITTED":
        core = ingest.commit_batch(cur, hotel_slug, core["id"])
    cur.execute("SELECT id, code FROM fin_coa_accounts WHERE hotel_slug = %s", (hotel_slug,))
    for r in cur.fetchall():
        if r["code"] in LINE_OF and not (mapping.live_map(cur, r["id"]) or {}).get("locked"):
            mapping.confirm_one(cur, hotel_slug, r["id"], actor_id=None, usali_line=LINE_OF[r["code"]])
    published = []
    for m in months:
        review.set_overrides(cur, hotel_slug, m, _stat_overrides(m, table[m], rng), hotel.currency)
        cur.execute("""SELECT id FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s
                       AND status NOT IN ('superseded', 'published') ORDER BY id DESC LIMIT 1""", (hotel_slug, m))
        row = cur.fetchone()
        if row is None:
            continue
        if review.refresh(cur, hotel_slug, row["id"]).get("status") == "review":
            publish.publish(cur, hotel_slug, row["id"], actor=None, actor_id=None)
            published.append(m.isoformat())
    from clefs.modules.finance import benchmarks

    baseline = benchmarks.get_baseline(cur, hotel_slug) or benchmarks.register_baseline(
        cur, hotel_slug, actor_id=None, end_month=LAST if LAST < anchor else None)
    return {"finance": {"published_months": published, "golden_month": LAST.isoformat(),
                        "flow_through_base": AUG25.isoformat(), "labour_anomaly_month": JUN.isoformat(),
                        "booked_net_month": JUL.isoformat(),
                        "baseline_registered_at": baseline["registered_at"].isoformat()}}
