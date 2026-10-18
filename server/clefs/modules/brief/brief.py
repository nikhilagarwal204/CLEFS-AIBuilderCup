"""
Owner Brief assembly (02 spec 6.1, Canon 18 cadence tiers, 04 spec 7.3).

    assemble(cur, hotel, "daily", day=d)   -> the brief as a dict (preview)
    generate(cur, hotel, "daily", ...)      -> assemble + persist a brf_runs row
    deliver(cur, run, ...)                  -> magic links + email (+ WhatsApp hook)
    push_now(cur, slug, cadence=...)        -> generate + deliver (brief.api door)

Cadence tiers (Canon 18):
    daily    money at risk and service L1 only: the daily-cadence signals,
             plus pending one-tap approvals (actions with surface='brief').
    weekly   the weekly-cadence signals (visibility, rank, AI share of voice,
             NRevPAR, review themes), the finance weekly pulse, the autonomy
             block (promotion and demotion suggestions) and the compliance
             digest (compliance never ranks into a top 5, Canon 6).
    monthly  the P&L pulse (finance.api.monthly_summary) and monthly signals.

Every tier ends with the "Verified results" line (24 h and month-to-date),
which never counts toward the 5, and pins actions the escalation ladder took
to level 3 as "Overdue" (escalate.py).

Nothing here is a state machine: decisions go through clefs.core.actions
(Canon 7 + K1, with the autonomy dial), which the router calls directly.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs.core import actions as core_actions
from clefs.core import clock, money
from clefs.core import signals as core_signals
from clefs.core import time as htime
from clefs.core.hotels import Hotel, get_hotel, get_module_settings
from clefs.modules.brief import quiet, ranking
from clefs.modules.brief.quiet import QuietWindow

log = logging.getLogger("clefs.brief")

Cadence = Literal["daily", "weekly", "monthly"]
CADENCES: tuple[str, ...] = ("daily", "weekly", "monthly")
RANKED_SECTIONS = ("money_lost", "money_available", "service_risk", "reviews", "verified")
WEEKLY_TOP_N = 8  # the weekly tier lists more, under the same caps
OPEN_WORK = ("approved", "assigned", "in_progress")
# States in which the card's ActionBar offers a decision (client pure.ts BY_STATUS).
DECIDABLE_STATES = frozenset({"proposed", "approved", "assigned", "in_progress", "snoozed"})
BRIEF_ACTION_CONFIDENCE = 0.8  # a pending approval with no signal behind it
LINK_TTL = timedelta(hours=72)  # 02 spec 6.1 brief_links.expires_at


# ─── Settings (hotel_module_settings["brief"]) ───


class BriefSettings(BaseModel):
    """Who gets the brief and on which tiers. The send time is the core
    hotel column `brief_time_local` (default 07:00), read by the scheduler."""

    daily_enabled: bool = True
    weekly_enabled: bool = True
    monthly_enabled: bool = True
    # Empty = every active owner and gm membership.
    recipient_user_ids: list[int] = Field(default_factory=list)
    channels: list[Literal["email", "whatsapp"]] = Field(default_factory=lambda: ["whatsapp", "email"])
    send_when_empty: bool = True  # an "all clear" brief still carries the verified line
    top_n: int = Field(default=ranking.TOP_N, ge=1, le=10)
    # Per-recipient quiet hours, hotel-local (quiet.py): scheduled briefs and
    # non-critical escalations wait for the window to end.
    quiet_hours: list[QuietWindow] = Field(default_factory=list)


def load_settings(cur: Any, hotel_slug: str) -> BriefSettings:
    return get_module_settings(cur, hotel_slug, "brief", BriefSettings)


# ─── Reference revenue (R_ref) ───


def r_ref_major(cur: Any, hotel: Hotel, day: date) -> float:
    """2 x the trailing-30-day average daily room revenue, in major units.
    Days without an occupancy row do not dilute the average; no history at
    all falls back to AED 20,000 (ranking.R_REF_FALLBACK_MAJOR)."""
    cur.execute(
        """
        SELECT AVG(room_revenue_minor)::FLOAT AS avg_minor
        FROM occupancy_daily
        WHERE hotel_slug = %s AND date >= %s AND date < %s AND currency = %s AND room_revenue_minor > 0
        """,
        (hotel.slug, day - timedelta(days=30), day, hotel.currency),
    )
    row = cur.fetchone()
    avg = (row or {}).get("avg_minor")
    if not avg:
        return float(ranking.R_REF_FALLBACK_MAJOR)
    return 2.0 * float(avg) / (10 ** money.minor_units(hotel.currency))


# ─── Verified results line ───


def _verified_sum(cur: Any, hotel: Hotel, start: datetime, end: datetime) -> tuple[int, int]:
    """(minor, count) of verified value in [start, end): verified actions
    that count toward verified profit (attribution caused), net of reversals,
    in the hotel's currency (the ledger converts on write)."""
    cur.execute(
        """
        SELECT COALESCE(SUM(outcome_minor), 0) AS minor, COUNT(*) AS n
        FROM actions
        WHERE hotel_slug = %s AND status = 'verified' AND counts_toward_verified_profit
          AND currency = %s AND verified_at >= %s AND verified_at < %s
        """,
        (hotel.slug, hotel.currency, start, end),
    )
    row = cur.fetchone()
    return int(row["minor"]), int(row["n"])


def verified_line(cur: Any, hotel: Hotel, *, now: datetime) -> dict:
    """The line every brief ends with (never counts toward the top 5)."""
    today = htime.local_date(hotel, now)
    month_start, _ = htime.local_day_bounds(hotel, today.replace(day=1))
    d24, n24 = _verified_sum(cur, hotel, now - timedelta(hours=24), now)
    mtd, nmtd = _verified_sum(cur, hotel, month_start, now)
    ccy = hotel.currency
    text = (
        f"Verified results: {money.fmt_money(d24, ccy, hotel.locale, minor=True)} in the last 24 h, "
        f"{money.fmt_money(mtd, ccy, hotel.locale, minor=True)} this month"
    )
    return {
        "currency": ccy,
        "last_24h_minor": d24,
        "last_24h_value": money.money_str(d24, ccy),
        "last_24h_actions": n24,
        "mtd_minor": mtd,
        "mtd_value": money.money_str(mtd, ccy),
        "mtd_actions": nmtd,
        "text": text,
    }


# ─── Candidates ───


def _minor_in(cur: Any, hotel: Hotel, minor: int | None, currency: str | None) -> int | None:
    """An impact in the hotel's currency (ranking compares like with like).
    A currency with no rate ranks as "no money figure" rather than failing."""
    if minor is None:
        return None
    ccy = (currency or hotel.currency).strip()
    if ccy == hotel.currency:
        return int(minor)
    try:
        return money.convert_minor(cur, int(minor), ccy, hotel.currency, htime.local_date(hotel))
    except Exception:  # fx_unavailable: rank it on severity instead
        return None


# ─── Evidence links ───
# Most detectors give evidence refs as {table, id} (Profit Guard findings,
# Service incidents), not a url. Falling back to /brief?signal=<id> sent the
# owner back to the page they were already on (QA OWN-02), so a ref's table,
# then the signal's subject, then its module map to the console page that
# shows the evidence. Paths are relative to /app/{slug}.

EVIDENCE_TABLE_PATHS = {
    "pfg_findings": "/profit/findings/{id}",
    "pfg_dispute_packets": "/profit/disputes/{id}",
    "inq_inquiries": "/inquiries?id={id}",
    "inquiries": "/inquiries?id={id}",
}
SUBJECT_PATHS = {
    "inquiry": "/inquiries?id={id}",
    "pfg_dispute_packet": "/profit/disputes/{id}",
    "pfg_finding_group": "/profit/findings",
    "pfg_recovery": "/profit/findings?status=ALL",
    "pfg_ota_contracts": "/profit/contracts",
    "pfg_td_ledger": "/profit/tax",
    "rev_review": "/reviews",
    "rev_theme": "/reviews",
    "reservation": "/guests",
    "gst_stay": "/guests",
    "fin_anomaly": "/finance",
    "fin_import_batch": "/finance/imports",
    "service_request": "/service", "staff_request": "/service", "incident": "/service", "room": "/service",
    "dr_rate_recommendation": "/demand", "dr_night": "/demand", "dr_event": "/demand/events",
    "dr_ghc_checklist": "/demand", "dr_guidance": "/demand",
    "amn_item": "/amenities/items/{id}", "amn_location": "/amenities",
    "vis_ai_fact": "/visibility", "trs_eval_run": "/trust/evals",
}
MODULE_PATHS = {
    "profit_guard": "/profit", "demand": "/demand", "service": "/service", "reviews": "/reviews",
    "inquiries": "/inquiries", "finance": "/finance", "amenities": "/amenities", "guests": "/guests",
    "concierge": "/concierge", "visibility": "/visibility", "trust": "/trust", "upsell": "/menu",
}


def _console_path(hotel: Hotel, tmpl: str, ident: Any) -> str | None:
    if "{id}" in tmpl:
        if ident is None or not str(ident).strip() or not str(ident).replace("-", "").isalnum():
            return None  # never build a link from an id that could carry a path
        tmpl = tmpl.replace("{id}", str(ident))
    return f"/app/{hotel.slug}{tmpl}"


def _evidence_url(hotel: Hotel, sig: dict | None, action_id: int | None) -> str:
    """Where "Evidence" leads: the detector's own link, else the page that
    shows the referenced row or subject, else the action's history, else
    the module's console, and only then the brief itself."""
    s = sig or {}
    refs = [ev.get("ref") for ev in s.get("evidence") or [] if isinstance(ev, dict)]
    refs = [r for r in refs if isinstance(r, dict)]
    for ref in refs:
        if isinstance(ref.get("url"), str) and ref["url"].startswith(("/", "https://")):
            return ref["url"]
    for ref in refs:
        tmpl = EVIDENCE_TABLE_PATHS.get(str(ref.get("table") or ""))
        url = _console_path(hotel, tmpl, ref.get("id")) if tmpl else None
        if url:
            return url
    tmpl = SUBJECT_PATHS.get(str(s.get("subject_type") or ""))
    url = _console_path(hotel, tmpl, s.get("subject_id")) if tmpl else None
    if url:
        return url
    if action_id:
        return f"/app/{hotel.slug}/brief/actions/{action_id}"
    if s.get("module") in MODULE_PATHS:
        return f"/app/{hotel.slug}{MODULE_PATHS[s['module']]}"
    return f"/app/{hotel.slug}/brief?signal={s.get('id')}"


def _signal_candidate(cur: Any, hotel: Hotel, sig: dict, action: dict | None, *, now: datetime) -> ranking.Candidate:
    impact = _minor_in(cur, hotel, sig.get("impact_minor"), sig.get("currency"))
    deadline = sig.get("due_at") or (action or {}).get("due_at")
    first_seen = sig.get("first_seen_at")
    recent_review = (
        sig.get("section") == "reviews" and not impact and first_seen is not None
        and first_seen >= now - timedelta(hours=48)
    )
    return ranking.Candidate(
        key=f"signal:{sig['id']}",
        section=sig["section"],
        detector=ranking.detector_key(sig),  # the cap counts source detectors, not kinds
        title=sig["title"],
        confidence=float(sig.get("confidence") or 0.0),
        impact_minor=impact,
        currency=hotel.currency,
        minor_per_major=10 ** money.minor_units(hotel.currency),
        deadline=deadline,
        severity=sig.get("severity"),
        recent_negative_review=recent_review,
        decidable=(action or {}).get("status") in DECIDABLE_STATES,
        data={"signal": sig, "action": action},
    )


def _action_candidate(cur: Any, hotel: Hotel, action: dict) -> ranking.Candidate:
    """A one-tap approval that no open signal carries (e.g. a group quote
    whose signal is weekly): the action's surface='brief' still reaches the
    brief (04 spec 7.3, inq.quote_awaiting_approval)."""
    impact = _minor_in(cur, hotel, action.get("expected_minor"), action.get("currency"))
    return ranking.Candidate(
        key=f"action:{action['id']}",
        section="money_available" if impact else "service_risk",
        detector=action["type"],
        title=action["title"],
        confidence=BRIEF_ACTION_CONFIDENCE,
        impact_minor=impact,
        currency=hotel.currency,
        minor_per_major=10 ** money.minor_units(hotel.currency),
        deadline=action.get("due_at") or action.get("expires_at"),
        decidable=action.get("status") in DECIDABLE_STATES,
        data={"signal": None, "action": action},
    )


def _actions_by_id(cur: Any, hotel: Hotel, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    cur.execute("SELECT * FROM actions WHERE hotel_slug = %s AND id = ANY(%s)", (hotel.slug, list(ids)))
    return {r["id"]: core_actions.to_api(dict(r)) for r in cur.fetchall()}


# ─── Stale period signals (QA OWN-14) ───
# Monthly signals are keyed per month ("fin.pl_pulse_ready:2025-08-01"), and
# nothing closes last year's "P&L pulse for August 2025 is ready", so year-old
# notices took top-5 slots in the monthly brief. A monthly signal whose key
# names a month before the brief's period is history, not news.

_KEY_MONTH = re.compile(r"(?:^|[:|/])(\d{4})-(\d{2})(?:-01)?$")


def signal_month(sig: dict) -> date | None:
    """The calendar month a signal's dedupe key names, if it names one."""
    m = _KEY_MONTH.search(str(sig.get("dedupe_key") or ""))
    if not m or not 1 <= int(m.group(2)) <= 12:
        return None
    return date(int(m.group(1)), int(m.group(2)), 1)


def is_stale_for(sig: dict, cadence: str, period_start: date) -> bool:
    month = signal_month(sig) if cadence == "monthly" else None
    return month is not None and month < period_start.replace(day=1)


def candidates(cur: Any, hotel: Hotel, cadence: str, *, now: datetime,
               period_start: date | None = None) -> list[ranking.Candidate]:
    """Open, un-snoozed, non-compliance signals of this cadence, plus the
    pending brief approvals no such signal already carries. With
    `period_start`, monthly signals about an earlier month are left out."""
    sigs = core_signals.list_signals(cur, hotel.slug, cadence=cadence, sections=list(RANKED_SECTIONS), limit=500)
    if period_start is not None:
        sigs = [s for s in sigs if not is_stale_for(s, cadence, period_start)]
    acts = _actions_by_id(cur, hotel, [s["action_id"] for s in sigs if s.get("action_id")])
    out = [_signal_candidate(cur, hotel, s, acts.get(s.get("action_id")), now=now) for s in sigs]
    if cadence in ("daily", "weekly"):
        carried = {s["id"] for s in sigs}
        cur.execute(
            """
            SELECT * FROM actions
            WHERE hotel_slug = %s AND status = 'proposed' AND surface = 'brief'
              AND (expires_at IS NULL OR expires_at > %s)
            ORDER BY due_at ASC NULLS LAST, id DESC LIMIT 200
            """,
            (hotel.slug, now),
        )
        for row in cur.fetchall():
            a = core_actions.to_api(dict(row))
            if a.get("signal_id") in carried:
                continue
            if a.get("signal_id"):
                # Its signal exists but belongs to another tier (or is closed):
                # the daily tier still shows it only when it is money at stake.
                if cadence == "daily" and not a.get("expected_minor"):
                    continue
            out.append(_action_candidate(cur, hotel, a))
    return out


# ─── Responsible person ───


def _members_by_role(cur: Any, hotel_slug: str) -> dict[str, dict]:
    """The first active member per role (owner first by id): the named
    person an item's default role maps to (02 spec 6.1)."""
    cur.execute(
        """
        SELECT m.role, u.id, u.name FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND m.is_active ORDER BY m.id
        """,
        (hotel_slug,),
    )
    out: dict[str, dict] = {}
    for r in cur.fetchall():
        out.setdefault(r["role"], {"user_id": r["id"], "name": r["name"]})
    return out


def _user_name(cur: Any, user_id: int | None) -> str | None:
    if not user_id:
        return None
    cur.execute("SELECT name FROM users WHERE id = %s", (user_id,))
    row = cur.fetchone()
    return row["name"] if row else None


def _staff_name(cur: Any, hotel_slug: str, staff_id: int | None) -> str | None:
    if not staff_id:
        return None
    try:
        cur.execute("SAVEPOINT brf_staff")
        cur.execute("SELECT name FROM staff_members WHERE id = %s AND hotel_slug = %s", (staff_id, hotel_slug))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT brf_staff")
    except Exception:  # staff_members is Service's legacy table; never fail the brief on it
        cur.execute("ROLLBACK TO SAVEPOINT brf_staff")
        return None
    return row["name"] if row else None


def responsible(cur: Any, hotel_slug: str, sig: dict | None, action: dict | None, members: dict[str, dict]) -> dict:
    """{role, user_id, staff_id, label}: the assignee when there is one, else
    the signal's named owner, else the first member holding the default role
    (the action type's, then the signal kind's)."""
    a, s = action or {}, sig or {}
    role = a.get("assignee_role") or a.get("owner_role") or s.get("owner_role")
    if not role:
        t = core_actions.get_action_type(a["type"]) if a.get("type") else None
        k = core_signals.get_kind(s["kind"]) if s.get("kind") else None
        role = (t.default_owner_role if t else None) or (k.default_owner_role if k else None) or "gm"
    if a.get("assignee_staff_id"):
        sid = int(a["assignee_staff_id"])
        return {"role": role, "user_id": None, "staff_id": sid, "label": _staff_name(cur, hotel_slug, sid) or role.upper()}
    uid = a.get("assignee_user_id") or s.get("owner_user_id")
    if uid:
        return {"role": role, "user_id": int(uid), "staff_id": None, "label": _user_name(cur, uid) or role}
    m = members.get(role)
    if m:
        return {"role": role, "user_id": m["user_id"], "staff_id": None, "label": m["name"]}
    return {"role": role, "user_id": None, "staff_id": None, "label": role.replace("_", " ").title()}


# ─── Items ───
# An estimated impact says so (04 spec 4.5: inquiry money at risk "is
# labelled 'est.' in the UI and the brief"; 02 spec 4.3: Demand's impact is
# an "estimated opportunity"). The source decides: impact_kind revenue_upside
# (inquiries, visibility exposure) or any Demand signal (it sets no
# impact_kind). The verified section is measured money, never an estimate.
# An approval with no signal behind it is an estimate when it is an inquiry
# or Demand proposal (its expected value is a quote or a forecast).

ESTIMATE_IMPACT_KINDS = frozenset({"revenue_upside", "estimate", "estimated"})
ESTIMATE_MODULES = frozenset({"demand"})
ESTIMATE_ACTION_PREFIXES = ("inquiries.", "demand.", "dr.")


def impact_estimated(sig: dict | None, act: dict | None, impact_minor: int | None) -> bool:
    if not impact_minor:
        return False
    if sig:
        if sig.get("section") == "verified":
            return False
        return (sig.get("impact_kind") in ESTIMATE_IMPACT_KINDS or sig.get("module") in ESTIMATE_MODULES
                or str(sig.get("kind") or "").startswith("dr."))
    return str((act or {}).get("type") or "").startswith(ESTIMATE_ACTION_PREFIXES)


# ─── Section labels (QA OWN-14) ───
# A zero-money to-do from a desk module sits in the service_risk section
# (Finance files "7 P&L accounts to confirm" there), and the console called it
# "Service risk". The section still drives the caps; the label names the desk
# instead. Keyed by the kind's prefix, so a stored run (which keeps the kind
# as `detector`, not the module) gets the same label.

DESK_BY_PREFIX = {
    "fin": "Finance", "finance": "Finance", "pg": "Profit Guard", "profit_guard": "Profit Guard",
    "dr": "Demand", "demand": "Demand", "inq": "Inquiries", "inquiries": "Inquiries",
    "vis": "Visibility", "visibility": "Visibility", "trust": "Trust", "trs": "Trust",
}


def section_label(section: str | None, detector: str | None) -> str | None:
    """None keeps the console's own label for the section."""
    if section != "service_risk":
        return None
    desk = DESK_BY_PREFIX.get(str(detector or "").split(".", 1)[0])
    return f"{desk} to-do" if desk else None


def _item(cur: Any, hotel: Hotel, c: ranking.Candidate, *, kind: str, members: dict[str, dict]) -> dict:
    sig, act = c.data.get("signal"), c.data.get("action")
    who = responsible(cur, hotel.slug, sig, act, members)
    rec = (sig or {}).get("recommended_action")
    if not rec and act:
        rec = {"type": act["type"], "label": act["title"]}
    return {
        "kind": kind,
        "section": c.section,
        # The kind (or action type) stays the item's public "detector": the
        # console falls back to it as the action type when there is no
        # recommended action; the finer cap key lives only in ranking.
        "detector": sig["kind"] if sig else c.detector,
        "section_label": section_label(c.section, sig["kind"] if sig else c.detector),
        "signal_id": (sig or {}).get("id"),
        "action_id": (act or {}).get("id"),
        "action_status": (act or {}).get("status"),
        "title": c.title,
        "impact_minor": c.impact_minor,
        "impact_value": money.money_str(c.impact_minor, hotel.currency) if c.impact_minor is not None else None,
        "impact_estimated": impact_estimated(sig, act, c.impact_minor),
        "currency": hotel.currency if c.impact_minor is not None else None,
        "confidence": round(float(c.confidence), 3),
        "importance": round(c.importance, 4),
        "urgency": round(c.urgency, 4),
        "score": c.score,
        "deadline_at": c.deadline.isoformat() if c.deadline else None,
        "owner_role": who["role"],
        "responsible_user_id": who["user_id"],
        "responsible_staff_id": who["staff_id"],
        "responsible_label": who["label"],
        "evidence_url": _evidence_url(hotel, sig, (act or {}).get("id")),
        "recommended_action": rec,
    }


def overdue_pins(cur: Any, hotel: Hotel, *, now: datetime) -> list[ranking.Candidate]:
    """Actions the escalation ladder took to level 3 (deadline + 24 h) and
    still open: pinned as "Overdue" above the ranked items, outside the 5."""
    cur.execute(
        """
        SELECT * FROM actions
        WHERE hotel_slug = %s AND escalation_level >= 3 AND status = ANY(%s)
        ORDER BY due_at ASC NULLS LAST, id LIMIT 10
        """,
        (hotel.slug, list(OPEN_WORK)),
    )
    acts = [core_actions.to_api(dict(r)) for r in cur.fetchall()]
    sig_ids = [a["signal_id"] for a in acts if a.get("signal_id")]
    sigs = {s["id"]: s for s in (core_signals.get_signal(cur, hotel.slug, i) for i in sig_ids) if s}
    out = []
    for a in acts:
        s = sigs.get(a.get("signal_id"))
        c = _signal_candidate(cur, hotel, s, a, now=now) if s else _action_candidate(cur, hotel, a)
        c.key = f"overdue:{a['id']}"
        c.title = f"Overdue: {a['title']}"
        ranking.score_all([c], now=now, r_ref_major=float(ranking.R_REF_FALLBACK_MAJOR))
        out.append(c)
    return out


# ─── Blocks ───


def autonomy_block(cur: Any, hotel: Hotel, *, now: datetime, days: int = 7) -> dict:
    """Promotion suggestions still open and demotions in the period (Canon
    17, 04 spec 1.5). Brief only shows them: accepting goes through
    POST /hotels/{slug}/autonomy/suggestions/{id}/accept (core.autonomy.*)."""
    cur.execute(
        """
        SELECT e.id, e.action_type, e.kind, e.from_level, e.to_level, e.trigger, e.at, e.status
        FROM action_autonomy_events e
        WHERE e.hotel_slug = %s
          AND ((e.kind = 'promotion_suggested' AND e.status = 'open')
               OR (e.kind = 'demoted' AND e.at >= %s))
        ORDER BY e.at DESC, e.id DESC LIMIT 20
        """,
        (hotel.slug, now - timedelta(days=days)),
    )
    promotions, demotions = [], []
    for r in cur.fetchall():
        t = core_actions.get_action_type(r["action_type"])
        row = {
            "event_id": r["id"],
            "action_type": r["action_type"],
            "label": t.label if t else r["action_type"],
            "from_level": r["from_level"],
            "to_level": r["to_level"],
            "at": r["at"].isoformat() if r["at"] else None,
        }
        if r["kind"] == "promotion_suggested":
            stats = core_actions.promotion_stats(cur, hotel.slug, r["action_type"], now=now) if t else {}
            row["stats"] = stats
            row["text"] = (
                f"{row['label']}: {stats.get('decisions', 0)} decisions in 30 days, "
                f"{round(100 * float(stats.get('approval_rate') or 0))}% approved unedited. "
                f"Suggest {r['from_level']} -> {r['to_level']}."
            )
            promotions.append(row)
        else:
            row["trigger"] = r["trigger"]
            row["text"] = f"{row['label']} lowered to {r['to_level']} after {str(r['trigger'] or 'an incident').replace('_', ' ')}."
            demotions.append(row)
    return {"promotions": promotions, "demotions": demotions}


def compliance_digest(cur: Any, hotel: Hotel) -> list[dict]:
    """Compliance items never rank (Canon 6); the weekly tier lists them."""
    rows = core_signals.list_signals(cur, hotel.slug, sections=["compliance"], limit=20)
    return [
        {
            "signal_id": s["id"], "kind": s["kind"], "title": s["title"],
            "due_at": s["due_at"].isoformat() if s.get("due_at") else None,
            "impact_value": s.get("impact_amount"), "currency": s.get("currency"),
        }
        for s in rows
        if s["kind"] not in ("core.autonomy_promotion", "core.autonomy_demoted")  # the autonomy block has them
    ]


def _call_api(cur: Any, fn: Any, *args: Any, **kw: Any) -> Any:
    """Another lane's api.py may still be a stub or may raise while it is
    being built; a brief block is optional, so any failure means "absent".
    The savepoint keeps a failed query from aborting the brief's transaction."""
    cur.execute("SAVEPOINT brf_block")
    try:
        out = fn(cur, *args, **kw)
    except Exception:  # noqa: BLE001 - advisory block, never fails the brief
        cur.execute("ROLLBACK TO SAVEPOINT brf_block")
        log.warning("brief block %s failed", getattr(fn, "__qualname__", fn), exc_info=True)
        return None
    cur.execute("RELEASE SAVEPOINT brf_block")
    return out


def finance_block(cur: Any, hotel: Hotel, cadence: str, *, day: date) -> dict | None:
    """Weekly: finance.api.weekly_pulse (NRevPAR trend). Monthly: the P&L
    pulse slot, finance.api.monthly_summary for the previous month. None
    while Finance is off or unbuilt (the brief shows no finance block)."""
    try:
        from clefs.modules.finance import api as finance_api
    except Exception:  # noqa: BLE001 - module absent in a slimmed build
        return None
    if cadence == "weekly":
        week_start = day - timedelta(days=day.isoweekday() - 1) - timedelta(days=7)
        data = _call_api(cur, finance_api.weekly_pulse, hotel.slug, week_start=week_start)
        return {"kind": "weekly_pulse", "week_start": week_start.isoformat(), "data": data} if data else None
    if cadence == "monthly":
        month = (day.replace(day=1) - timedelta(days=1)).replace(day=1)
        data = _call_api(cur, finance_api.monthly_summary, hotel.slug, month=month)
        return {"kind": "pl_pulse", "month": month.isoformat(), "data": data, "available": bool(data)}
    return None


def inquiries_block(cur: Any, hotel: Hotel, *, day: date) -> dict | None:
    """The inquiry lines ("2 unanswered > 2 h, AED 2.5k at risk (est.)");
    inquiries.api.brief_summary returns {} until that lane builds it."""
    try:
        from clefs.modules.inquiries import api as inquiries_api
    except Exception:  # noqa: BLE001
        return None
    data = _call_api(cur, inquiries_api.brief_summary, hotel.slug, day=day)
    return data or None


# ─── Assembly ───


def period_for(cadence: str, day: date) -> tuple[date, date]:
    """The window a tier summarises: the day itself, the previous ISO week
    (Mon-Sun), or the previous calendar month."""
    if cadence == "weekly":
        monday = day - timedelta(days=day.isoweekday() - 1)
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if cadence == "monthly":
        last = day.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    return day, day


def _headline(cadence: str, n: int, at_stake_minor: int, hotel: Hotel, *, estimated: bool = False) -> str:
    """`estimated`: the total sums at least one estimate with statement-backed
    money, so it says "(incl. est.)" rather than passing as all measured."""
    stake = money.fmt_money(at_stake_minor, hotel.currency, hotel.locale, minor=True)
    stake = f"{stake} at stake" + (" (incl. est.)" if estimated else "")
    if cadence == "daily":
        if n == 0:
            return "Nothing needs a decision today."
        return f"{n} decision{'s' if n != 1 else ''} pending today, {stake}."
    label = "This week" if cadence == "weekly" else "This month"
    if n == 0:
        return f"{label}: nothing open to review."
    return f"{label}: {n} item{'s' if n != 1 else ''} to review, {stake}."


def at_stake_minor(items: list[dict]) -> int:
    """Money still in play. A `verified` item (a recovered OTA credit, last
    week's commission avoided) is money already banked: it ranks so the owner
    sees the win, but counting it here would report a received AED 3,020
    credit as "AED 3,020 at stake". The verified line carries that money."""
    return sum(i["impact_minor"] or 0 for i in items if i.get("section") != "verified")


def assemble(cur: Any, hotel: Hotel | str, cadence: str, *, day: date | None = None,
             now: datetime | None = None, top_n: int | None = None) -> dict:
    """Build one brief without writing anything (preview and generate both
    call this). Deterministic for a given database state and `now`."""
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of {CADENCES}")
    if isinstance(hotel, str):
        hotel = get_hotel(cur, hotel)
    now = now or clock.now()
    day = day or htime.local_date(hotel, now)
    settings = load_settings(cur, hotel.slug)
    limit = top_n or (settings.top_n if cadence == "daily" else max(settings.top_n, WEEKLY_TOP_N))
    r_ref = r_ref_major(cur, hotel, day)
    members = _members_by_role(cur, hotel.slug)

    start, end = period_for(cadence, day)
    cands = candidates(cur, hotel, cadence, now=now, period_start=start)
    picked = ranking.rank(cands, now=now, r_ref_major=r_ref, limit=limit,
                          min_decidable=ranking.MIN_DECIDABLE if cadence != "monthly" else 0)
    pinned = overdue_pins(cur, hotel, now=now)
    pinned_actions = {c.data["action"]["id"] for c in pinned if c.data.get("action")}
    picked = [c for c in picked if (c.data.get("action") or {}).get("id") not in pinned_actions]

    items = [_item(cur, hotel, c, kind="ranked", members=members) for c in picked]
    overdue = [_item(cur, hotel, c, kind="overdue", members=members) for c in pinned]
    at_stake = at_stake_minor(items + overdue)

    blocks: dict[str, Any] = {}
    if cadence == "daily":
        inq = inquiries_block(cur, hotel, day=day)
        if inq:
            blocks["inquiries"] = inq
    if cadence == "weekly":
        blocks["autonomy"] = autonomy_block(cur, hotel, now=now)
        blocks["compliance"] = compliance_digest(cur, hotel)
    fin = finance_block(cur, hotel, cadence, day=day)
    if fin:
        blocks["finance"] = fin

    return {
        "hotel_slug": hotel.slug,
        "hotel_name": hotel.name,
        "cadence": cadence,
        "brief_date": day.isoformat(),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "generated_at": now.isoformat(),
        "currency": hotel.currency,
        "headline": _headline(cadence, len(items) + len(overdue), at_stake, hotel,
                              estimated=any(i.get("impact_estimated") for i in items + overdue)),
        "at_stake_minor": at_stake,
        "at_stake_value": money.money_str(at_stake, hotel.currency),
        "items": items,
        "overdue": overdue,
        "verified": verified_line(cur, hotel, now=now),
        "blocks": blocks,
        "counts": {"candidates": len(cands), "ranked": len(items), "overdue": len(overdue)},
        "r_ref_major": round(r_ref, 2),
    }


# ─── Persistence ───

RUN_COLUMNS = (
    "id, hotel_slug, cadence, brief_date, period_start, period_end, scheduled_local, generated_at, trigger, "
    "status, headline, currency, at_stake_minor, payload, delivery, sent_at, created_by, created_at"
)
ITEM_FIELDS = (
    "item_no", "kind", "section", "detector", "signal_id", "action_id", "title", "impact_minor", "impact_estimated",
    "currency", "confidence", "importance", "urgency", "score", "deadline_at", "owner_role", "responsible_user_id",
    "responsible_staff_id", "responsible_label", "evidence_url", "recommended_action",
)


def _insert_items(cur: Any, run_id: int, hotel_slug: str, items: list[dict]) -> None:
    from clefs.db import Json

    for it in items:
        cur.execute(
            f"""
            INSERT INTO brf_items (run_id, hotel_slug, {", ".join(ITEM_FIELDS)})
            VALUES (%s, %s, {", ".join(["%s"] * len(ITEM_FIELDS))})
            """,
            (run_id, hotel_slug, *[
                Json(it[f]) if f == "recommended_action" and it.get(f) is not None
                else bool(it.get(f)) if f == "impact_estimated" else it.get(f)
                for f in ITEM_FIELDS
            ]),
        )


def generate(cur: Any, hotel: Hotel | str, cadence: str, *, day: date | None = None, now: datetime | None = None,
             trigger: str = "schedule", scheduled_local: datetime | None = None,
             created_by: int | None = None) -> dict:
    """Assemble and store a run. A scheduled run exists once per (hotel,
    cadence, day): a second call returns the stored one (`created` False),
    so a retried job slot never double-sends. Send-now runs always insert."""
    from clefs.db import Json

    if isinstance(hotel, str):
        hotel = get_hotel(cur, hotel)
    now = now or clock.now()
    day = day or htime.local_date(hotel, now)
    if trigger == "schedule":
        existing = find_run(cur, hotel.slug, cadence, day, trigger="schedule")
        if existing:
            return {**existing, "created": False}
    data = assemble(cur, hotel, cadence, day=day, now=now)
    if cadence == "weekly":
        from clefs.modules.brief import weekly

        rep = weekly.ensure_report(cur, hotel, day=day, now=now)
        data["blocks"]["report"] = weekly.summary(rep)
    numbered = []
    for n, it in enumerate(data["items"] + data["overdue"], start=1):
        numbered.append({**it, "item_no": n})
    status = "generated" if numbered or data["blocks"] else "empty"
    cur.execute(
        f"""
        INSERT INTO brf_runs (hotel_slug, cadence, brief_date, period_start, period_end, scheduled_local,
                              generated_at, trigger, status, headline, currency, at_stake_minor, payload, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (hotel_slug, cadence, brief_date) WHERE trigger = 'schedule' DO NOTHING
        RETURNING {RUN_COLUMNS}
        """,
        (hotel.slug, cadence, day, data["period_start"], data["period_end"], scheduled_local, now, trigger, status,
         data["headline"], hotel.currency, data["at_stake_minor"],
         Json({k: data[k] for k in ("verified", "blocks", "counts", "r_ref_major", "at_stake_value")}), created_by),
    )
    row = cur.fetchone()
    if row is None:  # lost a race with another ticker: theirs stands
        existing = find_run(cur, hotel.slug, cadence, day, trigger="schedule")
        return {**(existing or {}), "created": False}
    _insert_items(cur, row["id"], hotel.slug, numbered)
    core_signals.mark_surfaced(cur, [it["signal_id"] for it in numbered if it.get("signal_id")])
    return {**get_run(cur, hotel.slug, row["id"]), "created": True}


def find_run(cur: Any, hotel_slug: str, cadence: str, day: date, *, trigger: str | None = None) -> dict | None:
    """The newest run for that day (the scheduled one when `trigger` says so)."""
    cur.execute(
        f"""
        SELECT id FROM brf_runs WHERE hotel_slug = %s AND cadence = %s AND brief_date = %s
          AND (%s::TEXT IS NULL OR trigger = %s)
        ORDER BY id DESC LIMIT 1
        """,
        (hotel_slug, cadence, day, trigger, trigger),
    )
    row = cur.fetchone()
    return get_run(cur, hotel_slug, row["id"]) if row else None


def _run_to_api(row: dict) -> dict:
    out = dict(row)
    payload = out.pop("payload", None) or {}
    for k in ("brief_date", "period_start", "period_end"):
        if out.get(k) is not None:
            out[k] = out[k].isoformat()
    out["currency"] = (out.get("currency") or "").strip() or None
    out["at_stake_value"] = money.money_str(out.get("at_stake_minor"), out["currency"])
    out["verified"] = payload.get("verified")
    out["blocks"] = payload.get("blocks") or {}
    out["counts"] = payload.get("counts") or {}
    return out


def _item_to_api(row: dict) -> dict:
    out = dict(row)
    ccy = (out.get("currency") or "").strip() or None
    out["currency"] = ccy
    out["impact_value"] = money.money_str(out.get("impact_minor"), ccy)
    out["section_label"] = section_label(out.get("section"), out.get("detector"))
    for k in ("confidence", "importance", "urgency", "score"):
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


def get_run(cur: Any, hotel_slug: str, run_id: int) -> dict | None:
    """A run with its items (ranked, then overdue) and the live status of
    each item's action, so a page opened later shows what was decided."""
    cur.execute(f"SELECT {RUN_COLUMNS} FROM brf_runs WHERE hotel_slug = %s AND id = %s", (hotel_slug, run_id))
    row = cur.fetchone()
    if row is None:
        return None
    run = _run_to_api(dict(row))
    cur.execute(
        """
        SELECT i.*, a.status AS action_status FROM brf_items i LEFT JOIN actions a ON a.id = i.action_id
        WHERE i.run_id = %s ORDER BY i.item_no
        """,
        (run_id,),
    )
    items = [_item_to_api(dict(r)) for r in cur.fetchall()]
    run["items"] = [i for i in items if i["kind"] == "ranked"]
    run["overdue"] = [i for i in items if i["kind"] == "overdue"]
    return run


def list_runs(cur: Any, hotel_slug: str, *, cadence: str | None = None, limit: int = 30) -> list[dict]:
    cur.execute(
        f"""
        SELECT {RUN_COLUMNS} FROM brf_runs WHERE hotel_slug = %s AND (%s::TEXT IS NULL OR cadence = %s)
        ORDER BY brief_date DESC, id DESC LIMIT %s
        """,
        (hotel_slug, cadence, cadence, max(1, min(int(limit), 200))),
    )
    return [_run_to_api(dict(r)) for r in cur.fetchall()]


# ─── Recipients and magic links ───


def recipients(cur: Any, hotel_slug: str, settings: BriefSettings, *, user_id: int | None = None) -> list[dict]:
    """Active members who receive the brief: one named user, else the
    configured list, else every owner and gm."""
    if user_id is not None:
        ids, roles = [int(user_id)], None
    elif settings.recipient_user_ids:
        ids, roles = [int(i) for i in settings.recipient_user_ids], None
    else:
        ids, roles = None, ["owner", "gm"]
    cur.execute(
        """
        SELECT u.id, u.name, u.email, u.phone_e164, u.locale, m.role
        FROM hotel_memberships m JOIN users u ON u.id = m.user_id
        WHERE m.hotel_slug = %s AND m.is_active AND u.is_active
          AND (%s::BIGINT[] IS NULL OR u.id = ANY(%s))
          AND (%s::TEXT[] IS NULL OR m.role = ANY(%s))
        ORDER BY m.id
        """,
        (hotel_slug, ids, ids, roles, roles),
    )
    return [dict(r) for r in cur.fetchall()]


def mint_link(cur: Any, run: dict, user_id: int) -> str:
    """A 72 h magic link scoped to this hotel and to exactly the actions the
    run surfaced (CONTRACTS Part 2, magic sessions): the session decides
    those ids with the member's full role, and nothing else."""
    from clefs.auth import magic, tokens
    from clefs.db import Json
    from clefs.settings import get_settings

    action_ids = sorted({i["action_id"] for i in (run.get("items") or []) + (run.get("overdue") or []) if i.get("action_id")})
    scope = {core_actions.MAGIC_SCOPE_ACTION_IDS: action_ids}
    token = magic.create_link(
        cur, user_id=user_id, hotel_slug=run["hotel_slug"], purpose="brief",
        next_path=f"/app/{run['hotel_slug']}/brief?run={run['id']}", scope=scope, ttl=LINK_TTL, max_uses=5,
    )
    cur.execute(
        "INSERT INTO brf_links (token_hash, hotel_slug, user_id, run_id, scope, expires_at) VALUES (%s, %s, %s, %s, %s, %s)",
        (tokens.hash_token(token), run["hotel_slug"], user_id, run["id"], Json(scope), clock.now() + LINK_TTL),
    )
    return f"{get_settings().web_base_url}{magic.link_path(token)}"


# ─── Channels ───
# fn(cur, hotel, run, recipient, link) -> {ok, status, outbox_id?, error?}.
# Email ships here; the WhatsApp part of this lane registers "whatsapp"
# (owner_brief_v1 with buttons, the 24 h window flows). Until it does, the
# fallback below sends the plain template only when Meta approved it.

ChannelFn = Any
_channels: dict[str, ChannelFn] = {}


def register_channel(name: str, fn: ChannelFn) -> ChannelFn:
    _channels[name] = fn
    return fn


def _send_email(cur: Any, hotel: Hotel, run: dict, rcpt: dict, link: str | None) -> dict:
    from clefs import notify
    from clefs.modules.brief import render

    if not rcpt.get("email"):
        return {"ok": False, "status": "no_address"}
    subject = f"{hotel.name}: {run['headline']}"[:200]
    res = notify.send_email(
        rcpt["email"], subject,
        render.email_html(run, hotel_name=hotel.name, tz=hotel.timezone, locale=hotel.locale, link=link),
        text=render.text(run, tz=hotel.timezone, locale=hotel.locale, link=link),
        hotel_slug=hotel.slug, purpose=f"brief.{run['cadence']}", category="utility",
        idempotency_key=f"brief:{run['id']}:{rcpt['id']}:email", cur=cur,
    )
    return {"ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id, "error": res.error}


def _send_whatsapp_fallback(cur: Any, hotel: Hotel, run: dict, rcpt: dict, link: str | None) -> dict:
    from clefs import notify
    from clefs.modules.brief import api as brief_api
    from clefs.modules.brief import render

    if not rcpt.get("phone_e164"):
        return {"ok": False, "status": "no_address"}
    info = brief_api.wa_template_info(cur, hotel.slug, name="owner_brief_v1", language="en")
    if not info or info.get("status") != "APPROVED":
        return {"ok": False, "status": "template_not_approved"}
    head = [str(len(run.get("items") or []) + len(run.get("overdue") or [])),
            render.stake_text(run.get("at_stake_minor"), hotel.currency, hotel.locale,
                              estimated=render.any_estimate(run))]
    lines = render.budget_lines(run, tz=hotel.timezone, locale=hotel.locale, fixed=sum(len(x) for x in head))
    res = notify.send_whatsapp_template(
        rcpt["phone_e164"], "owner_brief_v1", "en", head + lines, hotel_slug=hotel.slug,
        purpose=f"brief.{run['cadence']}", header=f"Clefs brief · {run['brief_date']}"[:60], category="utility",
        idempotency_key=f"brief:{run['id']}:{rcpt['id']}:whatsapp", cur=cur,
    )
    return {"ok": bool(res.ok), "status": res.status, "outbox_id": res.outbox_id, "error": res.error}


_DEFAULT_CHANNELS: dict[str, ChannelFn] = {"email": _send_email, "whatsapp": _send_whatsapp_fallback}


# ─── Delivery ───


def deliver(cur: Any, run: dict, *, user_id: int | None = None, channels: list[str] | None = None,
            respect_quiet: bool = False) -> dict:
    """Send a stored run to its recipients: per person, one magic link and
    the first channel that accepts (settings order, WhatsApp then email by
    default). Records the outcome on the run; returns {ok, sent_to, delivery}.
    `respect_quiet` (scheduled runs): a person inside their quiet hours gets
    a "deferred_quiet" outcome and deliver_deferred sends it later."""
    from clefs.db import Json

    hotel = get_hotel(cur, run["hotel_slug"])
    settings = load_settings(cur, hotel.slug)
    order = channels or list(settings.channels) or ["email"]
    people = recipients(cur, hotel.slug, settings, user_id=user_id)
    delivery: list[dict] = []
    sent_to: list[dict] = []
    now = clock.now()
    deferred = 0
    for p in people:
        until = quiet.quiet_until(hotel, settings.quiet_hours, p["id"], now) if respect_quiet else None
        if until is not None:
            delivery.append({"user_id": p["id"], "channel": None, "ok": False, "status": "deferred_quiet",
                             "until": until.isoformat(), "attempts": []})
            deferred += 1
            continue
        link = mint_link(cur, run, p["id"])
        outcome = {"user_id": p["id"], "channel": None, "ok": False, "status": "no_channel", "attempts": []}
        for ch in order:
            fn = _channels.get(ch) or _DEFAULT_CHANNELS.get(ch)
            if fn is None:
                continue
            cur.execute("SAVEPOINT brf_send")
            try:
                res = fn(cur, hotel, run, p, link) or {}
                cur.execute("RELEASE SAVEPOINT brf_send")
            except Exception as e:  # noqa: BLE001 - one bad channel must not stop the others
                cur.execute("ROLLBACK TO SAVEPOINT brf_send")
                log.warning("brief channel %s failed for user %s", ch, p["id"], exc_info=True)
                res = {"ok": False, "status": "error", "error": str(e)[:200]}
            outcome["attempts"].append({"channel": ch, **{k: res.get(k) for k in ("ok", "status", "outbox_id", "error")}})
            if res.get("ok"):
                outcome.update(channel=ch, ok=True, status=res.get("status") or "queued", outbox_id=res.get("outbox_id"))
                sent_to.append({"user_id": p["id"], "name": p["name"], "channel": ch})
                break
            outcome["status"] = res.get("status") or "failed"
        delivery.append(outcome)
    cur.execute("SELECT delivery FROM brf_runs WHERE id = %s", (run["id"],))
    status = run_status(list((cur.fetchone() or {}).get("delivery") or []) + delivery) if people else "failed"
    cur.execute(
        "UPDATE brf_runs SET status = %s, delivery = delivery || %s::jsonb, sent_at = COALESCE(sent_at, %s) WHERE id = %s",
        (status, Json(delivery), clock.now() if sent_to else None, run["id"]),
    )
    if people and not sent_to and not deferred:
        # No recipients at all is a configuration state (a hotel with no
        # owner or gm yet), not a failed delivery worth a daily alert.
        _raise_delivery_failed(cur, hotel, run, reason="all_channels_failed")
    return {"ok": bool(sent_to), "status": status, "sent_to": sent_to, "delivery": delivery}


def run_status(delivery: list[dict]) -> str:
    """A run's status from every outcome it recorded, the latest per person:
    everyone reached = sent; nobody yet but some waiting on quiet hours =
    generated (not sent yet); a mix = partial; nobody reachable = failed."""
    last: dict[Any, dict] = {}
    for d in delivery:
        last[d.get("user_id")] = d
    if not last:
        return "failed"
    ok = sum(1 for d in last.values() if d.get("ok"))
    waiting = sum(1 for d in last.values() if d.get("status") == "deferred_quiet")
    if ok == len(last):
        return "sent"
    if ok:
        return "partial"
    return "generated" if waiting else "failed"


def deliver_deferred(cur: Any, hotel_slug: str, *, now: datetime | None = None) -> int:
    """Send scheduled briefs that quiet hours held back, once the window
    ended (called by the brief.escalate pass every 15 min). Only runs from
    the last 36 h: a brief held past its next edition is not worth sending."""
    now = now or clock.now()
    cur.execute(
        """
        SELECT * FROM brf_runs WHERE hotel_slug = %s AND generated_at >= %s
          AND delivery @> '[{"status": "deferred_quiet"}]'::jsonb ORDER BY id
        """,
        (hotel_slug, now - timedelta(hours=36)),
    )
    sent = 0
    for run in [dict(r) for r in cur.fetchall()]:
        last: dict[Any, dict] = {}
        for d in run["delivery"] or []:
            last[d.get("user_id")] = d
        for uid, d in last.items():
            if d.get("status") != "deferred_quiet" or datetime.fromisoformat(d["until"]) > now:
                continue
            out = deliver(cur, run, user_id=int(uid), respect_quiet=True)
            sent += len(out["sent_to"])
    return sent


def _raise_delivery_failed(cur: Any, hotel: Hotel, run: dict, *, reason: str) -> None:
    """brief.delivery.failed (02 spec 6.4): the owner sees it in the console
    and in the next brief that does get through."""
    core_signals.upsert_signal(cur, hotel.slug, core_signals.SignalIn(
        module="brief", kind="brief.delivery.failed", dedupe_key=f"brief:delivery_failed:{run['cadence']}:{run['brief_date']}",
        title=f"The {run['cadence']} brief for {run['brief_date']} was not delivered",
        summary="Every channel failed for every recipient",
        severity=3, confidence=1.0, owner_role="owner",
        evidence=[core_signals.Evidence(kind="row", label="Brief run", ref={"table": "brf_runs", "id": run["id"]})],
    ))


# ─── push_now (brief.api door) ───


def push_now(cur: Any, hotel_slug: str, *, cadence: str = "daily", user_id: int | None = None,
             trigger: str = "send_now") -> dict:
    """Generate a fresh run and send it now. With `user_id`, only that
    member receives it (the console's "send me a test")."""
    if cadence not in CADENCES:
        return {"ok": False, "brief_id": None, "sent_to": [], "reason": "invalid_cadence"}
    hotel = get_hotel(cur, hotel_slug)
    run = generate(cur, hotel, cadence, trigger=trigger, created_by=user_id)
    out = deliver(cur, run, user_id=user_id)
    res = {"ok": out["ok"], "brief_id": run["id"], "sent_to": out["sent_to"], "status": out["status"]}
    if not out["ok"]:
        res["reason"] = "no_recipients" if not out["delivery"] else "not_delivered"
    return res


# ─── Signal kinds ───

core_signals.register_signal_kind(
    "brief.delivery.failed", label="Brief not delivered", default_owner_role="owner",
    section="service_risk", cadence="daily", icon="brief",
)


# ─── Scheduling (clefs.jobs; hotel-local, same-day catch-up) ───
# The daily slot follows hotels.brief_time_local (default 07:00 Asia/Dubai,
# i.e. 03:00 UTC, Canon: UAE defaults). A missed tick catches up later the
# same local day and never the next (jobs.due_slot), so no stale brief goes
# out at noon tomorrow. job_runs makes every slot run once whoever ticks.

from clefs import jobs  # noqa: E402 - registration at import, after the functions it calls


def _scheduled_run(ctx: jobs.JobCtx, cadence: str) -> dict:
    hotel = ctx.hotel
    settings = load_settings(ctx.cur, hotel.slug)
    if not getattr(settings, f"{cadence}_enabled"):
        return {"skipped": "disabled"}
    if not recipients(ctx.cur, hotel.slug, settings):
        # Legacy hotels without an owner or gm login: nobody to brief, so no
        # run is stored and no "not delivered" alert is raised every day.
        return {"skipped": "no_recipients"}
    run = generate(ctx.cur, hotel, cadence, trigger="schedule", scheduled_local=ctx.scheduled_for)
    if not run.get("created"):
        return {"run_id": run.get("id"), "skipped": "already_generated"}
    if run["status"] == "empty" and not settings.send_when_empty:
        return {"run_id": run["id"], "skipped": "empty"}
    out = deliver(ctx.cur, run, respect_quiet=True)
    return {"run_id": run["id"], "status": out["status"], "sent": len(out["sent_to"])}


@jobs.scheduled("brief.daily", jobs.daily_at("07:00", setting="brief_time_local"))
def _daily_job(ctx: jobs.JobCtx) -> dict:
    return _scheduled_run(ctx, "daily")


@jobs.scheduled("brief.weekly", jobs.weekly_at(1, "08:00"))
def _weekly_job(ctx: jobs.JobCtx) -> dict:
    """Monday 08:00 hotel-local (02 spec 6.4): the weekly tier and its
    verified-value report."""
    return _scheduled_run(ctx, "weekly")


@jobs.scheduled("brief.monthly", jobs.monthly_at(1, "08:00"))
def _monthly_job(ctx: jobs.JobCtx) -> dict:
    """The 1st at 08:00 hotel-local: the P&L pulse slot for last month."""
    return _scheduled_run(ctx, "monthly")
