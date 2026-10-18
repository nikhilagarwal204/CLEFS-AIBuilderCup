"""
Amenity action types (Canon 7, 17). All three are human tasks: nothing here
touches stock, folios or suppliers by itself.

- am.investigate_variance: count the closets and review the week's issuance
  after a spike. 02 spec 6.3: VERIFIED when the next 14 days (after the task
  is marked done) for the item and the flagged floor are below the spike
  threshold, V/E <= 15% or V money <= AED 100. Anything above it is
  not_verified: a dip that is still a leak is not a fix. The value is the
  flagged week's variance minus the new window's variance pro-rated to 7
  days (an average of what was observed, never a projection). Attribution is
  "caused": the task is the control (counts, locked closets, one-for-one
  issue) that stops the leak, and the baseline is the hotel's own flagged
  week, so the value counts toward Clefs-verified profit.
- am.replace_sup_item: agree single-use plastic replacements (compliance).
- am.post_minibar: post missing minibar charges. A folio change, so it is
  always approved by a person (Canon autonomy defaults).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from clefs.core import actions
from clefs.core import time as ctime
from clefs.core.money import to_minor
from clefs.modules.amenities import variance
from clefs.modules.amenities.constants import (
    SPIKE_MIN_MAJOR,
    SPIKE_RATIO,
    VERIFY_DAYS,
    VERIFY_MIN_COVERED_DAYS,
    WINDOW_DAYS,
)
from clefs.modules.amenities.detectors import INVESTIGATE

REPLACE_SUP = "am.replace_sup_item"
POST_MINIBAR = "am.post_minibar"


# ─── Investigation verifier ───


def below_threshold(window: dict, *, min_minor: int) -> bool:
    """The negation of the spike rule's ratio and money legs: V/E <= 15% or
    V money <= the bar. z is left out on purpose: a quiet history cannot turn
    a still-leaking window into a verified fix."""
    v, e, vm = window["variance"], window["expected"], int(window["variance_minor"])
    if v <= 0 or vm <= min_minor:
        return True
    return e > 0 and v / e <= SPIKE_RATIO


def _scope(p: dict) -> str | None:
    """The flagged floor, or None for hotel-wide. '' is "not floor-tagged",
    not a floor: scoping to it would compare untagged issuance with no
    expected use, so it falls back to the item hotel-wide."""
    f = p.get("floor")
    return f if isinstance(f, str) and f.strip() else None


def verify_investigation(ctx: Any) -> actions.VerifyResult:
    p, hotel = ctx.params or {}, ctx.hotel
    item_id = p.get("item_id")
    if not item_id or not p.get("window_end"):
        return actions.VerifyResult(status="pending")
    flagged_end = date.fromisoformat(p["window_end"])
    floor = _scope(p)
    done_at = (ctx.action or {}).get("executed_at")
    done_d = ctime.business_date(hotel, done_at) if done_at else ctime.business_date(hotel)
    # The window starts the day after the task is done, and never inside the
    # flagged week itself.
    start = max(done_d, flagged_end) + timedelta(days=1)
    end = start + timedelta(days=VERIFY_DAYS - 1)
    if ctime.business_date(hotel) - timedelta(days=1) < end:
        return actions.VerifyResult(status="pending")
    if variance.covered_days(ctx.cur, hotel.slug, start, end) < VERIFY_MIN_COVERED_DAYS:
        return actions.VerifyResult(status="pending")

    flagged = variance.scoped_sum(ctx.cur, hotel.slug, item_id, flagged_end - timedelta(days=WINDOW_DAYS - 1),
                                  flagged_end, floor=floor)
    before = flagged["variance_minor"] if flagged["rows"] else (0 if floor else int(p.get("v7_minor") or 0))
    after = variance.scoped_sum(ctx.cur, hotel.slug, item_id, start, end, floor=floor)
    after_7 = int((Decimal(after["variance_minor"]) * WINDOW_DAYS / VERIFY_DAYS).to_integral_value())
    saved = before - max(after_7, 0)
    below = below_threshold(after, min_minor=to_minor(SPIKE_MIN_MAJOR, hotel.currency))
    scope = f"floor {floor}" if floor else "all floors"
    evidence = [
        {"kind": "calc", "label": f"Above expected, flagged week ({scope})", "value": before,
         "ref": {"start": (flagged_end - timedelta(days=WINDOW_DAYS - 1)).isoformat(), "end": flagged_end.isoformat()}},
        {"kind": "calc", "label": f"Above expected, {VERIFY_DAYS} days after the count ({scope})",
         "value": after["variance_minor"], "ref": {"start": start.isoformat(), "end": end.isoformat(),
                                                    "issued": str(after["issued"]),
                                                    "expected": str(after["expected"])}},
        {"kind": "calc", "label": "Per week after the count", "value": after_7},
        {"kind": "calc", "label": "Below the spike threshold", "value": "yes" if below else "no"},
    ]
    if not below or saved <= 0:
        return actions.VerifyResult(status="not_verified", outcome_minor=0, currency=hotel.currency,
                                    evidence=evidence, confidence=0.7)
    return actions.VerifyResult(status="verified", outcome_minor=saved, currency=hotel.currency,
                                attribution="caused", evidence=evidence, confidence=0.7)


# ─── Registration ───

actions.register_action_type(actions.ActionType(
    key=INVESTIGATE, module="amenities", label="Count stock and review amenity issuance",
    # Day 15 is the first day the 14th business date after "done" is rolled
    # up; the extra weeks absorb a late PMS feed before it gives up.
    verifier=verify_investigation, verify_after=timedelta(days=VERIFY_DAYS + 1), verify_every=timedelta(days=1),
    verify_until=timedelta(days=VERIFY_DAYS + 21), default_owner_role="dept_head", default_level="approve",
    max_level="approve", decide_perm="amenities.manage", counts_toward_verified_profit=True,
    description="After an amenity spike: count the floor closets, compare with the issuance log and tighten the "
                "controls. Verified when the next 14 days for the item and floor are back below the spike "
                "threshold.",
))

actions.register_action_type(actions.ActionType(
    key=REPLACE_SUP, module="amenities", label="Replace single-use plastic amenities",
    default_owner_role="gm", default_level="suggest", max_level="approve", decide_perm="amenities.manage",
    description="Agree bamboo, paper or refillable replacements with the supplier for items flagged single-use "
                "plastic.",
))

actions.register_action_type(actions.ActionType(
    key=POST_MINIBAR, module="amenities", label="Post missing minibar charges",
    default_owner_role="front_desk", default_level="approve", max_level="approve", always_approve=True,
    decide_perm="amenities.manage",
    description="Check minibar refill sheets against folio postings and post what was consumed. A folio change: "
                "always approved by a person.",
))
