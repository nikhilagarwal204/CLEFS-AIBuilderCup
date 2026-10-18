"""
Escalation: who hears about a waiting request, on which channel, and how
several waiting requests become one message (03 spec 1.4 and 1.5).

Levels are fixed by role so the console reads them the same way for every
hotel: 0 the assignee (local: the device chime repeats until acked), 1 the
supervisor (Security on the urgent path), 2 the duty manager, 3 the GM.

Recipients come from the roster at the moment of the step: supervisor and
duty manager are shift roles on the current svc_shift_assignments; the GM
is the hotel's gm (else owner) membership. A hotel without a roster falls
back to staff whose role_key names the role and who are online now, so the
ladder still reaches a person.

Coalescing (coalesce_key = recipient + minute): every step one sweep crosses
for the same person becomes ONE digest ("3 waiting: 504 towels 6m, 612 AC
4m"), not three messages fighting WhatsApp's 6 s pair limit. Messages never
carry the guest's words; a sensitive case is named only as "sensitive".
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any

from clefs.core import clock
from clefs.core.phone import normalize_phone

log = logging.getLogger("clefs.service.escalation")

# ─── Levels, ladders, channels ───

ROLE_LEVEL = {"assignee": 0, "supervisor": 1, "security": 1, "duty_manager": 2, "gm": 3}
URGENT_ROLES = ("security", "duty_manager", "gm")
# The GM of a sensitive (not L1) case hears at 10 minutes, like the normal
# ladder's last step; L1 and emergencies page the GM at creation.
URGENT_GM_AFTER_MIN = 10
# Fallback order per role. constants.LADDER_CHANNELS lists the spec's first
# choice; SMS is added behind WhatsApp for the supervisor and Security so a
# person without a WhatsApp opt-in still hears (a step must reach someone).
ROLE_CHANNELS: dict[str, tuple[str, ...]] = {
    "supervisor": ("whatsapp", "sms"),
    "security": ("whatsapp", "sms"),
    "duty_manager": ("whatsapp", "sms"),
    "gm": ("whatsapp", "sms", "email"),
}
PURPOSE = "service.escalation"
TEMPLATE = "service_escalation_v1"  # utility template: {{1}} hotel, {{2}} digest


def _phone(value: str | None) -> str | None:
    try:
        return normalize_phone(value)
    except Exception:  # a legacy free-text number: unreachable, not an error
        return None


@dataclass
class Person:
    """One reachable human. `key` dedupes the same person across roles
    (a staff row and a user account with the same phone are one person)."""

    key: str
    role: str
    name: str
    staff_id: int | None = None
    user_id: int | None = None
    phone: str | None = None
    email: str | None = None
    whatsapp_ok: bool = False
    live_device: bool = False
    roles: set[str] = field(default_factory=set)

    def public(self) -> dict:
        return {"key": self.key, "role": self.role, "name": self.name, "staff_id": self.staff_id,
                "user_id": self.user_id, "live_device": self.live_device}


_STAFF_SQL = """
    SELECT s.id, s.name, s.whatsapp_e164, s.phone_number, s.whatsapp_opt_in, s.department_key, s.department,
           s.role_key, s.is_online, sa.shift_role, sa.department_key AS shift_department,
           EXISTS (SELECT 1 FROM device_sessions ds WHERE ds.staff_member_id = s.id AND ds.ended_at IS NULL) AS live_device
    FROM staff_members s
    LEFT JOIN LATERAL (
        SELECT shift_role, department_key FROM svc_shift_assignments a
        WHERE a.staff_member_id = s.id AND a.hotel_slug = s.hotel_slug AND a.status = 'planned'
          AND a.start_at <= %(now)s AND a.end_at > %(now)s
        ORDER BY a.start_at DESC LIMIT 1
    ) sa ON TRUE
    WHERE s.hotel_slug = %(slug)s AND s.is_active
    ORDER BY s.id
"""


def _staff_person(r: dict, role: str) -> Person:
    wa = _phone(r.get("whatsapp_e164"))
    phone = wa or _phone(r.get("phone_number"))
    return Person(
        key=f"staff:{r['id']}", role=role, name=r["name"], staff_id=r["id"], phone=phone,
        # business-initiated WhatsApp to staff needs their opt-in (0102)
        whatsapp_ok=bool(phone) and bool(r.get("whatsapp_opt_in")), live_device=bool(r.get("live_device")),
        roles={role},
    )


def _dept_of(r: dict) -> str | None:
    from clefs.modules.service import constants as C

    return r.get("shift_department") or r.get("department_key") or C.department_key(r.get("department"))


def people_for(cur: Any, hotel_slug: str, role: str, row: dict, *, staff: list[dict] | None = None) -> list[Person]:
    """Who holds `role` for this request right now. `staff` is the roster
    query's rows (pass it when resolving several roles in one sweep)."""
    if role == "gm":
        return _gms(cur, hotel_slug, staff)
    if staff is None:
        cur.execute(_STAFF_SQL, {"now": clock.now(), "slug": hotel_slug})
        staff = [dict(r) for r in cur.fetchall()]
    dept = row.get("department_key")
    if role == "assignee":
        sid = row.get("assigned_staff_id")
        return [_staff_person(r, role) for r in staff if sid and r["id"] == sid]
    if role == "security":
        on = [r for r in staff if _dept_of(r) == "security" and (r.get("shift_role") or r["live_device"] or r["is_online"])]
        return [_staff_person(r, role) for r in on]
    if role in ("supervisor", "duty_manager"):
        rostered = [r for r in staff if r.get("shift_role") == role]
        if role == "supervisor":
            same = [r for r in rostered if _dept_of(r) in (dept, None)]
            rostered = same or rostered
        if rostered:
            return [_staff_person(r, role) for r in rostered]
        # no roster: whoever holds the job title and is working now
        fallback = [r for r in staff if (r.get("role_key") or "") == role and (r["live_device"] or r["is_online"])]
        if role == "supervisor":
            fallback = [r for r in fallback if _dept_of(r) in (dept, None)] or fallback
        return [_staff_person(r, role) for r in fallback]
    return []


def _gms(cur: Any, hotel_slug: str, staff: list[dict] | None) -> list[Person]:
    """The hotel's GM accounts (owners when there is no GM), plus staff rows
    titled gm. Deduped by phone."""
    cur.execute(
        """SELECT u.id, u.name, u.email, u.phone_e164, m.role FROM hotel_memberships m JOIN users u ON u.id = m.user_id
           WHERE m.hotel_slug = %s AND m.is_active AND m.role IN ('gm', 'owner') ORDER BY m.role = 'gm' DESC, m.id""",
        (hotel_slug,),
    )
    users = [dict(r) for r in cur.fetchall()]
    if any(u["role"] == "gm" for u in users):
        users = [u for u in users if u["role"] == "gm"]
    out = [Person(key=f"user:{u['id']}", role="gm", name=u.get("name") or u.get("email") or "GM", user_id=u["id"],
                  phone=_phone(u.get("phone_e164")), email=u.get("email"), whatsapp_ok=bool(u.get("phone_e164")),
                  roles={"gm"}) for u in users]
    for r in staff or []:
        if (r.get("role_key") or "") == "gm":
            out.append(_staff_person(r, "gm"))
    seen: set[str] = set()
    uniq = []
    for p in out:
        k = p.phone or p.key
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    return uniq


# ─── Ladders ───


def is_urgent(row: dict) -> bool:
    """Sensitive or L1 skip the ladder and page at once (03 spec 1.4 step 1)."""
    return row.get("sensitivity") in ("emergency", "sensitive") or row.get("severity") == "L1"


def ladder_for(row: dict, policy_ladder: list[dict] | tuple[dict, ...] | None) -> list[dict]:
    """[{level, after_min, to}] for this request, in time order.

    Urgent requests use the urgent ladder: Security and the duty manager at
    minute 0, the GM at 0 for L1 or an emergency, else at 10. Others use
    their SLA policy's ladder (default 0/3/5/10: assignee, supervisor, duty
    manager, GM)."""
    from clefs.modules.service import constants as C

    if is_urgent(row):
        gm_after = 0 if (row.get("severity") == "L1" or row.get("sensitivity") == "emergency") else URGENT_GM_AFTER_MIN
        steps = [{"level": 0, "after_min": 0, "to": "assignee"}]
        # Security is paged for the safety pathways only (03 spec 1.5); an L1
        # that is not one (an AC failure in 40+ degree heat) goes straight to
        # the duty manager and the GM.
        if row.get("sensitivity") in ("emergency", "sensitive"):
            steps.append({"level": 1, "after_min": 0, "to": "security"})
        steps += [{"level": 2, "after_min": 0, "to": "duty_manager"}, {"level": 3, "after_min": gm_after, "to": "gm"}]
        return steps
    steps = sorted(list(policy_ladder or C.DEFAULT_LADDER), key=lambda s: int(s.get("after_min") or 0))
    out = []
    for i, st in enumerate(steps):
        to = str(st.get("to") or "")
        if to not in ROLE_LEVEL:
            continue  # a role this build cannot resolve is skipped, not guessed
        out.append({"level": ROLE_LEVEL.get(to, i), "after_min": max(0, int(st.get("after_min") or 0)), "to": to})
    return out


def channels_for(p: Person, role: str) -> tuple[str, ...]:
    """External channels for one person at one step, in fallback order.

    Level 0 is local (the device chime). A supervisor on a live device gets
    the device alert only; without one, WhatsApp (SMS when WhatsApp is not
    possible). Security and the duty manager: WhatsApp, then SMS. The GM:
    WhatsApp, SMS, then email."""
    if role == "assignee" or (role == "supervisor" and p.live_device):
        return ()
    out = []
    for ch in ROLE_CHANNELS.get(role, ()):
        if ch == "whatsapp" and p.phone and p.whatsapp_ok:
            out.append(ch)
        elif ch == "sms" and p.phone:
            out.append(ch)
        elif ch == "email" and p.email:
            out.append(ch)
    return tuple(out)


# ─── Digests ───


def item_label(row: dict) -> str:
    """How a waiting request is named in a message: never the guest's words;
    a sensitive case is only "sensitive" (details stay in the console)."""
    from clefs.modules.service import constants as C

    if row.get("sensitivity") == "emergency":
        return f"EMERGENCY {(row.get('sensitive_kind') or 'emergency').replace('_', ' ')}"
    if row.get("sensitivity") == "sensitive":
        return "sensitive matter"
    t = C.REQUEST_TYPES.get(row.get("request_type_key") or "", {})
    return t.get("label_en") or row.get("type") or "request"


def digest_text(hotel_name: str, items: list[dict]) -> str:
    """'Hotel: 3 waiting: 504 towels 6m, 612 Air conditioning 4m' (the most
    urgent and the longest waiting first). items: {room, label, minutes,
    urgent}."""
    items = sorted(items, key=lambda i: (not i.get("urgent"), -int(i.get("minutes") or 0)))
    parts = [f"{i.get('room') or '-'} {i['label']} {int(i.get('minutes') or 0)}m" for i in items[:8]]
    more = f", +{len(items) - 8} more" if len(items) > 8 else ""
    head = "URGENT " if any(i.get("urgent") for i in items) else ""
    return f"{head}{hotel_name}: {len(items)} waiting: {', '.join(parts)}{more}"


def coalesce_key(hotel_slug: str, p: Person, escalation_ids: list[int]) -> str:
    """recipient:minute, plus the escalation ids, so a second sweep within the
    same minute (cron and evaluate-on-read) still delivers its own steps."""
    minute = clock.now().strftime("%Y%m%d%H%M")
    ids = hashlib.sha256(",".join(str(i) for i in sorted(escalation_ids)).encode()).hexdigest()[:10]
    return f"svc.esc:{hotel_slug}:{p.key}:{minute}:{ids}"


def send_digest(cur: Any, hotel: Any, p: Person, items: list[dict], escalation_ids: list[int], *, live: bool) -> dict:
    """One message to one person for everything this sweep escalated to them.
    Shadow mode (live=False) computes the channels and sends nothing.
    -> {channels, sent, outbox_id, channel, status, text}."""
    role = max(p.roles or {p.role}, key=lambda r: ROLE_LEVEL.get(r, 0))
    channels = channels_for(p, role)
    text = digest_text(hotel.name, items)
    out = {"channels": list(channels), "sent": False, "outbox_id": None, "channel": None, "status": "shadow" if not live else "no_channel", "text": text}
    if not live or not channels:
        return out
    from clefs import notify

    try:
        res = notify.notify(
            notify.Recipient(phone=p.phone, email=p.email, name=p.name, user_id=p.user_id),
            notify.Message(
                purpose=PURPOSE, hotel_slug=hotel.slug, category="utility", sender="platform",
                whatsapp_text=text,
                whatsapp_template={"name": TEMPLATE, "lang": "en", "params": [hotel.name[:60], text[:900]]},
                sms_text=text[:300],  # no links: SMS to +971 may not carry them
                email_subject=f"{hotel.name}: {len(items)} request(s) waiting",
                email_text=text,
                idempotency_key=coalesce_key(hotel.slug, p, escalation_ids),
            ),
            channels=channels,
            cur=cur,
        )
    except Exception as exc:  # a provider fault never stops the sweep
        log.warning("escalation to %s failed for %s: %s", p.key, hotel.slug, exc)
        return {**out, "status": "error"}
    return {**out, "sent": bool(res.ok), "outbox_id": res.outbox_id, "channel": res.channel, "status": res.status}
