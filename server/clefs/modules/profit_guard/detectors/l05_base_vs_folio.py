"""L5: the OTA's commissionable amount differs from the PMS room revenue,
normalised through core/tax into the same commission base (02 spec 3.3).

The PMS side is `contracts.expected_commission` over the stay's
`reservation_breakdown`: the contract's base rule, the configured charges
and the hotel's tax convention all apply, so both sides are the same kind
of number. Lines of one booking in one document are added up first (a
modified booking prints several lines).

- OTA higher by more than tol: RECOVERABLE 0.75 x match score,
  pct x difference, dispute window.
- PMS higher: a SERVICE_RISK signal (the guest may have been overcharged
  at the desk), never a commission claim.
L1, L3 and L4 on the same booking supersede it (store.py): they explain
the gap more precisely.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.core import stays
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard import contracts
from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, dispute_deadline, pct_amount, step, tol,
    row_ev,
)

DETECTOR = "L5"
BASE = Decimal("0.75")
SERVICE_KIND = "pg.folio.overcharge_risk"
SERVICE_CONF = Decimal("0.7")


def _bookings(lines: list[dict]) -> list[dict]:
    """One row per (document, reservation): amounts summed over its lines."""
    out: dict[tuple, dict] = {}
    for ln in lines:
        if ln.get("reservation_id") is None or ln.get("commissionable_minor") is None:
            continue
        key = (ln["statement_id"], ln["reservation_id"], ln["currency"])
        agg = out.get(key)
        if agg is None:
            out[key] = dict(ln)
            continue
        agg["commissionable_minor"] += ln["commissionable_minor"]
        agg["commission_minor"] = (agg.get("commission_minor") or 0) + (ln.get("commission_minor") or 0)
    return list(out.values())


def _pms_base(ctx: Ctx, ln: dict) -> dict | None:
    try:
        breakdown = stays.reservation_breakdown(ctx.cur, ln["reservation_id"])
    except ClefsError:
        return None
    return contracts.expected_commission(ctx.cur, ctx.slug, ota_code=ln["ota_code"], breakdown=breakdown,
                                         on=ln.get("r_check_in") or ln.get("check_in") or ctx.today,
                                         booker_country=ln.get("booker_country"), rate_plan=ln.get("rate_plan"))


def _check(ctx: Ctx, ln: dict) -> FindingDraft | None:
    if ln.get("r_status") in ("NO_SHOW", "CANCELLED") or (ln.get("commission_minor") or 0) <= 0:
        return None
    if ln.get("r_actual_check_out") and ln.get("r_check_out") and ln["r_actual_check_out"] < ln["r_check_out"]:
        return None  # an early departure is L4's
    exp = _pms_base(ctx, ln)
    if exp is None or exp["base_minor"] <= 0 or exp["currency"] != ln["currency"]:
        return None
    reported, pms = int(ln["commissionable_minor"]), int(exp["base_minor"])
    diff = reported - pms
    t = tol(pms)
    if -diff > t:
        ctx.side_signals.append({
            "kind": SERVICE_KIND, "dedupe_key": f"pg:L5:service:{ln['ota_code']}:{ln.get('ota_res_id_norm')}",
            "title": f"{ln['ota_code'].title()} booking {ln.get('ota_res_id') or ln.get('ota_res_id_norm')}: "
                     "PMS revenue above the OTA's price",
            "summary": "The PMS base is higher than what the OTA reports: check the guest was not overcharged.",
            # SignalIn.confidence is required: without it the whole detector
            # run rolled back at the end (QA OWN-04). 0.7 = a matched PMS
            # stay, but the gap may be a legitimate desk upsell.
            "confidence": float(min(Decimal(1), SERVICE_CONF * Decimal(str(ln.get("match_score") or 1)))),
            "impact_minor": -diff, "currency": ln["currency"], "subject_type": "reservation",
            "subject_id": str(ln["reservation_id"]),
            "evidence": [{"kind": "calc", "label": "OTA commissionable vs PMS base",
                          "value": {"ota_minor": reported, "pms_minor": pms}}],
        })
        return None
    if diff <= t:
        return None
    pct = data.line_pct(ctx, ln) or Decimal(str(exp["pct"]))
    cred = data.credited(ctx, ln["ota_code"], ln.get("ota_res_id_norm"), ln["currency"])
    amount = pct_amount(diff, pct) - cred
    if amount <= 0:
        return None
    terms = data.terms_on(ctx, ln["ota_code"], ln.get("check_in"))
    deadline, _ = dispute_deadline(ctx.hotel, terms, check_out=ln.get("check_out") or ln.get("r_check_out"),
                                   doc_date=ln.get("doc_date"), collect_model=ln.get("collect_model"))
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=ln["currency"], base_confidence=BASE,
        credited_minor=cred,
        ota_code=ln["ota_code"], res_norm=ln.get("ota_res_id_norm"), doc=ln["doc_no"], group=f"doc:{ln['doc_no']}",
        match_score=Decimal(str(ln.get("match_score") or 1)),
        calc=exp["calc"] + [
            step("OTA commissionable", "Σ commissionable of the booking's lines", {}, reported),
            step("Difference", "OTA - PMS base", {"pms_base_minor": pms, "tol_minor": t}, diff),
            step("Overcharge", "pct x difference - already credited", {"pct": pct}, amount)],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", ln["id"]),
                  row_ev("PMS reservation", "reservations", ln["reservation_id"])],
        deadline_at=deadline, reservation_id=ln["reservation_id"], statement_line_id=ln["id"],
    )


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    return [d for d in (_check(ctx, b) for b in _bookings(data.statement_lines(ctx, scope))) if d is not None]
