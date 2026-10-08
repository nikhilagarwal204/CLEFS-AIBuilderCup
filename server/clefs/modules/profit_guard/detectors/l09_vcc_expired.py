"""L9: a virtual card passed its charge deadline with money still on it
(02 spec 3.3). Amount: the balance. MONEY_LOST at confidence 0.9 that the
money is gone; recoverable only through OTA support (confidence 0.3,
carried in the trace for the dispute packet). No deadline.
"""

from __future__ import annotations

from decimal import Decimal

from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, month_key, step
from clefs.modules.profit_guard.detectors.l08_vcc_uncharged import cards

DETECTOR = "L9"
BASE = Decimal("0.9")
RECOVERY_CONFIDENCE = Decimal("0.3")


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    for c in cards(ctx, scope, "status NOT IN ('CANCELLED', 'FULLY_CHARGED') AND charge_deadline < %(today)s"):
        out.append(FindingDraft(
            detector=DETECTOR, klass="RECOVERABLE", amount_minor=int(c["current_balance_minor"]),
            currency=c["currency"],
            base_confidence=BASE, ota_code=c["ota_code"], res_norm=c.get("ota_res_id_norm") or c["vcc_id"],
            doc=f"VCC:{c['vcc_id']}", group=f"month:{month_key(c['charge_deadline'])}",
            calc=[step("Balance left after the deadline", "current balance",
                       {"charge_deadline": c["charge_deadline"], "status": c["status"],
                        "recovery_confidence": RECOVERY_CONFIDENCE, "recovery_route": "OTA support"},
                       int(c["current_balance_minor"]))],
            evidence=[{"kind": "row", "label": "Virtual card", "ref": {"table": "pfg_virtual_cards", "id": c["id"]}}],
            reservation_id=c.get("reservation_id"), note="ask_ota_support",
        ))
    return out
