"""L10: the money that arrived is less than the money owed (02 spec 3.3).

Three checks, shortfalls only (an overpayment is not leakage):
- line:   partner_payout < total_paid - commission - Σ charges (in payout)
- payout: payout amount < Σ of its lines' partner payouts
- bank:   credit < payout - expected bank fee, the credit matched by
          reference (import linked it) or else the one credit in the same
          currency within ±3 days at 95-100% of the payout.
Each beyond tol. RECOVERABLE 0.85, no deadline (aging). A foreign-currency
credit is L11's.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, month_key, row_ev, step, tol
from clefs.modules.profit_guard.detectors.data import window

DETECTOR = "L10"
BASE = Decimal("0.85")
BANK_DAYS = 3
NEAR = Decimal("0.95")


def _payouts(ctx: Ctx, scope: Scope) -> list[dict]:
    since, until = window(ctx, scope)
    ctx.cur.execute(
        """
        SELECT p.* FROM pfg_ota_payouts p
         WHERE p.hotel_slug = %(h)s AND (%(ota)s::text IS NULL OR p.ota_code = %(ota)s)
           AND (CASE WHEN %(batch)s::bigint IS NOT NULL
                     THEN p.batch_id = %(batch)s OR EXISTS (SELECT 1 FROM pfg_bank_credits b
                          WHERE b.matched_payout_id = p.id AND b.batch_id = %(batch)s)
                     ELSE COALESCE(p.payout_date, p.created_at::date) BETWEEN %(since)s AND %(until)s END)
         ORDER BY p.id
        """,
        {"h": ctx.slug, "ota": scope.ota_code, "batch": scope.batch_id, "since": since, "until": until},
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def _draft(ctx: Ctx, p: dict, *, res_norm: str | None, doc: str, amount: int, calc: list[dict], evidence: list[dict],
           missing: list[str] | None = None, reservation_id: int | None = None) -> FindingDraft:
    return FindingDraft(
        detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=p["currency"], base_confidence=BASE,
        ota_code=p["ota_code"], res_norm=res_norm, doc=doc,
        group=f"month:{month_key(p.get('payout_date') or ctx.today)}", missing=missing or [], calc=calc,
        evidence=[{"kind": "row", "label": "Payout", "ref": {"table": "pfg_ota_payouts", "id": p["id"]}}] + evidence,
        reservation_id=reservation_id, note="payout_shortfall",
    )


def _lines(ctx: Ctx, p: dict) -> list[FindingDraft]:
    ctx.cur.execute("SELECT * FROM pfg_ota_payout_lines WHERE payout_id = %s AND line_kind = 'BOOKING' ORDER BY id",
                    (p["id"],))
    out = []
    for ln in ctx.cur.fetchall():
        paid, comm = ln["total_amount_paid_minor"], ln["commission_minor"]
        if paid is None or comm is None:
            continue
        charges = ln["charges_minor"]
        expected = int(paid) - int(comm) - int(charges or 0)
        short = expected - int(ln["partner_payout_minor"])
        if short <= tol(expected):
            continue
        out.append(_draft(
            ctx, p, res_norm=ln["ota_res_id_norm"], doc=p["payout_ref"], amount=short,
            missing=[] if charges is not None else ["charges"], reservation_id=ln["reservation_id"],
            calc=[step("Expected payout", "total_paid - commission - Σ charges in payout",
                       {"total_paid_minor": paid, "commission_minor": comm, "charges_minor": charges}, expected),
                  step("Shortfall", "expected - partner_payout", {"partner_payout_minor": ln["partner_payout_minor"]},
                       short)],
            evidence=[row_ev("Payout line", "pfg_ota_payout_lines", ln["id"])],
        ))
    return out


def _total(ctx: Ctx, p: dict) -> FindingDraft | None:
    ctx.cur.execute("SELECT COALESCE(SUM(partner_payout_minor), 0) AS s, COUNT(*) AS n FROM pfg_ota_payout_lines "
                    "WHERE payout_id = %s AND currency = %s", (p["id"], p["currency"]))
    r = ctx.cur.fetchone()
    if not r["n"]:
        return None
    short = int(r["s"]) - int(p["amount_minor"])
    if short <= tol(int(r["s"])):
        return None
    return _draft(ctx, p, res_norm=None, doc=f"{p['payout_ref']}:total", amount=short,
                  calc=[step("Σ lines", "Σ partner_payout", {"lines": r["n"]}, int(r["s"])),
                        step("Shortfall", "Σ lines - payout amount", {"payout_minor": p["amount_minor"]}, short)],
                  evidence=[])


def _credits(ctx: Ctx, p: dict) -> tuple[list[dict], str]:
    """Credits for a payout: the ones import linked, else the single near
    match in the same currency within ±3 days."""
    ctx.cur.execute("SELECT * FROM pfg_bank_credits WHERE hotel_slug = %s AND matched_payout_id = %s ORDER BY id",
                    (ctx.slug, p["id"]))
    linked = [dict(r) for r in ctx.cur.fetchall()]
    if linked:
        return linked, "reference"
    if p.get("payout_date") is None or p["amount_minor"] <= 0:
        return [], "none"
    ctx.cur.execute(
        """
        SELECT * FROM pfg_bank_credits WHERE hotel_slug = %s AND matched_payout_id IS NULL AND currency = %s
           AND value_date BETWEEN %s AND %s AND amount_minor >= %s AND amount_minor < %s ORDER BY id
        """,
        (ctx.slug, p["currency"], p["payout_date"] - timedelta(days=BANK_DAYS),
         p["payout_date"] + timedelta(days=BANK_DAYS),
         int(Decimal(p["amount_minor"]) * NEAR), p["amount_minor"]),
    )
    near = [dict(r) for r in ctx.cur.fetchall()]
    return (near, "amount") if len(near) == 1 else ([], "none")


def _bank(ctx: Ctx, p: dict, fee: int) -> FindingDraft | None:
    credits, how = _credits(ctx, p)
    same = [c for c in credits if c["currency"] == p["currency"]]
    if not same or len(same) != len(credits):
        return None  # nothing arrived yet, or a conversion (L11)
    got = sum(int(c["amount_minor"]) for c in same)
    expected = int(p["amount_minor"]) - fee
    short = expected - got
    if short <= tol(expected):
        return None
    return _draft(ctx, p, res_norm="bank", doc=p["payout_ref"], amount=short,
                  calc=[step("Expected credit", "payout - expected bank fee",
                             {"payout_minor": p["amount_minor"], "bank_fee_minor": fee}, expected),
                        step("Shortfall", "expected - credited", {"credited_minor": got, "matched_by": how}, short)],
                  evidence=[{"kind": "row", "label": "Bank credit", "ref": {"table": "pfg_bank_credits", "id": c["id"]}}
                            for c in same])


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    fee = int((ctx.cache.get("tuning", {}).get(DETECTOR) or {}).get("params", {}).get("bank_fee_minor") or 0)
    out: list[FindingDraft] = []
    for p in _payouts(ctx, scope):
        out += _lines(ctx, p)
        for d in (_total(ctx, p), _bank(ctx, p, fee)):
            if d is not None:
                out.append(d)
    return out
