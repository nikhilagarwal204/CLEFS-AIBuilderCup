"""
Escalation ladder (02 spec 6.3; Canon 7: Brief owns reminders and escalation).

An approved action is overdue when its deadline passed and it is still
approved, assigned or in progress. Every 15 minutes (`brief.escalate`):

    level 1  at the deadline   remind the assignee (WhatsApp in the window,
                               else SMS to the staff phone, else email) and
                               log a `remind` decision on the action
    level 2  deadline + 4 h    notify the GM (owner when there is no GM)
    level 3  deadline + 24 h   pin it in the next brief as "Overdue", notify
                               the owner, raise `brief.action.overdue`

Each (action, level) fires once (brf_escalations). A worker that was down
catches up by recording every level it skipped but notifying only for the
highest one, so nobody gets three messages at once. A moved deadline
restarts the ladder. Transitions stay in clefs.core.actions: the ladder only
reminds (no state change) and updates the counters the foundation keeps on
the actions row for Brief (escalation_level, escalated_at).

Quiet hours (quiet.py): a person inside their window gets a "deferred_quiet"
entry instead of a message, unless the item is critical (severity 4 signal);
flush_deferred sends it on the first pass after the window ends, or drops it
when the action closed or its deadline moved meanwhile.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from clefs import jobs
from clefs.core import actions as core_actions
from clefs.core import clock
from clefs.core import signals as core_signals
from clefs.core.hotels import Hotel, get_hotel
from clefs.db import Json
from clefs.modules.brief import quiet

log = logging.getLogger("clefs.brief.escalate")

LEVEL_AFTER: dict[int, timedelta] = {1: timedelta(0), 2: timedelta(hours=4), 3: timedelta(hours=24)}
OVERDUE_KIND = "brief.action.overdue"
OPEN_WORK = ("approved", "assigned", "in_progress")

core_signals.register_signal_kind(
    OVERDUE_KIND, label="Approved action overdue", default_owner_role="owner", section="service_risk",
    cadence="daily", icon="brief",
)


def level_for(due_at: datetime, now: datetime) -> int:
    """The ladder level an action with this deadline has reached at `now`."""
    level = 0
    for lv, after in LEVEL_AFTER.items():
        if now >= due_at + after:
            level = lv
    return level


# ─── Recipients ───


def _members(cur: Any, hotel_slug: str, roles: list[str]) -> list[dict]:
    cur.execute(
        """
        SELECT u.id, u.name, u.email, u.phone_e164, u.locale, m.role
        FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND m.is_active AND u.is_active AND m.role = ANY(%s) ORDER BY m.id
        """,
        (hotel_slug, roles),
    )
    return [dict(r) for r in cur.fetchall()]


def _user(cur: Any, user_id: int) -> dict | None:
    cur.execute("SELECT id, name, email, phone_e164, locale FROM users WHERE id = %s AND is_active", (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _staff(cur: Any, hotel_slug: str, staff_id: int) -> dict | None:
    cur.execute(
        "SELECT id, name, phone_number FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, hotel_slug)
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _assignees(cur: Any, hotel_slug: str, action: dict) -> list[dict]:
    """Level 1 goes to whoever holds the task: the assigned user, the
    assigned staff member, else the members of the assigned or owner role."""
    if action.get("assignee_user_id"):
        u = _user(cur, action["assignee_user_id"])
        return [u] if u else []
    if action.get("assignee_staff_id"):
        s = _staff(cur, hotel_slug, action["assignee_staff_id"])
        if s:
            return [{"id": None, "staff_id": s["id"], "name": s["name"], "phone_e164": s.get("phone_number"),
                     "email": None, "locale": "en"}]
    role = action.get("assignee_role") or action.get("owner_role")
    return _members(cur, hotel_slug, [role]) if role else []


def recipients_for(cur: Any, hotel_slug: str, action: dict, level: int) -> list[dict]:
    if level == 1:
        return _assignees(cur, hotel_slug, action) or _members(cur, hotel_slug, ["gm"])
    if level == 2:
        return _members(cur, hotel_slug, ["gm"]) or _members(cur, hotel_slug, ["owner"])
    return _members(cur, hotel_slug, ["owner"])


# ─── Messages ───


def _texts(hotel: Hotel, action: dict, level: int) -> tuple[str, str]:
    """(subject, body). No links: SMS to +971 may not carry URLs, and the
    same words go to every channel."""
    local = action["due_at"].astimezone(hotel.tz)
    due = f"{local.day} {local.strftime('%b')} {local.strftime('%H:%M')}"
    title = str(action["title"])[:120]
    if level == 1:
        return (f"Reminder: {title}", f"Clefs reminder: \"{title}\" was due {due}. Please finish it or update it in Clefs.")
    if level == 2:
        return (f"Overdue 4 h: {title}", f"Clefs: \"{title}\" is 4 hours past its deadline ({due}) and not done yet.")
    return (f"Overdue 24 h: {title}",
            f"Clefs: \"{title}\" is a day past its deadline ({due}). It is pinned in the next brief as overdue.")


def _notify(cur: Any, hotel: Hotel, action: dict, level: int, people: list[dict], *,
            now: datetime | None = None, windows: list | None = None, critical: bool = True) -> list[dict]:
    from clefs import notify

    subject, body = _texts(hotel, action, level)
    due_key = int(action["due_at"].timestamp())
    out = []
    for p in people:
        who = f"u{p['id']}" if p.get("id") else f"s{p.get('staff_id')}"
        until = None if critical or not windows else quiet.quiet_until(hotel, windows, p.get("id"), now or clock.now())
        if until is not None:
            out.append({"to": who, "user_id": p["id"], "channel": None, "ok": False, "status": "deferred_quiet",
                        "until": until.isoformat()})
            continue
        rcpt = notify.Recipient(phone=p.get("phone_e164"), email=p.get("email"), name=p.get("name"),
                                user_id=p.get("id"), locale=p.get("locale") or "en")
        msg = notify.Message(
            purpose="brief.escalation", hotel_slug=hotel.slug, category="utility", whatsapp_text=body, sms_text=body,
            email_subject=subject, email_text=body, email_html=f"<p>{notify_escape(body)}</p>",
            idempotency_key=f"brief:esc:{action['id']}:{level}:{due_key}:{who}", action_id=action["id"],
        )
        cur.execute("SAVEPOINT brf_esc")
        try:
            res = notify.notify(rcpt, msg, cur=cur)
            cur.execute("RELEASE SAVEPOINT brf_esc")
            out.append({"to": who, "channel": res.channel, "ok": bool(res.ok), "status": res.status})
        except Exception as e:  # noqa: BLE001 - an unreachable person must not stop the ladder
            cur.execute("ROLLBACK TO SAVEPOINT brf_esc")
            log.warning("escalation notify failed for action %s", action["id"], exc_info=True)
            out.append({"to": who, "channel": None, "ok": False, "status": "error", "error": str(e)[:200]})
    return out


def notify_escape(s: str) -> str:
    import html

    return html.escape(s)


# ─── The ladder ───


def _overdue_signal(cur: Any, hotel: Hotel, action: dict) -> None:
    core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module="brief", kind=OVERDUE_KIND, dedupe_key=f"brief:overdue:{action['id']}",
        title=f"Overdue: {action['title']}"[:300], severity=3, confidence=1.0,
        impact_minor=action.get("expected_minor"), currency=(action.get("currency") or "").strip() or None,
        owner_role="owner", due_at=action["due_at"], subject_type="action", subject_id=str(action["id"]),
        evidence=[core_signals.Evidence(kind="row", label="Action", ref={"table": "actions", "id": action["id"]})],
    ))


def escalate_action(cur: Any, hotel: Hotel, action: dict, *, now: datetime, windows: list | None = None) -> dict | None:
    """Walk one action up the ladder. Returns what fired, or None."""
    cur.execute("SELECT level, due_at FROM brf_escalations WHERE action_id = %s ORDER BY level", (action["id"],))
    done = {r["level"]: r["due_at"] for r in cur.fetchall()}
    if done and any(d != action["due_at"] for d in done.values()):
        # The deadline moved (re-planned or woken from a snooze): start over.
        cur.execute("DELETE FROM brf_escalations WHERE action_id = %s", (action["id"],))
        done = {}
    target = level_for(action["due_at"], now)
    missing = [lv for lv in range(1, target + 1) if lv not in done]
    if not missing:
        return None
    top = max(missing)
    if top == 1:
        # Canon 7 "remind": no state change, reminders_sent + 1, logged.
        core_actions.decide(cur, action["id"], "remind", actor=core_actions.SYSTEM, via="system",
                            note="Deadline passed", hotel_slug=hotel.slug)
    critical = not windows or quiet.is_critical(cur, action)
    notified = _notify(cur, hotel, action, top, recipients_for(cur, hotel.slug, action, top),
                       now=now, windows=windows, critical=critical)
    for lv in missing:
        cur.execute(
            """
            INSERT INTO brf_escalations (action_id, level, hotel_slug, due_at, at, notified)
            VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (action_id, level) DO NOTHING
            """,
            (action["id"], lv, hotel.slug, action["due_at"], now, Json(notified if lv == top else [])),
        )
    cur.execute(
        "UPDATE actions SET escalation_level = %s, escalated_at = %s, updated_at = %s WHERE id = %s",
        (top, now, now, action["id"]),
    )
    if top == 3:
        _overdue_signal(cur, hotel, action)
    return {"action_id": action["id"], "levels": missing, "notified": notified}


def flush_deferred(cur: Any, hotel: Hotel, *, now: datetime, windows: list | None) -> int:
    """Send the reminders quiet hours held back, once each window ended.
    An action that closed, or whose deadline moved, drops them instead."""
    cur.execute(
        """
        SELECT e.action_id, e.level, e.due_at, e.notified FROM brf_escalations e
        WHERE e.hotel_slug = %s AND e.notified @> '[{"status": "deferred_quiet"}]'::jsonb
        ORDER BY e.at, e.action_id LIMIT 200
        """,
        (hotel.slug,),
    )
    sent = 0
    for row in [dict(r) for r in cur.fetchall()]:
        cur.execute("SELECT * FROM actions WHERE id = %s", (row["action_id"],))
        a = cur.fetchone()
        a = dict(a) if a else None
        live = a is not None and a["status"] in OPEN_WORK and a["due_at"] == row["due_at"]
        entries = []
        for n in row["notified"] or []:
            if n.get("status") != "deferred_quiet":
                entries.append(n)
                continue
            if not live:
                entries.append({**n, "status": "dropped_closed"})
                continue
            later = quiet.quiet_until(hotel, windows or [], n.get("user_id"), now)
            person = _user(cur, int(n["user_id"])) if n.get("user_id") else None
            if later is not None or datetime.fromisoformat(n["until"]) > now:
                entries.append({**n, "until": (later or datetime.fromisoformat(n["until"])).isoformat()})
                continue
            if person is None:
                entries.append({**n, "status": "dropped_inactive"})
                continue
            res = _notify(cur, hotel, a, int(row["level"]), [person], now=now)
            entries.extend({**r, "deferred_until": n["until"]} for r in res)
            sent += 1
        cur.execute("UPDATE brf_escalations SET notified = %s WHERE action_id = %s AND level = %s",
                    (Json(entries), row["action_id"], row["level"]))
    return sent


def run(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> dict:
    """One pass for a hotel: escalate overdue actions, reset the counters of
    actions whose deadline moved back into the future, and resolve the
    overdue signals of actions that are no longer overdue."""
    from clefs.modules.brief import brief as brief_mod  # lazy: brief's settings model lives there

    now = now or clock.now()
    hotel = get_hotel(cur, hotel_slug)
    windows = list(brief_mod.load_settings(cur, hotel_slug).quiet_hours)
    flushed = flush_deferred(cur, hotel, now=now, windows=windows)
    briefs = brief_mod.deliver_deferred(cur, hotel_slug, now=now)
    cur.execute(
        """
        SELECT * FROM actions
        WHERE hotel_slug = %s AND status = ANY(%s) AND due_at IS NOT NULL AND due_at <= %s
        ORDER BY due_at, id LIMIT 500
        """,
        (hotel_slug, list(OPEN_WORK), now),
    )
    fired = []
    still_overdue = []
    for row in cur.fetchall():
        a = dict(row)
        res = escalate_action(cur, hotel, a, now=now, windows=windows)
        if res:
            fired.append(res)
        if level_for(a["due_at"], now) >= 3:
            still_overdue.append(f"brief:overdue:{a['id']}")
    cur.execute(
        """
        UPDATE actions SET escalation_level = 0, escalated_at = NULL
        WHERE hotel_slug = %s AND escalation_level > 0 AND status = ANY(%s) AND due_at > %s
        """,
        (hotel_slug, list(OPEN_WORK), now),
    )
    resolved = core_signals.resolve_missing(cur, hotel_slug, "brief", OVERDUE_KIND, still_overdue)
    return {"escalated": len(fired), "levels": sum(len(f["levels"]) for f in fired), "resolved": resolved,
            "deferred_sent": flushed, "briefs_sent": briefs}


@jobs.scheduled("brief.escalate", jobs.every(minutes=15))
def _job(ctx: jobs.JobCtx) -> dict:
    return run(ctx.cur, ctx.hotel_slug, now=ctx.now)
