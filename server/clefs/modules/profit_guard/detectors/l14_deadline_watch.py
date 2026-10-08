"""L14: act before the OTA's window closes (02 spec 3.3).

From PMS data alone, before any statement: a Booking-sourced NO_SHOW or
early departure the OTA has not been told about (no statement line for
the booking yet). Deadline: check-out 00:00 hotel-local + 48 h (the
conservative reading of Booking's marking window); Expedia Hotel Collect:
the 4th of the following month at 12:00 local. Amount: the expected
commission at risk (the whole stay for a no-show less pct x the fee
collected; the unstayed nights for an early departure). MONEY_AVAILABLE
0.85. Alerts at T-24h and T-6h: at 1 day or less the booking gets its own
signal (severity 4 in the last 6 hours). Once the deadline has passed the
booking becomes an L3 at confidence x 0.5 and this finding closes.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.core import stays
from clefs.core.errors import ClefsError
from clefs.core.tax import StayLine, compose_stay
from clefs.modules.profit_guard import contracts
from clefs.modules.profit_guard.detectors import l03_no_show_commission as l3
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, local_instant, month_key, net_once, next_month_day, pct_amount, step, tol,
)
from clefs.modules.profit_guard.models import normalize_ota

DETECTOR = "L14"
BASE = Decimal("0.85")
AFTER_FACTOR = Decimal("0.5")
BOOKING_HOURS = 48
LOOKBACK_DAYS = 45


def deadline_for(hotel, ota: str, check_out, collect_model: str | None):
    if ota == "BOOKING":
        return local_instant(hotel, check_out) + timedelta(hours=BOOKING_HOURS)
    if ota == "EXPEDIA" and collect_model == "HOTEL_COLLECT":
        return local_instant(hotel, next_month_day(check_out, 4), "12:00")
    return None


def _candidates(ctx: Ctx) -> list[dict]:
    ctx.cur.execute(
        """
        SELECT r.* FROM reservations r
         WHERE r.hotel_slug = %s AND r.check_out BETWEEN %s AND %s
           AND (r.status = 'NO_SHOW' OR (r.actual_check_out IS NOT NULL AND r.actual_check_out < r.check_out
                                          AND r.status IN ('CHECKED_OUT', 'IN_HOUSE')))
           AND NOT EXISTS (SELECT 1 FROM pfg_ota_statement_lines l
                            WHERE l.hotel_slug = r.hotel_slug
                              AND (l.reservation_id = r.id OR (r.ota_res_id_norm IS NOT NULL
                                   AND l.ota_res_id_norm = r.ota_res_id_norm)))
         ORDER BY r.check_out, r.id
        """,
        (ctx.slug, ctx.today - timedelta(days=LOOKBACK_DAYS), ctx.today + timedelta(days=60)),
    )
    return [dict(r) for r in ctx.cur.fetchall()]


def _unstayed_breakdown(ctx: Ctx, r: dict):
    ctx.cur.execute(
        "SELECT stay_date, room_type_code, rooms, rate_minor FROM reservation_nights "
        "WHERE reservation_id = %s AND stay_date >= %s ORDER BY stay_date, room_seq",
        (r["id"], r["actual_check_out"]),
    )
    lines = [StayLine(stay_date=n["stay_date"], room_type_code=n["room_type_code"], rooms=n["rooms"],
                      rate_minor=n["rate_minor"]) for n in ctx.cur.fetchall()]
    return compose_stay(ctx.cur, ctx.slug, lines=lines) if lines else None


def at_risk(ctx: Ctx, r: dict, ota: str) -> tuple[int, dict, str, int] | None:
    """(amount, expected commission dict, reason, fee) or None."""
    try:
        if r["status"] == "NO_SHOW":
            breakdown, reason = stays.reservation_breakdown(ctx.cur, r["id"]), "no-show"
        else:
            breakdown, reason = _unstayed_breakdown(ctx, r), "early departure"
    except ClefsError:
        return None
    if breakdown is None:
        return None
    exp = contracts.expected_commission(ctx.cur, ctx.slug, ota_code=ota, breakdown=breakdown, on=r["check_in"],
                                        booker_country=r.get("guest_country"), rate_plan=r.get("rate_plan_code"))
    if exp is None or exp["commission_minor"] <= 0:
        return None
    fee = int((r.get("no_show_fee_minor") if r["status"] == "NO_SHOW" else r.get("cancel_fee_minor")) or 0)
    amount = exp["commission_minor"] - pct_amount(fee, Decimal(str(exp["pct"])))
    return (amount, exp, reason, fee) if amount > tol(exp["commission_minor"]) else None


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    out: list[FindingDraft] = []
    for r in _candidates(ctx):
        ota = normalize_ota(r.get("channel_code"))
        if ota not in ("BOOKING", "EXPEDIA") or (scope.ota_code and ota != scope.ota_code):
            continue
        deadline = deadline_for(ctx.hotel, ota, r["check_out"], r.get("collect_model"))
        if deadline is None:
            continue
        risk = at_risk(ctx, r, ota)
        if risk is None:
            continue
        amount, exp, reason, fee = risk
        res_norm = r.get("ota_res_id_norm") or f"pms:{r['pms_res_id']}"
        month = month_key(r["check_out"])
        if ctx.now >= deadline:
            # The window closed: an L3 claim at half confidence (the marking
            # route is gone, a dispute through support remains).
            d = l3.build(ctx, ota=ota, res_norm=res_norm, check_out=r["check_out"], commission=exp["commission_minor"],
                         pct=Decimal(str(exp["pct"])), fee=fee, currency=exp["currency"],
                         reason=f"PMS {reason}, not reported",
                         match_score=Decimal(1), missing=["ota_statement"], group=f"month:{month}",
                         reservation_id=r["id"], line=None, terms=None, factor=AFTER_FACTOR, deadline=deadline)
            d.calc = exp["calc"] + d.calc
            out.append(d)
            continue
        hours = (deadline - ctx.now).total_seconds() / 3600
        days = int(-(-hours // 24))
        out.append(FindingDraft(
            detector=DETECTOR, klass="RECOVERABLE", amount_minor=amount, currency=exp["currency"], base_confidence=BASE,
            ota_code=ota, res_norm=res_norm, doc=month, group=f"month:{month}",
            calc=exp["calc"] + [step("Commission at risk", "expected commission - pct x fee collected",
                                     {"reason": reason, "fee_minor": fee, "hours_left": round(hours, 1)}, amount)],
            evidence=[{"kind": "row", "label": f"PMS {reason}", "ref": {"table": "reservations", "id": r["id"]}}],
            deadline_at=deadline, reservation_id=r["id"], urgency_days=days,
            severity=4 if hours <= 6 else 3 if hours <= 24 else 2,
            note="mark_no_show" if reason == "no-show" else "report_early_departure",
        ))
    return net_once(out)   # the L3 hand-overs net their fee and credit once (and drop below tol)
