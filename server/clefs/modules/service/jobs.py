"""
Service jobs (03 spec 1.7). Importing this module registers them, plus the
signal kinds (signals.py) and the metrics (metrics.py), so the manifest
needs one import line to load all three.

| Job                          | Schedule                    |
|------------------------------|-----------------------------|
| service.sla_sweep            | every minute                |
| service.scheduled_release    | every minute                |
| service.tracker_push         | every minute                |
| service.session_autologout   | every 5 minutes             |
| service.guest_confirm_expire | hourly                      |
| service.repeat_fault_detect  | hourly (and reopen spikes)  |
| service.daily_rollup         | 00:30 hotel time            |
| service.roster_labour_scan   | 05:00 hotel time            |
| service.proof_retention      | 03:10 hotel time            |
| service.translate_job (task) | queued when a job changes hands |

The sweep also closes completed jobs nobody answered for 30 minutes and
moves unaccepted ones at the supervisor step (05 spec, sla.py).

Every job is per hotel (the owning module must be on for that hotel) and
runs in its own transaction (JobCtx.cur). Each step that could fail on one
row runs in a savepoint, so one bad row never stalls a hotel's minute.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from clefs import jobs
from clefs.core import clock
from clefs.core import time as htime
from clefs.modules.service import metrics as _metrics  # noqa: F401  registers the metrics
from clefs.modules.service import settings as svc_settings
from clefs.modules.service import signals as svc_signals
from clefs.modules.service import sla, state

log = logging.getLogger("clefs.service.jobs")

# ─── Every minute ───


@jobs.scheduled("service.sla_sweep", jobs.every(minutes=1), timeout_s=50)
def sla_sweep(ctx: jobs.JobCtx) -> dict:
    out = dict(sla.sweep(ctx.cur, ctx.hotel_slug) or {})
    out["finished_signals"] = state.guarded(ctx.cur, "resolve_finished", svc_signals.resolve_finished, ctx.cur,
                                            ctx.hotel_slug) or 0
    return out


@jobs.scheduled("service.scheduled_release", jobs.every(minutes=1), timeout_s=50)
def scheduled_release(ctx: jobs.JobCtx) -> dict:
    """Scheduled requests (a wake-up call, turndown at 21:00) become new at
    their time, restart the SLA clock from there and are routed."""
    from clefs.modules.service import routing

    cur, slug = ctx.cur, ctx.hotel_slug
    cur.execute(
        """SELECT id FROM staff_requests WHERE hotel_slug = %s AND state = 'scheduled'
             AND scheduled_for IS NOT NULL AND scheduled_for <= %s ORDER BY scheduled_for LIMIT 200""",
        (slug, clock.now_ms()),
    )
    ids = [r["id"] for r in cur.fetchall()]
    released = 0
    for rid in ids:
        out = state.guarded(cur, f"release_{rid}", state.transition, cur, slug, rid, "release", state.SYSTEM)
        if out is None:
            continue
        released += 1
        state.guarded(cur, f"route_{rid}", routing.assign, cur, slug, rid)
    return {"released": released}


@jobs.scheduled("service.tracker_push", jobs.every(minutes=1), timeout_s=50)
def tracker_push(ctx: jobs.JobCtx) -> dict:
    """WhatsApp tracker updates for guests who opted in on /r/{token}. The
    body lives in whatsapp.py (imported lazily: it imports intake and state,
    and this file must stay importable on its own for the manifest)."""
    from clefs.modules.service import whatsapp

    return whatsapp.tracker_push_job(ctx)


# ─── Every 5 minutes ───

SHARED_KINDS = ("floor", "department", "desk")


@jobs.scheduled("service.session_autologout", jobs.every(minutes=5))
def session_autologout(ctx: jobs.JobCtx) -> dict:
    """End PIN sessions past shift end + grace, and sessions idle too long
    on shared devices (a personal device is left alone). Idle means neither
    a touch of last_activity_at nor an action by that person on that device
    (svc_request_events) within the window: a device merely polling its
    feed is not a person working. The legacy online flag and staff_shifts
    close with them, as a manual logout would."""
    from clefs.auth import devices as auth_devices

    cur, slug = ctx.cur, ctx.hotel_slug
    s = svc_settings.load(cur, slug)
    now = clock.now()
    cur.execute(
        """
        SELECT ds.id, ds.staff_member_id,
               CASE WHEN ds.planned_end_at IS NOT NULL AND ds.planned_end_at + %s < %s THEN 'shift_end'
                    ELSE 'idle' END AS reason
        FROM device_sessions ds JOIN devices d ON d.id = ds.device_id
        WHERE ds.hotel_slug = %s AND ds.ended_at IS NULL
          AND ((ds.planned_end_at IS NOT NULL AND ds.planned_end_at + %s < %s)
               OR (d.kind = ANY(%s) AND GREATEST(ds.last_activity_at, ds.started_at) < %s
                   AND NOT EXISTS (SELECT 1 FROM svc_request_events e
                                   WHERE e.hotel_slug = ds.hotel_slug AND e.device_id = ds.device_id
                                     AND e.actor_staff_id = ds.staff_member_id AND e.at >= %s)))
        """,
        (timedelta(minutes=s.shift_grace_min), now, slug, timedelta(minutes=s.shift_grace_min), now,
         list(SHARED_KINDS), now - timedelta(minutes=s.idle_logout_min), now - timedelta(minutes=s.idle_logout_min)),
    )
    ended = {"shift_end": 0, "idle": 0}
    for r in cur.fetchall():
        # close_session keeps is_online and the legacy staff_shifts in step.
        auth_devices.close_session(cur, slug, r["id"], r["staff_member_id"], reason=f"auto_{r['reason']}")
        ended[r["reason"]] += 1
    return ended


# ─── Hourly ───

CONFIRM_WINDOW_MS = 24 * 3600 * 1000
TOKEN_KEEP_DAYS = 30


@jobs.scheduled("service.guest_confirm_expire", jobs.every(hours=1))
def guest_confirm_expire(ctx: jobs.JobCtx) -> dict:
    """A guest who has not answered "was it resolved?" within 24 hours is
    "unknown" (the confirmed rate counts only real answers). Tracker tokens
    past expiry are dropped after 30 days. Also raises
    service.guest_unresolved for the last two hours' "not resolved" answers."""
    cur, slug = ctx.cur, ctx.hotel_slug
    cur.execute(
        """UPDATE staff_requests SET guest_confirmation = 'unknown'
           WHERE hotel_slug = %s AND state = 'completed' AND guest_confirmation IS NULL
             AND intake_version IS NOT NULL AND resolved_at IS NOT NULL AND resolved_at < %s""",
        (slug, clock.now_ms() - CONFIRM_WINDOW_MS),
    )
    expired = cur.rowcount
    cur.execute("DELETE FROM svc_guest_tokens WHERE hotel_slug = %s AND expires_at < %s",
                (slug, clock.now() - timedelta(days=TOKEN_KEEP_DAYS)))
    dropped = cur.rowcount
    unresolved = state.guarded(cur, "guest_unresolved", svc_signals.guest_unresolved, cur, ctx.hotel) or 0
    return {"expired": expired, "tokens_dropped": dropped, "unresolved_signals": unresolved}


@jobs.scheduled("service.repeat_fault_detect", jobs.every(hours=1))
def repeat_fault_detect(ctx: jobs.JobCtx) -> dict:
    cur, hotel = ctx.cur, ctx.hotel
    faults = state.guarded(cur, "repeat_faults", svc_signals.repeat_faults, cur, hotel) or 0
    spike = state.guarded(cur, "reopen_spike", svc_signals.reopen_spike, cur, hotel) or False
    return {"repeat_faults": faults, "reopen_spike": bool(spike)}


# ─── Daily ───

_ROLLUP_SQL = """
    INSERT INTO svc_daily_rollups (hotel_slug, local_date, department_key, floor, shift_key, staff_id, source, language,
        n, n_acked, ack_p50_s, ack_p90_s, resolve_p50_s, n_ack_breach, n_resolve_breach, n_reopened,
        n_confirmed_yes, n_confirmed_no, recovery_cost_minor, currency, computed_at)
    SELECT %(slug)s, %(day)s, COALESCE(r.department_key, ''), COALESCE(r.floor, ''), COALESCE(r.shift_key, ''),
        COALESCE(r.assigned_staff_id, 0), COALESCE(r.source, ''), COALESCE(r.language, ''),
        COUNT(*),
        COUNT(*) FILTER (WHERE r.intake_version IS NOT NULL AND r.acknowledged_at IS NOT NULL),
        (percentile_cont(0.5) WITHIN GROUP (ORDER BY (r.acknowledged_at - r."timestamp") / 1000.0)
            FILTER (WHERE r.intake_version IS NOT NULL AND r.acknowledged_at >= r."timestamp"))::INT,
        (percentile_cont(0.9) WITHIN GROUP (ORDER BY (r.acknowledged_at - r."timestamp") / 1000.0)
            FILTER (WHERE r.intake_version IS NOT NULL AND r.acknowledged_at >= r."timestamp"))::INT,
        (percentile_cont(0.5) WITHIN GROUP (ORDER BY (r.resolved_at - r."timestamp") / 1000.0)
            FILTER (WHERE r.state = 'completed' AND r.resolved_at >= r."timestamp"))::INT,
        COUNT(*) FILTER (WHERE r.sla_ack_breached),
        COUNT(*) FILTER (WHERE r.sla_resolve_breached),
        COUNT(*) FILTER (WHERE r.reopen_count > 0),
        COUNT(*) FILTER (WHERE r.guest_confirmation = 'yes'),
        COUNT(*) FILTER (WHERE r.guest_confirmation = 'no'),
        COALESCE(SUM(rc.cost), 0), %(currency)s, %(now)s
    FROM staff_requests r
    LEFT JOIN (
        SELECT c.request_id, SUM(a.value_minor) AS cost
        FROM svc_recovery_actions a JOIN svc_recovery_cases c ON c.id = a.case_id
        WHERE a.hotel_slug = %(slug)s AND a.status = 'given' AND a.currency = %(currency)s AND c.request_id IS NOT NULL
        GROUP BY c.request_id
    ) rc ON rc.request_id = r.id
    WHERE r.hotel_slug = %(slug)s AND r."timestamp" >= %(t0)s AND r."timestamp" < %(t1)s
    GROUP BY 3, 4, 5, 6, 7, 8
"""


def rollup_day(cur: Any, hotel: Any, day: Any) -> int:
    """(Re)compute one hotel-local day's rollup rows. -> rows written."""
    t0, t1 = htime.local_day_bounds(hotel, day)
    cur.execute("DELETE FROM svc_daily_rollups WHERE hotel_slug = %s AND local_date = %s", (hotel.slug, day))
    cur.execute(_ROLLUP_SQL, {"slug": hotel.slug, "day": day, "t0": htime.to_ms(t0), "t1": htime.to_ms(t1),
                              "currency": hotel.currency, "now": clock.now()})
    return cur.rowcount


@jobs.scheduled("service.daily_rollup", jobs.daily_at("00:30"))
def daily_rollup(ctx: jobs.JobCtx) -> dict:
    """Yesterday, hotel time (21:30 UTC is 01:30 in Dubai: tomorrow's day).
    The day before is recomputed too, for late syncs and reopens."""
    today = htime.local_date(ctx.hotel)
    days = [today - timedelta(days=1), today - timedelta(days=2)]
    return {str(d): rollup_day(ctx.cur, ctx.hotel, d) for d in days}


@jobs.scheduled("service.proof_retention", jobs.daily_at("03:10"))
def proof_retention(ctx: jobs.JobCtx) -> dict:
    """Completion photos are kept 7 days (05 spec, decision 9): drop the
    bytes (Postgres) or the object (the bucket, whose own lifecycle rule
    does the same) and keep the row as the record of the check."""
    from clefs.modules.service import proof

    return {"deleted": proof.purge(ctx.cur, ctx.hotel_slug)}


@jobs.task("service.translate_job", max_attempts=2, backoff_s=30)
def translate_job(ctx: jobs.JobCtx, payload: dict) -> dict:
    """A job's title and text in its holder's language, ahead of their
    first look (queued by staff_lang.prewarm when the holder changes)."""
    from clefs.modules.service import staff_lang

    from clefs.modules.service import constants as C

    row = state.load(ctx.cur, ctx.hotel_slug, int(payload["request_id"]))
    if row is None:
        return {"skipped": "gone"}
    if row.get("state") not in C.OPEN_STATES:
        # finished before the queue reached it (a replayed seed history):
        # nobody will read it, so no model call
        return {"skipped": "closed"}
    out = staff_lang.translate_job(ctx.hotel_slug, row, payload.get("lang") or "en", cur=ctx.cur, timeout_s=8.0)
    return {"translated": out.get("display_translated")}


@jobs.scheduled("service.roster_labour_scan", jobs.daily_at("05:00"))
def roster_labour_scan(ctx: jobs.JobCtx) -> dict:
    """Guidance, not legal advice: the next 7 days of the roster against the
    hotel's labour rules (weekly and daily hours). One service.labour_flag
    per person per week. The roster editor runs the full rule set on save."""
    cur, hotel = ctx.cur, ctx.hotel
    cur.execute("SELECT weekly_max_h, daily_max_h FROM svc_labour_rules WHERE hotel_slug = %s", (hotel.slug,))
    rules = cur.fetchone() or {"weekly_max_h": 48, "daily_max_h": 8}
    start = htime.local_date(hotel)
    t0, _ = htime.local_day_bounds(hotel, start)
    t1 = t0 + timedelta(days=7)
    cur.execute(
        """SELECT a.staff_member_id, s.name, a.business_date,
                  SUM(EXTRACT(EPOCH FROM (a.end_at - a.start_at)) / 3600.0) AS hours
           FROM svc_shift_assignments a JOIN staff_members s ON s.id = a.staff_member_id
           WHERE a.hotel_slug = %s AND a.status = 'planned' AND a.start_at >= %s AND a.start_at < %s
           GROUP BY 1, 2, 3""",
        (hotel.slug, t0, t1),
    )
    per: dict[int, dict] = {}
    for r in cur.fetchall():
        p = per.setdefault(r["staff_member_id"], {"name": r["name"], "week": 0.0, "long_days": []})
        p["week"] += float(r["hours"])
        if float(r["hours"]) > rules["daily_max_h"]:
            p["long_days"].append(str(r["business_date"]))
    flagged = 0
    for sid, p in per.items():
        reasons = []
        if p["week"] > rules["weekly_max_h"]:
            reasons.append(f"{p['week']:.0f} h this week (limit {rules['weekly_max_h']})")
        if p["long_days"]:
            reasons.append(f"over {rules['daily_max_h']} h on {', '.join(p['long_days'][:3])}")
        if not reasons:
            continue
        flagged += 1
        state.guarded(cur, f"labour_{sid}", svc_signals.raise_signal, cur, hotel.slug, "service.labour_flag",
                      f"service.labour_flag:{sid}:{start.isocalendar()[0]}-{start.isocalendar()[1]}",
                      f"{p['name']}: {'; '.join(reasons)}", severity=2, subject_type="staff_member", subject_id=str(sid))
    return {"people": len(per), "flagged": flagged}


