"""L1: commission charged on a base that includes charges the contract
excludes (02 spec 3.3; Canon K2, GDT v2601 cl. 2.3.2(ii)).

- Booking, fees configured as charges: flag reported_base - (gross -
  excluded charges) > tol. RECOVERABLE 0.9 from the line's price breakdown,
  0.7 when the fees are inferred through core/tax.
- Booking, fees NOT configured as charges (L1b): commission on them is due
  under the contract, so the fix is to configure them. PREVENTABLE 0.85,
  pct x (MF + TD) over the last 90 days / 3 per month.
- Agoda: the RSR is tax-inclusive, so commission lands on taxes by
  contract. STRUCTURAL: stored for net ADR and negotiation, never signalled.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.core.tax import decompose_night
from clefs.modules.profit_guard.contracts import CHARGE_FLAG
from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors import l02_commission_rate as l2
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, dispute_deadline, month_key, net_once, pct_amount, step, tol,
    row_ev,
)

DETECTOR = "L1"
DETECTOR_B = "L1b"
MF_TD = ("MUNICIPALITYFEE", "TOURISMFEE")
# core/tax components behind each OTA charge type.
COMPONENTS = {"MUNICIPALITYFEE": ("mf",), "TOURISMFEE": ("td", "tf", "dest"), "VAT": ("vat",)}
AGODA_TAXES = ("VAT", "MUNICIPALITYFEE", "TOURISMFEE", "CITYTAX")


# ─── Fee inference through core/tax ───


def infer(ctx: Ctx, line: dict, types: tuple[str, ...]) -> int | None:
    """Σ of the fee components for `types` inside the line's gross, night
    by night through the hotel's date-effective rules. None without dates."""
    ci, co, gross = line.get("check_in"), line.get("check_out"), line.get("gross_minor")
    if ci is None or co is None or not gross or gross <= 0:
        return None
    nights = max(1, int(line.get("room_nights") or (co - ci).days or 1))
    rooms = max(1, int(line.get("rooms") or 1))
    per, rest = divmod(int(gross), nights)
    comps = [c for t in types for c in COMPONENTS.get(t, ())]
    total = 0
    for i in range(nights):
        night = decompose_night(ctx.cur, ctx.slug, gross_minor=per + (rest if i == nights - 1 else 0),
                                stay_date=ci + timedelta(days=i), rooms=rooms,
                                td_liable=line.get("line_status") != "NO_SHOW")
        total += sum(int(night.get(c) or 0) for c in comps)
    return total


def fees(ctx: Ctx, line: dict, types: tuple[str, ...]) -> tuple[int | None, bool]:
    """(amount, inferred) of the given charge types on a line."""
    listed = data.charge_sum(line, types)
    if listed is not None:
        return listed, False
    return infer(ctx, line, types), True


# ─── Booking: reported base vs gross minus excluded charges ───


def _booking(ctx: Ctx, line: dict) -> FindingDraft | None:
    reported, gross = line.get("commissionable_minor"), line.get("gross_minor")
    if reported is None or not gross or (line.get("commission_minor") or 0) <= 0:
        return None
    terms = data.terms_on(ctx, "BOOKING", line.get("check_in"))
    cfg = data.config_on(ctx, "BOOKING", line.get("check_in"))
    listed = {str(x).upper() for x in (terms or {}).get("excluded_charge_types") or []}
    excluded = tuple(t for t in MF_TD if t in listed)
    configured = tuple(t for t in excluded if cfg.get(CHARGE_FLAG[t]))
    if not configured:
        return None  # not configured: commission on the fees is due (L1b is the hotel-level fix)
    exc, inferred = fees(ctx, line, configured)
    if exc is None:
        return None
    expected = int(gross) - exc
    excess = int(reported) - expected
    if excess <= tol(expected):
        return None
    pct = data.line_pct(ctx, line) or Decimal(0)
    # L1/L2 overlap (review money-correctness-l1-l2-overlap): when the rate
    # is also above contract, L2 claims (effective - contract) x the WHOLE
    # reported base, excess included. L1 then claims the excess at the
    # contract percent only, so L1 + L2 = commission - contract% x
    # expected base exactly, never (effective - contract) x excess twice.
    contract = l2.expected_pct(ctx, line)
    capped = contract is not None and pct - contract > l2.THRESHOLD_PP
    if capped:
        pct = contract
    gross = pct_amount(excess, pct)
    if gross <= 0:
        return None
    cred = data.credited(ctx, "BOOKING", line.get("ota_res_id_norm"), line["currency"])
    amount = gross - cred   # net_once() nets the booking's credit once over its lines
    deadline, basis = dispute_deadline(ctx.hotel, terms, check_out=line.get("check_out"), doc_date=line.get("doc_date"))
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=line["currency"], credited_minor=cred,
        base_confidence=Decimal("0.7") if inferred else Decimal("0.9"), ota_code="BOOKING",
        res_norm=line.get("ota_res_id_norm"), doc=line["doc_no"], group=f"doc:{line['doc_no']}",
        match_score=Decimal(1),
        calc=[
            step("Excluded charges", "Σ charges[" + ", ".join(configured) + "]",
                 {"source": "uae_tax" if inferred else "price_breakdown"}, exc),
            step("Expected base", "gross - excluded charges", {"gross_minor": gross, "excluded_minor": exc}, expected),
            step("Excess base", "reported_base - expected_base",
                 {"reported_minor": reported, "tol_minor": tol(expected)}, excess),
            step("Commission on the excess",
                 "contract pct x excess - already credited (L2 claims the rate gap)" if capped
                 else "pct x excess - already credited", {"pct": pct}, amount),
        ],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", line["id"]),
                  {"kind": "quote", "label": "Contract clause", "value": "GDT v2601 2.3.2(ii)",
                   "ref": {"contract_id": (terms or {}).get("id")}}],
        deadline_at=deadline, reservation_id=line.get("reservation_id"), statement_line_id=line["id"],
        note=basis, gross_minor=gross,
    )


# ─── Agoda: commission on taxes inside the RSR (STRUCTURAL) ───


def _agoda(ctx: Ctx, line: dict) -> FindingDraft | None:
    if not line.get("commissionable_minor") or (line.get("commission_minor") or 0) <= 0:
        return None
    terms = data.terms_on(ctx, "AGODA", line.get("check_in"))
    outside = {str(x).upper() for x in (terms or {}).get("excluded_charge_types") or []}
    types = tuple(t for t in AGODA_TAXES if t not in outside)
    taxes, inferred = fees(ctx, line, types)
    if not taxes or taxes <= 0:
        return None
    pct = data.line_pct(ctx, line) or Decimal(0)
    amount = pct_amount(taxes, pct)
    if amount <= tol(line["commission_minor"]):
        return None
    return FindingDraft(
        detector=DETECTOR, klass="STRUCTURAL", amount_minor=amount, currency=line["currency"],
        base_confidence=Decimal("0.7") if inferred else Decimal("0.9"), ota_code="AGODA",
        res_norm=line.get("ota_res_id_norm"), doc=line["doc_no"], group=f"month:{month_key(line.get('check_in'))}",
        signal=False,
        calc=[step("Taxes inside the RSR", "Σ " + " + ".join(types), {"source": "uae_tax" if inferred else "charges"},
                   taxes),
              step("Commission on taxes", "pct x taxes", {"pct": pct}, amount)],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", line["id"])],
        reservation_id=line.get("reservation_id"), statement_line_id=line["id"],
    )


# ─── L1b: fees not configured as charges (hotel level, per month) ───

L1B_DAYS = 90


def _l1b(ctx: Ctx) -> FindingDraft | None:
    terms = data.terms_on(ctx, "BOOKING", ctx.today)
    cfg = data.config_on(ctx, "BOOKING", ctx.today)
    excluded = {str(x).upper() for x in (terms or {}).get("excluded_charge_types") or []}
    unconfigured = tuple(t for t in MF_TD if t in excluded and cfg.get(CHARGE_FLAG[t]) is False)
    if not unconfigured:
        return None
    since = ctx.today - timedelta(days=L1B_DAYS)
    lines = data.statement_lines(ctx, Scope(kind="nightly", since=since, until=ctx.today), ota="BOOKING")
    total_fees, commission, n, inferred_any = 0, 0, 0, False
    for line in lines:
        if line.get("check_in") is None or line["check_in"] < since or line["currency"] != ctx.currency:
            continue
        if (line.get("commission_minor") or 0) <= 0:
            continue
        f, inferred = fees(ctx, line, unconfigured)
        if not f:
            continue
        pct = data.line_pct(ctx, line) or Decimal(str(terms["base_commission_pct"]))
        total_fees += f
        commission += pct_amount(f, pct)
        n += 1
        inferred_any = inferred_any or inferred
    monthly = round(Decimal(commission) / 3)
    if n == 0 or monthly <= tol(0):
        return None
    month = month_key(ctx.today)
    return FindingDraft(
        detector=DETECTOR_B, klass="PREVENTABLE", amount_minor=int(monthly), currency=ctx.currency,
        base_confidence=Decimal("0.85"), ota_code="BOOKING", res_norm=None, doc=month, group=f"month:{month}",
        calc=[step("Fees inside the commission base", f"Σ {' + '.join(unconfigured)} over {L1B_DAYS} days",
                   {"lines": n, "source": "mixed" if inferred_any else "price_breakdown"}, total_fees),
              step("Commission on those fees", "Σ pct x fees", {}, commission),
              step("Per month", "commission / 3", {}, int(monthly))],
        evidence=[{"kind": "link", "label": "Configure the fees as charges in the Booking.com extranet",
                   "ref": {"channel_config": {"ota_code": "BOOKING",
                                              "configured_from": str(cfg.get("configured_from"))}}}],
        note="configure_charges",
    )


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    booking_seen = False
    for line in data.statement_lines(ctx, scope):
        ota = line["ota_code"]
        if ota == "BOOKING":
            booking_seen = True
            d = _booking(ctx, line)
        elif ota == "AGODA":
            d = _agoda(ctx, line)
        else:
            d = None
        if d is not None:
            out.append(d)
    if scope.batch_id is None or booking_seen:
        b = _l1b(ctx)
        if b is not None:
            out.append(b)
    return net_once(out)
