"""
The planner (04 spec 3.3): turns stay events into journey plan rows.

- `guests.journeys` (@on_stay_event) consumes core/stays events, idempotent
  on stay_events.id (gst_journey_events).
- `guests.journey_plan_reconcile` (04:30 hotel time) repairs missed events.

Rows are cancelled and re-created, never moved between reservations, so
(hotel_slug, reservation_id, step, seq) stays unique among live rows. Every
step that does not apply is still written, as `skipped` with a reason, so
the console funnel can say why a guest got nothing ("skipped by reason").
Eligibility (contacts, consent, templates) is NOT decided here: the
dispatcher checks it at send time, because consent and templates change.
"""

from __future__ import annotations

import importlib
import logging
from datetime import date, datetime, time, timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core.hotels import get_hotel
from clefs.core.time import local_date
from clefs.db import Json
from clefs.modules.guests import linking, stays_port, timeutil
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import steps

log = logging.getLogger("clefs.guests.journeys.planner")

CONSUMER = "guests.journeys"
CANCELLED_STATUSES = ("CANCELLED", "NO_SHOW")

# ─── Timing ───


def _t(hhmm: str) -> time:
    return gsettings.parse_hhmm(hhmm)


def _local_date(hotel: Any, ts: datetime | None) -> date | None:
    return timeutil.local(hotel, ts).date() if ts else None


def timing(hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, step: str, seq: int = 1, *,
           now: datetime, event_at: datetime | None = None) -> tuple[datetime | None, datetime | None, str | None, bool]:
    """(due_at, valid_until, skip_reason, checkin_time_inferred) for one step
    of one stay (3.1). A skip reason means the row is written as skipped."""
    at = timeutil.at_local
    ci = stays_port.as_date(stay.get("check_in"))
    co = stays_port.as_date(stay.get("check_out"))
    if ci is None or co is None:
        return None, None, "no_dates", False
    booked = stays_port.as_datetime(stay.get("booked_at")) or now
    reason: str | None = None
    inferred = False
    if step == "j1":
        due, valid = max(now, booked), at(hotel, ci - timedelta(days=1), time(21, 0))
        if (stay.get("source") or "").upper() == "WALKIN":
            reason = "walkin"
        elif at(hotel, ci, cfg.checkin_t) - booked < steps.LATE_BOOKING:
            reason = "late_booking"  # J4 covers it
    elif step == "j2":
        due, valid = at(hotel, ci - timedelta(days=7), time(10, 0)), at(hotel, ci - timedelta(days=2), time(0, 0))
        lead = (ci - (_local_date(hotel, booked) or ci)).days
        if lead <= cfg.j2_min_lead_days:
            reason = "lead_time"
    elif step == "j3":
        due, valid = at(hotel, ci - timedelta(days=3), time(11, 0)), at(hotel, ci - timedelta(days=1), time(0, 0))
        if (stay.get("segment") or "").upper() in steps.NO_J3_SEGMENTS:
            reason = "segment"
    elif step == "j4":
        due = at(hotel, ci - timedelta(days=1), time(16, 0))
        if booked > due:
            due = at(hotel, ci, time(9, 0))
        valid = at(hotel, ci, cfg.checkin_t)
    elif step == "j5":
        base = stays_port.as_datetime(stay.get("checked_in_at"))
        if base is None:
            base, inferred = at(hotel, ci, cfg.checkin_t), True
        w = cfg.utility_window
        due = timeutil.next_window_start(hotel, base + timedelta(hours=cfg.j5_offset_h), w.t0, w.t1)
        valid = at(hotel, co, time(12, 0))
    elif step == "j6_pre":
        due = at(hotel, co, cfg.checkout_t) - timedelta(hours=3)
        valid = at(hotel, co, time(23, 59))
    elif step == "j6_post":
        due = event_at or stays_port.as_datetime(stay.get("checked_out_at")) or now
        valid = due + timedelta(hours=24)
    elif step == "j7":
        out_at = stays_port.as_datetime(stay.get("checked_out_at")) or at(hotel, co, cfg.checkout_t)
        due = at(hotel, _local_date(hotel, out_at) + timedelta(days=cfg.j7_offset_days), time(11, 0))
        valid = due + timedelta(days=7)
    elif step == "j8":
        offsets = cfg.j8_offsets
        if seq > len(offsets):
            return None, None, "no_offset", False
        due = at(hotel, co + timedelta(days=offsets[seq - 1]), time(11, 0))
        valid = due + timedelta(days=7)
    else:
        raise ValueError(f"unknown step {step}")
    if reason is None and step not in cfg.enabled_steps:
        reason = "step_disabled"
    if reason is None and valid <= now:
        reason = "too_late"
    if reason is None and due > valid:
        reason = "too_late"
    return due, valid, reason, inferred


# ─── Rows ───

PLAN_COLS = (
    "id, hotel_slug, reservation_id, step, seq, due_at, valid_until, status, skip_reason, channel, contact_point_id, "
    "template_id, offer_id, direct_code, action_id, outbound_message_id, wamid, attempt, error_code, category_at_send, "
    "plan_version, checkin_time_inferred, claimed_at, last_attempt_at, created_at, updated_at"
)


def plans_for(cur: Any, hotel_slug: str, reservation_id: int, *, live_only: bool = True) -> list[dict]:
    cur.execute(
        f"SELECT {PLAN_COLS} FROM gst_journey_plans WHERE hotel_slug = %s AND reservation_id = %s"
        + (" AND status <> 'cancelled'" if live_only else "") + " ORDER BY due_at, step, seq",
        (hotel_slug, reservation_id),
    )
    return [dict(r) for r in cur.fetchall()]


def plan_step(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, step: str, seq: int = 1, *,
              now: datetime, event_at: datetime | None = None, force_reason: str | None = None) -> int | None:
    """Write one plan row unless a live one exists (idempotent). Returns the
    new id, or None when a live row was already there."""
    due, valid, reason, inferred = timing(hotel, cfg, stay, step, seq, now=now, event_at=event_at)
    reason = force_reason or reason
    due = due or now
    valid = valid or now
    if reason is None and due < now:
        due = now  # late planning (reconcile, a late booking): send as soon as the guards allow
    cur.execute(
        """
        INSERT INTO gst_journey_plans (hotel_slug, reservation_id, step, seq, due_at, valid_until, status, skip_reason,
                                       checkin_time_inferred, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, reservation_id, step, seq) WHERE status <> 'cancelled' DO NOTHING
        RETURNING id
        """,
        (hotel.slug, stay["reservation_id"], step, seq, due, valid, "skipped" if reason else "planned", reason,
         inferred, now, now),
    )
    row = cur.fetchone()
    return int(row["id"]) if row else None


def cancel_unsent(cur: Any, hotel_slug: str, reservation_id: int, *, reason: str,
                  steps_: tuple[str, ...] | None = None) -> list[int]:
    """Every unsent row (planned, deferred, claimed, proposed) becomes
    cancelled. A proposed row's action is cancelled too, so nobody approves
    a message for a stay that no longer exists."""
    from clefs.core import actions

    sql = (
        "UPDATE gst_journey_plans SET status = 'cancelled', skip_reason = %s, updated_at = %s "
        "WHERE hotel_slug = %s AND reservation_id = %s AND status = ANY(%s)"
    )
    params: list[Any] = [reason, clock.now(), hotel_slug, reservation_id, list(steps.UNSENT)]
    if steps_:
        sql += " AND step = ANY(%s)"
        params.append(list(steps_))
    cur.execute(sql + " RETURNING id, action_id", params)
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        if r["action_id"]:
            cur.execute("SELECT status FROM actions WHERE id = %s", (r["action_id"],))
            a = cur.fetchone()
            if a and a["status"] in ("proposed", "approved", "snoozed", "assigned"):
                actions.cancel(cur, r["action_id"], actor=actions.SYSTEM, reason="superseded")
    return [r["id"] for r in rows]


# ─── Stay chains (04 spec D1: stay_chain_id = the chain's first reservation) ───


def _chain_members(cur: Any, hotel_slug: str, chain_id: int) -> list[dict]:
    """Every reservation of a chain: the snapshots we hold, plus core
    reservations read under a savepoint (a successor may exist in core
    before its own created event reached us)."""
    cur.execute(
        "SELECT reservation_id, check_in, check_out, status FROM gst_stays "
        "WHERE hotel_slug = %s AND stay_chain_id = %s AND reservation_id IS NOT NULL",
        (hotel_slug, chain_id),
    )
    out = {int(r["reservation_id"]): dict(r) for r in cur.fetchall()}
    cur.execute("SELECT to_regclass('public.reservations') IS NOT NULL AS ok")
    if cur.fetchone()["ok"]:
        cur.execute("SAVEPOINT gst_chain")
        try:
            cur.execute(
                "SELECT id AS reservation_id, check_in, check_out, status FROM reservations "
                "WHERE hotel_slug = %s AND (stay_chain_id = %s OR id = %s)",
                (hotel_slug, chain_id, chain_id),
            )
            for r in cur.fetchall():
                out.setdefault(int(r["reservation_id"]), dict(r))
            cur.execute("RELEASE SAVEPOINT gst_chain")
        except Exception:
            cur.execute("ROLLBACK TO SAVEPOINT gst_chain")
            cur.execute("RELEASE SAVEPOINT gst_chain")
    return list(out.values())


def is_continuation(stay: dict) -> bool:
    chain = stay.get("stay_chain_id")
    return chain is not None and int(chain) != int(stay["reservation_id"])


def predecessors(cur: Any, hotel_slug: str, stay: dict) -> list[int]:
    if stay.get("stay_chain_id") is None:
        return []
    ci = stays_port.as_date(stay.get("check_in"))
    return [m["reservation_id"] for m in _chain_members(cur, hotel_slug, int(stay["stay_chain_id"]))
            if m["reservation_id"] != stay["reservation_id"] and (ci is None or stays_port.as_date(m["check_in"]) < ci)]


def has_successor(cur: Any, hotel_slug: str, stay: dict) -> bool:
    """A later, live reservation continues this stay: the guest extended, so
    departure steps wait for the final check-out."""
    if stay.get("stay_chain_id") is None:
        return False
    ci = stays_port.as_date(stay.get("check_in"))
    for m in _chain_members(cur, hotel_slug, int(stay["stay_chain_id"])):
        if m["reservation_id"] == stay["reservation_id"] or (m.get("status") or "").upper() in CANCELLED_STATUSES:
            continue
        if ci is None or stays_port.as_date(m["check_in"]) >= ci:
            return True
    return False


# ─── Event handlers ───


def plan_created(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, *, now: datetime) -> list[int]:
    """3.3 step 2. A chain continuation gets no J1-J4 (written as skipped
    chain_continued); its predecessors' unsent departure steps are cancelled
    and this reservation plans its own J6_pre."""
    made: list[int] = []
    if is_continuation(stay):
        for pred in predecessors(cur, hotel.slug, stay):
            cancel_unsent(cur, hotel.slug, pred, reason="chain_continued",
                          steps_=("j5", "j6_pre", "j6_post", "j7", "j8"))
        for step in ("j1", "j2", "j3", "j4"):
            made.append(plan_step(cur, hotel, cfg, stay, step, now=now, force_reason="chain_continued"))
    else:
        for step in ("j1", "j2", "j3", "j4"):
            made.append(plan_step(cur, hotel, cfg, stay, step, now=now))
    if not has_successor(cur, hotel.slug, stay):
        made.append(plan_step(cur, hotel, cfg, stay, "j6_pre", now=now))
    return [m for m in made if m]


def plan_checked_in(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, *, now: datetime) -> list[int]:
    if (stay.get("status") or "").upper() == "CHECKED_OUT":
        return []
    made = plan_step(cur, hotel, cfg, stay, "j5", now=now)
    return [made] if made else []


def plan_checked_out(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, *, now: datetime,
                     event_at: datetime | None = None) -> list[int]:
    """3.3 step 6: J6_post, J7 and J8 (every offset), only when no later
    reservation continues the chain; an unsent J5 is cancelled."""
    cancel_unsent(cur, hotel.slug, stay["reservation_id"], reason="checked_out", steps_=("j5",))
    if has_successor(cur, hotel.slug, stay):
        return []
    made = [plan_step(cur, hotel, cfg, stay, "j6_post", now=now, event_at=event_at),
            plan_step(cur, hotel, cfg, stay, "j7", now=now)]
    for seq in range(1, len(cfg.j8_offsets) + 1):
        made.append(plan_step(cur, hotel, cfg, stay, "j8", seq, now=now))
    return [m for m in made if m]


def replan_dates(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, stay: dict, *, now: datetime) -> dict:
    """3.3 step 3: recompute planned/deferred rows (plan_version + 1); rows
    that no longer fit are skipped `dates_changed`. A J2 skipped for lead
    time comes back when the new dates give enough lead."""
    moved = skipped = revived = 0
    for p in plans_for(cur, hotel.slug, stay["reservation_id"]):
        due, valid, reason, inferred = timing(hotel, cfg, stay, p["step"], p["seq"], now=now)
        if p["status"] in ("planned", "deferred"):
            if reason:
                cur.execute(
                    "UPDATE gst_journey_plans SET status = 'skipped', skip_reason = 'dates_changed', updated_at = %s WHERE id = %s",
                    (now, p["id"]),
                )
                skipped += 1
            else:
                cur.execute(
                    "UPDATE gst_journey_plans SET due_at = %s, valid_until = %s, plan_version = plan_version + 1, "
                    "checkin_time_inferred = %s, updated_at = %s WHERE id = %s",
                    (max(due, now), valid, inferred, now, p["id"]),
                )
                moved += 1
        elif p["status"] == "skipped" and p["skip_reason"] in ("lead_time", "late_booking") and reason is None:
            cur.execute(
                "UPDATE gst_journey_plans SET status = 'planned', skip_reason = NULL, due_at = %s, valid_until = %s, "
                "plan_version = plan_version + 1, updated_at = %s WHERE id = %s",
                (max(due, now), valid, now, p["id"]),
            )
            revived += 1
    co = stays_port.as_date(stay.get("check_out"))
    if co is not None:
        cur.execute(
            "UPDATE gst_stay_prefs SET purge_after = %s WHERE hotel_slug = %s AND reservation_id = %s",
            (timeutil.at_local(hotel, co + timedelta(days=30), time(23, 59)), hotel.slug, stay["reservation_id"]),
        )
    return {"moved": moved, "skipped": skipped, "revived": revived}


def cancel_stay(cur: Any, hotel_slug: str, reservation_id: int, *, reason: str) -> dict:
    """3.3 step 4: every unsent row is cancelled; a sent J3 stays sent; a
    pending guests.offer_accept task for the stay is cancelled."""
    from clefs.core import actions

    ids = cancel_unsent(cur, hotel_slug, reservation_id, reason=reason)
    cur.execute(
        "SELECT id FROM actions WHERE hotel_slug = %s AND type = %s AND params->>'reservation_id' = %s "
        "AND status IN ('proposed', 'approved', 'assigned', 'in_progress', 'snoozed')",
        (hotel_slug, steps.OFFER_ACCEPT, str(reservation_id)),
    )
    tasks = [r["id"] for r in cur.fetchall()]
    for a in tasks:
        actions.cancel(cur, a, actor=actions.SYSTEM, reason="superseded")
    return {"cancelled": len(ids), "tasks_cancelled": len(tasks)}


# ─── Consumer ───


def _stay(cur: Any, hotel_slug: str, reservation_id: int, ev: dict | None = None) -> dict | None:
    """Snapshot the reservation (link_reservation also mirrors its contacts
    and links a consented profile) and return the gst_stays row. When core
    cannot be read, the event's own `changes` are the fallback."""
    stay = linking.link_reservation(cur, hotel_slug, reservation_id)
    if stay is not None:
        return stay
    if ev is None:
        return None
    ch = ev.get("changes") or {}

    def new(k: str) -> Any:
        v = ch.get(k)
        return v[1] if isinstance(v, (list, tuple)) and len(v) == 2 else None

    if new("check_in") is None:
        return None
    return {"reservation_id": reservation_id, "check_in": new("check_in"), "check_out": new("check_out"),
            "status": new("status"), "checked_in_at": new("checked_in_at"), "checked_out_at": new("checked_out_at"),
            "stay_chain_id": None, "booked_at": None, "source": None, "segment": None}


def handle_event(cur: Any, ev: dict) -> dict:
    """Apply one stay event. Idempotent on the event id."""
    hotel_slug, rid = ev["hotel_slug"], int(ev["reservation_id"])
    cur.execute(
        "INSERT INTO gst_journey_events (event_id, hotel_slug, reservation_id, event, processed_at) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
        (int(ev["id"]), hotel_slug, rid, ev["event"], clock.now()),
    )
    if cur.fetchone() is None:
        return {"duplicate": True}
    hotel = get_hotel(cur, hotel_slug)
    cfg = gsettings.load(cur, hotel_slug)
    now = clock.now()
    event = ev["event"]
    result: dict[str, Any] = {"event": event}
    if event in ("cancelled", "no_show"):
        result.update(cancel_stay(cur, hotel_slug, rid, reason=event))
    else:
        stay = _stay(cur, hotel_slug, rid, ev)
        if stay is None:
            result["unknown_reservation"] = True
        elif event == "created":
            result["planned"] = len(plan_created(cur, hotel, cfg, stay, now=now))
        elif event == "dates_changed":
            result.update(replan_dates(cur, hotel, cfg, stay, now=now))
        elif event == "checked_in":
            result["planned"] = len(plan_checked_in(cur, hotel, cfg, stay, now=now))
        elif event == "checked_out":
            result["planned"] = len(plan_checked_out(cur, hotel, cfg, stay, now=now,
                                                     event_at=stays_port.as_datetime(ev.get("occurred_at"))))
        # contact_changed: the snapshot above re-mirrored the contacts; the
        # channel is resolved at dispatch, so nothing is re-planned (3.3 step 7).
    cur.execute("UPDATE gst_journey_events SET result = %s WHERE event_id = %s", (Json(result), int(ev["id"])))
    return result


def _register_consumer() -> bool:
    """@on_stay_event lives in core/stays (the domain lane); a database
    without the package simply has no events, and the nightly reconcile
    still plans from the reservations it can read."""
    try:
        stays = importlib.import_module("clefs.core.stays")
    except ModuleNotFoundError as exc:
        if exc.name != "clefs.core.stays":
            raise
        return False
    deco = getattr(stays, "on_stay_event", None)
    if deco is None:
        return False
    deco(CONSUMER)(on_stay_event)
    return True


def on_stay_event(cur: Any, ev: dict) -> None:
    handle_event(cur, dict(ev))


REGISTERED = _register_consumer()


# ─── Nightly repair (3.3 step 8) ───


def reconcile(cur: Any, hotel_slug: str, *, today: date | None = None) -> dict:
    """Re-plan every reservation checking out from yesterday on, from its
    current status. Inserts are idempotent, so this only fills gaps; a PMS
    that never reports checked_in_at gets check-in day + standard time
    (checkin_time_inferred)."""
    hotel = get_hotel(cur, hotel_slug)
    cfg = gsettings.load(cur, hotel_slug)
    today = today or local_date(hotel)
    now = clock.now()
    counts = {"reservations": 0, "planned": 0, "cancelled": 0}
    for rid in stays_port.backend().reservations_since(cur, hotel_slug, check_out_from=today - timedelta(days=1)):
        stay = linking.link_reservation(cur, hotel_slug, rid)
        if stay is None:
            continue
        counts["reservations"] += 1
        status = (stay.get("status") or "").upper()
        if status in CANCELLED_STATUSES:
            counts["cancelled"] += cancel_stay(cur, hotel_slug, rid, reason=status.lower())["cancelled"]
            continue
        if status == "CHECKED_OUT":
            counts["planned"] += len(plan_checked_out(cur, hotel, cfg, stay, now=now))
            continue
        if not plans_for(cur, hotel_slug, rid, live_only=False):
            counts["planned"] += len(plan_created(cur, hotel, cfg, stay, now=now))
        if status == "IN_HOUSE":
            counts["planned"] += len(plan_checked_in(cur, hotel, cfg, stay, now=now))
    return counts


@jobs.scheduled("guests.journey_plan_reconcile", jobs.daily_at("04:30"), timeout_s=240)
def reconcile_job(ctx: jobs.JobCtx) -> dict:
    return reconcile(ctx.cur, ctx.hotel_slug)
