"""
Inquiry Guard signals on the quote-to-cash side, the weighted pipeline and
stage-probability recalibration (04 spec 4.5, 4.10, 4.11).

The intake side (inq.unanswered, inq.lead_fetch_failed) is registered in
jobs.py. Canon 18: only money at risk is daily, so an awaiting-approval quote
is daily only while the guest has had no substantive reply AND the SLA is
breached (the unanswered-inquiry case); otherwise weekly.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from clefs.core import clock, money
from clefs.core.hotels import Hotel
from clefs.core.signals import Evidence, RecommendedAction, SignalIn, register_signal_kind, resolve_signal, upsert_signal
from clefs.core.time import local_date
from clefs.modules.inquiries.models import OPEN_STAGES, PRIOR_PROBABILITY

# ─── Kinds (4.11) ───

register_signal_kind(
    "inq.quote_awaiting_approval", label="Quote waiting for approval", default_owner_role="gm",
    section="money_available", cadence="weekly", icon="receipt",
)
register_signal_kind(
    "inq.paid_not_in_pms", label="Paid inquiry missing from the PMS", default_owner_role="front_desk",
    section="service_risk", cadence="weekly", icon="alert",
)
register_signal_kind(
    "inq.leaked_to_ota", label="Direct inquiry booked through an OTA", default_owner_role="gm",
    section="money_lost", cadence="weekly", icon="leak",
)
register_signal_kind(
    "inq.pipeline_weekly", label="Inquiry pipeline and verified bookings", default_owner_role="owner",
    section="verified", cadence="weekly", icon="chart",
)


def _label(minor: int, ccy: str) -> str:
    return money.fmt_money(money.from_minor(minor, ccy), ccy)


# ─── Awaiting approval ───


def awaiting_key(quote_id: int) -> str:
    return f"inq.quote_awaiting_approval:{quote_id}"


def upsert_awaiting_approval(cur: Any, hotel: Hotel, inquiry: dict, quote: dict, *, action_id: int) -> dict:
    urgent = bool(inquiry.get("sla_breached")) and inquiry.get("first_response_at") is None
    return upsert_signal(cur, hotel.slug, SignalIn(
        module="inquiries", kind="inq.quote_awaiting_approval", dedupe_key=awaiting_key(quote["id"]),
        title=f"Quote {inquiry['ref'].upper()}-v{quote['version']} waits for approval "
              f"({_label(quote['total_minor'], quote['currency'])})",
        summary=", ".join(quote.get("auto_send_reasons") or []) or None,
        cadence="daily" if urgent else "weekly",
        impact_minor=inquiry.get("money_at_risk_minor"), currency=inquiry.get("currency") or hotel.currency,
        impact_kind="revenue_upside", confidence=0.6,
        evidence=[Evidence(kind="calc", label="Quote total", value=_label(quote["total_minor"], quote["currency"])),
                  Evidence(kind="row", label="Why approval", value=", ".join(quote.get("auto_send_reasons") or []))],
        recommended_action=RecommendedAction(type="inquiries.send_quote", label="Approve and send",
                                             params={"action_id": action_id, "quote_id": quote["id"]}),
        owner_role="gm", due_at=quote["valid_until"], subject_type="inquiry", subject_id=str(inquiry["id"]),
    ))


def resolve_awaiting_approval(cur: Any, hotel_slug: str, quote_id: int) -> bool:
    return resolve_signal(cur, hotel_slug, awaiting_key(quote_id), resolution="quote_sent")


# ─── Paid but not in the PMS (4.8) ───


def paid_key(inquiry_id: int) -> str:
    return f"inq.paid_not_in_pms:{inquiry_id}"


def upsert_paid_not_in_pms(cur: Any, hotel: Hotel, inquiry: dict, *, paid_minor: int, currency: str,
                           action_id: int | None) -> dict:
    """Weekly, daily once check-in is within 48 h (7.3)."""
    soon = inquiry.get("check_in") and inquiry["check_in"] <= local_date(hotel) + timedelta(days=2)
    return upsert_signal(cur, hotel.slug, SignalIn(
        module="inquiries", kind="inq.paid_not_in_pms", dedupe_key=paid_key(inquiry["id"]),
        title=f"Inquiry {inquiry['ref'].upper()} paid {_label(paid_minor, currency)} but has no PMS reservation",
        cadence="daily" if soon else "weekly", impact_minor=paid_minor, currency=currency, impact_kind="risk", confidence=0.9,
        evidence=[Evidence(kind="calc", label="Paid", value=_label(paid_minor, currency))],
        recommended_action=RecommendedAction(type="inquiries.create_pms_reservation",
                                             label="Create the reservation in the PMS",
                                             params={"inquiry_id": inquiry["id"], "action_id": action_id}),
        owner_role="front_desk", subject_type="inquiry", subject_id=str(inquiry["id"]),
    ))


def resolve_paid_not_in_pms(cur: Any, hotel_slug: str, inquiry_id: int) -> bool:
    return resolve_signal(cur, hotel_slug, paid_key(inquiry_id), resolution="reservation_found")


# ─── Leaked to an OTA (internal only, Canon 19) ───


def upsert_leaked(cur: Any, hotel: Hotel, inquiry: dict, *, channel: str, commission_minor: int | None,
                  currency: str | None = None) -> dict:
    """The owner's weekly money-lost line. It names the inquiry ref and the
    channel only: no guest name or contact (masked everywhere, never sent).
    `currency` is the commission's own (leak.to_hotel_currency already
    converted it to the hotel's when a rate exists)."""
    return upsert_signal(cur, hotel.slug, SignalIn(
        module="inquiries", kind="inq.leaked_to_ota", dedupe_key=f"inq.leaked_to_ota:{inquiry['id']}",
        title=f"Inquiry {inquiry['ref'].upper()} booked through {channel} instead of direct",
        summary="Internal only: never contact the guest about this.",
        impact_minor=commission_minor, currency=(currency or hotel.currency) if commission_minor is not None else None,
        impact_kind="cost_leak" if commission_minor is not None else None, confidence=0.8,
        evidence=[Evidence(kind="row", label="Channel", value=channel)],
        owner_role="gm", subject_type="inquiry", subject_id=str(inquiry["id"]),
    ))


# ─── Weighted pipeline (4.10) ───


def pipeline(cur: Any, hotel: Hotel, *, today: date | None = None) -> dict:
    """Sum of money at risk over open inquiries arriving today or later, by
    check-in month and segment. Always an estimate ("est.")."""
    today = today or local_date(hotel)
    cur.execute(
        """
        SELECT to_char(check_in, 'YYYY-MM') AS month, segment, COUNT(*) AS n,
               COALESCE(SUM(money_at_risk_minor), 0) AS minor
        FROM inq_inquiries
        WHERE hotel_slug = %s AND stage = ANY(%s) AND check_in >= %s AND duplicate_of IS NULL AND NOT is_test
        GROUP BY 1, 2 ORDER BY 1, 2
        """,
        (hotel.slug, list(OPEN_STAGES), today),
    )
    rows = [dict(r) for r in cur.fetchall()]
    ccy = hotel.currency
    by_month: dict[str, int] = defaultdict(int)
    for r in rows:
        by_month[r["month"]] += int(r["minor"])
        r["value"] = money.money_str(int(r["minor"]), ccy)
        r["weighted_minor"] = int(r.pop("minor"))
        r["count"] = int(r.pop("n"))
    total = sum(by_month.values())
    return {"currency": ccy, "estimated": True, "label": "est.", "as_of": today.isoformat(),
            "total_minor": total, "total_value": money.money_str(total, ccy),
            "count": sum(r["count"] for r in rows), "rows": rows,
            "months": [{"month": m, "weighted_minor": v, "value": money.money_str(v, ccy)} for m, v in sorted(by_month.items())]}


def _compact(minor: int, ccy: str) -> str:
    v = money.from_minor(minor, ccy) or Decimal(0)
    if v >= 1000:
        return f"{ccy} {(v / 1000).quantize(Decimal('0.1'), ROUND_HALF_UP)}k".replace(".0k", "k")
    return f"{ccy} {v.quantize(Decimal(1), ROUND_HALF_UP)}"


def pipeline_weekly(cur: Any, hotel: Hotel) -> dict:
    """"9 inquiries (AED 21k potential, est.); last week 4 verified bookings,
    AED 11.2k, AED 1.7k commission avoided (net of payment fees)"."""
    p = pipeline(cur, hotel)
    since = clock.now() - timedelta(days=7)
    cur.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(verified_revenue_minor), 0) AS rev, COALESCE(SUM(outcome_net_minor), 0) AS net
        FROM inq_outcomes WHERE hotel_slug = %s AND outcome = 'verified_direct' AND decided_at >= %s
        """,
        (hotel.slug, since),
    )
    v = dict(cur.fetchone())
    ccy = hotel.currency
    title = (f"{p['count']} inquiries ({_compact(p['total_minor'], ccy)} potential, est.); last week "
             f"{int(v['n'])} verified booking{'s' if int(v['n']) != 1 else ''}, {_compact(int(v['rev']), ccy)}, "
             f"{_compact(int(v['net']), ccy)} commission avoided (net of payment fees)")
    week = local_date(hotel).isocalendar()
    return upsert_signal(cur, hotel.slug, SignalIn(
        module="inquiries", kind="inq.pipeline_weekly", dedupe_key=f"inq.pipeline_weekly:{week.year}-W{week.week:02d}",
        title=title, impact_minor=int(v["net"]), currency=ccy, impact_kind="revenue_upside", confidence=0.9,
        evidence=[Evidence(kind="calc", label="Weighted pipeline (est.)", value=p["total_value"]),
                  Evidence(kind="metric", label="Verified bookings last week", value=int(v["n"]))],
        owner_role="owner", expires_at=clock.now() + timedelta(days=7),
    ))


# ─── Stage-probability recalibration (4.5, monthly) ───

MIN_CLOSED = 30


def recalibrate(cur: Any, hotel_slug: str) -> dict:
    """p(stage) = share of inquiries that ever reached the stage and later
    verified, over the last 365 days of CLOSED inquiries. Written only with
    at least 30 closed inquiries behind a stage; below that the priors stay."""
    since = clock.now() - timedelta(days=365)
    written = {}
    for stage in PRIOR_PROBABILITY:
        base = "quoted" if stage == "quoted_engaged" else stage
        engaged = "AND i.guest_engaged" if stage == "quoted_engaged" else ""
        cur.execute(
            f"""
            SELECT COUNT(*) AS n, COUNT(*) FILTER (WHERE i.stage = 'confirmed_verified') AS won
            FROM inq_inquiries i
            WHERE i.hotel_slug = %s AND i.created_at >= %s AND NOT i.is_test AND i.duplicate_of IS NULL
              AND i.stage IN ('confirmed_verified', 'lost', 'turned_down') {engaged}
              AND EXISTS (SELECT 1 FROM inq_stage_events e WHERE e.inquiry_id = i.id AND e.to_stage = %s
                          UNION ALL SELECT 1 WHERE %s = 'new')
            """,
            (hotel_slug, since, base, base),
        )
        r = cur.fetchone()
        n, won = int(r["n"]), int(r["won"])
        if n < MIN_CLOSED:
            continue
        p = (Decimal(won) / Decimal(n)).quantize(Decimal("0.001"), ROUND_HALF_UP)
        cur.execute(
            """
            INSERT INTO inq_stage_probabilities (hotel_slug, stage, p, n, computed_at) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (hotel_slug, stage) DO UPDATE SET p = EXCLUDED.p, n = EXCLUDED.n, computed_at = EXCLUDED.computed_at
            """,
            (hotel_slug, stage, p, n, clock.now()),
        )
        written[stage] = str(p)
    return {"written": written}


# ─── For the brief summary (summary.py owner delegates here) ───


def awaiting_approval(cur: Any, hotel_slug: str) -> dict:
    """{count, total_minor, items[{quote_id, inquiry_id, ref, version, total_minor, valid_until, action_id}]}
    for quotes whose send action waits for a person."""
    cur.execute(
        """
        SELECT q.id AS quote_id, q.inquiry_id, i.ref, q.version, q.total_minor, q.currency, q.valid_until, q.action_id
        FROM inq_quotes q JOIN inq_inquiries i ON i.id = q.inquiry_id
        WHERE q.hotel_slug = %s AND q.status = 'awaiting_approval' AND q.valid_until > %s ORDER BY q.valid_until
        """,
        (hotel_slug, clock.now()),
    )
    items = [dict(r) for r in cur.fetchall()]
    return {"count": len(items), "total_minor": sum(int(r["total_minor"]) for r in items), "items": items}
