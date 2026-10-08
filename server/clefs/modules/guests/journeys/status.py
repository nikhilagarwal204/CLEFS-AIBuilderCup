"""
Delivery status of journey messages (04 spec 3.7 step 6, 3.5 Meta 131049,
3.10 guests.journey_cost).

`@on_outbound_status("guests.journey")` hears every outbox row whose purpose
starts with it; only step sends (purpose guests.journey.<step>) move plans.
A follow-up row the outbox made itself (131056 retry, 131047 fallback
template) reports under the same purpose; its plan is found through
`fallback_of`.

Meta 131049 (marketing frequency cap, "healthy ecosystem"):
- first time: the plan is `deferred` with error_code 131049, attempt 2 and
  due_at = max(now + 24 h, next window start). The dispatcher re-sends under
  the SAME action with key gst:{plan_id}:a2 (no new action, no approval).
  The outbox's own 24 h retry copy is cancelled so the guest gets one
  message, inside the hotel's window.
- second time: email if the guest is eligible there (marketing needs
  marketing_email consent), else skipped `meta_131049`.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from clefs import notify
from clefs.core import clock, money, signals
from clefs.core.hotels import get_hotel
from clefs.modules.guests import timeutil
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import executor, guards, steps
from clefs.settings import get_settings

log = logging.getLogger("clefs.guests.journeys.status")

PURPOSE_PREFIX = "guests.journey"
SERVICE_CHARGING_FROM = date(2026, 10, 1)  # A4: service messages charged at the utility rate from here
MARKETS = {"+971": "AE", "+91": "IN", "+966": "SA", "+44": "GB", "+1": "US"}
META_CAP_KIND = "guests.meta_cap_blocks"


def plan_for_row(cur: Any, row: dict) -> dict | None:
    """The plan a message belongs to, following fallback_of up to 5 hops."""
    oid, hops = row["id"], 0
    while oid is not None and hops < 6:
        cur.execute(f"SELECT {executor.planner.PLAN_COLS} FROM gst_journey_plans WHERE outbound_message_id = %s", (oid,))
        p = cur.fetchone()
        if p:
            return dict(p)
        cur.execute("SELECT fallback_of FROM outbound_messages WHERE id = %s", (oid,))
        r = cur.fetchone()
        oid, hops = (r["fallback_of"] if r else None), hops + 1
    return None


def _cancel_outbox_retry(cur: Any, retry_id: Any) -> None:
    # The outbox's 131049 retry copy is unbounded and ignores the hotel's
    # window, so the journey takes the re-send over and retracts the copy
    # (and its pending delivery task) through notify's public door.
    if retry_id:
        notify.cancel_queued(cur, outbox_id=int(retry_id), reason="meta_131049_resend")


def handle_131049(cur: Any, plan: dict, row: dict) -> str:
    _cancel_outbox_retry(cur, (row.get("meta") or {}).get("retried_by"))
    # Meta held the message back, so the guest never received it: it must
    # not count toward the marketing caps (the re-send records it again).
    cur.execute("DELETE FROM gst_marketing_sends WHERE hotel_slug = %s AND ref = %s",
                (plan["hotel_slug"], f"plan:{plan['id']}"))
    hotel = get_hotel(cur, plan["hotel_slug"])
    now = clock.now()
    _raise_meta_cap(cur, hotel, now)
    if plan["attempt"] >= 2:
        out = executor.send_plan(cur, hotel, {**plan, "status": "claimed"}, action_id=plan["action_id"], attempt=3,
                                 now=now, only=("email",))
        if out["status"] != "sent":
            executor.set_plan(cur, plan["id"], status="skipped", skip_reason="meta_131049", error_code="131049")
            return "skipped"
        return "email_fallback"
    cfg = gsettings.load(cur, hotel.slug)
    category = plan.get("category_at_send") or steps.EXPECTED_CATEGORY.get(plan["step"], "UTILITY")
    t0, t1 = guards.window_for(cfg, category)
    due = timeutil.next_window_start(hotel, now + steps.META_131049_DELAY, t0, t1)
    if due >= plan["valid_until"]:
        executor.set_plan(cur, plan["id"], status="skipped", skip_reason="meta_131049", error_code="131049")
        return "skipped"
    executor.set_plan(cur, plan["id"], status="deferred", error_code="131049", attempt=2, due_at=due, claimed_at=None)
    return "deferred"


def _raise_meta_cap(cur: Any, hotel: Any, now: datetime) -> None:
    week = timeutil.local(hotel, now).date().isocalendar()
    cur.execute(
        "SELECT COUNT(*) AS n FROM gst_journey_plans WHERE hotel_slug = %s AND error_code = '131049' AND updated_at >= %s",
        (hotel.slug, now - timedelta(days=7)),
    )
    n = int(cur.fetchone()["n"]) + 1
    signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
        module="guests", confidence=1.0, kind=META_CAP_KIND, dedupe_key=f"{META_CAP_KIND}:{week.year}-W{week.week:02d}",
        title=f"Meta held back {n} marketing message{'s' if n != 1 else ''} this week (frequency cap)",
        summary="Meta limits how many marketing messages one person receives from all businesses. "
                "Journeys retry once after 24 hours inside the send window, then fall back to email or skip.",
        evidence=[signals.Evidence(kind="metric", label="131049 blocks, 7 days", value=n)],
    ))


# ─── Cost (3.10 guests.journey_cost; A4, rates UNVERIFIED) ───


def market_of(recipient: str | None, hotel: Any) -> str:
    r = recipient or ""
    for prefix in sorted(MARKETS, key=len, reverse=True):
        if r.startswith(prefix):
            return MARKETS[prefix]
    return (getattr(hotel, "country", None) or "AE").upper()[:2]


def _rate(cur: Any, market: str, category: str, on: date) -> tuple[int, str]:
    cur.execute(
        "SELECT rate_micros, currency FROM gst_wa_rate_card WHERE market = %s AND category = %s AND effective_from <= %s "
        "ORDER BY effective_from DESC LIMIT 1",
        (market, category, on),
    )
    r = cur.fetchone()
    if r is None and market != "AE":
        return _rate(cur, "AE", category, on)
    return (int(r["rate_micros"]), r["currency"]) if r else (0, "USD")


def _window_open_at_send(cur: Any, plan: dict, sent_at: datetime) -> bool:
    if not plan.get("contact_point_id"):
        return False
    cur.execute("SELECT value_hmac FROM gst_contact_points WHERE id = %s", (plan["contact_point_id"],))
    r = cur.fetchone()
    if not r or r["value_hmac"] is None:
        return False
    until = notify.wa_window_open_until(cur, plan["hotel_slug"], sender_hmac=bytes(r["value_hmac"]), at=sent_at)
    return until is not None and until > sent_at


def charged_category(cur: Any, plan: dict | None, row: dict, sent_at: datetime) -> str:
    """Marketing templates, and J7, at the marketing rate. Utility templates
    outside a window at the utility rate. Inside a window: free before
    2026-10-01, then the service rate less WA_SERVICE_FREE_ALLOWANCE per
    business number per month, applied in send order across all purposes."""
    step = (plan or {}).get("step")
    if row.get("category") == "marketing" or step in steps.CHARGED_AS_MARKETING:
        return "marketing"
    if not _window_open_at_send(cur, plan or {}, sent_at) and row.get("kind") == "template":
        return "utility"
    if sent_at.date() < SERVICE_CHARGING_FROM:
        return "free"
    allowance = int(getattr(get_settings(), "WA_SERVICE_FREE_ALLOWANCE", 0) or 0)
    if allowance > 0:
        month0 = sent_at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cur.execute(
            "SELECT COUNT(*) AS n FROM gst_message_costs c JOIN outbound_messages o ON o.id = c.outbound_message_id "
            "WHERE o.sender_id IS NOT DISTINCT FROM %s AND c.charged_category IN ('service', 'free') "
            "AND c.charged_at >= %s AND c.charged_at < %s",
            (row.get("sender_id"), month0, sent_at),
        )
        if int(cur.fetchone()["n"]) < allowance:
            return "free"
    return "service"


def record_cost(cur: Any, plan: dict | None, row: dict, *, message_id: int | None = None) -> None:
    """Price one delivered message once (UNIQUE outbound_message_id)."""
    hotel = get_hotel(cur, row["hotel_slug"])
    sent_at = row.get("sent_at") or row.get("created_at") or clock.now()
    if row["channel"] != "whatsapp":
        cat, rate, rate_ccy, market = "free", 0, hotel.currency, None
    else:
        cat = charged_category(cur, plan, row, sent_at)
        market = market_of(row.get("recipient"), hotel)
        rate, rate_ccy = (0, "USD") if cat == "free" else _rate(cur, market, "utility" if cat == "service" else cat,
                                                                sent_at.date() if cat != "service" else SERVICE_CHARGING_FROM)
    cost = 0
    if rate:
        try:
            major = money.convert(cur, Decimal(rate) / Decimal(1_000_000), rate_ccy, hotel.currency, sent_at.date())
            cost = int((Decimal(major) * Decimal(1_000_000)).to_integral_value())
        except Exception:  # fx_unavailable: keep the vendor rate, cost unknown (0)
            log.info("no fx %s->%s for a journey message cost", rate_ccy, hotel.currency)
    cur.execute(
        """
        INSERT INTO gst_message_costs (hotel_slug, plan_id, message_id, outbound_message_id, step, channel,
                                       charged_category, market, rate_micros, rate_currency, cost_micros, currency,
                                       charged_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (outbound_message_id) DO NOTHING
        """,
        (hotel.slug, (plan or {}).get("id"), message_id, row["id"], (plan or {}).get("step"), row["channel"], cat,
         market, rate, rate_ccy, cost, hotel.currency, sent_at),
    )


# ─── The hook ───

FORWARD = {"sent": 1, "delivered": 2, "read": 3, "replied": 4}


def _journey_message(cur: Any, row: dict) -> dict | None:
    cur.execute("SELECT * FROM gst_journey_messages WHERE outbound_message_id = %s", (row["id"],))
    r = cur.fetchone()
    return dict(r) if r else None


@notify.on_outbound_status(PURPOSE_PREFIX)
def on_status(cur: Any, row: dict, status: str, error_code: str | None) -> None:
    purpose = row.get("purpose") or ""
    if not purpose.startswith(PURPOSE_PREFIX + "."):
        # Free-form journey messages (consent ask, acknowledgements) are
        # priced but move no plan.
        if status == "delivered" and purpose.startswith("guests.journey_msg"):
            jm = _journey_message(cur, row)
            record_cost(cur, None, row, message_id=(jm or {}).get("id"))
        return
    plan = plan_for_row(cur, row)
    if plan is None:
        return
    now = clock.now()
    if status == "failed":
        meta = row.get("meta") or {}
        if error_code == "131049":
            handle_131049(cur, plan, row)
        elif meta.get("retried_by") or meta.get("fallback_id"):
            # The outbox is re-sending (131056) or sent the fallback template
            # (131047): follow the new row; the plan stays sent.
            executor.set_plan(cur, plan["id"], outbound_message_id=int(meta.get("retried_by") or meta["fallback_id"]),
                              error_code=error_code)
        elif plan["status"] not in ("skipped", "cancelled", "done"):
            executor.set_plan(cur, plan["id"], status="failed", error_code=(error_code or "failed")[:40])
        return
    if status == "sent" and row["channel"] == "email":
        status = "delivered"  # Resend's accept is the only status email has here
    changes: dict[str, Any] = {}
    if status == "sent" and row.get("provider_id") and row["channel"] == "whatsapp":
        changes["wamid"] = row["provider_id"]
    if status in ("delivered", "read") and FORWARD.get(plan["status"], 0) < FORWARD[status] and plan["status"] in FORWARD:
        changes["status"] = status
    if row.get("id") != plan["outbound_message_id"]:
        changes["outbound_message_id"] = row["id"]
    if changes:
        executor.set_plan(cur, plan["id"], **changes)
    if status == "delivered" or (status == "read" and row.get("delivered_at") is None):
        record_cost(cur, plan, row)
    if status in ("delivered", "read") and plan.get("contact_point_id"):
        cur.execute("UPDATE gst_contact_points SET last_used_at = GREATEST(last_used_at, %s) WHERE id = %s",
                    (now, plan["contact_point_id"]))


def replay(cur: Any, outbox_id: int | None) -> None:
    """Inline delivery (NOTIFY_MODE capture/log, or an immediate provider
    failure) fires the hook before send_plan has linked the row to its plan;
    replay what the row already says once the link exists."""
    if not outbox_id:
        return
    cur.execute("SELECT * FROM outbound_messages WHERE id = %s", (outbox_id,))
    row = cur.fetchone()
    if row is None:
        return
    row = dict(row)
    if row["status"] in ("sent", "delivered", "read", "failed", "outside_window"):
        st = "failed" if row["status"] == "outside_window" else row["status"]
        on_status(cur, row, st, row.get("error_code"))
