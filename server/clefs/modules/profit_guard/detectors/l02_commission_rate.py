"""L2: the commission percent charged exceeds the contract percent plus the
uplifts in force (02 spec 3.3).

effective = commission / commissionable base; expected = base pct + Σ
active COMMISSION_UPLIFT programs for the stay date, booker country and
rate plan (Preferred, Preferred Plus, Visibility Booster, AGX). Flag when
effective - expected > 0.1 pp (exactly 0.1 pp does not flag) and the
amount clears the tolerance. RECOVERABLE 0.85; 0.5 while a country-scoped
Visibility Booster is active and the booker country is unknown (the uplift
may apply). Deadline: invoice date + 14 business days.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard import contracts, repo
from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, add_business_days, end_of_local_day, net_once, rnd, step, tol,
    row_ev,
)

DETECTOR = "L2"
THRESHOLD_PP = Decimal("0.1")
INVOICE_BUSINESS_DAYS = 14


def _vb_country_scoped(ctx: Ctx, ota: str, on) -> bool:
    """A Visibility Booster limited to booker countries is active that day."""
    return any(p["program"] == "VISIBILITY_BOOSTER" and p["booker_countries"] and p["kind"] == "COMMISSION_UPLIFT"
               and not (p["stay_from"] and on < p["stay_from"]) and not (p["stay_to"] and on > p["stay_to"])
               for p in repo.list_programs(ctx.cur, ctx.slug, ota_code=ota, active_only=True))


def expected_pct(ctx: Ctx, line: dict) -> Decimal | None:
    """The contract percent plus uplifts in force for this line (what L2
    compares against); None without terms. L1 uses it too: see there."""
    on = line.get("check_in") or line.get("doc_date") or ctx.today
    terms = data.terms_on(ctx, line["ota_code"], on)
    if terms is None:
        return None
    country = (line.get("booker_country") or line.get("r_guest_country") or None)
    expected, _ = contracts.commission_pct(ctx.cur, ctx.slug, terms, on, booker_country=country,
                                           rate_plan=line.get("rate_plan"))
    return Decimal(str(expected))


def _check(ctx: Ctx, line: dict) -> FindingDraft | None:
    base, comm = line.get("commissionable_minor"), line.get("commission_minor")
    if not base or base <= 0 or comm is None or comm <= 0:
        return None
    on = line.get("check_in") or line.get("doc_date") or ctx.today
    terms = data.terms_on(ctx, line["ota_code"], on)
    if terms is None:
        return None
    country = (line.get("booker_country") or line.get("r_guest_country") or None)
    expected, uplifts = contracts.commission_pct(ctx.cur, ctx.slug, terms, on, booker_country=country,
                                                 rate_plan=line.get("rate_plan"))
    effective = Decimal(comm) * 100 / Decimal(base)
    diff = effective - expected
    if diff <= THRESHOLD_PP:
        return None
    amount = rnd(diff * Decimal(base) / 100)
    if amount <= tol(rnd(expected * Decimal(base) / 100)):
        return None
    gross = amount
    cred = data.credited(ctx, line["ota_code"], line.get("ota_res_id_norm"), line["currency"])
    amount = gross - cred   # net_once() nets the booking's credit once over its lines
    unsure = country is None and _vb_country_scoped(ctx, line["ota_code"], on)
    doc_date = line.get("doc_date")
    deadline = end_of_local_day(ctx.hotel, add_business_days(ctx.hotel, doc_date, INVOICE_BUSINESS_DAYS)) \
        if doc_date else None
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=line["currency"], credited_minor=cred,
        base_confidence=Decimal("0.5") if unsure else Decimal("0.85"), ota_code=line["ota_code"],
        res_norm=line.get("ota_res_id_norm"), doc=line["doc_no"], group=f"doc:{line['doc_no']}",
        missing=[] if country else ["booker_country"],
        calc=[
            step("Effective percent", "commission / commissionable x 100",
                 {"commission_minor": comm, "base_minor": base}, result=effective.quantize(Decimal("0.001"))),
            step("Expected percent", "contract pct + Σ uplifts",
                 {"contract_pct": terms["base_commission_pct"],
                  "basis": "default" if terms.get("is_default") else "contract",
                  "uplifts": ",".join(f"{p['program']}:{p['pct']}" for p in uplifts)}, result=expected),
            step("Overcharge", "(effective - expected) x base", {"diff_pp": diff.quantize(Decimal("0.001")),
                                                                 "threshold_pp": THRESHOLD_PP}, amount),
        ],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", line["id"]),
                  {"kind": "quote", "label": "Contract commission", "value": str(terms["base_commission_pct"]),
                   "ref": {"contract_id": terms.get("id")}}],
        deadline_at=deadline, reservation_id=line.get("reservation_id"), statement_line_id=line["id"],
        gross_minor=gross,
    )


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    return net_once([d for d in (_check(ctx, ln) for ln in data.statement_lines(ctx, scope)) if d is not None])
