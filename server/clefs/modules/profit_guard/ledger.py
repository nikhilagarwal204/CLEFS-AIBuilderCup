"""The recovery ledger (02 spec 3.4-3.5): finding status moves, automatic
verification of credits, the 45-day timeout, the pilot scorecard and the
findings/ledger/deadline routes.

Status flow: DETECTED -> ACCEPTED (owner agrees) -> DISPUTED (packet
submitted) -> RECOVERED | REJECTED | UNRESOLVED; any open state can go to
DISMISSED. pfg_finding_events is the ledger itself: every move is one row,
and every credited amount is one row with channel 'credit', so "recovered"
is always a sum over evidence, never a number someone typed over.

Auto-verification reads the OTA's own documents. For each booking with an
open RECOVERABLE finding it takes the larger of:
- statement credits: credit-note and adjustment lines with a negative
  commission, plus the drop in invoiced commission since the dispute was
  submitted (a corrected invoice re-imported over the original);
- payout credits: negative-commission REFUND/ADJUSTMENT payout lines.
The larger, not the sum, because one credit often shows up in both the
credit note and the next payout (repo.booking_credit, the same measure
the detectors net off a finding). Only commission claims take credit
(repo.COMMISSION_DETECTORS: never a VCC, payout or chargeback finding).
What earlier runs already attributed is subtracted, each finding only
takes credit above its own baseline (what was netted into its amount at
detection), and the rest is allocated in deadline order. 95% or more of a
finding's amount sets RECOVERED; a partial credit on an unfiled claim
keeps its state.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs import db
from clefs.audit import audit
from clefs.core import clock
from clefs.core import time as htime
from clefs.core.errors import ClefsError
from clefs.core.hotels import get_hotel
from clefs.modules.profit_guard import signals as pg_signals

# ─── States ───

STATUSES = ("DETECTED", "ACCEPTED", "DISPUTED", "RECOVERED", "REJECTED", "UNRESOLVED", "DISMISSED")
OPEN = ("DETECTED", "ACCEPTED", "DISPUTED")
VALID = ("ACCEPTED", "DISPUTED", "RECOVERED")          # the pilot scorecard's "valid" (3.4)
DISMISS_REASONS = ("FALSE_POSITIVE_DATA", "CONTRACT_EXCEPTION", "ALREADY_HANDLED", "TOO_SMALL",
                   "NOT_WORTH_DISPUTING", "OTHER")
ALLOWED: dict[str, frozenset[str]] = {
    "DETECTED": frozenset({"ACCEPTED", "DISPUTED", "RECOVERED", "DISMISSED"}),
    "ACCEPTED": frozenset({"DETECTED", "DISPUTED", "RECOVERED", "DISMISSED"}),
    "DISPUTED": frozenset({"RECOVERED", "REJECTED", "UNRESOLVED", "DISMISSED"}),
    # The OTA said no: submit again with more evidence, or give up.
    "REJECTED": frozenset({"DISPUTED", "UNRESOLVED", "RECOVERED"}),
    # A late credit still counts; a re-submission reopens the dispute.
    "UNRESOLVED": frozenset({"DISPUTED", "RECOVERED"}),
    # Undo a dismissal (the finding comes back as new).
    "DISMISSED": frozenset({"DETECTED"}),
    "RECOVERED": frozenset(),
}
RECOVERED_SHARE = Decimal("0.95")      # 3.5: 95% or more of the amount credited
TIMEOUT_DAYS = 45                      # 3.5: submitted, no credit, a later invoice issued
CREDIT = pg_signals.CREDIT_CHANNEL     # event channel of every credited amount
VERIFY_STATUSES = ("DETECTED", "ACCEPTED", "DISPUTED", "UNRESOLVED", "REJECTED")


def _actor_label(actor: Any) -> str:
    if actor is None:
        return "system"
    if isinstance(actor, str):
        return actor[:80]
    principal = getattr(actor, "principal", actor)
    uid = getattr(principal, "user_id", None)
    return f"user:{uid}" if uid is not None else str(getattr(principal, "label", None) or "system")[:80]


def _actor_uid(actor: Any) -> int | None:
    principal = getattr(actor, "principal", actor)
    return getattr(principal, "user_id", None) if principal is not None and not isinstance(actor, str) else None


def share(recovered: int, amount: int) -> Decimal:
    return Decimal(recovered) / Decimal(amount) if amount else Decimal(1)


# ─── Transitions ───


def _load(cur: Any, hotel_slug: str, finding_id: int) -> dict:
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = %s FOR UPDATE", (hotel_slug, finding_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Finding not found", "finding_not_found")
    return dict(row)


def log_event(cur: Any, finding: dict, to_status: str, *, actor: Any = None, channel: str | None = None,
              note: str | None = None, amount_minor: int | None = None, evidence: list | None = None,
              from_status: str | None = None) -> None:
    cur.execute(
        """
        INSERT INTO pfg_finding_events (finding_id, hotel_slug, at, from_status, to_status, actor_user_id, actor,
                                        channel, note, amount_minor, evidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (finding["id"], finding["hotel_slug"], clock.now(), from_status or finding["status"], to_status,
         _actor_uid(actor), _actor_label(actor), channel, (note or None) and note[:2000], amount_minor,
         db.Json(evidence or [])),
    )


def transition(cur: Any, hotel_slug: str, finding_id: int, to: str, *, actor: Any = None, note: str | None = None,
               channel: str = "console", dismiss_reason: str | None = None, recovered_minor: int | None = None,
               verified_by: str | None = None, evidence: list | None = None, refresh: bool = True,
               force: bool = False) -> dict:
    """Move one finding (409 illegal_transition off the ALLOWED map unless
    `force`, which only the ledger's own automation uses). RECOVERED takes
    the credited total (default: the full amount); the increment is logged
    as a 'credit' event so the recovered metric sums evidence. Returns the
    updated row."""
    to = (to or "").upper()
    if to not in STATUSES:
        raise ClefsError(400, f"Unknown status {to}", "invalid_status")
    f = _load(cur, hotel_slug, finding_id)
    frm = f["status"]
    if to == frm and to != "RECOVERED":
        return f
    if not force and to not in ALLOWED.get(frm, frozenset()):
        raise ClefsError(409, f"A {frm} finding cannot become {to}", "illegal_transition")
    if to == "DISMISSED":
        reason = (dismiss_reason or "").upper()
        if reason not in DISMISS_REASONS:
            raise ClefsError(400, "A dismissal needs a reason", "dismiss_reason_required")
    sets: dict[str, Any] = {"status": to}
    if to == "DISMISSED":
        sets.update(dismiss_reason=dismiss_reason.upper(), dismiss_note=(note or None) and note[:2000])
    elif frm == "DISMISSED":
        sets.update(dismiss_reason=None, dismiss_note=None)
    credit = 0
    if to == "RECOVERED":
        total = int(f["amount_minor"]) if recovered_minor is None else max(0, int(recovered_minor))
        credit = max(0, total - int(f["recovered_minor"] or 0))
        sets.update(recovered_minor=max(total, int(f["recovered_minor"] or 0)),
                    verified_by=verified_by or ("MANUAL" if channel == "console" else "SYSTEM"))
    cols = ", ".join(f"{k} = %s" for k in sets)
    cur.execute(f"UPDATE pfg_findings SET {cols}, updated_at = NOW() WHERE id = %s RETURNING *",
                (*sets.values(), finding_id))
    row = dict(cur.fetchone())
    log_event(cur, f, to, actor=actor, channel=CREDIT if credit else channel, note=note,
              amount_minor=credit if credit else int(f["amount_minor"]), evidence=evidence, from_status=frm)
    if actor is not None and not isinstance(actor, str) and hasattr(actor, "hotel"):
        audit(cur, actor, "profit.finding_transition", "pfg_finding", finding_id,
              before={"status": frm}, after={"status": to, "dismiss_reason": sets.get("dismiss_reason")},
              meta={"note": note, "recovered_minor": sets.get("recovered_minor")}, hotel_slug=hotel_slug)
    if credit:
        pg_signals.raise_recovery_verified(cur, get_hotel(cur, hotel_slug), row["ota_code"],
                                           htime.local_date(get_hotel(cur, hotel_slug), clock.now()))
    if refresh:
        pg_signals.refresh_findings_signals(cur, hotel_slug, [finding_id])
    return row


def dismiss(cur: Any, hotel_slug: str, finding_id: int, *, reason: str, note: str | None = None,
            actor: Any = None) -> dict:
    """Any open state can be dismissed, with one of the 3.4 reasons (the
    weekly feedback tuning reads them)."""
    f = _load(cur, hotel_slug, finding_id)
    if f["status"] not in OPEN:
        raise ClefsError(409, f"A {f['status']} finding cannot be dismissed", "illegal_transition")
    return transition(cur, hotel_slug, finding_id, "DISMISSED", actor=actor, note=note, dismiss_reason=reason)


# ─── Credits and auto-verification ───

INVOICED_LABEL = "invoiced_commission_minor"   # the snapshot a DISPUTED event carries


def res_norm_of(finding: dict) -> str | None:
    """The booking a finding is about: the item half of subject_ref
    ("<group>|<ota_res_id_norm>"), else its statement line's id."""
    item = str(finding.get("subject_ref") or "").partition("|")[2]
    return item or None


def invoiced_commission(cur: Any, hotel_slug: str, ota_code: str, res_norm: str, currency: str) -> int:
    """What the OTA currently invoices for a booking: the positive
    commission lines of every document (credit notes excluded)."""
    cur.execute(
        "SELECT COALESCE(SUM(commission_minor), 0) AS s FROM pfg_ota_statement_lines WHERE hotel_slug = %s "
        "AND ota_code = %s AND ota_res_id_norm = %s AND currency = %s AND commission_minor > 0",
        (hotel_slug, ota_code, res_norm, currency),
    )
    return int(cur.fetchone()["s"])


def _snapshot(cur: Any, finding_ids: list[int]) -> int | None:
    """The earliest invoiced-commission snapshot among these findings'
    DISPUTED events (taken when the packet went out)."""
    cur.execute("SELECT evidence FROM pfg_finding_events WHERE finding_id = ANY(%s) AND to_status = 'DISPUTED' "
                "ORDER BY at, id", (finding_ids,))
    for r in cur.fetchall():
        for e in r["evidence"] or []:
            if isinstance(e, dict) and e.get("label") == INVOICED_LABEL and e.get("value") is not None:
                return int(e["value"])
    return None


def booking_credits(cur: Any, hotel_slug: str, ota_code: str, res_norm: str, currency: str,
                    finding_ids: list[int]) -> dict:
    """{credit_minor, source STATEMENT|PAYOUT, lines[evidence]} for one booking (see the module docstring)."""
    from clefs.modules.profit_guard import repo

    lines = repo.credit_lines(cur, hotel_slug, ota_code, res_norm, currency)
    neg = lines["statement"]
    stmt = sum(int(r["credit_minor"]) for r in neg)
    ev = [{"kind": "row", "label": f"{r['doc_kind'].replace('_', ' ').lower()} {r['doc_no']}",
           "value": int(r["credit_minor"]), "ref": {"table": "pfg_ota_statement_lines", "id": r["id"]}}
          for r in neg]
    base = _snapshot(cur, finding_ids)
    if base is not None:
        now_inv = invoiced_commission(cur, hotel_slug, ota_code, res_norm, currency)
        if now_inv < base:
            stmt += base - now_inv
            ev.append({"kind": "calc", "label": "invoice corrected", "value": base - now_inv,
                       "ref": {"invoiced_at_dispute": base, "invoiced_now": now_inv}})
    pay_rows = lines["payout"]
    pay = sum(int(r["credit_minor"]) for r in pay_rows)
    if pay > stmt:
        return {"credit_minor": pay, "source": "PAYOUT", "lines": [
            {"kind": "row", "label": f"payout {r['payout_ref']}", "value": int(r["credit_minor"]),
             "ref": {"table": "pfg_ota_payout_lines", "id": r["id"]}} for r in pay_rows]}
    return {"credit_minor": stmt, "source": "STATEMENT", "lines": ev}


def _candidates(cur: Any, hotel_slug: str, ota_code: str | None) -> dict[tuple, list[dict]]:
    """Commission claims only (repo.COMMISSION_DETECTORS). A VCC balance
    (L8/L9), a payout shortfall (L10) or a chargeback (L13) shares the
    booking's id but is settled by charging the card or by the OTA paying
    out, never by a commission credit note: crediting one would close a
    card alert that still needs the card charged."""
    from clefs.modules.profit_guard import repo

    cur.execute(
        """
        SELECT * FROM pfg_findings WHERE hotel_slug = %s AND class = 'RECOVERABLE' AND NOT internal_only
           AND ota_code IS NOT NULL AND (%s::text IS NULL OR ota_code = %s) AND detector = ANY(%s)
           AND (status = ANY(%s) OR (status = 'RECOVERED' AND recovered_minor > 0))
         ORDER BY deadline_at NULLS LAST, id
        """,
        (hotel_slug, ota_code, ota_code, list(repo.COMMISSION_DETECTORS), list(VERIFY_STATUSES)),
    )
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in cur.fetchall():
        norm = res_norm_of(r)
        if norm and norm not in ("fx",):
            groups[(r["ota_code"], norm, r["currency"])].append(dict(r))
    return groups


def auto_verify(cur: Any, hotel_slug: str, *, ota_code: str | None = None) -> dict:
    """3.5 auto-verification over every booking with an open RECOVERABLE
    finding. Idempotent: a second run with no new document changes
    nothing. Returns {bookings, credited_minor, recovered, partial,
    unresolved, packets}."""
    hotel = get_hotel(cur, hotel_slug)
    now = clock.now()
    out = {"bookings": 0, "credited_minor": 0, "recovered": [], "partial": [], "unresolved": [], "packets": []}
    touched_otas: set[str] = set()
    for (ota, norm, ccy), rows in _candidates(cur, hotel_slug, ota_code).items():
        if all(r["status"] == "RECOVERED" for r in rows):
            continue
        out["bookings"] += 1
        got = booking_credits(cur, hotel_slug, ota, norm, ccy, [r["id"] for r in rows])
        # What earlier runs attributed on this booking is spent. A finding
        # can only take credit above its own baseline (the credit already
        # netted into its amount when it was detected), so a credit that
        # shrank the claim is never counted again as its recovery.
        consumed = sum(int(r["recovered_minor"] or 0) for r in rows)
        for r in rows:
            if r["status"] == "RECOVERED":
                continue
            need = int(r["amount_minor"]) - int(r["recovered_minor"] or 0)
            take = min(need, got["credit_minor"] - int(r.get("credit_baseline_minor") or 0) - consumed)
            if take <= 0:
                continue
            consumed += take
            _apply_credit(cur, hotel, r, take, got, out)
            out["credited_minor"] += take
            touched_otas.add(ota)
    out["unresolved"] = timeouts(cur, hotel, now=now)
    moved = out["recovered"] + out["partial"] + out["unresolved"]
    if moved:
        pg_signals.refresh_findings_signals(cur, hotel_slug, moved)
        from clefs.modules.profit_guard import disputes  # disputes imports this module

        out["packets"] = disputes.sync_packets_for(cur, hotel, moved)
    for ota in sorted(touched_otas):
        pg_signals.raise_recovery_verified(cur, hotel, ota, htime.local_date(hotel, now))
    return out


def _apply_credit(cur: Any, hotel: Any, r: dict, take: int, got: dict, out: dict) -> None:
    """95% or more: RECOVERED. Less is a partial credit: a rejected or
    timed-out claim is live again (DISPUTED); a claim nobody filed yet
    (DETECTED/ACCEPTED) keeps its state, so its alert stays up for the
    part still owed and no dispute is implied that was never sent."""
    total = int(r["recovered_minor"] or 0) + take
    full = share(total, int(r["amount_minor"])) >= RECOVERED_SHARE
    to = "RECOVERED" if full else ("DISPUTED" if r["status"] in ("REJECTED", "UNRESOLVED") else r["status"])
    note = None if full else (f"Partial credit: {total} of {r['amount_minor']} minor units")
    cur.execute(
        "UPDATE pfg_findings SET status = %s, recovered_minor = %s, verified_by = %s, updated_at = NOW() "
        "WHERE id = %s", (to, total, got["source"] if full else r.get("verified_by"), r["id"]),
    )
    log_event(cur, r, to, actor="statement" if got["source"] == "STATEMENT" else "payout", channel=CREDIT,
              note=note, amount_minor=take, evidence=got["lines"][:50])
    (out["recovered"] if full else out["partial"]).append(r["id"])


# ─── Timeout ───


def submitted_at(cur: Any, finding: dict) -> datetime | None:
    """When the dispute went out: the packet's submitted_at, else the
    finding's latest move to DISPUTED."""
    if finding.get("dispute_packet_id"):
        cur.execute("SELECT submitted_at FROM pfg_dispute_packets WHERE id = %s", (finding["dispute_packet_id"],))
        row = cur.fetchone()
        if row and row["submitted_at"]:
            return row["submitted_at"]
    cur.execute("SELECT MAX(at) AS at FROM pfg_finding_events WHERE finding_id = %s AND to_status = 'DISPUTED' "
                "AND COALESCE(channel, '') <> %s", (finding["id"], CREDIT))
    row = cur.fetchone()
    return row["at"] if row else None


def timeouts(cur: Any, hotel: Any, *, now: datetime) -> list[int]:
    """DISPUTED findings with no credit 45 days after submission, once the
    OTA has issued a later invoice (so the credit had its chance to show
    up), become UNRESOLVED. A partial credit is not 'no credit'."""
    cur.execute(
        "SELECT * FROM pfg_findings WHERE hotel_slug = %s AND status = 'DISPUTED' AND recovered_minor = 0 "
        "AND ota_code IS NOT NULL ORDER BY id", (hotel.slug,),
    )
    moved = []
    for f in [dict(r) for r in cur.fetchall()]:
        sent = submitted_at(cur, f)
        if sent is None or now - sent < timedelta(days=TIMEOUT_DAYS):
            continue
        cur.execute(
            "SELECT doc_no, doc_date FROM pfg_ota_statements WHERE hotel_slug = %s AND ota_code = %s "
            "AND doc_kind IN ('COMMISSION_INVOICE', 'RES_STATEMENT', 'PAYOUT_STATEMENT') AND doc_date > %s "
            "ORDER BY doc_date LIMIT 1", (hotel.slug, f["ota_code"], htime.local_date(hotel, sent)),
        )
        later = cur.fetchone()
        if later is None:
            continue
        transition(cur, hotel.slug, f["id"], "UNRESOLVED", actor="ledger", channel="system", refresh=False,
                   note=f"No credit {TIMEOUT_DAYS} days after submission; later invoice {later['doc_no']} "
                        f"({later['doc_date']}) carries none",
                   evidence=[{"kind": "row", "label": f"invoice {later['doc_no']}",
                              "ref": {"table": "pfg_ota_statements", "doc_no": later["doc_no"]}}])
        moved.append(f["id"])
    return moved


# ─── Reads ───

FINDING_MONEY = ("amount_minor", "recovered_minor")


def finding_out(row: dict, *, now: datetime | None = None) -> dict:
    from clefs.modules.profit_guard import repo

    out = repo.out(row, money_fields=FINDING_MONEY)
    group, _, item = str(row.get("subject_ref") or "").partition("|")
    out.update(group=group or None, booking_ref=item or None)
    dl = row.get("deadline_at")
    out["hours_left"] = round((dl - (now or clock.now())).total_seconds() / 3600, 1) if dl else None
    return out


def list_findings(cur: Any, hotel_slug: str, *, status: list[str] | None = None, detector: list[str] | None = None,
                  ota_code: str | None = None, klass: str | None = None, start: date | None = None,
                  end: date | None = None, limit: int = 200) -> list[dict]:
    """Findings newest first. Internal-only rows (Inquiry Guard's OTA
    leaks, Canon 19) never list here."""
    hotel = get_hotel(cur, hotel_slug)
    detector = [d.upper() for d in detector] if detector else None     # "l1b" finds L1b
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=hotel.tz) if start else None
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=hotel.tz) if end else None
    cur.execute(
        """
        SELECT * FROM pfg_findings WHERE hotel_slug = %s AND NOT internal_only
           AND (%s::text[] IS NULL OR status = ANY(%s)) AND (%s::text[] IS NULL OR UPPER(detector) = ANY(%s))
           AND (%s::text IS NULL OR ota_code = %s) AND (%s::text IS NULL OR class = %s)
           AND (%s::timestamptz IS NULL OR created_at >= %s) AND (%s::timestamptz IS NULL OR created_at < %s)
         ORDER BY created_at DESC, id DESC LIMIT %s
        """,
        (hotel_slug, status, status, detector, detector, ota_code, ota_code, klass, klass, t0, t0, t1, t1,
         max(1, min(int(limit), 500))),
    )
    now = clock.now()
    return [finding_out(dict(r), now=now) for r in cur.fetchall()]


def finding_detail(cur: Any, hotel_slug: str, finding_id: int) -> dict:
    cur.execute("SELECT * FROM pfg_findings WHERE hotel_slug = %s AND id = %s AND NOT internal_only",
                (hotel_slug, finding_id))
    row = cur.fetchone()
    if row is None:
        raise ClefsError(404, "Finding not found", "finding_not_found")
    out = finding_out(dict(row))
    cur.execute("SELECT id, at, from_status, to_status, actor, channel, note, amount_minor, evidence "
                "FROM pfg_finding_events WHERE finding_id = %s ORDER BY at, id", (finding_id,))
    out["events"] = [{**dict(e), "at": e["at"].isoformat()} for e in cur.fetchall()]
    out["dispute_packet"] = None
    if row["dispute_packet_id"]:
        cur.execute("SELECT id, status, total_minor, currency, submitted_at FROM pfg_dispute_packets WHERE id = %s",
                    (row["dispute_packet_id"],))
        p = cur.fetchone()
        out["dispute_packet"] = dict(p) if p else None
    return out


def summary(cur: Any, hotel_slug: str, *, start: date | None = None, end: date | None = None) -> dict:
    """The Overview and ledger figures in the hotel's currency: found,
    recovered (credit events), open, due within 72 h, the status funnel,
    leakage by detector and the latest ledger events. Findings in another
    currency are counted but not summed (they carry their own currency)."""
    hotel = get_hotel(cur, hotel_slug)
    now = clock.now()
    t0 = datetime.combine(start, datetime.min.time(), tzinfo=hotel.tz) if start else None
    t1 = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=hotel.tz) if end else None
    cur.execute(
        """
        SELECT detector, class, status, currency, COUNT(*) AS n, SUM(amount_minor) AS amount,
               SUM(recovered_minor) AS recovered,
               COUNT(*) FILTER (WHERE deadline_at > %s AND deadline_at <= %s AND status IN ('DETECTED','ACCEPTED'))
                   AS due72, SUM(amount_minor) FILTER (WHERE deadline_at > %s AND deadline_at <= %s
                   AND status IN ('DETECTED','ACCEPTED')) AS due72_amount
          FROM pfg_findings WHERE hotel_slug = %s AND NOT internal_only AND class <> 'STRUCTURAL'
           AND (%s::timestamptz IS NULL OR created_at >= %s) AND (%s::timestamptz IS NULL OR created_at < %s)
         GROUP BY detector, class, status, currency
        """,
        (now, now + timedelta(hours=72), now, now + timedelta(hours=72), hotel_slug, t0, t0, t1, t1),
    )
    rows = [dict(r) for r in cur.fetchall()]
    mine = [r for r in rows if r["currency"] == hotel.currency]
    # COMPLIANCE never enters money totals, nor does T5 (Tourism Dirham owed back to a guest).
    money_rows = [r for r in mine if r["class"] in ("RECOVERABLE", "PREVENTABLE")
                  and r["detector"] not in pg_signals.TAX_DETECTORS]
    funnel = {s: {"count": 0, "amount_minor": 0} for s in STATUSES}
    by_det: dict[str, dict] = {}
    for r in money_rows:
        funnel[r["status"]]["count"] += int(r["n"])
        funnel[r["status"]]["amount_minor"] += int(r["amount"] or 0)
        if r["status"] != "DISMISSED":
            d = by_det.setdefault(r["detector"], {"detector": r["detector"], "count": 0, "amount_minor": 0})
            d["count"] += int(r["n"])
            d["amount_minor"] += int(r["amount"] or 0)
    found = sum(int(r["amount"] or 0) for r in money_rows if r["status"] != "DISMISSED")
    opened = sum(int(r["amount"] or 0) for r in money_rows if r["status"] in OPEN)
    cur.execute(
        "SELECT COALESCE(SUM(e.amount_minor), 0) AS s FROM pfg_finding_events e JOIN pfg_findings f "
        "ON f.id = e.finding_id WHERE e.hotel_slug = %s AND e.channel = %s AND f.currency = %s "
        "AND (%s::timestamptz IS NULL OR e.at >= %s) AND (%s::timestamptz IS NULL OR e.at < %s)",
        (hotel_slug, CREDIT, hotel.currency, t0, t0, t1, t1),
    )
    recovered = int(cur.fetchone()["s"])
    return {
        "currency": hotel.currency, "found_minor": found, "recovered_minor": recovered, "open_minor": opened,
        "due_72h_count": sum(int(r["due72"] or 0) for r in mine),
        "due_72h_minor": sum(int(r["due72_amount"] or 0) for r in mine),
        "compliance_open": sum(int(r["n"]) for r in rows if r["class"] == "COMPLIANCE" and r["status"] in OPEN),
        "funnel": funnel, "by_detector": sorted(by_det.values(), key=lambda d: -d["amount_minor"]),
        "other_currency_findings": sum(int(r["n"]) for r in rows if r["currency"] != hotel.currency),
    }


def recent_events(cur: Any, hotel_slug: str, *, limit: int = 100) -> list[dict]:
    cur.execute(
        """
        SELECT e.id, e.finding_id, e.at, e.from_status, e.to_status, e.actor, e.channel, e.note, e.amount_minor,
               f.detector, f.ota_code, f.currency
          FROM pfg_finding_events e JOIN pfg_findings f ON f.id = e.finding_id
         WHERE e.hotel_slug = %s AND NOT f.internal_only ORDER BY e.at DESC, e.id DESC LIMIT %s
        """,
        (hotel_slug, max(1, min(int(limit), 500))),
    )
    return [{**dict(r), "at": r["at"].isoformat()} for r in cur.fetchall()]


def deadlines(cur: Any, hotel_slug: str, *, days: int = 14) -> list[dict]:
    """Everything with a clock on it in the next `days`, soonest first:
    open findings with a deadline (dispute windows, cards, no-show
    reporting) and unremitted Tourism Dirham months."""
    hotel = get_hotel(cur, hotel_slug)
    now = clock.now()
    cur.execute(
        "SELECT * FROM pfg_findings WHERE hotel_slug = %s AND status IN ('DETECTED', 'ACCEPTED', 'DISPUTED') "
        "AND NOT internal_only AND deadline_at IS NOT NULL AND deadline_at > %s AND deadline_at <= %s "
        "ORDER BY deadline_at, id LIMIT 500", (hotel_slug, now, now + timedelta(days=days)),
    )
    out = [{"kind": "finding", **finding_out(dict(r), now=now)} for r in cur.fetchall()]
    today = htime.local_date(hotel, now)
    cur.execute("SELECT * FROM pfg_td_ledger WHERE hotel_slug = %s AND status <> 'REMITTED' AND due_date IS NOT NULL "
                "AND due_date <= %s ORDER BY due_date", (hotel_slug, today + timedelta(days=days)))
    for r in cur.fetchall():
        due = datetime.combine(r["due_date"] + timedelta(days=1), datetime.min.time(), tzinfo=hotel.tz)
        out.append({"kind": "td_remittance", "month": r["month"].isoformat(), "deadline_at": due,
                    "amount_minor": int(r["td_collected_minor"] or r["td_expected_minor"] or 0),
                    "currency": r["currency"], "hours_left": round((due - now).total_seconds() / 3600, 1)})
    out.sort(key=lambda x: x["deadline_at"])
    for x in out:
        x["deadline_at"] = x["deadline_at"].isoformat() if isinstance(x["deadline_at"], datetime) else x["deadline_at"]
    return out


# ─── Pilot scorecard ───

PILOT_MIN_VALID = 3
PILOT_MULTIPLE = 3


def pilot_terms(cur: Any, hotel_slug: str) -> dict:
    """The pilot's terms from hotel_module_settings['profit_guard'].settings:
    pilot_started_on (ISO date), pilot_months (default 1) and
    subscription_minor_month (the monthly price in minor units; unset means
    only the count criterion can be judged)."""
    from clefs.core.hotels import module_settings_row

    raw = (module_settings_row(cur, hotel_slug, "profit_guard") or {}).get("settings") or {}
    started = None
    try:
        started = date.fromisoformat(str(raw["pilot_started_on"])) if raw.get("pilot_started_on") else None
    except ValueError:
        started = None
    try:
        months = max(1, int(raw.get("pilot_months") or 1))
    except (TypeError, ValueError):
        months = 1
    try:
        sub = int(raw["subscription_minor_month"]) if raw.get("subscription_minor_month") is not None else None
    except (TypeError, ValueError):
        sub = None
    return {"pilot_started_on": started, "pilot_months": months, "subscription_minor_month": sub}


def pilot_scorecard(cur: Any, hotel_slug: str) -> dict:
    """3.4: valid = findings ACCEPTED, DISPUTED or RECOVERED. Success is
    valid >= 3, or the valid RECOVERABLE amount >= 3 x the monthly
    subscription x pilot months. Counted from the pilot start when set."""
    hotel = get_hotel(cur, hotel_slug)
    terms = pilot_terms(cur, hotel_slug)
    since = (datetime.combine(terms["pilot_started_on"], datetime.min.time(), tzinfo=hotel.tz)
             if terms["pilot_started_on"] else None)
    cur.execute(
        """
        SELECT status, class, currency, detector, COUNT(*) AS n, SUM(amount_minor) AS amount,
               SUM(recovered_minor) AS rec
          FROM pfg_findings WHERE hotel_slug = %s AND NOT internal_only AND status = ANY(%s)
           AND (%s::timestamptz IS NULL OR created_at >= %s)
         GROUP BY status, class, currency, detector
        """,
        (hotel_slug, list(VALID), since, since),
    )
    rows = [dict(r) for r in cur.fetchall()]
    valid = sum(int(r["n"]) for r in rows)
    recoverable = sum(int(r["amount"] or 0) for r in rows if r["class"] == "RECOVERABLE"
                      and r["currency"] == hotel.currency and r["detector"] not in pg_signals.TAX_DETECTORS)
    recovered = sum(int(r["rec"] or 0) for r in rows if r["currency"] == hotel.currency)
    threshold = (PILOT_MULTIPLE * terms["subscription_minor_month"] * terms["pilot_months"]
                 if terms["subscription_minor_month"] is not None else None)
    by_status = {s: sum(int(r["n"]) for r in rows if r["status"] == s) for s in VALID}
    count_ok = valid >= PILOT_MIN_VALID
    amount_ok = threshold is not None and recoverable >= threshold
    return {
        "currency": hotel.currency, "valid_findings": valid, "by_status": by_status,
        "valid_recoverable_minor": recoverable, "recovered_minor": recovered,
        "min_valid": PILOT_MIN_VALID, "amount_threshold_minor": threshold,
        "criteria": {"count": count_ok, "amount": amount_ok}, "success": count_ok or amount_ok,
        "pilot_started_on": terms["pilot_started_on"].isoformat() if terms["pilot_started_on"] else None,
        "pilot_months": terms["pilot_months"], "subscription_minor_month": terms["subscription_minor_month"],
    }


# ─── Routes ───
# Mounted by the manifest (FOUNDATION-REQUEST in the lane result until
# module.py lists ledger.router): reads need profit.view, moves profit.manage.

from fastapi import APIRouter, Depends, Query  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from clefs.auth.deps import HotelCtx, require_perm  # noqa: E402
from clefs.core import money  # noqa: E402

router = APIRouter(prefix="/hotels/{slug}/profit-guard", tags=["profit_guard"])
VIEW = require_perm("profit.view")
MANAGE = require_perm("profit.manage")


def _csv(v: str | None) -> list[str] | None:
    """`a,b` query values, upper-cased; None when empty (no filter)."""
    return [x.strip().upper() for x in (v or "").split(",") if x.strip()] or None


def _money(d: dict, ccy: str) -> dict:
    """Add the Decimal-string twin of every *_minor field (Canon 2)."""
    for k in list(d):
        if k.endswith("_minor") and isinstance(d[k], int):
            d[k.removesuffix("_minor")] = money.money_str(d[k], ccy)
    return d


class TransitionIn(BaseModel):
    to: str = Field(max_length=20)
    note: str | None = Field(default=None, max_length=2000)
    amount: Decimal | None = Field(default=None, ge=0)     # RECOVERED: the credited total


class DismissIn(BaseModel):
    reason: str = Field(max_length=40)
    note: str | None = Field(default=None, max_length=2000)


@router.get("/findings")
def get_findings(slug: str, status: str | None = None, detector: str | None = None, ota: str | None = None,
                 klass: str | None = Query(default=None, alias="class"), from_: date | None = Query(None, alias="from"),
                 to: date | None = None, limit: int = Query(200, ge=1, le=500),
                 ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return list_findings(cur, slug, status=_csv(status), detector=_csv(detector),
                             ota_code=(ota or "").upper() or None, klass=(klass or "").upper() or None,
                             start=from_, end=to, limit=limit)


@router.get("/findings/{finding_id:int}")
def get_finding(slug: str, finding_id: int, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        return finding_detail(cur, slug, finding_id)


@router.post("/findings/{finding_id:int}/transition")
def post_transition(slug: str, finding_id: int, body: TransitionIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        f = _load(cur, slug, finding_id)
        if f["internal_only"]:
            raise ClefsError(404, "Finding not found", "finding_not_found")
        rec = money.to_minor(body.amount, f["currency"]) if body.amount is not None else None
        if body.to.upper() == "DISMISSED":
            raise ClefsError(400, "Use /dismiss with a reason", "dismiss_reason_required")
        transition(cur, slug, finding_id, body.to, actor=ctx, note=body.note, recovered_minor=rec)
        if body.to.upper() in ("RECOVERED", "REJECTED", "UNRESOLVED") and f.get("dispute_packet_id"):
            from clefs.modules.profit_guard import disputes

            disputes.sync_packets_for(cur, get_hotel(cur, slug), [finding_id])
        return finding_detail(cur, slug, finding_id)


@router.post("/findings/{finding_id:int}/dismiss")
def post_dismiss(slug: str, finding_id: int, body: DismissIn, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    with db.db() as cur:
        dismiss(cur, slug, finding_id, reason=body.reason, note=body.note, actor=ctx)
        return finding_detail(cur, slug, finding_id)


@router.post("/findings/verify")
def post_verify(slug: str, ctx: HotelCtx = Depends(MANAGE)) -> dict:
    """Run auto-verification now (it also runs after every OTA statement,
    credit note or payout commit, and daily)."""
    with db.db() as cur:
        get_hotel(cur, slug)
        out = auto_verify(cur, slug)
        audit(cur, ctx, "profit.auto_verify", "pfg_findings", None, meta={
            "credited_minor": out["credited_minor"], "recovered": len(out["recovered"])}, hotel_slug=slug)
        return out


@router.get("/ledger")
def get_ledger(slug: str, from_: date | None = Query(None, alias="from"), to: date | None = None,
               limit: int = Query(100, ge=1, le=500), ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        s = summary(cur, slug, start=from_, end=to)
        ccy = s["currency"]
        _money(s, ccy)
        for v in s["funnel"].values():
            _money(v, ccy)
        for d in s["by_detector"]:
            _money(d, ccy)
        events = [_money({**e, "amount_minor": e["amount_minor"]}, e["currency"]) for e in
                  recent_events(cur, slug, limit=limit)]
        return {**s, "events": events}


@router.get("/deadlines")
def get_deadlines(slug: str, days: int = Query(14, ge=1, le=120), ctx: HotelCtx = Depends(VIEW)) -> list[dict]:
    with db.db(readonly=True) as cur:
        return [_money(d, d.get("currency") or "AED") for d in deadlines(cur, slug, days=days)]


@router.get("/pilot-scorecard")
def get_pilot_scorecard(slug: str, ctx: HotelCtx = Depends(VIEW)) -> dict:
    with db.db(readonly=True) as cur:
        s = pilot_scorecard(cur, slug)
        return _money(s, s["currency"])
