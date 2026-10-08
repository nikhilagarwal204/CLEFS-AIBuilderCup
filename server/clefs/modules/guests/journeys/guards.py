"""
Journey guards (04 spec 3.5), checked when the dispatcher claims a plan and
again in the executor right before the send.

A verdict is one of:
- pass (send now),
- defer to an instant (outside the window, lost a daily-cap collision, a
  complaint is open), which the dispatcher writes as `deferred`, or skips
  when that instant is past valid_until,
- skip with a reason (no contact, no offer, caps, stay gone).

The dial guard is the ledger's (create_action: off suppresses, observe
records): it is not re-implemented here.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from clefs.modules.guests import marketing, stays_port, timeutil
from clefs.modules.guests import settings as gsettings
from clefs.modules.guests.journeys import channels, steps
from clefs.modules.service import api as service_api

log = logging.getLogger("clefs.guests.journeys.guards")

HARD_SKIPS = frozenset({"no_eligible_contact", "alias_expired", "ota_contact_off"})


@dataclass
class Verdict:
    ok: bool
    reason: str | None = None
    defer_to: datetime | None = None
    resolution: channels.Resolution | None = None
    offer: dict | None = None

    @property
    def skip(self) -> bool:
        return not self.ok and self.defer_to is None


def _skip(reason: str, res: channels.Resolution | None = None) -> Verdict:
    return Verdict(False, reason, None, res)


def _defer(plan: dict, until: datetime, reason: str, res: channels.Resolution | None = None) -> Verdict:
    """Defer within valid_until, else skip with the same reason."""
    if until >= plan["valid_until"]:
        return _skip("window_expired" if reason in ("outside_window",) else reason, res)
    return Verdict(False, reason, until, res)


# ─── Offers (J3, J8) ───


def live_offer(cur: Any, hotel_slug: str, step: str, on: date) -> dict | None:
    """The newest owner-approved, active offer for the step valid on `on`."""
    cur.execute(
        "SELECT * FROM gst_journey_offers WHERE hotel_slug = %s AND step = %s AND active AND approved_at IS NOT NULL "
        "AND valid_from <= %s AND valid_to >= %s ORDER BY approved_at DESC, id DESC LIMIT 1",
        (hotel_slug, step, on, on),
    )
    row = cur.fetchone()
    return dict(row) if row else None


# ─── Windows ───


def window_for(cfg: gsettings.GuestsSettings, category: str) -> tuple[Any, Any]:
    """Half-open [t0, t1) in hotel time. Marketing: settings may narrow
    09:00-18:00, never widen it (clamped here too, whatever is stored)."""
    if category == "MARKETING":
        w = cfg.marketing_window
        return max(w.t0, gsettings.MARKETING_EARLIEST), min(w.t1, gsettings.MARKETING_LATEST)
    return cfg.utility_window.t0, cfg.utility_window.t1


def window_start(hotel: Any, cfg: gsettings.GuestsSettings, plan: dict, stay: dict, category: str,
                 now: datetime) -> datetime:
    """`now` when the step may go now, else the next window start. J1 is
    exempt from the utility window within 2 h of booking."""
    if plan["step"] == "j1" and category == "UTILITY":
        booked = stays_port.as_datetime(stay.get("booked_at"))
        if booked is not None and now - booked <= steps.J1_UTILITY_EXEMPT:
            return now
    t0, t1 = window_for(cfg, category)
    return timeutil.next_window_start(hotel, now, t0, t1)


# ─── Daily cap ───


def journey_sends_today(cur: Any, hotel_slug: str, h: bytes, d0: datetime, d1: datetime, *,
                        exclude_plan: int | None = None) -> list[str]:
    """Steps of business-initiated journey messages sent to this identity
    in the local day [d0, d1), across every reservation."""
    cur.execute(
        """
        SELECT pl.step FROM gst_journey_plans pl JOIN gst_contact_points cp ON cp.id = pl.contact_point_id
        WHERE pl.hotel_slug = %s AND cp.value_hmac = %s AND pl.last_attempt_at >= %s AND pl.last_attempt_at < %s
          AND pl.status IN ('sent', 'delivered', 'read', 'replied') AND pl.id <> %s
        """,
        (hotel_slug, bytes(h), d0, d1, exclude_plan or 0),
    )
    return [r["step"] for r in cur.fetchall()]


def guest_wrote_today(cur: Any, hotel_slug: str, h: bytes, d0: datetime, d1: datetime) -> bool:
    cur.execute(
        "SELECT 1 FROM inbound_messages WHERE hotel_slug = %s AND sender_hmac = %s AND received_at >= %s "
        "AND received_at < %s LIMIT 1",
        (hotel_slug, bytes(h), d0, d1),
    )
    return cur.fetchone() is not None


# ─── The check ───


def _complaint_open(cur: Any, hotel: Any, stay: dict, now: datetime) -> bool:
    ci = stays_port.as_date(stay.get("check_in"))
    since = now - timedelta(days=60) if ci is None else timeutil.at_local(hotel, ci - timedelta(days=1), time(0))
    hotel_slug = hotel.slug
    try:
        return bool(service_api.has_open_complaint(cur, hotel_slug, room=stay.get("room"),
                                                   reservation_id=stay["reservation_id"], since=since))
    except Exception:  # Service's answer is advice; a failure must not unpause marketing
        log.exception("has_open_complaint failed at %s", hotel_slug)
        return True


def check(cur: Any, hotel: Any, cfg: gsettings.GuestsSettings, plan: dict, stay: dict | None, *,
          now: datetime, sync: bool = True, only: tuple[str, ...] | None = None) -> Verdict:
    """Every guard for one send-step plan at `now` (3.5)."""
    step = plan["step"]
    if stay is None:
        return _skip("unknown_reservation")
    status = (stay.get("status") or "").upper()
    if status in ("CANCELLED", "NO_SHOW"):
        return _skip("stay_cancelled")
    if step == "j5" and status == "CHECKED_OUT":
        return _skip("checked_out")
    if step not in cfg.enabled_steps:
        return _skip("step_disabled")
    if plan["valid_until"] <= now:
        return _skip("window_expired")
    offer = None
    if step in steps.MARKETING_STEPS:
        offer = live_offer(cur, hotel.slug, step, timeutil.local(hotel, now).date())
        if offer is None:
            return _skip("no_offer")
    res = channels.resolve(cur, hotel, cfg, stay, step, at=now, sync=sync, only=only)
    if res.reason in HARD_SKIPS:
        return _skip(res.reason, res)
    category = res.choice.category if res.choice else steps.EXPECTED_CATEGORY[step]
    start = window_start(hotel, cfg, plan, stay, category, now)
    if start > now:
        return _defer(plan, start, "outside_window", res)
    if res.choice is None:
        return Verdict(True, res.reason, None, res, offer)  # template_missing: the gate records it as observed
    h = bytes(res.choice.point["value_hmac"])
    d0, d1 = timeutil.day_bounds(hotel, now)
    if not guest_wrote_today(cur, hotel.slug, h, d0, d1):
        sent = journey_sends_today(cur, hotel.slug, h, d0, d1, exclude_plan=plan["id"])
        if sent and step not in steps.TRANSACTIONAL_STEPS:
            # The loser of a collision moves to the next day's window (J1 and
            # J4 always send, but they count, so they make others wait).
            return _defer(plan, window_start(hotel, cfg, plan, stay, category, d1), "cap_daily", res)
    if category == "MARKETING":
        if step != "j7" and _complaint_open(cur, hotel, stay, now):
            return _defer(plan, window_start(hotel, cfg, plan, stay, category, d1), "complaint_open", res)
        phase = steps.phase_of(step)
        ok, why = marketing.can_send_marketing(cur, hotel.slug, identity_hmacs=[h], channel=res.choice.channel,
                                               at=now, phase=phase)
        if not ok:
            if why == "cap_daily":
                return _defer(plan, window_start(hotel, cfg, plan, stay, category, d1), "cap_daily", res)
            if why == "outside_window":
                return _defer(plan, window_start(hotel, cfg, plan, stay, category, now), "outside_window", res)
            return _skip(why, res)
    return Verdict(True, None, None, res, offer)
