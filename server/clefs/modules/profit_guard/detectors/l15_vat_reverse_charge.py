"""L15: reverse-charge VAT to self-account on commission invoiced by a
foreign OTA entity (02 spec 3.3). Monthly: Σ commission (credit notes net
off) from an entity whose contract says entity_country ≠ AE; amount 5% of
it. COMPLIANCE, kept out of every money total. Deadline: the VAT return,
the 28th day after the end of the calendar quarter. The entity country
comes only from a confirmed contract: default terms name no entity, so
nothing is guessed.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from clefs.modules.profit_guard import contracts
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, end_of_local_day, month_key, pct_amount, step, tol,
)
from clefs.modules.profit_guard.detectors.data import window

DETECTOR = "L15"
BASE = Decimal("0.9")
VAT_PCT = Decimal("5")
HOME = "AE"


def vat_return_due(d: date) -> date:
    """28 days after the end of d's calendar quarter."""
    q_end_month = ((d.month - 1) // 3 + 1) * 3
    nxt = date(d.year + 1, 1, 1) if q_end_month == 12 else date(d.year, q_end_month + 1, 1)
    return nxt - timedelta(days=1) + timedelta(days=28)


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    if (getattr(ctx.hotel, "country", None) or HOME).upper() != HOME:
        return []
    since, until = window(ctx, scope)
    ctx.cur.execute(
        """
        SELECT s.ota_code, date_trunc('month', COALESCE(s.doc_date, s.period_start))::date AS month, s.currency,
               SUM(l.commission_minor) AS commission, array_agg(DISTINCT s.doc_no) AS docs
          FROM pfg_ota_statements s JOIN pfg_ota_statement_lines l ON l.statement_id = s.id
         WHERE s.hotel_slug = %(h)s AND COALESCE(s.doc_date, s.period_start) IS NOT NULL
           AND (%(ota)s::text IS NULL OR s.ota_code = %(ota)s)
           AND (CASE WHEN %(batch)s::bigint IS NOT NULL
                     THEN date_trunc('month', COALESCE(s.doc_date, s.period_start)) IN (
                          SELECT date_trunc('month', COALESCE(doc_date, period_start)) FROM pfg_ota_statements
                           WHERE hotel_slug = %(h)s AND batch_id = %(batch)s)
                     ELSE COALESCE(s.doc_date, s.period_start) BETWEEN %(since)s AND %(until)s END)
         GROUP BY 1, 2, 3
        """,
        {"h": ctx.slug, "ota": scope.ota_code, "batch": scope.batch_id, "since": since, "until": until},
    )
    out: list[FindingDraft] = []
    for r in ctx.cur.fetchall():
        terms = contracts.contract_on(ctx.cur, ctx.slug, r["ota_code"], r["month"])
        country = ((terms or {}).get("entity_country") or "").strip().upper()
        commission = int(r["commission"] or 0)
        if not country or country == HOME or commission <= 0:
            continue
        amount = pct_amount(commission, VAT_PCT)
        if amount <= tol(0):
            continue
        month = month_key(r["month"])
        out.append(FindingDraft(
            detector=DETECTOR, klass="COMPLIANCE", amount_minor=amount, currency=r["currency"], base_confidence=BASE,
            ota_code=r["ota_code"], res_norm=None, doc=month, group=f"month:{month}",
            calc=[step("Commission invoiced", "Σ commission (credit notes net)",
                       {"entity": (terms or {}).get("entity_name"), "entity_country": country,
                        "documents": ",".join(sorted(r["docs"]))}, commission),
                  step("Reverse-charge VAT", "5% x commission", {"vat_pct": VAT_PCT}, amount)],
            evidence=[{"kind": "row", "label": "Contract entity", "ref": {"table": "pfg_ota_contracts",
                                                                          "id": (terms or {}).get("id")}}],
            deadline_at=end_of_local_day(ctx.hotel, vat_return_due(r["month"])), note="self_account_vat",
        ))
    return out
