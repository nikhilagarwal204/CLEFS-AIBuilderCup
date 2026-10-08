"""
The step library (04 spec 3.1): names, action types, categories and the
daily-cap collision order. Pure constants, imported by every journeys file.
"""

from __future__ import annotations

from datetime import timedelta

# ─── Steps ───

STEPS: tuple[str, ...] = ("j1", "j2", "j3", "j4", "j5", "j6_pre", "j6_post", "j7", "j8")
# Steps that send a guest message (J6 gates are internal: no template).
SEND_STEPS: tuple[str, ...] = ("j1", "j2", "j3", "j4", "j5", "j7", "j8")
GATE_STEPS: tuple[str, ...] = ("j6_pre", "j6_post")
MARKETING_STEPS = frozenset({"j3", "j8"})
UTILITY_STEPS = frozenset({"j1", "j2", "j4", "j5", "j7"})
# J1 and J4 are transactional: they always send, but they count toward the
# daily cap (3.5).
TRANSACTIONAL_STEPS = frozenset({"j1", "j4"})
PRE_STAY_STEPS = frozenset({"j1", "j2", "j3", "j4"})

# Daily-cap collision priority, highest first (3.5): J1 > J4 > J5 > J7 > J2 > J3 > J8.
PRIORITY: tuple[str, ...] = ("j1", "j4", "j5", "j7", "j2", "j3", "j8")

ACTION_TYPES: dict[str, str] = {
    "j1": "guests.journey_j1_confirm",
    "j2": "guests.journey_j2_planning",
    "j3": "guests.journey_j3_offer",
    "j4": "guests.journey_j4_checkin",
    "j5": "guests.journey_j5_daycheck",
    "j7": "guests.journey_j7_feedback",
    "j8": "guests.journey_j8_rebook",
}
STEP_OF_TYPE: dict[str, str] = {v: k for k, v in ACTION_TYPES.items()}
CHECKOUT_RECOVERY = "guests.checkout_recovery"
OFFER_ACCEPT = "guests.offer_accept"

LABELS: dict[str, str] = {
    "j1": "Booking confirmation",
    "j2": "Planning info",
    "j3": "Pre-arrival offer",
    "j4": "Check-in instructions",
    "j5": "Day-1 check",
    "j6_pre": "Check-out gate",
    "j6_post": "Post-stay gate",
    "j7": "Stay feedback",
    "j8": "Rebooking offer",
}

# The category we expect Meta to assign (3.6). J7 is UTILITY at Meta but
# budgeted as marketing (A1): journey_cost charges it at the marketing rate.
EXPECTED_CATEGORY: dict[str, str] = {s: ("MARKETING" if s in MARKETING_STEPS else "UTILITY") for s in SEND_STEPS}
CHARGED_AS_MARKETING = frozenset({"j3", "j8", "j7"})

# Marketing phase of a marketing step, for can_send_marketing's caps.
PHASE: dict[str, str] = {"j3": "pre_stay", "j8": "post_stay"}
SEND_SOURCE: dict[str, str] = {"j3": "journey_j3", "j8": "journey_j8"}

# Segments that never get a J3 offer (3.1): the booker is not the guest.
NO_J3_SEGMENTS = frozenset({"GROUP", "CORPORATE", "MICE", "CREW"})

# ─── Timing constants ───

J1_UTILITY_EXEMPT = timedelta(hours=2)  # J1 ignores the utility window this long after booking
LATE_BOOKING = timedelta(hours=24)  # booked closer to check-in than this: J4 covers J1
CLAIM_TIMEOUT = timedelta(minutes=10)  # a claimed row older than this is released
CAP_DEFER = timedelta(days=1)  # a daily-cap loser moves one day
META_131049_DELAY = timedelta(hours=24)
UTILITY_VERIFY_WINDOW = timedelta(hours=48)
CONSENT_TOKEN_TTL = timedelta(hours=24)
OPTOUT_ATTRIBUTION = timedelta(hours=72)
REPLY_MATCH_WINDOW = timedelta(hours=24)

# Plan states (3.3). Unsent = no message left the building yet.
UNSENT = ("planned", "deferred", "claimed", "proposed")
LIVE_SENT = ("sent", "delivered", "read", "replied")


def step_rank(step: str) -> int:
    """Lower is more important on a daily-cap collision."""
    return PRIORITY.index(step) if step in PRIORITY else len(PRIORITY)


def phase_of(step: str) -> str:
    """Marketing phase for can_send_marketing's caps, also used when Meta
    recategorised a utility step to MARKETING (it then follows those rules)."""
    if step in PHASE:
        return PHASE[step]
    if step in PRE_STAY_STEPS:
        return "pre_stay"
    return "in_stay" if step == "j5" else "post_stay"
