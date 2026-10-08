"""L11: a foreign-currency payout lost value on conversion (02 spec 3.3).

expected = payout amount x reference rate (core.money: the fx_rates table,
else the USD/AED 3.6725 peg; ECB rows for EUR), loss = expected - credited
- expected bank fee. Flag when the loss is more than 0.5% of expected or
more than AED 50 (and beyond tol). PREVENTABLE 0.8 (action: change the
payout currency or the bank). No deadline.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.core import money
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, month_key, rnd, row_ev, step, tol
from clefs.modules.profit_guard.detectors.data import window

DETECTOR = "L11"
BASE = Decimal("0.8")
LOSS_PCT = Decimal("0.005")
LOSS_ABS_MAJOR = Decimal("50")     # AED 50, converted to the hotel currency's minor units


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    since, until = window(ctx, scope)
    ctx.cur.execute(
        """
        SELECT p.id AS payout_id, p.payout_ref, p.ota_code, p.currency AS p_currency, p.amount_minor AS p_amount,
               p.payout_date, b.currency AS b_currency, SUM(b.amount_minor) AS credited,
               MIN(b.value_date) AS value_date,
               array_agg(b.id ORDER BY b.id) AS credit_ids
          FROM pfg_bank_credits b JOIN pfg_ota_payouts p ON p.id = b.matched_payout_id
         WHERE b.hotel_slug = %(h)s AND b.currency <> p.currency AND b.currency = %(ccy)s
           AND (%(ota)s::text IS NULL OR p.ota_code = %(ota)s)
           AND (CASE WHEN %(batch)s::bigint IS NOT NULL THEN b.batch_id = %(batch)s OR p.batch_id = %(batch)s
                     ELSE b.value_date BETWEEN %(since)s AND %(until)s END)
         GROUP BY p.id, b.currency
        """,
        {"h": ctx.slug, "ccy": ctx.currency, "ota": scope.ota_code, "batch": scope.batch_id, "since": since,
         "until": until},
    )
    fee = int((ctx.cache.get("tuning", {}).get(DETECTOR) or {}).get("params", {}).get("bank_fee_minor") or 0)
    abs_floor = money.to_minor(LOSS_ABS_MAJOR, ctx.currency) if ctx.currency == "AED" else None
    out: list[FindingDraft] = []
    for r in ctx.cur.fetchall():
        try:
            rate = money.fx_rate(ctx.cur, r["p_currency"], r["b_currency"], r["value_date"])
        except ClefsError:
            rate = None
        if rate is None:
            continue
        expected = rnd(Decimal(r["p_amount"]) * Decimal(rate))
        loss = expected - int(r["credited"]) - fee
        big = loss > rnd(Decimal(expected) * LOSS_PCT) or (abs_floor is not None and loss > abs_floor)
        if not big or loss <= tol(expected):
            continue
        out.append(FindingDraft(
            detector=DETECTOR, klass="PREVENTABLE", amount_minor=loss, currency=r["b_currency"], base_confidence=BASE,
            ota_code=r["ota_code"], res_norm="fx", doc=r["payout_ref"], group=f"month:{month_key(r['value_date'])}",
            calc=[step("Expected at the reference rate", "payout x ref_rate",
                       {"payout_minor": r["p_amount"], "payout_currency": r["p_currency"], "ref_rate": rate,
                        "on": r["value_date"]}, expected),
                  step("Loss", "expected - credited - expected fee",
                       {"credited_minor": int(r["credited"]), "bank_fee_minor": fee,
                        "threshold": "0.5% or AED 50"}, loss)],
            evidence=[{"kind": "row", "label": "Payout", "ref": {"table": "pfg_ota_payouts", "id": r["payout_id"]}}]
            + [row_ev("Bank credit", "pfg_bank_credits", i) for i in r["credit_ids"]],
            note="change_payout_currency",
        ))
    return out
