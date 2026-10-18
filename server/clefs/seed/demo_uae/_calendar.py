"""
Pure demand curves for the demo: target occupancy and ADR by night, and the
2024-2025 baseline formula. No database, no clock: tests call these
directly, and the stays generator samples around them.

Event dates follow Demand Radar's curated calendar
(modules/demand/seeds/venue_events_2026q4_2027.yaml): CABSAT 5-7 Oct 2026 at
DEC, GITEX Global 7-11 Dec 2026 at DEC, NYE 31 Dec. "Demo Summer Expo
(DEMO)" at DWTC 18-20 Aug 2026 is the planted calibration event (02 spec 9).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

# ─── Events ───


@dataclass(frozen=True)
class DemoEvent:
    key: str
    start: date
    end: date
    occ: float        # target occupancy on its nights (final, not on the books)
    adr_mult: float   # ADR multiplier on its nights
    early: float      # share of its bookings made far ahead (pace)
    lead_in: int = 1  # nights before the start that fill too (arrivals)


EVENTS: tuple[DemoEvent, ...] = (
    DemoEvent("demo-summer-expo-2026", date(2026, 8, 18), date(2026, 8, 20), 0.74, 1.20, 0.35),
    DemoEvent("cabsat-2026", date(2026, 10, 5), date(2026, 10, 7), 0.88, 1.30, 0.50),
    DemoEvent("gitex-global-2026", date(2026, 12, 7), date(2026, 12, 11), 0.97, 1.56, 0.65, lead_in=2),
    DemoEvent("nye-dubai-2026", date(2026, 12, 30), date(2026, 12, 31), 0.96, 2.40, 0.70),
)


def event_on(d: date) -> DemoEvent | None:
    for ev in EVENTS:
        if ev.start - timedelta(days=ev.lead_in) <= d <= ev.end:
            return ev
    return None


# ─── Nightly targets (2026-27 demo window) ───

# Month -> (occupancy, net ADR in AED). Summer sits in the spec's ranges
# (occupancy 55-66%, ADR 240-330); Q4 is Dubai's high season.
MONTHS: dict[int, tuple[float, float]] = {
    1: (0.78, 375), 2: (0.80, 380), 3: (0.74, 350), 4: (0.70, 330), 5: (0.64, 300), 6: (0.60, 300),
    7: (0.58, 262), 8: (0.60, 250), 9: (0.63, 292), 10: (0.73, 355), 11: (0.80, 395), 12: (0.82, 415),
}
# ISO weekday -> occupancy shift. A Deira 3-star fills on Thursday and Friday
# nights (weekend arrivals, regional visitors) and dips on Sunday.
DOW_OCC = {1: -0.02, 2: 0.0, 3: 0.0, 4: 0.03, 5: 0.04, 6: 0.01, 7: -0.04}
SUMMER = (6, 7, 8, 9)


def target_occ(d: date) -> float:
    ev = event_on(d)
    if ev is not None:
        return ev.occ
    occ = MONTHS[d.month][0] + DOW_OCC[d.isoweekday()]
    if d.month in SUMMER:
        occ = min(max(occ, 0.55), 0.66)
    return occ


def target_adr(d: date) -> float:
    """Net room ADR in AED (before fees)."""
    adr = MONTHS[d.month][1] * (1.04 if d.isoweekday() in (4, 5) else 1.0)
    ev = event_on(d)
    if ev is not None:
        adr *= ev.adr_mult
    return adr


def early_share(d: date) -> float:
    """Share of a night's bookings made 30-180 days ahead (the rest follow
    an exponential lead time): events book early, which is their pace."""
    ev = event_on(d)
    return ev.early if ev is not None else 0.30


# ─── 2024-2025 baselines and the 2026 distortion (02 spec 9) ───

BASELINE_START = date(2024, 1, 1)
# Conflict distortion: the Mar-Jul 2026 slump Demand's calibration must not
# learn from. The global baseline_exclusions row (0021 seed) covers it.
DISTORTION = (date(2026, 3, 1), date(2026, 7, 31))
DISTORTION_OCC, DISTORTION_ADR = 0.72, 0.85

RAMADAN = (
    (date(2024, 3, 11), date(2024, 4, 9)),
    (date(2025, 3, 1), date(2025, 3, 29)),
    (date(2026, 2, 18), date(2026, 3, 19)),
)
# (start, end, occupancy bump, ADR multiplier)
BASELINE_BUMPS = (
    (date(2024, 2, 19), date(2024, 2, 23), 0.08, 1.15),   # Gulfood 2024
    (date(2024, 4, 10), date(2024, 4, 12), 0.06, 1.10),   # Eid Al Fitr 2024
    (date(2024, 5, 6), date(2024, 5, 9), 0.06, 1.10),     # Arabian Travel Market 2024
    (date(2024, 6, 16), date(2024, 6, 18), 0.05, 1.05),   # Eid Al Adha 2024
    (date(2024, 10, 14), date(2024, 10, 18), 0.15, 1.60), # GITEX Global 2024
    (date(2025, 2, 17), date(2025, 2, 21), 0.08, 1.15),   # Gulfood 2025
    (date(2025, 3, 30), date(2025, 4, 1), 0.06, 1.10),    # Eid Al Fitr 2025
    (date(2025, 4, 28), date(2025, 5, 1), 0.06, 1.10),    # Arabian Travel Market 2025
    (date(2025, 6, 6), date(2025, 6, 8), 0.05, 1.05),     # Eid Al Adha 2025
    (date(2025, 10, 13), date(2025, 10, 17), 0.15, 1.60), # GITEX Global 2025
    (date(2025, 11, 17), date(2025, 11, 21), 0.08, 1.20), # Dubai Airshow 2025
)
DOW_BASELINE = {1: -0.02, 2: 0.0, 3: 0.0, 4: 0.02, 5: 0.03, 6: 0.01, 7: -0.04}


def _in(d: date, ranges: tuple) -> bool:
    return any(a <= d <= b for a, b, *_ in ranges)


def baseline_occ(d: date) -> float:
    """occ = 0.70 + 0.14 cos(2 pi (doy - 20) / 365) + dow + event bumps, a
    Ramadan dip, NYE at 94%, and the 2026 Mar-Jul distortion on top."""
    if d.month == 12 and d.day == 31:
        occ = 0.94
    else:
        doy = d.timetuple().tm_yday
        occ = 0.70 + 0.14 * math.cos(2 * math.pi * (doy - 20) / 365) + DOW_BASELINE[d.isoweekday()]
        occ += sum(bump for a, b, bump, _m in BASELINE_BUMPS if a <= d <= b)
        if _in(d, RAMADAN):
            occ -= 0.12
    if DISTORTION[0] <= d <= DISTORTION[1]:
        occ *= DISTORTION_OCC
    return min(max(occ, 0.25), 0.98)


def baseline_adr(d: date) -> float:
    doy = d.timetuple().tm_yday
    adr = 330 + 60 * math.cos(2 * math.pi * (doy - 20) / 365)
    for a, b, _bump, mult in BASELINE_BUMPS:
        if a <= d <= b:
            adr *= mult
    if d.month == 12 and d.day in (30, 31):
        adr *= 2.2
    if _in(d, RAMADAN):
        adr *= 0.92
    if DISTORTION[0] <= d <= DISTORTION[1]:
        adr *= DISTORTION_ADR
    return adr
