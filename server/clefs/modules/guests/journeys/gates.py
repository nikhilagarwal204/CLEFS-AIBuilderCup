"""
The J6 check-out gates (04 spec 3.9): recovery first, before the guest
leaves, and a post-stay follow-up for what is still open after.

J6_pre (departure day, standard check-out - 3 h):
- open Service items for the room since check-in, plus negative day-1
  replies nobody filed a ticket for;
- if any: the human task guests.checkout_recovery (owner gm), a note to the
  duty manager (service.api.on_duty), and the signal
  guests.checkout_open_issues (daily when an item is L1, else weekly).
J6_post (at check-out): items still open become a post_stay_recovery
follow-up (service.api.create_followup, source journey_gate).

Both end `done`. By design this file never reads or writes the feedback
step's plans, and recovery scripts never mention reviews: feedback is asked
of every guest identically (feedback.py), whatever happened here.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from typing import Any

from clefs import notify
from clefs.core import actions, signals
from clefs.modules.guests import stays_port, timeutil
from clefs.modules.guests.journeys import executor, steps
from clefs.modules.service import api as service_api

log = logging.getLogger("clefs.guests.journeys.gates")

SIGNAL_KIND = "guests.checkout_open_issues"
TASK_CARD_RULES = ("Never mention reviews or ratings during recovery.",
                   "Staff never ask guests for ratings on the premises.")


def _since(hotel: Any, stay: dict, now: datetime) -> datetime:
    ci = stays_port.as_date(stay.get("check_in"))
    return timeutil.at_local(hotel, ci, time(0)) if ci else now - timedelta(days=30)


def open_items(cur: Any, hotel: Any, stay: dict, now: datetime) -> list[dict]:
    if not stay.get("room"):
        return []
    try:
        return list(service_api.open_items_for_stay(cur, hotel.slug, room=stay["room"], since=_since(hotel, stay, now)))
    except Exception:
        log.exception("open_items_for_stay failed at %s", hotel.slug)
        return []


def unfiled_negative_replies(cur: Any, hotel_slug: str, reservation_id: int) -> list[dict]:
    """Negative day-1 replies with no Service ticket behind them (a filed
    one is covered by open_items once Service reports it)."""
    cur.execute(
        """
        SELECT r.id, r.summary_en, r.classification FROM gst_journey_replies r
        JOIN gst_journey_plans p ON p.id = r.plan_id
        WHERE r.hotel_slug = %s AND r.reservation_id = %s AND p.step = 'j5' AND r.service_ref IS NULL
          AND (r.classification->>'negative')::boolean IS TRUE
        """,
        (hotel_slug, reservation_id),
    )
    return [dict(r) for r in cur.fetchall()]


def _describe(stay: dict, items: list[dict], replies: list[dict]) -> str:
    room = stay.get("room") or f"booking {stay.get('booking_ref') or stay['reservation_id']}"
    kinds = sorted({str(i.get("request_type") or "service") for i in items})
    parts = []
    if items:
        parts.append(f"{len(items)} open {'/'.join(kinds)} ticket{'s' if len(items) != 1 else ''}")
    if replies:
        parts.append(f"{len(replies)} unresolved day-1 complaint{'s' if len(replies) != 1 else ''}")
    return f"Room {room} departs today with {' and '.join(parts)}; attempt recovery at check-out"


def _tell_duty_manager(cur: Any, hotel: Any, text: str, now: datetime, *, key: str) -> int:
    try:
        people = service_api.on_duty(cur, hotel.slug, role="duty_manager", at=now) or []
    except Exception:
        log.exception("on_duty failed at %s", hotel.slug)
        return 0
    sent = 0
    for p in people:
        if not p.get("user_id"):
            continue
        cur.execute("SELECT phone_e164, email, name FROM users WHERE id = %s", (p["user_id"],))
        u = cur.fetchone()
        if not u:
            continue
        res = notify.notify(
            notify.Recipient(phone=u["phone_e164"], email=u["email"], name=u["name"], user_id=p["user_id"]),
            notify.Message(purpose="guests.checkout_recovery", hotel_slug=hotel.slug, category="utility",
                           sender="platform", whatsapp_text=text, email_subject="Check-out recovery", email_text=text,
                           email_html=f"<p>{text}</p>", idempotency_key=f"{key}:u{p['user_id']}"),
            channels=("whatsapp", "email"), cur=cur,
        )
        sent += 1 if res.ok else 0
    return sent


# ─── The gates ───


def run_pre(cur: Any, hotel: Any, plan: dict, stay: dict, *, now: datetime) -> dict:
    items = open_items(cur, hotel, stay, now)
    replies = unfiled_negative_replies(cur, hotel.slug, stay["reservation_id"])
    if not items and not replies:
        return {"open": 0}
    text = _describe(stay, items, replies)
    rid = stay["reservation_id"]
    task = actions.create_action(
        cur, hotel.slug, type=steps.CHECKOUT_RECOVERY, title=text[:300], owner_role="gm",
        params={"reservation_id": rid, "room": stay.get("room"), "items": [i.get("id") for i in items],
                "reply_ids": [r["id"] for r in replies], "rules": list(TASK_CARD_RULES)},
        due_at=plan["due_at"] + timedelta(hours=3), expires_at=plan["valid_until"],
        dedupe_key=f"gst:recovery:{rid}", actor=actions.SYSTEM,
    )
    told = _tell_duty_manager(cur, hotel, text, now, key=f"gst:j6:{rid}")
    l1 = any(str(i.get("severity") or "").upper() == "L1" for i in items)
    signals.upsert_signal(cur, hotel.slug, signals.SignalIn(
        module="guests", confidence=1.0, kind=SIGNAL_KIND, dedupe_key=f"{SIGNAL_KIND}:{rid}", title=text[:200],
        summary="Recovery before departure. Recovery scripts never mention reviews.",
        cadence="daily" if l1 else "weekly", severity=3 if l1 else 2,
        evidence=[signals.Evidence(kind="row", label="open items", value=len(items) + len(replies),
                                   ref={"reservation_id": rid})],
        owner_role="gm", subject_type="reservation", subject_id=str(rid), expires_at=plan["valid_until"],
    ))
    return {"open": len(items) + len(replies), "task_id": task.get("id"), "notified": told, "l1": l1}


def run_post(cur: Any, hotel: Any, plan: dict, stay: dict, *, now: datetime) -> dict:
    items = open_items(cur, hotel, stay, now)
    if not items:
        return {"open": 0}
    room = stay.get("room")
    fu = service_api.create_followup(
        cur, hotel.slug, kind="post_stay_recovery",
        summary=f"{len(items)} item(s) still open after check-out" + (f" (room {room})" if room else ""),
        guest_ref=stay["reservation_id"], owner_role="gm", due_at=now + timedelta(hours=24), source="journey_gate",
        room=room,
    )
    return {"open": len(items), "followup": fu.get("id")}


def run_gate(cur: Any, hotel: Any, plan: dict, *, now: datetime) -> dict:
    stay = executor.load_stay(cur, hotel.slug, plan["reservation_id"])
    if stay is None:
        executor.set_plan(cur, plan["id"], status="skipped", skip_reason="unknown_reservation", claimed_at=None)
        return {"skipped": True}
    out = run_pre(cur, hotel, plan, stay, now=now) if plan["step"] == "j6_pre" else run_post(cur, hotel, plan, stay, now=now)
    executor.set_plan(cur, plan["id"], status="done", claimed_at=None, last_attempt_at=now)
    return out
