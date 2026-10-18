"""L12: the guest was refunded but the commission was not reduced pro rata
(02 spec 3.3). A refund is a payout line of kind REFUND or a statement
line with a negative gross. Per booking: expected reduction = pct x
refunded; actual = the commission already credited back. Amount:
expected - actual beyond tol. RECOVERABLE 0.8, dispute window.

One refund often prints twice: as a REFUND line in the payout file and as
a negative-gross line on the reservation statement. So, per booking,
refunded is the LARGER of the payout-refund total and the statement
refund total, never their sum (the same rule as repo.booking_credit),
read across every document of the booking whatever batch triggered the
run (a split import must not claim, or fingerprint, one refund twice).
The credit side is repo.booking_credit(payouts=True), the ledger's own
measure, so the baseline netted here is exactly the one the ledger later
counts recovery above (review money-correctness-l12-refund-double-count).
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, dispute_deadline, pct_amount, step, tol

DETECTOR = "L12"
BASE = Decimal("0.8")


REFUNDS_SQL = """
        SELECT 'payout' AS src, pl.id, pl.ota_code, pl.ota_res_id_norm, pl.currency, pl.reservation_id,
               ABS(COALESCE(pl.total_amount_paid_minor, pl.commissionable_minor, pl.partner_payout_minor)) AS refunded,
               GREATEST(0, -COALESCE(pl.commission_minor, 0)) AS reduced, p.payout_ref AS doc,
               p.payout_date AS doc_date,
               pl.check_out
          FROM pfg_ota_payout_lines pl JOIN pfg_ota_payouts p ON p.id = pl.payout_id
         WHERE pl.hotel_slug = %(h)s AND pl.line_kind = 'REFUND'
           AND {payout_scope}
        UNION ALL
        SELECT 'statement', l.id, l.ota_code, l.ota_res_id_norm, l.currency, l.reservation_id, ABS(l.gross_minor),
               0, s.doc_no, s.doc_date, l.check_out
          FROM pfg_ota_statement_lines l JOIN pfg_ota_statements s ON s.id = l.statement_id
         WHERE l.hotel_slug = %(h)s AND l.gross_minor < 0 AND s.doc_kind <> 'CREDIT_NOTE'
           AND {stmt_scope}
        """
IN_SCOPE = ("""(CASE WHEN %(batch)s::bigint IS NOT NULL THEN pl.batch_id = %(batch)s
                ELSE COALESCE(pl.event_date, p.payout_date) BETWEEN %(since)s AND %(until)s END)""",
            """(CASE WHEN %(batch)s::bigint IS NOT NULL THEN l.batch_id = %(batch)s
                ELSE s.doc_date BETWEEN %(since)s AND %(until)s END)""")
OF_BOOKING = ("pl.ota_code = %(o)s AND pl.ota_res_id_norm = %(r)s AND pl.currency = %(c)s",
              "l.ota_code = %(o)s AND l.ota_res_id_norm = %(r)s AND l.currency = %(c)s")


def _refunds(ctx: Ctx, scope: Scope) -> list[dict]:
    """The refund lines the run's scope covers (they pick the bookings)."""
    since, until = data.window(ctx, scope)
    ctx.cur.execute(REFUNDS_SQL.format(payout_scope=IN_SCOPE[0], stmt_scope=IN_SCOPE[1]),
                    {"h": ctx.slug, "batch": scope.batch_id, "since": since, "until": until})
    return [dict(r) for r in ctx.cur.fetchall() if not scope.ota_code or r["ota_code"] == scope.ota_code]


def _booking_refunds(ctx: Ctx, ota: str, res_norm: str, ccy: str) -> list[dict]:
    """Every refund line of one booking, in any document, oldest first:
    the fingerprint's doc is the first one's, so it is stable across runs."""
    ctx.cur.execute(REFUNDS_SQL.format(payout_scope=OF_BOOKING[0], stmt_scope=OF_BOOKING[1])
                    + " ORDER BY doc_date NULLS LAST, src, id",
                    {"h": ctx.slug, "o": ota, "r": res_norm, "c": ccy})
    return [dict(r) for r in ctx.cur.fetchall() if r["refunded"]]


def refunded_total(rows: list[dict]) -> int:
    """The larger of the payout-refund and statement-refund totals."""
    by_src = {"payout": 0, "statement": 0}
    for r in rows:
        by_src[r["src"]] += int(r["refunded"])
    return max(by_src.values())


def _booking_pct(ctx: Ctx, ota: str, res_norm: str, currency: str) -> Decimal | None:
    """The percent the OTA charged on the original booking."""
    ctx.cur.execute(
        """
        SELECT SUM(commission_minor) AS c, SUM(commissionable_minor) AS b FROM (
            SELECT commission_minor, commissionable_minor FROM pfg_ota_statement_lines
             WHERE hotel_slug = %(h)s AND ota_code = %(o)s AND ota_res_id_norm = %(r)s AND currency = %(c)s
               AND commission_minor > 0 AND commissionable_minor > 0
            UNION ALL
            SELECT commission_minor, commissionable_minor FROM pfg_ota_payout_lines
             WHERE hotel_slug = %(h)s AND ota_code = %(o)s AND ota_res_id_norm = %(r)s AND currency = %(c)s
               AND line_kind = 'BOOKING' AND commission_minor > 0 AND commissionable_minor > 0) x
        """,
        {"h": ctx.slug, "o": ota, "r": res_norm, "c": currency},
    )
    r = ctx.cur.fetchone()
    if r and r["b"]:
        return Decimal(r["c"]) * 100 / Decimal(r["b"])
    return None


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    keys = sorted({(r["ota_code"], r["ota_res_id_norm"], r["currency"]) for r in _refunds(ctx, scope)
                   if r["ota_res_id_norm"] and r["refunded"]})
    out: list[FindingDraft] = []
    for ota, res_norm, ccy in keys:
        rows = _booking_refunds(ctx, ota, res_norm, ccy)
        if not rows:
            continue
        refunded = refunded_total(rows)
        pct = _booking_pct(ctx, ota, res_norm, ccy)
        missing = []
        if pct is None:
            terms = data.terms_on(ctx, ota, rows[0].get("check_out"))
            if terms is None:
                continue
            pct, missing = Decimal(str(terms["base_commission_pct"])), ["booking_pct"]
        expected = pct_amount(refunded, pct)
        # The ledger's measure (larger of statement and payout credits): a
        # REFUND payout line's negative commission is a payout credit.
        actual = data.credited(ctx, ota, res_norm, ccy, payouts=True)
        short = expected - actual
        if short <= tol(expected):
            continue
        first = rows[0]
        terms = data.terms_on(ctx, ota, first.get("check_out"))
        deadline, _ = dispute_deadline(ctx.hotel, terms, check_out=first.get("check_out"),
                                       doc_date=first.get("doc_date"))
        out.append(FindingDraft(
            detector=DETECTOR, klass="RECOVERABLE", amount_minor=short, currency=ccy, base_confidence=BASE,
            credited_minor=actual,
            ota_code=ota, res_norm=res_norm, doc=first["doc"], group=f"doc:{first['doc']}", missing=missing,
            calc=[step("Refunded", "max(Σ payout refund lines, Σ statement refund lines)", {"lines": len(rows)},
                       refunded),
                  step("Expected commission reduction", "pct x refunded", {"pct": pct.quantize(Decimal("0.001"))},
                       expected),
                  step("Not reduced", "expected - commission already credited", {"actual_minor": actual},
                       short)],
            evidence=[{"kind": "row", "label": "Refund line",
                       "ref": {"table": "pfg_ota_payout_lines" if r["src"] == "payout" else "pfg_ota_statement_lines",
                               "id": r["id"]}} for r in rows],
            deadline_at=deadline, reservation_id=first.get("reservation_id"),
            statement_line_id=first["id"] if first["src"] == "statement" else None,
        ))
    return out
