"""Mapping applied: table rows -> staged canonical rows (02 spec 2.2 steps
4 and 6; 04 spec D4 for the P&L month split).

Pure functions. A bad cell makes its row ERROR (with a message naming the
field), never the batch FAILED: a person fixes or skips rows in review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from clefs.core.ingest import transforms as tf
from clefs.core.ingest.kinds import KindSchema, schema_for
from clefs.core.ingest.sniff import Table, normalize_header

# Keys that make two rows the same record within one file (later ones DUP).
DEDUPE_KEYS: dict[str, tuple[str, ...]] = {
    "PMS_RESERVATIONS": ("pms_res_id",),
    "PMS_FOLIO": ("pms_txn_id",),
    "OCCUPANCY": ("date",),
    "VCC_REPORT": ("vcc_id",),
    "BOOKING_ENGINE_ATTRIBUTION": ("booking_ref",),
    "PL_STATEMENT": ("code", "name", "period_month"),
    "TRIAL_BALANCE": ("code", "name"),
}


@dataclass
class StagedRow:
    row_no: int
    raw: dict[str, Any]
    canonical: dict[str, Any] = field(default_factory=dict)
    status: str = "OK"
    messages: list[str] = field(default_factory=list)
    page: int | None = None
    line_ref: str | None = None


def json_safe(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def raw_dict(headers: list[str], row: list[Any]) -> dict[str, Any]:
    return {h: json_safe(v) for h, v in zip(headers, row) if v is not None}


# ─── Canonical row ───


def canonical_row(raw: dict[str, Any], schema: KindSchema, mapping: dict[str, str], transforms: dict[str, dict],
                  *, currency: str, defaults: dict[str, Any] | None = None) -> tuple[dict, str, list[str]]:
    """(canonical, status, messages) for one row's raw values. The row's
    currency is its currency column, else one named in a money cell, else
    the batch's (money needs it for minor units, so it is read first)."""
    messages: list[str] = []
    status = "OK"
    row_ccy = currency
    ccy_header = mapping.get("currency")
    if ccy_header and raw.get(ccy_header) not in (None, ""):
        try:
            row_ccy = tf.parse_currency(raw[ccy_header])
        except ValueError as exc:
            messages.append(f"currency: {exc}")
            status = "WARN"
    else:
        for f in schema.fields:
            if f.type == "money" and f.name in mapping:
                named = tf.currency_in(raw.get(mapping[f.name]))
                if named:
                    row_ccy = named
                    break
    out: dict[str, Any] = {}
    for f in schema.fields:
        header = mapping.get(f.name)
        value = raw.get(header) if header else None
        if value is None and defaults and f.name in defaults:
            value = defaults[f.name]
        try:
            v, warn = tf.coerce(value, f.type, currency=row_ccy, transforms=transforms.get(f.name),
                                status_map=schema.status_map)
        except ValueError as exc:
            messages.append(f"{f.name}: {exc}")
            status = "ERROR"
            continue
        if warn:
            messages.append(f"{f.name}: {warn}")
            status = "WARN" if status == "OK" else status
        if v is not None:
            out[f.name] = v
    if any(f.type == "money" and f.name in out for f in schema.fields) or "currency" in schema.names:
        out["currency"] = out.get("currency") or row_ccy
    for req in schema.required:
        if req not in out and not any(m.startswith(f"{req}:") for m in messages):
            messages.append(f"{req}: missing")
            status = "ERROR"
    return out, status, messages


def mark_duplicates(rows: list[StagedRow], kind: str) -> None:
    """Later rows repeating an earlier row's key (or the whole canonical
    row) are DUP: an export that lists a reservation twice must not write
    it twice."""
    key_fields = DEDUPE_KEYS.get(kind)
    seen: dict[Any, int] = {}
    for r in rows:
        if r.status in ("ERROR", "SKIP"):
            continue
        if key_fields:
            key = tuple(r.canonical.get(k) for k in key_fields)
            if all(v is None for v in key):
                continue
        else:
            key = tuple(sorted((k, str(v)) for k, v in r.canonical.items()))
        if key in seen:
            r.status = "DUP"
            r.messages.append(f"same record as row {seen[key]}")
        else:
            seen[key] = r.row_no


# ─── P&L: sections and month columns (04 spec D4, 6.3) ───

# Section headers drive sign and group. Values are the canonical section.
SECTION_WORDS: tuple[tuple[str, str], ...] = (
    ("other income", "other_income"), ("non operating income", "other_income"),
    ("cost of sales", "cost_of_sales"), ("cost of goods sold", "cost_of_sales"), ("direct expenses", "cost_of_sales"),
    ("purchase accounts", "cost_of_sales"), ("cost of revenue", "cost_of_sales"),
    ("operating expenses", "expense"), ("indirect expenses", "expense"), ("overheads", "expense"),
    ("expenses", "expense"), ("المصروفات", "expense"), ("مصروفات", "expense"),
    ("direct incomes", "income"), ("indirect incomes", "other_income"), ("sales accounts", "income"),
    ("income", "income"), ("revenue", "income"), ("trading income", "income"), ("الإيرادات", "income"),
    ("الايرادات", "income"), ("duties taxes", "excluded"),
)
EXPENSE_SECTIONS = ("cost_of_sales", "expense")
COMPUTED_LINE = re.compile(r"^(gross|net|operating) (profit|loss|income|margin)|^ebitda|^net (profit|loss)|^profit")


def section_of(text: Any) -> str | None:
    n = normalize_header(text)
    return next((sec for word, sec in SECTION_WORDS if n == word), None)


def month_columns(headers: list[str]) -> dict[str, date]:
    """Headers that name a month ("Oct 2025", "2025-10", "October-25"),
    when there are at least two: then each is one period of a multi-month
    P&L. A single month column is an ordinary amount column."""
    found: dict[str, date] = {}
    for h in headers:
        s = str(h).strip()
        if not re.search(r"\d", s) or len(s) > 20:
            continue
        if not re.fullmatch(r"(\d{4}[-/.]\d{1,2})|([A-Za-z]{3,9}[-\s/']*(\d{2}|\d{4}))", s):
            continue
        try:
            found[h] = tf.parse_month(s)
        except ValueError:
            continue
    return found if len(found) >= 2 else {}


def _pl_rows(table: Table, schema: KindSchema, mapping: dict, transforms: dict, *, currency: str,
             period_month: date | None) -> list[StagedRow]:
    """One row per account per month column; section header rows set the
    section (and sign: income positive, expenses negative) of the rows under
    them and are staged as SKIP; computed lines (Gross profit) are SKIP."""
    months = month_columns(table.headers)
    name_h = mapping.get("name")
    staged: list[StagedRow] = []
    section: str | None = None
    n = 0
    for i, row in enumerate(table.rows):
        raw = raw_dict(table.headers, row)
        amounts = [raw.get(h) for h in months] if months else [raw.get(mapping.get("amount_minor", ""))]
        name = raw.get(name_h) if name_h else None
        sec = section_of(name) if name is not None else None
        if sec and all(a in (None, "") for a in amounts):
            section = sec
            n += 1
            staged.append(StagedRow(n, raw, {"section": sec}, "SKIP", [f"section header: {sec}"]))
            continue
        if name is not None and COMPUTED_LINE.search(normalize_header(name)):
            n += 1
            staged.append(StagedRow(n, raw, {}, "SKIP", ["computed line"]))
            continue
        periods = list(months.items()) if months else [(mapping.get("amount_minor"), None)]
        for header, month in periods:
            n += 1
            local_map = dict(mapping)
            if header:
                local_map["amount_minor"] = header
            defaults = {"period_month": (month or period_month).isoformat() if (month or period_month) else None}
            canon, status, msgs = canonical_row(raw, schema, local_map, transforms, currency=currency,
                                                defaults=defaults)
            if months and "amount_minor" not in canon and status == "ERROR":
                # An account with no figure for this month is simply absent.
                staged.append(StagedRow(n, raw, canon, "SKIP", ["no amount this month"]))
                continue
            sec_now = canon.get("section") and section_of(canon["section"]) or section
            if sec_now:
                canon["section"] = sec_now
            if sec_now == "excluded":
                staged.append(StagedRow(n, raw, canon, "SKIP", ["duties and taxes are not P&L lines"]))
                continue
            if sec_now in EXPENSE_SECTIONS and isinstance(canon.get("amount_minor"), int):
                canon["amount_minor"] = -abs(canon["amount_minor"])
            elif sec_now in ("income", "other_income") and isinstance(canon.get("amount_minor"), int):
                canon["amount_minor"] = abs(canon["amount_minor"])
            elif not sec_now:
                msgs.append("section unknown: sign kept as printed")
                status = "WARN" if status == "OK" else status
            staged.append(StagedRow(n, raw, canon, status, msgs))
    return staged


# ─── Entry point ───


def stage_table(table: Table, kind: str, mapping: dict[str, str], transforms: dict[str, dict], *, currency: str,
                period_month: date | None = None) -> list[StagedRow]:
    schema = schema_for(kind)
    if kind == "PL_STATEMENT":
        rows = _pl_rows(table, schema, mapping, transforms, currency=currency, period_month=period_month)
    else:
        rows = []
        for i, row in enumerate(table.rows, start=1):
            raw = raw_dict(table.headers, row)
            canon, status, msgs = canonical_row(raw, schema, mapping, transforms, currency=currency)
            rows.append(StagedRow(i, raw, canon, status, msgs))
    mark_duplicates(rows, kind)
    return rows
