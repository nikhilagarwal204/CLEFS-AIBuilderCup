"""L7: an OTA sells below the hotel's own price (02 spec 3.3).

For one comparable_key (date|los|adults|room|device|login, so logged-in
and member prices only ever meet like-for-like) and stay date, take each
channel's latest observation; an OTA channel breaches when
price_ch < min(direct, other OTAs) x (1 - parity tolerance %).
exposure = (direct - ota) x rooms booked on that channel for the date
(the cheapest other price stands in when no direct price was captured).
PREVENTABLE 0.6; 0.75 with a screenshot. Rows Visibility recorded
(emit_signal false) are stored but never signalled: Visibility already
raised its own gap.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from clefs.modules.profit_guard import repo
from clefs.modules.profit_guard.detectors.base import Ctx, FindingDraft, Scope, month_key, rnd, row_ev, step

DETECTOR = "L7"
FRESH_DAYS = 14
DIRECT = ("DIRECT", "DIRECT_WEB", "WEBSITE", "BOOKING_ENGINE", "BRAND")


def _is_direct(ctx: Ctx, channel: str) -> bool:
    kinds = ctx.cache.setdefault("channel_kinds", None)
    if kinds is None:
        kinds = ctx.cache["channel_kinds"] = repo.channel_kinds(ctx.cur, ctx.slug)
    c = channel.upper()
    return c in DIRECT or c.startswith("DIRECT") or kinds.get(c) == "direct"


def _latest(ctx: Ctx) -> dict[tuple, dict[str, dict]]:
    """{(stay_date, comparable_key, currency, taxes): {channel: newest row}} for future stays."""
    ctx.cur.execute(
        """
        SELECT DISTINCT ON (stay_date, comparable_key, currency, taxes_included, channel) *
          FROM pfg_rate_evidence
         WHERE hotel_slug = %s AND stay_date >= %s AND captured_at >= %s
         ORDER BY stay_date, comparable_key, currency, taxes_included, channel, captured_at DESC, id DESC
        """,
        (ctx.slug, ctx.today, ctx.now - timedelta(days=FRESH_DAYS)),
    )
    out: dict[tuple, dict[str, dict]] = {}
    for r in ctx.cur.fetchall():
        key = (r["stay_date"], r["comparable_key"], r["currency"], r["taxes_included"])
        out.setdefault(key, {})[r["channel"].upper()] = dict(r)
    return out


def _rooms_booked(ctx: Ctx, channel: str, stay_date) -> int:
    ctx.cur.execute(
        """
        SELECT COALESCE(SUM(n.rooms), 0) AS rooms FROM reservation_nights n
          JOIN reservations r ON r.id = n.reservation_id
         WHERE n.hotel_slug = %s AND n.stay_date = %s AND UPPER(r.channel_code) = %s
           AND r.status NOT IN ('CANCELLED', 'NO_SHOW')
        """,
        (ctx.slug, stay_date, channel.upper()),
    )
    return int(ctx.cur.fetchone()["rooms"])


def _shot(row: dict) -> str | None:
    return str(row["screenshot_file_id"]) if row.get("screenshot_file_id") else None


def run(ctx: Ctx, scope: Scope) -> list[FindingDraft]:
    tol_pct = Decimal(str(ctx.policies.get("parity_tolerance_pct") or "1.0"))
    out: list[FindingDraft] = []
    for (stay_date, key, currency, taxes), by_ch in _latest(ctx).items():
        if len(by_ch) < 2:
            continue
        direct = [r["price_minor"] for ch, r in by_ch.items() if _is_direct(ctx, ch)]
        for ch, row in by_ch.items():
            if _is_direct(ctx, ch) or (scope.ota_code and ch != scope.ota_code):
                continue
            others = [r["price_minor"] for c, r in by_ch.items() if c != ch]
            ref = min(others)
            limit = Decimal(ref) * (1 - tol_pct / 100)
            if Decimal(row["price_minor"]) >= limit:
                continue
            anchor = min(direct) if direct else ref
            rooms = _rooms_booked(ctx, ch, stay_date)
            gap = anchor - row["price_minor"]
            amount = rnd(Decimal(gap) * rooms)
            shot = row.get("screenshot_file_id") is not None
            out.append(FindingDraft(
                detector=DETECTOR, klass="PREVENTABLE", amount_minor=amount, currency=currency,
                base_confidence=Decimal("0.75") if shot else Decimal("0.6"), ota_code=ch, res_norm=key,
                doc=stay_date.isoformat(), group=f"month:{month_key(stay_date)}",
                missing=[] if direct else ["direct_price"], signal=bool(row.get("emit_signal", True)),
                calc=[step("Cheapest other price", "min(direct, other OTAs)",
                           {c: r["price_minor"] for c, r in by_ch.items() if c != ch}, ref),
                      step("Breach", "price_ch < ref x (1 - tol%)",
                           {"price_minor": row["price_minor"], "tol_pct": tol_pct, "limit_minor": rnd(limit)},
                           result=True),
                      step("Exposure", "(direct - ota) x rooms booked on the channel",
                           {"anchor_minor": anchor, "anchor": "direct" if direct else "cheapest_other",
                            "rooms_booked": rooms}, amount)],
                evidence=[{**row_ev(f"{r['channel']} {r['login_state'].lower()} {r['device'].lower()}",
                                    "pfg_rate_evidence", r["id"], screenshot_file_id=_shot(r)),
                           "value": r["price_minor"]} for r in by_ch.values()],
                note="parity",
            ))
    return out
