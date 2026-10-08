"""
Request priority (03 spec 1.3 step 6).

calculate_priority is the monolith's heuristic, unchanged (moved here from
main.py via legacy.py): the golden legacy contract pins its outcomes. v2 adds
three rules on top, in apply_rules:
  - an AC request while it is hot outside becomes high and L1. Hot is
    demand.api.outdoor_temp_c above the hotel's threshold (40 C); with no
    reading (the Demand stub returns None until wave 2) June-September
    11:00-17:00 hotel time counts as hot (Canon 12);
  - a VIP or returning guest is at least medium;
  - any L1 is high.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from clefs.core import time as htime
from clefs.modules.demand import api as demand_api
from clefs.modules.service import constants as C

log = logging.getLogger("clefs.service.priority")

# ─── Legacy department mapping (mirrors the dashboard's CATEGORIES) ───
CATEGORY_DEPARTMENT: dict[str, str] = {
    "Towel": "Housekeeping",
    "Clean Sheets": "Housekeeping",
    "Cleaning": "Housekeeping",
    "In-Room Dining": "Food & Beverage",
    "Food": "Food & Beverage",
    "Water Supply": "Maintenance",
    "Water": "Housekeeping",
    "Wake-Up Call": "Front Desk",
    "Maintenance": "Maintenance",
    "Luggage Assistance": "Concierge",
    "Spa Booking": "Concierge",
    "Billing": "Front Desk",
    "Direct Conversation": "Front Desk",
    "Checkout": "Front Desk",
    "Discount": "Front Desk",
    "Front Desk": "Front Desk",
    "Other": "General",
}

_HIGH_KEYWORDS = {
    "urgent", "emergency", "immediately", "asap", "right away", "right now",
    "broken", "not working", "doesn't work", "won't work", "stopped working",
    "leak", "leaking", "flood", "flooded", "overflow",
    "sick", "pain", "hurt", "fever", "injury", "help",
    "no hot water", "no water", "no power", "no electricity", "no ac", "no heat",
    "can't sleep", "cannot sleep", "can't breathe", "unsafe", "safety",
    "malfunction", "stuck", "trapped",
}

_LOW_KEYWORDS = {
    "whenever", "no rush", "no hurry", "sometime", "when you can",
    "when free", "if possible", "not urgent", "for tomorrow",
    "later today", "at your convenience",
}

# Request types that are inherently higher urgency
_HIGH_PRIORITY_TYPES = {"Maintenance", "Water Supply"}
_LOW_PRIORITY_TYPES = {"Spa Booking", "Wake-Up Call"}


def calculate_priority(
    request_type: str,
    description: str,
    category: str,
    loyalty_tier: Optional[str] = None,
) -> str:
    """
    Determines request priority from type, description keywords, and guest loyalty.
    Used as a smart fallback / override when the AI doesn't specify priority,
    or to validate that an AI-assigned 'medium' wasn't under-assessed.
    """
    desc = (description or "").lower()

    # 1. Hard urgency keywords in description -> always high
    if any(kw in desc for kw in _HIGH_KEYWORDS):
        return "high"

    # 2. Explicit low-urgency phrasing -> low (unless blocked by tier)
    if any(kw in desc for kw in _LOW_KEYWORDS):
        if loyalty_tier not in ("globalist", "explorist"):
            return "low"

    # 3. Request type defaults
    if request_type in _HIGH_PRIORITY_TYPES or category in _HIGH_PRIORITY_TYPES:
        return "high"
    if request_type in _LOW_PRIORITY_TYPES:
        return "low"

    # 4. Top-tier guests get elevated priority on any standard request
    if loyalty_tier == "globalist":
        return "high"
    if loyalty_tier == "explorist":
        return "medium"  # already medium, but explicit

    return "medium"


def final_priority(hint: Optional[str], smart: str) -> str:
    """Trust the caller if they passed something higher than the heuristic;
    otherwise take the heuristic. Unknown hints count as medium."""
    ai = hint if hint in C.PRIORITY_RANK else "medium"
    return ai if C.PRIORITY_RANK.get(ai, 1) >= C.PRIORITY_RANK.get(smart, 1) else smart


def at_least(priority: str, floor: str) -> str:
    return priority if C.PRIORITY_RANK.get(priority, 1) >= C.PRIORITY_RANK[floor] else floor


# ─── v2 rules ───


def is_hot(hotel: Any, at: datetime, threshold_c: float) -> tuple[bool, float | None, str]:
    """(hot, temperature, basis). The Demand weather cache first; without a
    reading, June-September 11:00-17:00 hotel-local counts as hot."""
    temp = None
    try:
        temp = demand_api.outdoor_temp_c(hotel, at)
    except Exception:  # a broken weather feed must never block a request
        log.exception("outdoor_temp_c failed for %s", getattr(hotel, "slug", hotel))
    if temp is not None:
        return float(temp) > threshold_c, float(temp), "weather"
    local = at.astimezone(htime.tz_of(hotel))
    hot = 6 <= local.month <= 9 and 11 <= local.hour < 17
    return hot, None, "heuristic"


def is_vip(guest: dict | None) -> bool:
    """VIP or returning, from the allowlisted guest context of the room."""
    if not guest:
        return False
    # guests.api marks a recognised repeat guest (consent-gated profiles)
    if guest.get("returningGuest"):
        return True
    tier = str(guest.get("loyaltyTier") or "").strip().lower()
    if tier in C.VIP_TIERS:
        return True
    prefs = guest.get("preferences") or {}
    tags = prefs.get("tags") if isinstance(prefs, dict) else None
    if isinstance(tags, list):
        return any(str(t).strip().lower() in C.VIP_TAGS for t in tags)
    return False


def apply_rules(
    priority: str,
    severity: str,
    *,
    request_type_key: str | None,
    hotel: Any,
    at: datetime,
    guest: dict | None,
    heat_threshold_c: float,
) -> tuple[str, str, dict]:
    """(priority, severity, flags) after the three v2 rules."""
    flags: dict = {}
    if request_type_key in C.AC_TYPES:
        hot, temp, basis = is_hot(hotel, at, heat_threshold_c)
        if hot:
            priority, severity = "high", "L1"
            flags["heat"] = {"temp_c": temp, "basis": basis}
    if is_vip(guest):
        priority = at_least(priority, "medium")
        flags["vip"] = True
    if severity == "L1":
        priority = "high"
    return priority, severity, flags
