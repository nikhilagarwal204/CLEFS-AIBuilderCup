"""
core/ingest commit handlers for the Profit Guard kinds (04 spec D4; 02
spec 2.2 step 6 and 3.1): OTA_STATEMENT, CREDIT_NOTE, OTA_PAYOUT,
VCC_REPORT, BANK and CONTRACT_PDF.

Each handler runs inside ingest's commit transaction and receives the whole
reviewed file. A corrected re-upload arrives as a new batch carrying
`supersedes_batch_id`, so handlers UPSERT by natural key (document number,
then a per-document line key) and drop rows the corrected file no longer
has, never append. Raising aborts the commit and the batch stays in REVIEW.

Every OTA line is linked to its PMS stay through core.stays.match_claim
(PMS is truth, the OTA a claim: they only meet in reservation_matches); the
match result is copied onto the line so detectors can join cheaply.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any

from clefs import db
from clefs.core import clock, money, privacy, stays
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.core.ingest import register_commit_handler
from clefs.modules.profit_guard import contracts
from clefs.modules.profit_guard.models import normalize_ota

log = logging.getLogger("clefs.profit_guard")

KINDS = ("OTA_STATEMENT", "CREDIT_NOTE", "OTA_PAYOUT", "VCC_REPORT", "BANK", "CONTRACT_PDF")
# reservation_matches.line_kind per claim table (statement_line_id is the
# claim row's id in that table).
LINE_KIND_STATEMENT = "ota_statement_line"
LINE_KIND_PAYOUT = "ota_payout_line"
LINE_KIND_VCC = "virtual_card"

# ─── Helpers ───


def _d(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _int(v: Any) -> int | None:
    try:
        return None if v is None or v == "" else int(v)
    except (TypeError, ValueError):
        return None


def _text(v: Any, limit: int = 500) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s[:limit] or None


def _ota_for(batch: dict) -> str:
    ota = normalize_ota(batch.get("ota_code"))
    if not ota:
        raise ClefsError(409, "Say which OTA this file is from (ota_code) before committing it", "ota_required")
    return ota


def _hotel_currency(cur: Any, hotel_slug: str) -> str:
    return get_hotel(cur, hotel_slug).currency


def _fx(cur: Any, src: str, dst: str, on: date | None) -> Decimal | None:
    """Units of `dst` per 1 `src` on the day (1 when they are the same),
    None when core has no rate (the line keeps its own currency)."""
    if src == dst:
        return Decimal(1)
    try:
        return money.fx_rate(cur, src, dst, on or clock.now().date())
    except ClefsError:
        return None


def _line_keys(rows: list[dict], id_field: str, ota: str) -> list[tuple[str, str | None]]:
    """(line_key, ota_res_id_norm) per row: the normalised id plus its
    occurrence in the file, so a modified booking's second line keeps its
    own row and the same file re-imported maps line to line."""
    seen: dict[str, int] = defaultdict(int)
    out = []
    for r in rows:
        norm = stays.normalize_ota_res_id(ota, r.get(id_field)) if r.get(id_field) else None
        base = norm or f"row{r.get('_row_no')}"
        seen[base] += 1
        out.append((f"{base}#{seen[base]}", norm))
    return out


def _programs(v: Any) -> list[str]:
    return [p.strip().upper() for p in re.split(r"[,;|/]", str(v or "")) if p.strip()][:20]


def _doc_no(batch: dict, rows: list[dict], prefix: str) -> str:
    """The printed document number, else one derived from the source and
    period, so a corrected file of the same period lands on the same row."""
    printed = batch.get("doc_no") or next((r.get("doc_no") for r in rows if r.get("doc_no")), None)
    if printed:
        return str(printed)[:120]
    if batch.get("period_start") or batch.get("period_end"):
        source = batch.get("source_code") or batch["kind"].lower()
        return f"{prefix}:{source}:{batch.get('period_start')}..{batch.get('period_end')}"
    return f"{prefix}:batch:{batch['id']}"


def _match(cur: Any, hotel_slug: str, *, line_id: int, line_kind: str, ota: str, row: dict,
           amount_minor: int | None, currency: str | None) -> dict:
    """core.stays.match_claim, never fatal: a line that cannot be matched
    stays UNMATCHED (an L3 candidate), it does not abort the import."""
    try:
        m = stays.match_claim(
            cur, hotel_slug, statement_line_id=line_id, line_kind=line_kind, ota_code=ota,
            ota_res_id=row.get("ota_res_id"), guest_name=row.get("guest_name"), check_in=_d(row.get("check_in")),
            check_out=_d(row.get("check_out")), nights=_int(row.get("room_nights")), amount_minor=amount_minor,
            currency=currency,
        )
    except ClefsError as exc:
        log.info("profit_guard: match of %s %s failed: %s", line_kind, line_id, exc.detail)
        return {"status": "UNMATCHED", "reservation_id": None, "score": None}
    return m


def _supersede(cur: Any, batch: dict, table: str, keep_ids: list[int]) -> int:
    """Rows the superseded batch wrote that this corrected file did not
    upsert are dropped (their lines cascade)."""
    prev = batch.get("supersedes_batch_id")
    if not prev:
        return 0
    child = {"pfg_ota_statements": ("pfg_ota_statement_lines", "statement_id", LINE_KIND_STATEMENT),
             "pfg_ota_payouts": ("pfg_ota_payout_lines", "payout_id", LINE_KIND_PAYOUT)}.get(table)
    cur.execute(f"DELETE FROM {table} WHERE hotel_slug = %s AND batch_id = %s AND NOT (id = ANY(%s)) RETURNING id",
                (batch["hotel_slug"], prev, keep_ids or [0]))
    gone = [r["id"] for r in cur.fetchall()]
    if child and gone:
        # The lines cascaded; their match rows are dropped explicitly.
        cur.execute(f"SELECT statement_line_id AS id FROM reservation_matches WHERE hotel_slug = %s "
                    f"AND line_kind = %s AND NOT EXISTS (SELECT 1 FROM {child[0]} l WHERE l.id = statement_line_id)",
                    (batch["hotel_slug"], child[2]))
        _drop_matches(cur, batch["hotel_slug"], child[2], [r["id"] for r in cur.fetchall()])
    return len(gone)


# ─── Statements and credit notes ───

LINE_COLS = ("hotel_slug", "ota_code", "line_key", "ota_res_id", "ota_res_id_norm", "guest_name", "check_in",
             "check_out", "room_nights", "rooms", "line_status", "collect_model", "gross_minor", "commissionable_minor",
             "commission_rate", "commission_minor", "charges", "booker_country", "programs", "rate_plan", "room_type",
             "booked_at", "cancelled_at", "remarks", "currency", "fx_rate", "batch_id", "import_row_id", "page")


def _upsert_statement(cur: Any, batch: dict, *, ota: str, doc_kind: str, doc_no: str, currency: str,
                      related_doc_no: str | None, doc_date: date | None) -> dict:
    cur.execute(
        """
        INSERT INTO pfg_ota_statements (hotel_slug, ota_code, doc_kind, doc_no, doc_date, related_doc_no, period_start,
            period_end, currency, batch_id, source_code)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, ota_code, doc_no) DO UPDATE SET
            doc_kind = EXCLUDED.doc_kind, doc_date = COALESCE(EXCLUDED.doc_date, pfg_ota_statements.doc_date),
            related_doc_no = COALESCE(EXCLUDED.related_doc_no, pfg_ota_statements.related_doc_no),
            period_start = EXCLUDED.period_start, period_end = EXCLUDED.period_end, currency = EXCLUDED.currency,
            batch_id = EXCLUDED.batch_id, source_code = EXCLUDED.source_code, updated_at = NOW()
        RETURNING *
        """,
        (batch["hotel_slug"], ota, doc_kind, doc_no, doc_date, related_doc_no, batch.get("period_start"),
         batch.get("period_end"), currency, batch["id"], batch.get("source_code")),
    )
    return dict(cur.fetchone())


def _write_lines(cur: Any, statement: dict, lines: list[dict]) -> list[dict]:
    """Upsert the document's lines by line_key and drop the ones the new
    file no longer carries (the file is the whole document)."""
    written = []
    for ln in lines:
        vals = [db.Json(ln[c]) if c == "charges" else ln.get(c) for c in LINE_COLS]
        cur.execute(
            f"""
            INSERT INTO pfg_ota_statement_lines (statement_id, {', '.join(LINE_COLS)})
            VALUES (%s, {', '.join(['%s'] * len(LINE_COLS))})
            ON CONFLICT (statement_id, line_key) DO UPDATE SET
                {', '.join(f'{c} = EXCLUDED.{c}' for c in LINE_COLS if c not in ('hotel_slug', 'line_key'))},
                updated_at = NOW()
            RETURNING *
            """,
            (statement["id"], *vals),
        )
        written.append(dict(cur.fetchone()))
    cur.execute("DELETE FROM pfg_ota_statement_lines WHERE statement_id = %s AND NOT (id = ANY(%s)) RETURNING id",
                (statement["id"], [w["id"] for w in written] or [0]))
    _drop_matches(cur, statement["hotel_slug"], LINE_KIND_STATEMENT, [r["id"] for r in cur.fetchall()])
    return written


def _drop_matches(cur: Any, hotel_slug: str, line_kind: str, ids: list[int]) -> None:
    """A claim line that left its document takes its match row with it (a
    human decision on it no longer has a subject); core/stays owns the table."""
    stays.delete_matches(cur, hotel_slug, line_kind, ids)


def _match_lines(cur: Any, hotel_slug: str, ota: str, written: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for ln in written:
        amount = ln["gross_minor"] if ln["gross_minor"] and ln["gross_minor"] > 0 else None
        m = _match(cur, hotel_slug, line_id=ln["id"], line_kind=LINE_KIND_STATEMENT, ota=ota, row=ln,
                   amount_minor=amount, currency=ln["currency"])
        cur.execute("UPDATE pfg_ota_statement_lines SET reservation_id = %s, match_status = %s, match_score = %s "
                    "WHERE id = %s", (m.get("reservation_id"), m.get("status"), m.get("score"), ln["id"]))
        counts[m.get("status") or "UNMATCHED"] += 1
    return dict(counts)


def _finish_statement(cur: Any, statement: dict, written: list[dict], total: int) -> None:
    cur.execute("UPDATE pfg_ota_statements SET total_minor = %s, line_count = %s, updated_at = NOW() WHERE id = %s",
                (total, len(written), statement["id"]))


def commit_statement(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """OTA_STATEMENT: one document (reservation statement or commission
    invoice) and its lines."""
    slug = batch["hotel_slug"]
    ota = _ota_for(batch)
    hotel_ccy = _hotel_currency(cur, slug)
    currency = batch.get("currency") or next((r["currency"] for r in rows if r.get("currency")), None) or hotel_ccy
    doc_kind = "COMMISSION_INVOICE" if batch.get("file_format") == "pdf" else "RES_STATEMENT"
    statement = _upsert_statement(cur, batch, ota=ota, doc_kind=doc_kind, doc_no=_doc_no(batch, rows, "stmt"),
                                  currency=currency, related_doc_no=None, doc_date=batch.get("period_end"))
    lines = []
    for r, (key, norm) in zip(rows, _line_keys(rows, "ota_res_id", ota)):
        ccy = r.get("currency") or currency
        check_in, check_out = _d(r.get("check_in")), _d(r.get("check_out"))
        nights = _int(r.get("room_nights"))
        if nights is None and check_in and check_out:
            nights = (check_out - check_in).days * (_int(r.get("rooms")) or 1)
        lines.append({
            "hotel_slug": slug, "ota_code": ota, "line_key": key, "ota_res_id": _text(r.get("ota_res_id"), 80),
            "ota_res_id_norm": norm, "guest_name": _text(r.get("guest_name"), 200), "check_in": check_in,
            "check_out": check_out, "room_nights": nights, "rooms": _int(r.get("rooms")),
            "line_status": r.get("line_status") if r.get("line_status") in
            ("OK", "NO_SHOW", "CANCELLED", "MODIFIED", "ADJUSTMENT") else "OK",
            "collect_model": _text(r.get("collect_model"), 40), "gross_minor": _int(r.get("gross_minor")),
            "commissionable_minor": _int(r.get("commissionable_minor")),
            "commission_rate": Decimal(r["commission_pct"]) if r.get("commission_pct") not in (None, "") else None,
            "commission_minor": _int(r.get("commission_minor")), "charges": [],
            "booker_country": (_text(r.get("booker_country"), 40) or "").upper() or None,
            "programs": _programs(r.get("programs")), "rate_plan": _text(r.get("rate_plan"), 120),
            "room_type": _text(r.get("room_type"), 120), "booked_at": _d(r.get("booked_at")),
            "cancelled_at": _d(r.get("cancelled_at")), "remarks": _text(r.get("remarks"), 1000), "currency": ccy,
            "fx_rate": _fx(cur, ccy, hotel_ccy, check_out or check_in), "batch_id": batch["id"],
            "import_row_id": r.get("_row_id"), "page": r.get("_page"),
        })
    written = _write_lines(cur, statement, lines)
    total = sum(w["commission_minor"] or 0 for w in written if w["currency"] == currency)
    _finish_statement(cur, statement, written, total)
    dropped = _supersede(cur, batch, "pfg_ota_statements", [statement["id"]])
    matches = _match_lines(cur, slug, ota, written)
    return {"statement_id": statement["id"], "doc_no": statement["doc_no"], "ota_code": ota, "lines": len(written),
            "commission_minor": total, "currency": currency, "matches": matches, "superseded_statements": dropped}


def commit_credit_note(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """CREDIT_NOTE: each document number becomes a CREDIT_NOTE statement.
    Its lines carry the credited commission as a NEGATIVE commission_minor
    (and the positive amount as the statement total), so summing commission
    per reservation across documents nets the credit off, which is what
    auto-verification (02 spec 3.5) reads."""
    slug = batch["hotel_slug"]
    ota = _ota_for(batch)
    hotel_ccy = _hotel_currency(cur, slug)
    fallback = _doc_no(batch, [], "credit")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get("doc_no") or batch.get("doc_no") or fallback)[:120]].append(r)
    out, keep, matches = [], [], defaultdict(int)
    for doc_no, grp in groups.items():
        currency = next((r["currency"] for r in grp if r.get("currency")), None) or batch.get("currency") or hotel_ccy
        related = next((_text(r.get("related_doc_no"), 120) for r in grp if r.get("related_doc_no")), None)
        st = _upsert_statement(cur, batch, ota=ota, doc_kind="CREDIT_NOTE", doc_no=doc_no, currency=currency,
                               related_doc_no=related, doc_date=batch.get("period_end"))
        lines = []
        for r, (key, norm) in zip(grp, _line_keys(grp, "ota_res_id", ota)):
            amount = abs(_int(r.get("amount_minor")) or 0)
            ccy = r.get("currency") or currency
            lines.append({
                "hotel_slug": slug, "ota_code": ota, "line_key": key, "ota_res_id": _text(r.get("ota_res_id"), 80),
                "ota_res_id_norm": norm, "guest_name": _text(r.get("guest_name"), 200),
                "check_in": _d(r.get("check_in")), "line_status": "ADJUSTMENT", "commission_minor": -amount,
                "charges": [], "programs": [], "remarks": _text(r.get("reason"), 1000), "currency": ccy,
                "fx_rate": _fx(cur, ccy, hotel_ccy, _d(r.get("check_in"))), "batch_id": batch["id"],
                "import_row_id": r.get("_row_id"), "page": r.get("_page"),
            })
        written = _write_lines(cur, st, lines)
        total = sum(-(w["commission_minor"] or 0) for w in written if w["currency"] == currency)
        _finish_statement(cur, st, written, total)
        for k, v in _match_lines(cur, slug, ota, written).items():
            matches[k] += v
        keep.append(st["id"])
        out.append({"statement_id": st["id"], "doc_no": doc_no, "related_doc_no": related, "lines": len(written),
                    "credited_minor": total, "currency": currency})
    dropped = _supersede(cur, batch, "pfg_ota_statements", keep)
    return {"ota_code": ota, "credit_notes": out, "matches": dict(matches), "superseded_statements": dropped}


# ─── Payouts ───

PAYOUT_LINE_COLS = ("hotel_slug", "ota_code", "line_key", "ota_res_id", "ota_res_id_norm", "guest_name", "check_in",
                    "check_out", "total_amount_paid_minor", "commissionable_minor", "commission_minor", "charges_minor",
                    "partner_payout_minor", "line_kind", "event_date", "currency", "batch_id", "import_row_id")


def _line_kind(v: Any, amount: int | None) -> str:
    t = str(v or "").lower()
    if "charge" in t and "back" in t or "dispute" in t:
        return "CHARGEBACK"
    if "refund" in t or "cancel" in t:
        return "REFUND"
    if "adjust" in t or "correction" in t:
        return "ADJUSTMENT"
    if not t and amount is not None and amount < 0:
        return "REFUND"
    return "BOOKING"


def commit_payout(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """OTA_PAYOUT: rows grouped by payout reference (one bank transfer each);
    the payout amount is the sum of partner_payout over its lines."""
    slug = batch["hotel_slug"]
    ota = _ota_for(batch)
    hotel_ccy = _hotel_currency(cur, slug)
    fallback = _doc_no(batch, [], "payout")
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[str(r.get("payout_ref") or fallback)[:120]].append(r)
    out, keep, matches = [], [], defaultdict(int)
    for ref, grp in groups.items():
        currency = next((r["currency"] for r in grp if r.get("currency")), None) or batch.get("currency") or hotel_ccy
        pay_date = max((d for d in (_d(r.get("payout_date")) for r in grp) if d), default=None)
        ptype = next((str(r["payout_type"]).upper() for r in grp if str(r.get("payout_type") or "").upper()
                      in ("GROSS", "NET")), None)
        bank_ref = next((_text(r.get("bank_transfer_reference"), 120) for r in grp if r.get("bank_transfer_reference")),
                        None)
        cur.execute(
            """
            INSERT INTO pfg_ota_payouts (hotel_slug, ota_code, payout_ref, payout_date, payout_type, currency,
                bank_transfer_reference, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, ota_code, payout_ref) DO UPDATE SET
                payout_date = COALESCE(EXCLUDED.payout_date, pfg_ota_payouts.payout_date),
                payout_type = COALESCE(EXCLUDED.payout_type, pfg_ota_payouts.payout_type),
                currency = EXCLUDED.currency, batch_id = EXCLUDED.batch_id,
                bank_transfer_reference = COALESCE(EXCLUDED.bank_transfer_reference,
                                                   pfg_ota_payouts.bank_transfer_reference), updated_at = NOW()
            RETURNING *
            """,
            (slug, ota, ref, pay_date, ptype, currency, bank_ref, batch["id"]),
        )
        payout = dict(cur.fetchone())
        written = []
        for r, (key, norm) in zip(grp, _line_keys(grp, "ota_res_id", ota)):
            partner = _int(r.get("partner_payout_minor")) or 0
            ln = {
                "hotel_slug": slug, "ota_code": ota, "line_key": key, "ota_res_id": _text(r.get("ota_res_id"), 80),
                "ota_res_id_norm": norm, "guest_name": _text(r.get("guest_name"), 200),
                "check_in": _d(r.get("check_in")), "check_out": _d(r.get("check_out")),
                "total_amount_paid_minor": _int(r.get("total_amount_paid_minor")),
                "commissionable_minor": _int(r.get("commissionable_minor")),
                "commission_minor": _int(r.get("commission_minor")), "charges_minor": _int(r.get("charges_minor")),
                "partner_payout_minor": partner, "line_kind": _line_kind(r.get("line_kind"), partner),
                "event_date": _d(r.get("event_date")), "currency": r.get("currency") or currency,
                "batch_id": batch["id"], "import_row_id": r.get("_row_id"),
            }
            cur.execute(
                f"""
                INSERT INTO pfg_ota_payout_lines (payout_id, {', '.join(PAYOUT_LINE_COLS)})
                VALUES (%s, {', '.join(['%s'] * len(PAYOUT_LINE_COLS))})
                ON CONFLICT (payout_id, line_key) DO UPDATE SET
                    {', '.join(f'{c} = EXCLUDED.{c}' for c in PAYOUT_LINE_COLS if c not in ('hotel_slug', 'line_key'))},
                    updated_at = NOW()
                RETURNING *
                """,
                (payout["id"], *[ln[c] for c in PAYOUT_LINE_COLS]),
            )
            written.append(dict(cur.fetchone()))
        cur.execute("DELETE FROM pfg_ota_payout_lines WHERE payout_id = %s AND NOT (id = ANY(%s)) RETURNING id",
                    (payout["id"], [w["id"] for w in written] or [0]))
        _drop_matches(cur, slug, LINE_KIND_PAYOUT, [r["id"] for r in cur.fetchall()])
        amount = sum(w["partner_payout_minor"] for w in written if w["currency"] == currency)
        cur.execute("UPDATE pfg_ota_payouts SET amount_minor = %s, line_count = %s WHERE id = %s",
                    (amount, len(written), payout["id"]))
        for w in written:
            m = _match(cur, slug, line_id=w["id"], line_kind=LINE_KIND_PAYOUT, ota=ota, row=w,
                       amount_minor=w["total_amount_paid_minor"] if (w["total_amount_paid_minor"] or 0) > 0 else None,
                       currency=w["currency"])
            cur.execute("UPDATE pfg_ota_payout_lines SET reservation_id = %s, match_status = %s WHERE id = %s",
                        (m.get("reservation_id"), m.get("status"), w["id"]))
            matches[m.get("status") or "UNMATCHED"] += 1
        keep.append(payout["id"])
        out.append({"payout_id": payout["id"], "payout_ref": ref, "lines": len(written), "amount_minor": amount,
                    "currency": currency})
    dropped = _supersede(cur, batch, "pfg_ota_payouts", keep)
    linked = link_bank_credits(cur, slug)
    return {"ota_code": ota, "payouts": out, "matches": dict(matches), "superseded_payouts": dropped,
            "bank_links": linked}


# ─── Bank credits ───

BANK_WINDOW_DAYS = 3


def _norm_ref(v: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(v or "").upper())


def link_bank_credits(cur: Any, hotel_slug: str) -> int:
    """Link unmatched bank credits to payouts: by reference first (the
    payout's bank transfer reference or payout id inside the credit's
    reference or description), else by the exact amount in the same
    currency within 3 days of the payout date when exactly one payout fits
    (02 spec 3.3 L10). Bundled transfers are left to the L10 detector."""
    cur.execute("SELECT * FROM pfg_bank_credits WHERE hotel_slug = %s AND matched_payout_id IS NULL "
                "ORDER BY value_date, id", (hotel_slug,))
    credits = [dict(r) for r in cur.fetchall()]
    if not credits:
        return 0
    cur.execute("SELECT * FROM pfg_ota_payouts WHERE hotel_slug = %s AND bank_credit_id IS NULL", (hotel_slug,))
    payouts = [dict(r) for r in cur.fetchall()]
    linked = 0
    for c in credits:
        text = _norm_ref(c.get("reference")) + "|" + _norm_ref(c.get("description"))
        hit = next((p for p in payouts if any(len(r) >= 5 and r in text for r in
                                              (_norm_ref(p.get("bank_transfer_reference")), _norm_ref(p["payout_ref"])))),
                   None)
        if hit is None:
            fits = [p for p in payouts if p["currency"] == c["currency"] and p["amount_minor"] == c["amount_minor"]
                    and p.get("payout_date") and abs((c["value_date"] - p["payout_date"]).days) <= BANK_WINDOW_DAYS]
            hit = fits[0] if len(fits) == 1 else None
        if hit is None:
            continue
        cur.execute("UPDATE pfg_bank_credits SET matched_payout_id = %s, updated_at = NOW() WHERE id = %s",
                    (hit["id"], c["id"]))
        cur.execute("UPDATE pfg_ota_payouts SET bank_credit_id = %s, updated_at = NOW() WHERE id = %s",
                    (c["id"], hit["id"]))
        payouts.remove(hit)
        linked += 1
    return linked


def commit_bank(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """BANK: incoming credits only (debits are not OTA money), keyed by a
    digest of date, amount, currency and reference so overlapping exports
    never double a credit; each gets its hotel-currency value."""
    slug = batch["hotel_slug"]
    hotel_ccy = _hotel_currency(cur, slug)
    written, skipped = [], 0
    for r in rows:
        amount = _int(r.get("amount_minor"))
        day = _d(r.get("value_date"))
        if amount is None or amount <= 0 or day is None:
            skipped += 1
            continue
        ccy = r.get("currency") or batch.get("currency") or hotel_ccy
        key = hashlib.sha1("|".join(str(x or "") for x in (day, amount, ccy, r.get("reference"), r.get("counterparty"),
                                                          r.get("description"))).encode()).hexdigest()
        try:
            home = money.convert_minor(cur, amount, ccy, hotel_ccy, day)
        except ClefsError:
            home = None
        cur.execute(
            """
            INSERT INTO pfg_bank_credits (hotel_slug, line_key, value_date, currency, amount_minor, hotel_currency,
                hotel_amount_minor, reference, counterparty, description, batch_id, import_row_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, line_key) DO UPDATE SET hotel_amount_minor = EXCLUDED.hotel_amount_minor,
                batch_id = EXCLUDED.batch_id, import_row_id = EXCLUDED.import_row_id, updated_at = NOW()
            RETURNING id
            """,
            (slug, key, day, ccy, amount, hotel_ccy, home, _text(r.get("reference"), 200),
             _text(r.get("counterparty"), 200), _text(r.get("description"), 500), batch["id"], r.get("_row_id")),
        )
        written.append(cur.fetchone()["id"])
    dropped = _supersede(cur, batch, "pfg_bank_credits", written)
    return {"credits": len(written), "skipped_debits": skipped, "superseded_credits": dropped,
            "bank_links": link_bank_credits(cur, slug)}


# ─── Virtual cards ───

VCC_STATUS_WORDS = {
    "available": "AVAILABLE", "active": "AVAILABLE", "open": "AVAILABLE", "funded": "FUNDED", "loaded": "FUNDED",
    "not loaded": "NOT_LOADED", "not_loaded": "NOT_LOADED", "pending": "NOT_LOADED",
    "partially charged": "PARTIALLY_CHARGED", "partially_charged": "PARTIALLY_CHARGED", "partial": "PARTIALLY_CHARGED",
    "fully charged": "FULLY_CHARGED", "fully_charged": "FULLY_CHARGED", "charged": "FULLY_CHARGED",
    "used": "FULLY_CHARGED", "cancelled": "CANCELLED", "canceled": "CANCELLED", "expired": "EXPIRED",
}


def _vcc_status(raw: Any, *, balance: int, initial: int | None) -> str:
    word = VCC_STATUS_WORDS.get(re.sub(r"\s+", " ", str(raw or "").strip().lower()))
    if word:
        return word
    if balance <= 0:
        return "FULLY_CHARGED" if initial else "NOT_LOADED"
    if initial and balance < initial:
        return "PARTIALLY_CHARGED"
    return "AVAILABLE"


def commit_vcc(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """VCC_REPORT: the OTA's card list is the truth for balances. The
    charge deadline is min(card expiry, the contract's rule from the stay's
    check-out), 02 spec 3.1, once the card is matched to its stay."""
    slug = batch["hotel_slug"]
    ota = _ota_for(batch)
    hotel_ccy = _hotel_currency(cur, slug)
    seen, matches = [], defaultdict(int)
    for r in rows:
        vcc_id = _text(r.get("vcc_id"), 120)
        expiry = _d(r.get("expiration_date"))
        balance = _int(r.get("current_balance_minor"))
        if not vcc_id or expiry is None or balance is None:
            continue
        initial = _int(r.get("initial_minor"))
        norm = stays.normalize_ota_res_id(ota, r.get("ota_res_id")) if r.get("ota_res_id") else None
        activation = _d(r.get("activation_date"))
        cur.execute(
            """
            INSERT INTO pfg_virtual_cards (hotel_slug, ota_code, vcc_id, ota_res_id, ota_res_id_norm, activation_date,
                expiration_date, charge_deadline, deadline_basis, initial_minor, current_balance_minor, currency,
                status, last_seen_batch_id, import_row_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'card_expiry', %s, %s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, ota_code, vcc_id) DO UPDATE SET
                ota_res_id = EXCLUDED.ota_res_id, ota_res_id_norm = EXCLUDED.ota_res_id_norm,
                activation_date = EXCLUDED.activation_date, expiration_date = EXCLUDED.expiration_date,
                initial_minor = COALESCE(EXCLUDED.initial_minor, pfg_virtual_cards.initial_minor),
                current_balance_minor = EXCLUDED.current_balance_minor, currency = EXCLUDED.currency,
                status = EXCLUDED.status, last_seen_batch_id = EXCLUDED.last_seen_batch_id,
                import_row_id = EXCLUDED.import_row_id, updated_at = NOW()
            RETURNING *
            """,
            (slug, ota, vcc_id, _text(r.get("ota_res_id"), 80), norm, activation, expiry, expiry, initial, balance,
             r.get("currency") or batch.get("currency") or hotel_ccy,
             _vcc_status(r.get("status"), balance=balance, initial=initial), batch["id"], r.get("_row_id")),
        )
        card = dict(cur.fetchone())
        m = _match(cur, slug, line_id=card["id"], line_kind=LINE_KIND_VCC, ota=ota, row=r, amount_minor=None,
                   currency=None)
        matches[m.get("status") or "UNMATCHED"] += 1
        check_out = None
        if m.get("reservation_id"):
            res = stays.get_reservation(cur, m["reservation_id"]) or {}
            check_out = _d(res.get("actual_check_out")) or _d(res.get("check_out"))
        terms = contracts.contract_on(cur, slug, ota, check_out or expiry)
        deadline, basis = contracts.vcc_charge_deadline(terms, expiration_date=expiry, check_out=check_out,
                                                        activation_date=activation)
        cur.execute("UPDATE pfg_virtual_cards SET reservation_id = %s, charge_deadline = %s, deadline_basis = %s "
                    "WHERE id = %s", (m.get("reservation_id"), deadline, basis, card["id"]))
        seen.append(card["id"])
    return {"ota_code": ota, "cards": len(seen), "matches": dict(matches)}


# ─── Contract PDFs ───


def commit_contract_pdf(cur: Any, batch: dict, rows: list[dict]) -> dict:
    """CONTRACT_PDF: ingest already read the clauses (clause_ref, text,
    page); the terms are extracted from them with citations into a DRAFT
    contract a person confirms (PUT /profit-guard/contracts/{id})."""
    slug = batch["hotel_slug"]
    by_page: dict[int, list[str]] = defaultdict(list)
    for r in rows:
        page = _int(r.get("page")) or _int(r.get("_page")) or 1
        by_page[max(1, page)].append(f"{r.get('clause_ref') or ''} {r.get('text') or ''}".strip())
    pages = ["\n".join(by_page.get(i, [])) for i in range(1, max(by_page, default=1) + 1)]
    draft = contracts.extract_contract(cur, slug, pages=pages, ota_code=batch.get("ota_code"),
                                       file_id=str(batch["file_id"]) if batch.get("file_id") else None,
                                       batch_id=batch["id"])
    return {"contract_id": draft["id"], "status": draft["status"], "ota_code": draft["ota_code"],
            "review_flags": (draft.get("extraction") or {}).get("review_flags", [])}


# ─── Registration ───

HANDLERS = {
    "OTA_STATEMENT": commit_statement, "CREDIT_NOTE": commit_credit_note, "OTA_PAYOUT": commit_payout,
    "VCC_REPORT": commit_vcc, "BANK": commit_bank, "CONTRACT_PDF": commit_contract_pdf,
}


def _hotel_only(fn: Any) -> Any:
    """The public audit's sandbox batches (hotel_slug NULL, 02 spec 3.11)
    are analysed in place and never committed into hotel tables."""

    @functools.wraps(fn)
    def handler(cur: Any, batch: dict, rows: list[dict]) -> dict:
        if not batch.get("hotel_slug"):
            raise ClefsError(409, "A public-audit upload is analysed, not committed", "sandbox_batch")
        return fn(cur, batch, rows)

    return handler


_REGISTERED = {kind: _hotel_only(fn) for kind, fn in HANDLERS.items()}


def register() -> None:
    """Idempotent: core treats re-registering the same dotted name as a
    no-op (the wrapper keeps the handler's name)."""
    for kind, fn in _REGISTERED.items():
        register_commit_handler(kind, fn)


register()

# ─── PII registry (Trust retention and DSAR) ───

# OTA claims keep the guest name the OTA printed; they follow the finance
# retention of reservations (1095 days). Public-audit leads live 30 days
# (02 spec 3.11); the purge job deletes them sooner, this is the backstop.
FINANCE_RETENTION_DAYS = 1095
for _table in ("pfg_ota_statement_lines", "pfg_ota_payout_lines"):
    privacy.register_pii("profit_guard", _table, subject_columns={"guest_name": "name"}, ts_column="created_at",
                         retention_days=FINANCE_RETENTION_DAYS, strategy="redact", redact_columns=("guest_name",))
# The contacts are sealed (email_enc, whatsapp_enc: QA PUB-8), so a DSAR
# finds a lead by name, as vis_public_checks and dr_leads do.
privacy.register_pii("profit_guard", "pfg_lead_audits", subject_columns={"lead_name": "name"},
                     ts_column="created_at", retention_days=30, strategy="delete")
