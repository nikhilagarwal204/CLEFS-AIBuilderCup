"""The am.investigate_variance verifier (02 spec 6.3): the next 14 days for
the item and the flagged floor must be below the spike threshold, and the
value is the flagged week minus the new window pro-rated to 7 days."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from clefs.core import actions, signals
from clefs.modules.amenities import catalog, detectors, variance
from clefs.modules.amenities.actions import below_threshold, verify_investigation

from .conftest import DXB, END_DEFAULT, at

START = END_DEFAULT - timedelta(days=62)
DONE = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)            # business date 29 Sep
W_START, W_END = date(2026, 9, 30), date(2026, 10, 13)     # the 14 days after done


# ─── Helpers ───


def _soap(cur, slug):
    return catalog.create_item(cur, slug, "AED", catalog.ItemIn(
        key="TEST_SOAP", name_en="Test soap", category="BATH", basis="PER_OCCUPIED_ROOM_NIGHT",
        std_qty=Decimal("1"), unit_cost=Decimal("1.00")))


def _flag(cur, hotel, clock, occ_range, issue, *, floors=None):
    """Nine weeks at standard, then 200 extra soaps in the last week (on
    floor 2 when floors are given). Returns (item, action) with the task
    done on 29 Sep, its params taken from the spike signal."""
    clock.freeze(DONE)
    item = _soap(cur, hotel.slug)
    occ_range(START, 63, 50, floors=floors)
    for i in range(63):
        d = START + timedelta(days=i)
        if floors:
            for f, n in floors.items():
                issue(item, n, at(d), floor=f)
        else:
            issue(item, 50, at(d))
    issue(item, 200, at(END_DEFAULT), floor="2" if floors else None)
    variance.rollup(cur, hotel, START, END_DEFAULT)
    assert detectors.detect_spikes(cur, hotel, END_DEFAULT)["fired"] == 1
    sig = next(s for s in signals.list_signals(cur, hotel.slug, modules=["amenities"]) if s["kind"] == detectors.SPIKE)
    params = sig["recommended_action"]["params"]
    a = actions.create_action(cur, hotel.slug, type=detectors.INVESTIGATE, title="Count soap", params=params,
                              expected_minor=params["v7_minor"], currency="AED")
    actions.decide(cur, a["id"], "approve", actor=None, hotel_slug=hotel.slug)
    actions.mark_done(cur, a["id"], actor=None)
    return item, a


def _after(cur, hotel, clock, occ_range, issue, item, *, days=14, extra=None, floors=None, today=date(2026, 10, 14)):
    """Standard use for `days` days from 30 Sep, plus {floor or '': qty}
    extra on the last day, then roll up and freeze on `today`. Lines are
    written with the clock after the window (no future-dated issuance)."""
    clock.freeze(datetime(2026, 10, 14, 10, 0, tzinfo=DXB))
    occ_range(date(2026, 9, 29), days + 1, 50, floors=floors)
    for i in range(days + 1):
        d = date(2026, 9, 29) + timedelta(days=i)
        if floors:
            for f, n in floors.items():
                issue(item, n, at(d), floor=f)
        else:
            issue(item, 50, at(d))
    for f, qty in (extra or {}).items():
        issue(item, qty, at(date(2026, 9, 29) + timedelta(days=days)), floor=f or None)
    variance.rollup(cur, hotel, date(2026, 9, 29), W_END)
    clock.freeze(datetime(today.year, today.month, today.day, 10, 0, tzinfo=DXB))


def _verify(cur, hotel, a):
    row = actions.get_action(cur, hotel.slug, a["id"])
    ctx = actions.ActionCtx(cur=cur, hotel=hotel, action=row, action_type=actions.get_action_type(
        detectors.INVESTIGATE), actor=None)
    return verify_investigation(ctx)


# ─── Tests ───


def test_verified_when_the_next_14_days_are_back_below_threshold(cur, hotel_uae, clock, occ_range, issue):
    item, a = _flag(cur, hotel_uae, clock, occ_range, issue)
    _after(cur, hotel_uae, clock, occ_range, issue, item, extra={"": 20})     # 20 over 700 expected: 2.9%
    res = _verify(cur, hotel_uae, a)
    # Flagged week 200 x AED 1 = 20000 fils; 14 days 2000 fils -> 1000 per week.
    assert res.status == "verified" and res.outcome_minor == 19000 and res.attribution == "caused"
    labels = {e.label: e.value for e in res.evidence}
    assert labels["Below the spike threshold"] == "yes" and labels["Per week after the count"] == 1000


def test_a_drop_that_is_still_above_threshold_is_not_verified(cur, hotel_uae, clock, occ_range, issue):
    # 150 over 700 expected (21%, AED 150) is less than the flagged week's 200
    # but still a spike: week-to-week noise must not become verified money.
    item, a = _flag(cur, hotel_uae, clock, occ_range, issue)
    _after(cur, hotel_uae, clock, occ_range, issue, item, extra={"": 150})
    res = _verify(cur, hotel_uae, a)
    assert res.status == "not_verified" and res.outcome_minor == 0


def test_pending_until_the_14_day_window_has_closed(cur, hotel_uae, clock, occ_range, issue):
    item, a = _flag(cur, hotel_uae, clock, occ_range, issue)
    _after(cur, hotel_uae, clock, occ_range, issue, item, today=date(2026, 10, 7))   # only 7 days in
    assert _verify(cur, hotel_uae, a).status == "pending"
    clock.freeze(datetime(2026, 10, 13, 10, 0, tzinfo=DXB))                          # 13 days in
    assert _verify(cur, hotel_uae, a).status == "pending"


def test_pending_while_the_window_lacks_occupancy_days(cur, hotel_uae, clock, occ_range, issue):
    item, a = _flag(cur, hotel_uae, clock, occ_range, issue)
    _after(cur, hotel_uae, clock, occ_range, issue, item, days=5)            # 5 covered days of 14
    assert _verify(cur, hotel_uae, a).status == "pending"


def test_verification_is_scoped_to_the_flagged_floor(cur, hotel_uae, clock, occ_range, issue):
    floors = {"1": 25, "2": 25}
    item, a = _flag(cur, hotel_uae, clock, occ_range, issue, floors=floors)
    assert a["params"]["floor"] == "2"                                        # the floor that leaked
    # Floor 2 is fixed; floor 1 leaks 300 in the new window. Judged
    # hotel-wide this would fail; judged on the flagged floor it passes.
    _after(cur, hotel_uae, clock, occ_range, issue, item, floors=floors, extra={"1": 300})
    res = _verify(cur, hotel_uae, a)
    assert res.status == "verified" and res.outcome_minor == 20000
    assert any("floor 2" in e.label for e in res.evidence)


def test_the_action_waits_14_days_before_its_first_check(cur, hotel_uae, clock, occ_range, issue):
    _, a = _flag(cur, hotel_uae, clock, occ_range, issue)
    row = actions.get_action(cur, hotel_uae.slug, a["id"])
    assert row["verify_after"] - DONE >= timedelta(days=15)


def test_below_threshold_is_ratio_or_money():
    bar = 10000                                                              # AED 100
    w = {"expected": Decimal(700), "variance": Decimal(150), "variance_minor": 15000}
    assert below_threshold(w, min_minor=bar) is False                        # 21% and AED 150
    assert below_threshold({**w, "variance": Decimal(100)}, min_minor=bar) is True          # 14%
    assert below_threshold({**w, "variance_minor": 9000}, min_minor=bar) is True           # AED 90
    assert below_threshold({"expected": Decimal(0), "variance": Decimal(5), "variance_minor": 50000},
                           min_minor=bar) is False                           # nothing expected, all excess
    assert below_threshold({**w, "variance": Decimal(-3), "variance_minor": -300}, min_minor=bar) is True
