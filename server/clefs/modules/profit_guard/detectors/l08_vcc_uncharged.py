"""L8: money waiting on a virtual card (02 spec 3.3).

Balance > 0, status AVAILABLE / FUNDED / PARTIALLY_CHARGED, activation
reached and charge_deadline >= today (the deadline is already
min(card expiry, contract rule), so Agoda's UPC 24 h rule is inside it).
Amount: the current balance. RECOVERABLE (MONEY_AVAILABLE) 0.95. Urgency
tiers at 30 / 14 / 7 / 1 days set the signal severity; at 1 day or less
the card gets its own signal instead of riding in the monthly group.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, end_of_local_day, month_key, step

DETECTOR = "L8"
BASE = Decimal("0.95")
OPEN = ("AVAILABLE", "FUNDED", "PARTIALLY_CHARGED")
TIERS = ((1, 4), (7, 3), (14, 3), (30, 2))


def severity(days: int) -> int:
    for limit, sev in TIERS:
        if days <= limit:
            return sev
    return 1


def cards(ctx: Ctx, scope: Scope, where: str) -> list[dict]:
    ctx.cur.execute(
        f"""
        SELECT * FROM pfg_virtual_cards
         WHERE hotel_slug = %(h)s AND current_balance_minor > 0 AND {where}
           AND (%(ota)s::text IS NULL OR ota_code = %(ota)s)
           AND (%(batch)s::bigint IS NULL OR last_seen_batch_id = %(batch)s)
         ORDER BY charge_deadline, id
        """,
        {"h": ctx.slug, "today": ctx.today, "ota": scope.ota_code, "batch": scope.batch_id, "open": list(OPEN)},
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    where = ("status = ANY(%(open)s) AND (activation_date IS NULL OR activation_date <= %(today)s) "
             "AND charge_deadline >= %(today)s")
    for c in cards(ctx, scope, where):
        days = (c["charge_deadline"] - ctx.today).days
        out.append(FindingDraft(
            detector=DETECTOR, klass="RECOVERABLE", amount_minor=int(c["current_balance_minor"]),
            currency=c["currency"],
            base_confidence=BASE, ota_code=c["ota_code"], res_norm=c.get("ota_res_id_norm") or c["vcc_id"],
            doc=f"VCC:{c['vcc_id']}", group=f"month:{month_key(c['charge_deadline'])}",
            calc=[step("Balance to charge", "current balance",
                       {"status": c["status"], "charge_deadline": c["charge_deadline"],
                        "basis": c.get("deadline_basis"), "days_left": days}, int(c["current_balance_minor"]))],
            evidence=[{"kind": "row", "label": "Virtual card", "ref": {"table": "pfg_virtual_cards", "id": c["id"]}}],
            deadline_at=end_of_local_day(ctx.hotel, c["charge_deadline"]), reservation_id=c.get("reservation_id"),
            urgency_days=days, severity=severity(days), note="charge_card",
        ))
    return out
