"""L13: chargebacks and disputes deducted from payouts, aged in buckets
0-30 / 31-60 / 61-90 / 90+ days (02 spec 3.3). Amount: the disputed amount
net of any adjustment that reversed it for the same booking. MONEY_LOST
0.7; urgency (signal severity) rises with age. No deadline.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, month_key, step, tol

DETECTOR = "L13"
BASE = Decimal("0.7")
BUCKETS = ((30, "0-30", 2), (60, "31-60", 3), (90, "61-90", 3))


def bucket(age: int) -> tuple[str, int]:
    for limit, label, sev in BUCKETS:
        if age <= limit:
            return label, sev
    return "90+", 4


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    ctx.cur.execute(
        """
        SELECT pl.ota_code, pl.ota_res_id_norm, pl.currency,
               SUM(pl.partner_payout_minor) FILTER (WHERE pl.line_kind = 'CHARGEBACK') AS cb,
               SUM(pl.partner_payout_minor) FILTER (WHERE pl.line_kind = 'ADJUSTMENT') AS adj,
               MIN(COALESCE(pl.event_date, p.payout_date)) FILTER (WHERE pl.line_kind = 'CHARGEBACK') AS since,
               MIN(pl.reservation_id) AS reservation_id,
               array_agg(pl.id ORDER BY pl.id) FILTER (WHERE pl.line_kind = 'CHARGEBACK') AS ids,
               MIN(p.payout_ref) FILTER (WHERE pl.line_kind = 'CHARGEBACK') AS payout_ref
          FROM pfg_ota_payout_lines pl JOIN pfg_ota_payouts p ON p.id = pl.payout_id
         WHERE pl.hotel_slug = %(h)s AND pl.line_kind IN ('CHARGEBACK', 'ADJUSTMENT')
           AND (%(ota)s::text IS NULL OR pl.ota_code = %(ota)s)
         GROUP BY pl.ota_code, pl.ota_res_id_norm, pl.currency
        HAVING COUNT(*) FILTER (WHERE pl.line_kind = 'CHARGEBACK') > 0
        """,
        {"h": ctx.slug, "ota": scope.ota_code},
    )
    out: list[FindingDraft] = []
    for r in ctx.cur.fetchall():
        disputed = -int(r["cb"] or 0)           # chargebacks come off the payout as negatives
        reversed_ = int(r["adj"] or 0) if (r["adj"] or 0) > 0 else 0
        amount = disputed - reversed_
        if disputed <= 0 or amount <= tol(disputed):
            continue
        age = (ctx.today - r["since"]).days if r["since"] else 0
        label, sev = bucket(age)
        out.append(FindingDraft(
            detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=r["currency"], base_confidence=BASE,
            ota_code=r["ota_code"], res_norm=r["ota_res_id_norm"] or f"payout:{r['payout_ref']}",
            doc=f"CB:{r['payout_ref']}", group=f"month:{month_key(r['since'])}", severity=sev,
            calc=[step("Disputed", "Σ chargeback lines", {"lines": len(r["ids"] or [])}, disputed),
                  step("Outstanding", "disputed - reversals", {"reversed_minor": reversed_, "age_days": age,
                                                              "bucket": label}, amount)],
            evidence=[{"kind": "row", "label": "Chargeback line", "ref": {"table": "pfg_ota_payout_lines", "id": i}}
                      for i in r["ids"] or []],
            reservation_id=r["reservation_id"], note=f"age:{label}",
        ))
    return out
