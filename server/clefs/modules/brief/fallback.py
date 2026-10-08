"""
Delivery fallback for the WhatsApp brief (02 spec 6.2).

A WhatsApp brief counts only once Meta says DELIVERED (or READ). When it
fails, or has not been delivered 30 minutes after it was queued, the owner
gets the HTML email brief with a fresh magic link instead. When the brief
holds urgent money (an item due within 24 h, U = 1, at or above
URGENT_MIN_MAJOR in the hotel currency), an SMS nudge goes too. The SMS
never carries a URL: SMS to +971 may not contain links (CONTRACTS notify,
UAE rule), so it reads "Clefs: 2 urgent items, AED 4,120 at stake. Open Clefs."

Two triggers, one code path (fall_back):
- the status hook (notify.on_outbound_status("brief.")): a terminal failure
  with no retry or fallback template in flight falls back at once;
- the job brief.delivery_fallback (every 10 minutes): anything still not
  delivered after 30 minutes.

brf_deliveries is the ledger: pending -> delivered | fell_back |
fallback_failed, each row resolved once.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from clefs import db, jobs, notify
from clefs.core import clock, money
from clefs.core.hotels import Hotel, get_hotel
from clefs.modules.brief import brief, render

log = logging.getLogger("clefs.brief.fallback")

DELIVERY_GRACE = timedelta(minutes=30)
URGENT_MIN_MAJOR = 1000  # SMS only for urgent items worth at least this (hotel currency, major units)
URGENT_U = 1.0  # ranking.urgency for a deadline within 24 h (or already passed)
IN_FLIGHT = ("queued", "sending", "sent")
DELIVERED = ("delivered", "read")
_URL_RE = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.(com|ae|io|world|net|org|app)\b", re.IGNORECASE)


# ─── Outbox state (the row and every retry or fallback template after it) ───


def chain_state(cur: Any, outbox_id: int | None) -> str:
    """delivered | in_flight | failed | unknown for an outbox row and its
    follow-ups (131056 retries and 131047 fallback templates carry
    fallback_of = the row they replace)."""
    if not outbox_id:
        return "unknown"
    cur.execute(
        """
        WITH RECURSIVE chain AS (
            SELECT id, status FROM outbound_messages WHERE id = %s
            UNION ALL
            SELECT o.id, o.status FROM outbound_messages o JOIN chain c ON o.fallback_of = c.id
        )
        SELECT array_agg(status) AS statuses FROM chain
        """,
        (outbox_id,),
    )
    statuses = list((cur.fetchone() or {}).get("statuses") or [])
    if not statuses:
        return "unknown"
    if any(s in DELIVERED for s in statuses):
        return "delivered"
    if any(s in IN_FLIGHT for s in statuses):
        return "in_flight"
    return "failed"


# ─── SMS nudge (no URL) ───


def urgent_items(run: dict, hotel: Hotel) -> list[dict]:
    floor = money.to_minor(URGENT_MIN_MAJOR, hotel.currency)
    out = []
    for it in list(run.get("items") or []) + list(run.get("overdue") or []):
        if it.get("section") == "verified":
            continue  # money already banked is never "at stake" (brief.at_stake_minor)
        u = float(it.get("urgency") or 0)
        if u >= URGENT_U and int(it.get("impact_minor") or 0) >= floor:
            out.append(it)
    return out


def sms_text(urgent: list[dict], hotel: Hotel) -> str:
    """"Clefs: 2 urgent items, AED 4,120 at stake. Open Clefs." Never a URL."""
    counted = [i for i in urgent if (i.get("currency") or hotel.currency) == hotel.currency]
    total = sum(int(i.get("impact_minor") or 0) for i in counted)
    amount = money.fmt_money(total, hotel.currency, hotel.locale, minor=True).replace(".00", "")
    # An estimate in the total says so (04 spec 4.5, 02 spec 4.3).
    est = " (incl. est.)" if any(i.get("impact_estimated") and i.get("impact_minor") for i in counted) else ""
    n = len(urgent)
    text = f"Clefs: {n} urgent item{'s' if n != 1 else ''}, {amount} at stake{est}. Open Clefs."
    if _URL_RE.search(text):  # a currency code or locale can never add one, but the rule is absolute
        text = "Clefs: urgent items are waiting. Open Clefs."
    return text


# ─── Fall back ───


def _user(cur: Any, user_id: int) -> dict | None:
    cur.execute("SELECT id, name, email, phone_e164, locale FROM users WHERE id = %s AND is_active", (user_id,))
    r = cur.fetchone()
    return dict(r) if r else None


def fall_back(cur: Any, delivery: dict, *, reason: str) -> dict:
    """Send the email brief (fresh magic link), plus the SMS nudge for urgent
    money, and resolve the ledger row. -> {status, attempts[]}."""
    hotel = get_hotel(cur, delivery["hotel_slug"])
    run = brief.get_run(cur, hotel.slug, delivery["run_id"])
    user = _user(cur, delivery["user_id"])
    attempts: list[dict] = []
    if run and user and user.get("email"):
        link = brief.mint_link(cur, run, user["id"])
        res = notify.send_email(
            user["email"], f"{hotel.name}: {run['headline']}"[:200],
            render.email_html(run, hotel_name=hotel.name, tz=hotel.timezone, locale=hotel.locale, link=link),
            text=render.text(run, tz=hotel.timezone, locale=hotel.locale, link=link), hotel_slug=hotel.slug,
            purpose="brief.fallback", category="utility",
            idempotency_key=f"brief:{run['id']}:{user['id']}:email_fallback", cur=cur,
        )
        attempts.append({"channel": "email", "ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id})
    urgent = urgent_items(run, hotel) if run else []
    if urgent and user and user.get("phone_e164"):
        res = notify.send_sms(
            user["phone_e164"], sms_text(urgent, hotel), hotel_slug=hotel.slug, purpose="brief.fallback_sms",
            idempotency_key=f"brief:{run['id']}:{user['id']}:sms_fallback", cur=cur,
        )
        attempts.append({"channel": "sms", "ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id})
    status = "fell_back" if any(a["ok"] for a in attempts) else "fallback_failed"
    cur.execute(
        "UPDATE brf_deliveries SET status = %s, reason = %s, fallback = %s, resolved_at = %s WHERE id = %s",
        (status, reason, db.Json(attempts), clock.now(), delivery["id"]),
    )
    if run:
        entry = [{"user_id": delivery["user_id"], "channel": a["channel"], "ok": a["ok"], "status": a["status"],
                  "outbox_id": a.get("outbox_id"), "fallback_of": "whatsapp", "reason": reason} for a in attempts]
        cur.execute("UPDATE brf_runs SET delivery = delivery || %s::jsonb WHERE id = %s", (db.Json(entry), run["id"]))
    if status == "fallback_failed":
        log.warning("brief fallback failed for run %s user %s (%s)", delivery["run_id"], delivery["user_id"], reason)
    return {"status": status, "attempts": attempts}


def _mark_delivered(cur: Any, delivery_id: int) -> None:
    cur.execute("UPDATE brf_deliveries SET status = 'delivered', resolved_at = %s WHERE id = %s AND status = 'pending'",
                (clock.now(), delivery_id))


# ─── Sweep (brief.delivery_fallback) ───


def process(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    now = now or clock.now()
    cur.execute(
        """
        SELECT * FROM brf_deliveries WHERE hotel_slug = %s AND status = 'pending'
        ORDER BY id FOR UPDATE SKIP LOCKED
        """,
        (hotel_slug,),
    )
    counts = {"delivered": 0, "fell_back": 0, "waiting": 0}
    for d in [dict(r) for r in cur.fetchall()]:
        state = chain_state(cur, d["outbox_id"])
        if state == "delivered":
            _mark_delivered(cur, d["id"])
            counts["delivered"] += 1
        elif state == "failed":
            fall_back(cur, d, reason="failed")
            counts["fell_back"] += 1
        elif d["created_at"] <= now - DELIVERY_GRACE:
            fall_back(cur, d, reason="not_delivered_30m")
            counts["fell_back"] += 1
        else:
            counts["waiting"] += 1
    return counts


@jobs.scheduled("brief.delivery_fallback", jobs.every(minutes=10))
def _fallback_job(ctx: jobs.JobCtx) -> dict:
    return process(ctx.cur, ctx.hotel.slug, now=ctx.now)


# ─── Status hook (immediate) ───


def _root_id(cur: Any, outbox_id: int) -> int:
    cur.execute(
        """
        WITH RECURSIVE up AS (
            SELECT id, fallback_of FROM outbound_messages WHERE id = %s
            UNION ALL
            SELECT o.id, o.fallback_of FROM outbound_messages o JOIN up ON o.id = up.fallback_of
        )
        SELECT id FROM up WHERE fallback_of IS NULL LIMIT 1
        """,
        (outbox_id,),
    )
    r = cur.fetchone()
    return int(r["id"]) if r else int(outbox_id)


@notify.on_outbound_status("brief.")
def on_status(cur: Any, row: dict, status: str, error_code: str | None) -> None:
    if row.get("channel") != "whatsapp" or status not in ("delivered", "read", "failed"):
        return
    root = _root_id(cur, int(row["id"]))
    cur.execute("SELECT * FROM brf_deliveries WHERE outbox_id = %s AND status = 'pending' FOR UPDATE", (root,))
    d = cur.fetchone()
    if d is None:
        return
    if status in DELIVERED:
        _mark_delivered(cur, d["id"])
        return
    meta = row.get("meta") or {}
    if meta.get("retried_by") or meta.get("fallback_id") or chain_state(cur, root) != "failed":
        return  # a retry or fallback template is still going
    fall_back(cur, dict(d), reason="failed")
