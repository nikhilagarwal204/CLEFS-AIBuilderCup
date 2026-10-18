"""
Monthly statements in: the core-ingest commit handler (uploads) and the
connector writer (Xero, QuickBooks) both land here (04 spec 6.3).

Each month becomes one fin_import_batches row with its lines in
fin_pl_lines, then mapping proposals run (rules, locked reuse, memory inline;
the LLM as task finance.map_propose) and the batch status follows: mapping
required until every account holds a confirmed mapping, then review or
checks_failed after the pre-publish checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from clefs import jobs
from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.core.ingest import normalize_header, register_commit_handler
from clefs.db import Json
from clefs.modules.finance import accounts, calc, mapping

KIND_OF = {"PL_STATEMENT": "pl", "TRIAL_BALANCE": "trial_balance"}
FORMAT_SOURCE = {"XLSX": "upload_xlsx", "XLS": "upload_xlsx", "CSV": "upload_csv", "TSV": "upload_csv",
                 "TXT": "upload_csv", "PDF": "upload_pdf"}
# Tally's primary groups (ingest reads them as section headers): a sheet
# carrying them is a Tally export, whose ledgers keep their own accounts.
TALLY_GROUPS = ("direct incomes", "indirect incomes", "direct expenses", "indirect expenses", "sales accounts",
                "purchase accounts", "duties taxes", "duties and taxes")
NET_PROFIT_LINE = re.compile(r"^net (profit|income|loss)|^صافي (الربح|الدخل)|^profit for the (period|month|year)")


# ─── Helpers ───


def _source_for(cur: Any, batch: dict, rows: list[dict]) -> str:
    """tally_upload when the sheet carries Tally's primary groups (as section
    header rows, which ingest stages as SKIP, or as a group column), else the
    upload format."""
    seen = {normalize_header(r.get("group_raw") or "") for r in rows}
    cur.execute("SELECT raw FROM import_rows WHERE batch_id = %s AND status = 'SKIP' LIMIT 500", (batch["id"],))
    for r in cur.fetchall():
        seen |= {normalize_header(v) for v in (r["raw"] or {}).values() if isinstance(v, str)}
    if seen & set(TALLY_GROUPS) or "tally" in normalize_header(batch.get("filename") or ""):
        return "tally_upload"
    return FORMAT_SOURCE.get(str(batch.get("file_format") or "").upper(), "upload_csv")


def _file_sha(cur: Any, batch: dict) -> str:
    if batch.get("file_id"):
        cur.execute("SELECT sha256 FROM files WHERE id = %s", (batch["file_id"],))
        r = cur.fetchone()
        if r:
            return r["sha256"]
    return hashlib.sha256(f"core-batch:{batch['id']}".encode()).hexdigest()


def parse_amount(v: Any, currency: str) -> int | None:
    """'126,000.00', '(1,234)', 'AED 5,000', 126000 -> minor units, or None."""
    if v is None or v == "":
        return None
    s = str(v).strip()
    neg = s.startswith("(") and s.endswith(")") or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        amount = money.to_minor(Decimal(s), currency)
    except (InvalidOperation, ValueError):
        return None
    return -amount if neg else amount


def canonical_sha(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


_MONTH_FORMATS = ("%b %Y", "%B %Y", "%b-%y", "%b %y", "%B-%y", "%b-%Y", "%B-%Y", "%b'%y", "%m/%Y", "%Y/%m")


def header_month(h: Any) -> date | None:
    from datetime import datetime

    s = str(h or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}", s):
        try:
            return calc.parse_month(s)
        except ValueError:
            return None
    for fmt in _MONTH_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().replace(day=1)
        except ValueError:
            continue
    return None


def printed_net_profit(cur: Any, batch: dict, month: date) -> int | None:
    """The Net Profit the uploaded statement prints for `month` (ingest stages
    computed lines as SKIP), or None when it prints none."""
    name_h = (batch.get("mapping") or {}).get("name")
    if not name_h:
        return None
    cur.execute("SELECT raw FROM import_rows WHERE batch_id = %s AND status = 'SKIP' ORDER BY row_no",
                (batch["id"],))
    found = None
    for r in cur.fetchall():
        raw = r["raw"] or {}
        if not NET_PROFIT_LINE.search(normalize_header(raw.get(name_h) or "")):
            continue
        months = {h: header_month(h) for h in raw if header_month(h)}
        if len(months) >= 2:
            header = next((h for h, m in months.items() if m == month), None)
        else:
            header = (batch.get("mapping") or {}).get("amount_minor")
        if header is not None:
            v = parse_amount(raw.get(header), batch.get("currency") or "AED")
            found = v if v is not None else found
    return found


# ─── Writing one month ───


def write_month(cur: Any, hotel_slug: str, *, period_month: date, statement_kind: str, source: str, sha256: str,
                currency: str, lines: list[dict], core_batch_id: int | None = None, file_id: Any = None,
                source_net_profit_minor: int | None = None, basis: str | None = None,
                meta: dict | None = None, actor_id: int | None = None) -> dict:
    """Create (or rewrite, for the same bytes) the month's batch and its lines.
    `lines`: [{code?, name, group_raw?, type_raw?, amount_minor, row_ref?, tracking?}]."""
    period_month = period_month.replace(day=1)
    cur.execute(
        """SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND period_month = %s AND statement_kind = %s
             AND sha256 = %s FOR UPDATE""",
        (hotel_slug, period_month, statement_kind, sha256),
    )
    existing = cur.fetchone()
    if existing is not None and existing["status"] in ("published", "superseded"):
        return dict(existing, reused=True)
    if existing is not None:
        cur.execute("DELETE FROM fin_pl_lines WHERE batch_id = %s", (existing["id"],))
        cur.execute(
            """UPDATE fin_import_batches SET status = 'uploaded', core_import_batch_id = COALESCE(%s, core_import_batch_id),
                   source_net_profit_minor = %s, basis = %s, connector_meta = %s, currency = %s
               WHERE id = %s""",
            (core_batch_id, source_net_profit_minor, basis, Json(meta or {}), currency, existing["id"]),
        )
        batch_id = existing["id"]
    else:
        cur.execute(
            """INSERT INTO fin_import_batches (hotel_slug, period_month, statement_kind, source, core_import_batch_id,
                   file_id, sha256, currency, basis, status, source_net_profit_minor, connector_meta, created_by)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'uploaded', %s, %s, %s) RETURNING id""",
            (hotel_slug, period_month, statement_kind, source, core_batch_id, file_id, sha256, currency, basis,
             source_net_profit_minor, Json(meta or {}), actor_id),
        )
        batch_id = cur.fetchone()["id"]
    # A newer statement for the month retires the older ones still in review;
    # a published month is superseded only when its replacement publishes.
    cur.execute(
        """UPDATE fin_import_batches SET status = 'superseded', superseded_by = %s
           WHERE hotel_slug = %s AND period_month = %s AND statement_kind = %s AND id <> %s
             AND status NOT IN ('published', 'superseded')""",
        (batch_id, hotel_slug, period_month, statement_kind, batch_id),
    )
    touched: dict[int, dict] = {}
    for ln in lines:
        aid, changed = accounts.upsert_account(cur, hotel_slug, source=source, code=ln.get("code"), name=ln["name"],
                                               group_raw=ln.get("group_raw"), type_raw=ln.get("type_raw"),
                                               batch_id=batch_id)
        t = touched.setdefault(aid, {"id": aid, "changed": False, "amount_minor": 0})
        t["changed"] = t["changed"] or changed
        t["amount_minor"] += int(ln["amount_minor"])
        cur.execute(
            """INSERT INTO fin_pl_lines (batch_id, hotel_slug, account_id, amount_minor, currency, row_ref, tracking)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (batch_id, hotel_slug, aid, int(ln["amount_minor"]), currency, Json(ln.get("row_ref") or {}),
             Json(ln["tracking"]) if ln.get("tracking") else None),
        )
    res = mapping.propose(cur, hotel_slug, list(touched.values()), data_origin=source)
    if res["pending"]:
        jobs.enqueue("finance.map_propose", {"batch_id": batch_id, "account_ids": res["pending"]},
                     hotel_slug=hotel_slug, dedupe_key=f"finance.map_propose:{batch_id}", cur=cur)
    from clefs.modules.finance import review

    review.refresh(cur, hotel_slug, batch_id)
    return dict(get_batch(cur, hotel_slug, batch_id), mapping_pending=len(res["pending"]))


def get_batch(cur: Any, hotel_slug: str, batch_id: int) -> dict:
    cur.execute("SELECT * FROM fin_import_batches WHERE hotel_slug = %s AND id = %s", (hotel_slug, batch_id))
    r = cur.fetchone()
    if r is None:
        raise ClefsError(404, "import not found", "import_not_found")
    return dict(r)


# ─── Core ingest commit handler (PL_STATEMENT, TRIAL_BALANCE) ───


def commit_statement(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """Split the committed rows by month and write one finance batch each.
    A trial balance line's amount is credit - debit (income positive,
    expenses negative, like a P&L line); the debit and credit totals go in
    the batch meta for check 6."""
    hotel_slug = batch["hotel_slug"]
    if not hotel_slug:
        raise ClefsError(400, "a statement needs a hotel", "hotel_required")
    kind = KIND_OF[batch["kind"]]
    currency = (batch.get("currency") or "").strip() or "AED"
    source = _source_for(cur, batch, rows)
    sha = _file_sha(cur, batch)
    by_month: dict[date, list[dict]] = defaultdict(list)
    tb: dict[date, dict] = defaultdict(lambda: {"debit_minor": 0, "credit_minor": 0})
    for r in rows:
        if kind == "pl":
            m = r.get("period_month") or batch.get("period_month")
            if not m:
                # Reached from the ingest screen's commit: the way out is the
                # Finance upload with a month, which replaces this open batch
                # (router._create_core_batch), so the message says exactly that.
                raise ClefsError(400, "This P&L has no month column: upload it again from Finance > P&L imports "
                                 "with the month it covers", "period_month_required")
            month = calc.parse_month(str(m)[:10])
            amount = r.get("amount_minor")
        else:
            month = calc.parse_month(str(batch.get("period_month"))[:10])
            dr, cr = int(r.get("debit_minor") or 0), int(r.get("credit_minor") or 0)
            tb[month]["debit_minor"] += dr
            tb[month]["credit_minor"] += cr
            amount = cr - dr
        if amount is None or not r.get("name"):
            continue
        by_month[month].append({
            "code": r.get("code"), "name": r["name"], "group_raw": r.get("group_raw"),
            "type_raw": r.get("type_raw") or r.get("section"), "amount_minor": int(amount),
            "row_ref": {"core_batch_id": batch["id"], "row_id": r.get("_row_id"), "row_no": r.get("_row_no"),
                        "page": r.get("_page")},
        })
    if not by_month:
        raise ClefsError(400, "no statement lines to import", "nothing_to_import")
    out = []
    actor = batch.get("committed_by") or batch.get("uploaded_by")
    for month in sorted(by_month):
        printed = printed_net_profit(cur, batch, month) if kind == "pl" else None
        fb = write_month(cur, hotel_slug, period_month=month, statement_kind=kind, source=source, sha256=sha,
                         currency=currency, lines=by_month[month], core_batch_id=batch["id"],
                         file_id=batch.get("file_id"), source_net_profit_minor=printed, basis="unknown",
                         meta={"tb": tb[month]} if kind == "trial_balance" else {"printed_net_profit": printed},
                         actor_id=actor)
        out.append({"id": fb["id"], "period_month": month.isoformat(), "status": fb["status"]})
    return {"finance_batches": out, "source": source}


register_commit_handler("PL_STATEMENT", commit_statement)
register_commit_handler("TRIAL_BALANCE", commit_statement)
