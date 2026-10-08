"""Demo seed determinism and idempotency (02 spec 9: seeded RNG, no wall
clock; a re-run deletes only the demo hotel's rows)."""

from __future__ import annotations

from datetime import date

from clefs.core import clock
from clefs.seed import demo_uae
from clefs.seed.demo_uae import DemoCtx, _calendar as cal, core, core_stays

ANCHOR = date(2026, 9, 27)


def _plan(anchor=ANCHOR, scale=1.0):
    ctx = DemoCtx(cur=None, slug="x", anchor=anchor, scale=scale)
    ctx.ids["rooms"] = {r["room_number"]: r for r in core.room_plan()}
    return core_stays.plan_stays(ctx)


def _key(s):
    return (s.pms_res_id, s.channel, s.room_type, s.room_number, s.check_in, s.check_out, s.status, s.booked_at,
            s.cancelled_at, tuple(s.rates), s.guest_name, s.ota_res_id)


def test_the_plan_is_a_pure_function_of_seed_and_anchor():
    assert [_key(s) for s in _plan()] == [_key(s) for s in _plan()]
    assert [_key(s) for s in _plan()] != [_key(s) for s in _plan(anchor=date(2026, 9, 28))]


def test_the_full_plan_meets_the_spec_shape():
    plan = _plan()
    start = ANCHOR.toordinal() - 119
    hist = [s for s in plan if s.check_in.toordinal() >= start - 14 and s.check_in <= ANCHOR]
    assert 2200 <= len(hist) <= 3000  # ~2,600 reservations (02 spec 9)
    nights = {}
    for s in plan:
        if s.status in ("CANCELLED", "NO_SHOW"):
            continue
        for i, r in enumerate(s.rates):
            d = date.fromordinal(s.check_in.toordinal() + i)
            if start <= d.toordinal() <= ANCHOR.toordinal():
                n, rev = nights.get(d, (0, 0))
                nights[d] = (n + 1, rev + r)
    room_nights = sum(n for n, _r in nights.values())
    assert 5400 <= room_nights <= 6600  # ~6,200 room nights
    assert 0.55 <= room_nights / (80 * 120) <= 0.66
    adr = sum(r for _n, r in nights.values()) / room_nights / 100
    assert 240 <= adr <= 330
    # nothing double-books a room
    held: dict = {}
    for s in plan:
        if s.room_number and s.status != "CANCELLED":
            for i in range(s.nights):
                k = (s.room_number, s.check_in.toordinal() + i)
                assert k not in held, k
                held[k] = s.pms_res_id


def test_pace_builds_into_gitex_and_nye():
    plan = _plan()
    def otb(d):
        return sum(1 for s in plan if s.status == "CONFIRMED" and s.check_in <= d < s.check_out)
    quiet = otb(date(2026, 11, 24))  # a plain Tuesday two months out
    assert otb(date(2026, 12, 9)) > quiet * 1.5 and otb(date(2026, 12, 31)) > quiet
    gitex = [r for s in plan if s.check_in <= date(2026, 12, 9) < s.check_out and s.status == "CONFIRMED"
             for i, r in enumerate(s.rates) if s.check_in.toordinal() + i == date(2026, 12, 9).toordinal()]
    assert 580 <= sum(gitex) / len(gitex) / 100 <= 760  # ADR about AED 650


def test_baseline_formula_and_distortion():
    assert cal.baseline_occ(date(2025, 12, 31)) == 0.94
    assert cal.baseline_occ(date(2025, 3, 10)) < cal.baseline_occ(date(2025, 1, 20))  # Ramadan dip
    assert cal.baseline_occ(date(2026, 5, 10)) < cal.baseline_occ(date(2025, 5, 11)) * 0.8  # 2026 distortion


def test_seed_restores_the_clock(cur):
    clock.freeze(clock.now().replace(year=2030))
    frozen = clock.now()
    demo_uae.seed(cur, "demo-t-clock", anchor=ANCHOR, parts=["core"], password="demo-pass-123")
    assert clock.now() == frozen
