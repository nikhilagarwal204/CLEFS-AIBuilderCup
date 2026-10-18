"""L6: stacked OTA discounts deeper than the owner's cap (02 spec 3.3).

depth = 1 - sell / public, where public is the BAR for the date and room
(Demand's price_guardrails); the stacked program depth is 1 - Π(1 - dᵢ)
over the DISCOUNT programs the line names. The deeper of the two is
compared with pfg_owner_policies.max_total_discount_pct (default 25):
exactly the cap does not flag. Amount: (depth - cap) x public x nights.
PREVENTABLE 0.8 (action: restrict stacking), no deadline. Without a BAR
the public price is sell / (1 - stacked depth) and the BAR counts as a
missing input. Legitimate Genius + Mobile (10% + 10% = 19%) stays under.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.core import money
from clefs.modules.demand import api as demand_api
from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors import data
from clefs.modules.profit_guard.detectors.base import (
    Ctx, FindingDraft, Scope, month_key, rnd, row_ev, step, tol,
)

DETECTOR = "L6"
BASE = Decimal("0.8")
Q = Decimal("0.0001")


def _stacked(ctx: Ctx, line: dict) -> tuple[Decimal, list[dict]]:
    named = {str(p).upper() for p in line.get("programs") or []}
    if not named or line.get("check_in") is None:
        return Decimal(0), []
    progs = [p for p in repo.active_programs(ctx.cur, ctx.slug, line["ota_code"], line["check_in"],
                                             booker_country=line.get("booker_country"), rate_plan=line.get("rate_plan"))
             if p["kind"] == "DISCOUNT" and p["pct"] is not None and p["program"] in named]
    keep = Decimal(1)
    for p in progs:
        keep *= 1 - Decimal(str(p["pct"])) / 100
    return 1 - keep, progs


def _guardrails(ctx: Ctx, stay_date, room_type) -> dict | None:
    """Demand's view of one night, inside a savepoint: Demand runs in the
    same transaction, and its failure (or its tables missing where Demand
    is switched off) must neither stop Profit Guard nor abort the caller."""
    ctx.cur.execute("SAVEPOINT pg_l6_bar")
    try:
        g = demand_api.price_guardrails(ctx.cur, ctx.slug, stay_date, room_type)
    except Exception:  # noqa: BLE001
        ctx.cur.execute("ROLLBACK TO SAVEPOINT pg_l6_bar")
        g = None
    ctx.cur.execute("RELEASE SAVEPOINT pg_l6_bar")
    return g


def _bar_minor(ctx: Ctx, line: dict) -> int | None:
    """BAR per room-night in the line's currency, None when Demand has no view."""
    cache = ctx.cache.setdefault("bar", {})
    key = (line["check_in"], line.get("room_type"))
    if key not in cache:
        cache[key] = _guardrails(ctx, *key)
    g = cache[key]
    if not g or g.get("bar") in (None, ""):
        return None
    ccy = g.get("currency") or ctx.currency
    if ccy != line["currency"]:
        return None
    return money.to_minor(g["bar"], ccy)


def _check(ctx: Ctx, line: dict, cap: Decimal) -> FindingDraft | None:
    gross, ci, co = line.get("gross_minor"), line.get("check_in"), line.get("check_out")
    if not gross or gross <= 0 or ci is None:
        return None
    nights = max(1, int(line.get("room_nights") or ((co - ci).days if co else 1)))
    rooms = max(1, int(line.get("rooms") or 1))
    sell = Decimal(gross) / (nights * rooms)
    stacked, progs = _stacked(ctx, line)
    bar = _bar_minor(ctx, line)
    bar_depth = (1 - sell / Decimal(bar)) if bar else None
    depth = max(stacked, bar_depth or Decimal(0))
    if depth <= cap:
        return None
    public = Decimal(bar) if bar else (sell / (1 - stacked) if stacked < 1 else sell)
    amount = rnd((depth - cap) * public * nights * rooms)
    if amount <= tol(0):
        return None
    return FindingDraft(
        detector=DETECTOR, klass="PREVENTABLE", amount_minor=amount, currency=line["currency"], base_confidence=BASE,
        ota_code=line["ota_code"], res_norm=line.get("ota_res_id_norm"), doc=line["doc_no"],
        group=f"month:{month_key(ci)}", missing=[] if bar else ["bar"],
        calc=[step("Stacked program depth", "1 - Π(1 - dᵢ)",
                   {"programs": ",".join(f"{p['program']}:{p['pct']}" for p in progs)}, result=stacked.quantize(Q)),
              step("Depth against BAR", "1 - sell / BAR", {"sell_minor": rnd(sell), "bar_minor": bar},
                   result=bar_depth.quantize(Q) if bar_depth is not None else "n/a"),
              step("Over the cap", "(depth - cap) x public x nights x rooms",
                   {"depth": depth.quantize(Q), "cap": cap, "public_minor": rnd(public), "nights": nights,
                    "rooms": rooms}, amount)],
        evidence=[row_ev("Statement line", "pfg_ota_statement_lines", line["id"])]
        + [{"kind": "row", "label": f"Program {p['program']}", "ref": {"table": "pfg_ota_programs", "id": p["id"]}}
           for p in progs],
        reservation_id=line.get("reservation_id"), statement_line_id=line["id"], note="restrict_stacking",
    )


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    cap = Decimal(str(ctx.policies.get("max_total_discount_pct") or 25)) / 100
    if scope.batch_id is None and scope.since is None:
        scope = Scope(kind=scope.kind, ota_code=scope.ota_code, since=ctx.today - timedelta(days=120),
                      until=scope.until)
    return [d for d in (_check(ctx, ln, cap) for ln in data.statement_lines(ctx, scope)) if d is not None]
