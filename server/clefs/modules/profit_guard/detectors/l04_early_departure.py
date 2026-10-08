"""L4: commission charged on nights the guest never stayed (02 spec 3.3).

The PMS shows actual_check_out < check_out while the OTA still reports
the original room nights. Amount: pct x Σ nightly rates of the unstayed
nights - early-departure fee, computed as the line's commission times the
unstayed share of the PMS nightly rates (so the OTA's own base, taxes and
all, carries through), less pct x the fee collected. RECOVERABLE 0.8 x
match score. Deadline: the dispute window, as L3.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, dispute_deadline, net_once, pct_amount, rnd, step,
    row_ev,
)

DETECTOR = "L4"
BASE = Decimal("0.8")


def _rates(ctx: Ctx, reservation_id: int, cut) -> tuple[int, int, int, int]:
    """(Σ rate of all nights, Σ rate of nights from `cut`, nights, unstayed nights)."""
    ctx.cur.execute(
        """
        SELECT COALESCE(SUM(rate_minor * rooms), 0) AS total,
               COALESCE(SUM(rate_minor * rooms) FILTER (WHERE stay_date >= %s), 0) AS unstayed,
               COUNT(DISTINCT stay_date) AS n, COUNT(DISTINCT stay_date) FILTER (WHERE stay_date >= %s) AS nu
          FROM reservation_nights WHERE hotel_slug = %s AND reservation_id = %s
        """,
        (cut, cut, ctx.slug, reservation_id),
    )
    r = ctx.cur.fetchone()
    return int(r["total"]), int(r["unstayed"]), int(r["n"]), int(r["nu"])


def _check(ctx: Ctx, line: dict) -> FindingDraft | None:
    commission = int(line.get("commission_minor") or 0)
    rid, actual, booked_out = line.get("reservation_id"), line.get("r_actual_check_out"), line.get("r_check_out")
    if commission <= 0 or rid is None or actual is None or booked_out is None or actual >= booked_out:
        return None
    if line.get("r_status") in ("NO_SHOW", "CANCELLED"):
        return None  # L3's case
    booked_nights = int(line.get("r_nights") or (booked_out - line["r_check_in"]).days)
    reported = int(line.get("room_nights") or 0) or (
        (line["check_out"] - line["check_in"]).days if line.get("check_in") and line.get("check_out") else 0)
    still_original = reported >= booked_nights or (
        line.get("check_out") is not None and line["check_out"] >= booked_out)
    if not still_original:
        return None  # the OTA already reports the shortened stay
    total, unstayed, n, nu = _rates(ctx, rid, actual)
    missing: list[str] = []
    if total > 0 and unstayed > 0:
        share = Decimal(unstayed) / Decimal(total)
    else:
        nu = nu or (booked_out - actual).days
        share = Decimal(nu) / Decimal(max(booked_nights, 1))
        missing.append("nightly_rates")
    pct = data.line_pct(ctx, line) or Decimal(0)
    fee = data.folio_fee(ctx, rid, ("CXL_FEE",))
    on_unstayed = rnd(Decimal(commission) * share)
    if on_unstayed <= 0:
        return None
    due = pct_amount(fee, pct)
    cred = data.credited(ctx, line["ota_code"], line.get("ota_res_id_norm"), line["currency"])
    amount = on_unstayed - due - cred   # net_once() nets fee and credit once per booking
    terms = data.terms_on(ctx, line["ota_code"], line.get("check_in"))
    deadline, _ = dispute_deadline(ctx.hotel, terms, check_out=booked_out, doc_date=line.get("doc_date"),
                                   collect_model=line.get("collect_model") or line.get("r_collect_model"))
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=line["currency"], base_confidence=BASE,
        credited_minor=cred,
        ota_code=line["ota_code"], res_norm=line.get("ota_res_id_norm"), doc=line["doc_no"],
        group=f"doc:{line['doc_no']}", match_score=Decimal(str(line.get("match_score") or 1)), missing=missing,
        calc=[step("Unstayed share", "Σ rate(unstayed nights) / Σ rate(all nights)",
                   {"actual_check_out": actual, "check_out": booked_out, "unstayed_nights": nu,
                    "booked_nights": booked_nights,
                    "reported_nights": reported}, result=share.quantize(Decimal("0.0001"))),
              step("Commission on unstayed nights", "commission x share", {"commission_minor": commission},
                   on_unstayed),
              step("Overcharge", "on_unstayed - pct x early-departure fee - already credited",
                   {"pct": pct, "fee_minor": fee}, amount)],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", line["id"]),
                  {"kind": "row", "label": "PMS reservation", "ref": {"table": "reservations", "id": rid}}],
        deadline_at=deadline, reservation_id=rid, statement_line_id=line["id"],
        gross_minor=on_unstayed, deduct_minor=due, floor_base_minor=commission,
    )


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    return net_once([d for d in (_check(ctx, ln) for ln in data.statement_lines(ctx, scope)) if d is not None])
