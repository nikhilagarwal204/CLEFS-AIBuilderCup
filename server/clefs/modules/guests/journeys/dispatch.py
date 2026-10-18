"""
The dispatcher (04 spec 3.7 steps 1-3): `guests.journey_dispatch`, every
minute per hotel.

1. Release rows left `claimed` for 10 minutes (a crashed tick).
2. Follow proposed rows' actions: rejected -> skipped(rejected), expired ->
   skipped(approval_expired), observed -> observed, cancelled -> cancelled.
3. Claim due rows (FOR UPDATE SKIP LOCKED, batch 200), highest collision
   priority first (J1 > J4 > J5 > J7 > J2 > J3 > J8), then:
   - J6 gates run in place (gates.py) and end `done`;
   - a row whose action is already approved (a deferred send, a 131049
     re-send) is sent under that SAME action, with no new approval;
   - otherwise the guards run: skip, defer, or create_action and let the
     dial decide (off: skipped dial_off; observe: observed; suggest or
     approve: proposed; auto: executed right away).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from clefs import jobs
from clefs.core import actions, clock
from clefs.core.hotels import get_hotel
from clefs.modules.guests import identity
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import executor, guards, steps

log = logging.getLogger("clefs.guests.journeys.dispatch")

BATCH = 200
APPROVED_ACTION = ("approved", "assigned", "in_progress", "done", "verified", "not_verified")


def release_stale(cur: Any, hotel_slug: str, now: datetime) -> int:
    cur.execute(
        """
        UPDATE gst_journey_plans SET status = CASE WHEN attempt > 0 OR error_code IS NOT NULL OR skip_reason IS NOT NULL
                                              THEN 'deferred' ELSE 'planned' END,
               claimed_at = NULL, updated_at = %s
        WHERE hotel_slug = %s AND status = 'claimed' AND claimed_at < %s
        """,
        (now, hotel_slug, now - steps.CLAIM_TIMEOUT),
    )
    return cur.rowcount


ACTION_TO_PLAN = {
    "rejected": ("skipped", "rejected"),
    "expired": ("skipped", "approval_expired"),
    "observed": ("observed", None),
    "cancelled": ("cancelled", "action_cancelled"),
    "failed": ("failed", "execution_failed"),
}


def follow_proposed(cur: Any, hotel_slug: str, now: datetime) -> int:
    """3.3 plan states: proposed -> skipped (rejected | approval_expired)."""
    cur.execute(
        """
        SELECT p.id, a.status AS action_status FROM gst_journey_plans p JOIN actions a ON a.id = p.action_id
        WHERE p.hotel_slug = %s AND p.status = 'proposed' AND a.status = ANY(%s)
        """,
        (hotel_slug, list(ACTION_TO_PLAN)),
    )
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        status, reason = ACTION_TO_PLAN[r["action_status"]]
        cur.execute("UPDATE gst_journey_plans SET status = %s, skip_reason = COALESCE(%s, skip_reason), updated_at = %s "
                    "WHERE id = %s", (status, reason, now, r["id"]))
    return len(rows)


def claim(cur: Any, hotel_slug: str, now: datetime, *, limit: int = BATCH) -> list[dict]:
    cur.execute(
        f"""
        UPDATE gst_journey_plans SET status = 'claimed', claimed_at = %s, updated_at = %s
        WHERE id IN (SELECT id FROM gst_journey_plans
                     WHERE hotel_slug = %s AND status IN ('planned', 'deferred') AND due_at <= %s
                     ORDER BY due_at LIMIT %s FOR UPDATE SKIP LOCKED)
        RETURNING {executor.planner.PLAN_COLS}
        """,
        (now, now, hotel_slug, now, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    return sorted(rows, key=lambda p: (steps.step_rank(p["step"]), p["due_at"], p["id"]))


# ─── One plan ───


def _action_status(cur: Any, action_id: int | None) -> str | None:
    if not action_id:
        return None
    cur.execute("SELECT status FROM actions WHERE id = %s", (action_id,))
    row = cur.fetchone()
    return row["status"] if row else None


def _title(plan: dict, stay: dict | None) -> str:
    ref = (stay or {}).get("booking_ref") or plan["reservation_id"]
    return f"{steps.LABELS[plan['step']]} · booking {ref}"


def dispatch_one(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, plan: dict, *, now: datetime) -> str:
    """Handle one claimed row; returns what happened (for the job report)."""
    step = plan["step"]
    if step in steps.GATE_STEPS:
        from clefs.modules.guests.journeys import gates

        gates.run_gate(cur, hotel, plan, now=now)
        return "gate"
    if _action_status(cur, plan["action_id"]) in APPROVED_ACTION:
        return executor.send_plan(cur, hotel, plan, action_id=plan["action_id"], attempt=max(1, plan["attempt"]),
                                  now=now)["status"]
    stay = executor.load_stay(cur, hotel.slug, plan["reservation_id"])
    v = guards.check(cur, hotel, cfg, plan, stay, now=now)
    if v.skip:
        executor.set_plan(cur, plan["id"], status="skipped", skip_reason=v.reason, claimed_at=None)
        return "skipped"
    if v.defer_to is not None:
        executor.set_plan(cur, plan["id"], status="deferred", due_at=v.defer_to, skip_reason=v.reason, claimed_at=None)
        return "deferred"
    offer = v.offer or {}
    if step == "j8" and not plan.get("direct_code"):
        code = identity.issue_direct_code(
            cur, hotel.slug, valid_to=offer.get("valid_to"), discount_pct=offer.get("discount_pct"),
            plan_id=plan["id"], offer_id=offer.get("id"), prefix=offer.get("code_prefix"),
        )
        plan = executor.set_plan(cur, plan["id"], direct_code=code["code"], offer_id=offer.get("id"))
    choice = v.resolution.choice if v.resolution else None
    params = {
        "plan_id": plan["id"], "reservation_id": plan["reservation_id"], "step": step,
        "template_id": choice.template["id"] if choice else None,
        "contact_point_id": choice.point["id"] if choice else None,
        "channel": choice.channel if choice else None,
        "offer_id": offer.get("id"), "offer_price_minor": offer.get("price_minor"),
    }
    a = actions.create_action(
        cur, hotel.slug, type=steps.ACTION_TYPES[step], title=_title(plan, stay), params=params,
        expires_at=plan["valid_until"], actor=actions.SYSTEM, dedupe_key=f"gst:plan:{plan['id']}",
    )
    if a.get("id") is None:
        executor.set_plan(cur, plan["id"], status="skipped", skip_reason="dial_off", claimed_at=None)
        return "dial_off"
    status = a["status"]
    if status == "observed":
        executor.set_plan(cur, plan["id"], status="observed", action_id=a["id"], claimed_at=None,
                          skip_reason="template_missing" if choice is None else "dial_observe")
        return "observed"
    if status == "proposed":
        executor.set_plan(cur, plan["id"], status="proposed", action_id=a["id"], claimed_at=None)
        return "proposed"
    executor.set_plan(cur, plan["id"], action_id=a["id"])
    if status == "approved":
        # The dial approved it: run the executor now rather than a minute
        # later (the queued core.actions.execute then finds it executed).
        actions.execute_action(cur, a["id"])
    current = executor.load_plan(cur, plan["id"])
    return current["status"] if current else "missing"


def dispatch_hotel(cur: Any, hotel_slug: str, *, now: datetime | None = None, limit: int = BATCH) -> dict:
    now = now or clock.now()
    hotel = get_hotel(cur, hotel_slug)
    cfg = gsettings.load(cur, hotel_slug)
    report: dict[str, int] = {"released": release_stale(cur, hotel_slug, now),
                              "followed": follow_proposed(cur, hotel_slug, now)}
    for plan in claim(cur, hotel_slug, now, limit=limit):
        cur.execute("SAVEPOINT gst_dispatch_one")
        try:
            what = dispatch_one(cur, hotel, cfg, plan, now=now)
            cur.execute("RELEASE SAVEPOINT gst_dispatch_one")
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT gst_dispatch_one")
            cur.execute("RELEASE SAVEPOINT gst_dispatch_one")
            log.exception("journey plan %s failed to dispatch", plan["id"])
            executor.set_plan(cur, plan["id"], status="failed", error_code=type(exc).__name__[:40], claimed_at=None)
            what = "error"
        report[what] = report.get(what, 0) + 1
    return report


@jobs.scheduled("guests.journey_dispatch", jobs.every(minutes=1), timeout_s=55)
def dispatch_job(ctx: jobs.JobCtx) -> dict:
    return dispatch_hotel(ctx.cur, ctx.hotel_slug)
