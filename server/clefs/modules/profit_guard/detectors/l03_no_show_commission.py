"""L3: commission charged on a stay that never produced revenue (02 spec 3.3).

- The matched PMS reservation is NO_SHOW (no fee collected) or CANCELLED
  with the fee waived, while the OTA line is OK or carries commission.
- The statement's own status says NO_SHOW or CANCELLED and still carries
  commission (the public audit's only source; no PMS needed).
- An unmatched line with commission while the PMS has data for those
  dates: kept, but at a low match score (the match may just have failed).

Amount: commission - pct x fee actually collected - already credited.
RECOVERABLE 0.9 x match score. Deadline: the contract's dispute window
(Booking 48 h after check-out, Expedia the 4th of the next month).

Fingerprint: the stay's check-out month (not the doc number) so the L3
that L14 raises before any statement arrives and the one the statement
confirms are the same finding.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, dispute_deadline, month_key, net_once, pct_amount, step,
)

DETECTOR = "L3"
BASE = Decimal("0.9")
UNMATCHED_SCORE = Decimal("0.5")
NO_REVENUE = ("NO_SHOW", "CANCELLED")


def doc_for(check_out) -> str:
    return f"M{month_key(check_out)}"


def build(ctx: Ctx, *, ota: str, res_norm: str | None, check_out, commission: int, pct: Decimal, fee: int,
          currency: str, reason: str, match_score: Decimal, missing: list[str], group: str,
          reservation_id: int | None, line: dict | None, terms: dict | None, doc_date=None,
          collect_model: str | None = None, factor: Decimal = Decimal(1),
          deadline: datetime | None = None) -> FindingDraft:
    """Shared by L3 and by L14 once its deadline passed (factor 0.5). The
    draft may net at or below its floor: the caller's run() passes its
    drafts through net_once(), which drops those."""
    due = pct_amount(fee, pct)
    cred = data.credited(ctx, ota, res_norm, currency)
    # The fee and the credit are the booking's, the commission the line's:
    # net_once() (called by run) subtracts due and cred once per booking
    # and applies the tolerance on the booking's summed commission.
    amount = commission - due - cred
    if deadline is None:
        deadline, _ = dispute_deadline(ctx.hotel, terms, check_out=check_out, doc_date=doc_date,
                                       collect_model=collect_model)
    evidence = [{"kind": "calc", "label": "PMS status", "value": reason,
                 "ref": {"table": "reservations", "id": reservation_id} if reservation_id else None}]
    if line is not None:
        evidence.insert(0, {"kind": "row", "label": "Statement line",
                            "ref": {"table": "pfg_ota_statement_lines", "id": line["id"]}})
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=currency, credited_minor=cred,
        base_confidence=(BASE * factor).quantize(Decimal("0.001")), ota_code=ota, res_norm=res_norm,
        doc=doc_for(check_out), group=group, match_score=match_score, missing=missing,
        calc=[step("Commission charged", "commission on the line", {"reason": reason}, commission),
              step("Commission due on the fee collected", "pct x fee collected", {"pct": pct, "fee_minor": fee}, due),
              step("Overcharge", "commission - due - already credited", {"factor": factor}, amount)],
        evidence=evidence, deadline_at=deadline, reservation_id=reservation_id,
        statement_line_id=line["id"] if line else None,
        gross_minor=commission, deduct_minor=due, floor_base_minor=commission,
    )


# ─── Statement lines ───


def _fee_collected(ctx: Ctx, line: dict, status: str) -> int:
    if status == "NO_SHOW":
        return max(data.folio_fee(ctx, line.get("reservation_id"), ("NOSHOW_FEE",)),
                   int(line.get("r_no_show_fee") or 0))
    return max(data.folio_fee(ctx, line.get("reservation_id"), ("CXL_FEE",)), int(line.get("r_cancel_fee") or 0))


def _check(ctx: Ctx, line: dict) -> FindingDraft | None:
    commission = int(line.get("commission_minor") or 0)
    if commission <= 0:
        return None
    ota = line["ota_code"]
    pms = line.get("r_status")
    own = line.get("line_status")
    missing: list[str] = []
    if line.get("reservation_id") is not None:
        if pms not in NO_REVENUE:
            return None  # the PMS says the guest stayed: commission is due
        if line.get("r_currency") and line["r_currency"] != line["currency"]:
            return None  # fee in another currency: L5 territory, not a clean comparison
        status, score = pms, Decimal(str(line.get("match_score") or 1))
        fee = _fee_collected(ctx, line, status)
        reason = f"PMS {status}"
    elif line.get("match_status") == "REVIEW":
        return None  # a person is choosing the reservation; judge after that
    elif own in NO_REVENUE:
        status, score, fee = own, Decimal(1), 0
        missing.append("fee_collected")
        reason = f"OTA statement {own}"
    elif data.has_pms_coverage(ctx, line.get("check_in")):
        status, score, fee = "UNMATCHED", UNMATCHED_SCORE, 0
        missing.append("pms_reservation")
        reason = "No PMS reservation"
    else:
        return None
    pct = data.line_pct(ctx, line) or Decimal(0)
    terms = data.terms_on(ctx, ota, line.get("check_in"))
    return build(ctx, ota=ota, res_norm=line.get("ota_res_id_norm"),
                 check_out=line.get("check_out") or line.get("r_check_out"), commission=commission, pct=pct,
                 fee=fee, currency=line["currency"], reason=reason, match_score=score, missing=missing,
                 group=f"doc:{line['doc_no']}", reservation_id=line.get("reservation_id"), line=line,
                 terms=terms, doc_date=line.get("doc_date"),
                 collect_model=line.get("collect_model") or line.get("r_collect_model"))


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    return net_once([d for d in (_check(ctx, ln) for ln in data.statement_lines(ctx, scope)) if d is not None])
