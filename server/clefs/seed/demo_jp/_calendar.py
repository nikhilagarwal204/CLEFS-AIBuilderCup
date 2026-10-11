"""
Pure demand curves for the Tokyo demo hotel: target occupancy and ADR by
night (JPY, tax included, as Japanese rate tables show them). No database,
no clock. The stays planner (demo_uae.core_stays) samples around these
through ctx.data("core_stays").cal, exactly as it does the Dubai curves.

Tokyo runs full most of the year (inbound demand on top of domestic
travel): about 85% in summer and autumn, with Saturday nights the fullest
and Sunday nights the emptiest. The peaks are Japan's long holidays and
the Lunar New Year, when the Chinese-, Korean- and Taiwanese-speaking
markets travel.
"""

from __future__ import annotations

from datetime import date, timedelta

from clefs.seed.demo_uae._calendar import DemoEvent

# ─── Events (the window the seed covers: summer 2026 to spring 2027) ───

EVENTS: tuple[DemoEvent, ...] = (
    DemoEvent("obon-2026", date(2026, 8, 8), date(2026, 8, 16), 0.93, 1.18, 0.45),
    DemoEvent("silver-week-2026", date(2026, 9, 19), date(2026, 9, 22), 0.95, 1.22, 0.50),
    DemoEvent("sports-day-weekend-2026", date(2026, 10, 10), date(2026, 10, 11), 0.94, 1.15, 0.40),
    DemoEvent("labour-thanksgiving-weekend-2026", date(2026, 11, 21), date(2026, 11, 22), 0.95, 1.18, 0.45),
    DemoEvent("christmas-2026", date(2026, 12, 23), date(2026, 12, 25), 0.93, 1.25, 0.50),
    DemoEvent("new-year-2027", date(2026, 12, 29), date(2027, 1, 3), 0.96, 1.45, 0.60),
    DemoEvent("lunar-new-year-2027", date(2027, 2, 5), date(2027, 2, 13), 0.95, 1.35, 0.55, lead_in=2),
)


def event_on(d: date) -> DemoEvent | None:
    for ev in EVENTS:
        if ev.start - timedelta(days=ev.lead_in) <= d <= ev.end:
            return ev
    return None


# ─── Nightly targets ───

# Month -> (occupancy, ADR in JPY for a Moderate Double, tax included).
# Cherry blossoms (late March, April) and autumn leaves (November) are the
# year's dearest months; January and February the cheapest.
MONTHS: dict[int, tuple[float, float]] = {
    1: (0.80, 15500), 2: (0.82, 15000), 3: (0.88, 19000), 4: (0.91, 21000), 5: (0.85, 17500), 6: (0.82, 15500),
    7: (0.85, 17000), 8: (0.86, 17500), 9: (0.84, 16500), 10: (0.88, 19500), 11: (0.90, 20500), 12: (0.86, 18500),
}
# ISO weekday -> occupancy shift: domestic weekend trips fill Friday and
# Saturday nights; Sunday is the quiet night.
DOW_OCC = {1: -0.03, 2: -0.01, 3: 0.0, 4: 0.0, 5: 0.04, 6: 0.06, 7: -0.08}
DOW_ADR = {5: 1.08, 6: 1.15}
MAX_OCC = 0.97


def target_occ(d: date) -> float:
    ev = event_on(d)
    if ev is not None:
        return ev.occ
    return min(MONTHS[d.month][0] + DOW_OCC[d.isoweekday()], MAX_OCC)


def target_adr(d: date) -> float:
    """ADR in JPY for the base room type (tax included)."""
    adr = MONTHS[d.month][1] * DOW_ADR.get(d.isoweekday(), 1.0)
    ev = event_on(d)
    if ev is not None:
        adr *= ev.adr_mult
    return adr


def early_share(d: date) -> float:
    """Share of a night's bookings made 30-180 days ahead: holidays book
    early, which is their pace."""
    ev = event_on(d)
    return ev.early if ev is not None else 0.30
