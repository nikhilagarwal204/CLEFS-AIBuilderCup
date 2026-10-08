"""Deterministic ranking and caps (02 spec 6.1, 6.4 tests)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from clefs.modules.brief import ranking as R

NOW = datetime(2026, 10, 1, 3, 0, tzinfo=timezone.utc)


def _c(key, section, detector, *, aed=None, conf=0.9, hours=None, severity=None):
    return R.Candidate(
        key=key, section=section, detector=detector, title=key, confidence=conf,
        impact_minor=None if aed is None else int(aed * 100), currency="AED",
        deadline=None if hours is None else NOW + timedelta(hours=hours), severity=severity,
    )


# ─── Factors ───


def test_importance_is_log_scaled_against_r_ref_and_capped():
    assert R.importance(20_000, 20_000) == 1.0
    assert R.importance(40_000, 20_000) == 1.0
    assert math.isclose(R.importance(1_020, 20_000), math.log1p(1020) / math.log1p(20000))
    # No revenue history: the AED 20,000 fallback
    assert R.importance(1_020, 0) == R.importance(1_020, 20_000)


def test_importance_for_zero_money_items_uses_severity_and_review_weights():
    assert R.importance(None, 20_000, severity=4) == 0.9
    assert R.importance(0, 20_000, severity=3) == 0.7
    assert R.importance(None, 20_000, severity=2) == 0.4
    assert R.importance(None, 20_000, recent_negative_review=True) == 0.5


def test_urgency_steps():
    assert R.urgency(NOW - timedelta(hours=1), NOW) == 1.0
    assert R.urgency(NOW + timedelta(hours=24), NOW) == 1.0
    assert R.urgency(NOW + timedelta(hours=25), NOW) == 0.85
    assert R.urgency(NOW + timedelta(hours=72), NOW) == 0.85
    assert R.urgency(NOW + timedelta(days=7), NOW) == 0.6
    assert R.urgency(NOW + timedelta(days=30), NOW) == 0.4
    assert R.urgency(NOW + timedelta(days=31), NOW) == 0.25
    assert R.urgency(None, NOW) == 0.25


# ─── Ranking ───


def test_rank_is_deterministic_and_respects_top_n():
    cands = [_c(f"s{i}", "money_lost", f"k{i}", aed=1000 * (i + 1), hours=10) for i in range(8)]
    first = [c.key for c in R.rank(list(cands), now=NOW, r_ref_major=20_000, section_cap=10)]
    again = [c.key for c in R.rank(list(reversed(cands)), now=NOW, r_ref_major=20_000, section_cap=10)]
    assert first == again == ["s7", "s6", "s5", "s4", "s3"]


def test_section_cap_of_three():
    cands = [_c(f"lost{i}", "money_lost", f"k{i}", aed=5000, hours=10) for i in range(5)]
    cands += [_c("avail", "money_available", "inq.unanswered", aed=100, hours=200)]
    out = R.rank(cands, now=NOW, r_ref_major=20_000)
    assert sum(1 for c in out if c.section == "money_lost") == 3
    assert "avail" in [c.key for c in out]


def test_detector_cap_of_two():
    cands = [_c(f"a{i}", "money_lost", "pg.no_show", aed=9000, hours=5) for i in range(4)]
    cands += [_c("b", "money_lost", "pg.vcc", aed=10, hours=500)]
    out = R.rank(cands, now=NOW, r_ref_major=20_000)
    assert sum(1 for c in out if c.detector == "pg.no_show") == 2
    assert "b" in [c.key for c in out]


def test_ties_break_on_the_earliest_deadline():
    late = _c("late", "money_lost", "k1", aed=500, hours=10)
    early = _c("early", "money_lost", "k2", aed=500, hours=2)
    out = R.rank([late, early], now=NOW, r_ref_major=20_000)
    assert [c.key for c in out] == ["early", "late"]


def test_compliance_never_ranks():
    out = R.rank([_c("tax", "compliance", "tax.td_remit", aed=99_999, hours=1)], now=NOW, r_ref_major=20_000)
    assert out == []


def test_zero_money_l1_service_item_outranks_a_small_money_item():
    l1 = _c("l1", "service_risk", "brief.sla_breach", severity=4, hours=0)
    small = _c("small", "money_lost", "pg.x", aed=50, hours=0)
    out = R.rank([small, l1], now=NOW, r_ref_major=20_000)
    assert out[0].key == "l1"


# ─── Source detector (02 spec 6.1: "at most 2 per source detector") ───


def test_detector_key_splits_profit_guard_leaks_by_detector():
    leak = {"kind": "pg.leak.detected", "dedupe_key": "pg:L2:BOOKING:doc:17"}
    assert R.detector_key(leak) == "pg.leak.detected:L2"
    assert R.detector_key({"kind": "pg.leak.detected", "dedupe_key": "pg:L13:-:item:abcd"}) == "pg.leak.detected:L13"
    # An explicit detector field wins; anything else is its kind.
    assert R.detector_key({"kind": "x.k", "detector": "d1", "dedupe_key": "pg:L2:-:g"}) == "x.k:d1"
    assert R.detector_key({"kind": "inq.unanswered", "dedupe_key": "inq.unanswered:7"}) == "inq.unanswered"
    assert R.detector_key({"kind": "pg.vcc.expiring", "dedupe_key": "pg"}) == "pg.vcc.expiring"


def test_seven_leak_detectors_under_one_kind_are_not_collapsed_into_one_cap():
    # Three detectors, one kind: a per-kind cap would keep only two items and
    # let a small, far-off item from another module take the third slot.
    cands = [_c(f"{d}-{i}", "money_lost", R.detector_key({"kind": "pg.leak.detected", "dedupe_key": f"pg:{d}:-:g{i}"}),
                aed=8000 - i, hours=5) for d in ("L1", "L2", "L3") for i in range(2)]
    cands += [_c("small", "money_available", "dr.opportunity", aed=50, hours=600)]
    out = R.rank(cands, now=NOW, r_ref_major=20_000, section_cap=5)
    keys = [c.key for c in out]
    assert {k.split("-")[0] for k in keys if k != "small"} == {"L1", "L2", "L3"}
    assert "small" not in keys and len(out) == 5
    per = {}
    for c in out:
        per[c.detector] = per.get(c.detector, 0) + 1
    assert max(per.values()) <= 2


# ─── Decision slots (QA OWN-02) ───


def test_min_decidable_swaps_read_only_picks_for_live_approvals():
    # Three overdue zero-money L1 incidents and two more read-only items
    # outscore every priced approval; the daily tier still keeps two
    # decidable items in the top 5, dropping the lowest read-only picks.
    l1 = [_c(f"l1-{i}", "service_risk", f"svc{i}", severity=4, hours=-900) for i in range(3)]
    ro = [_c("ghc", "money_available", "dr.ghc", aed=9000, hours=0), _c("vcc", "money_lost", "pg.vcc", aed=3637, hours=0)]
    acts = [_c(f"rate-{i}", "money_available", "dr.rate_change", aed=5000 - i, conf=0.6, hours=100) for i in range(3)]
    for a in acts:
        a.decidable = True
    plain = [c.key for c in R.rank(l1 + ro + acts, now=NOW, r_ref_major=20_000)]
    assert not any(k.startswith("rate-") for k in plain)
    out = R.rank(l1 + ro + acts, now=NOW, r_ref_major=20_000, min_decidable=2)
    keys = [c.key for c in out]
    assert len(keys) == 5 and sum(c.decidable for c in out) == 2
    assert "rate-0" in keys and "rate-1" in keys  # the detector cap of 2 still holds
    assert [c.score for c in out] == sorted((c.score for c in out), reverse=True)


def test_min_decidable_respects_caps_and_needs_no_swap_when_already_met():
    a = _c("a", "money_lost", "k1", aed=10_000, hours=1)
    a.decidable = True
    b = _c("b", "money_lost", "k2", aed=9_000, hours=1)
    b.decidable = True
    assert [c.key for c in R.rank([a, b], now=NOW, r_ref_major=20_000, min_decidable=2)] == ["a", "b"]
    # A decidable candidate the section cap forbids is never forced in.
    full = [_c(f"s{i}", "service_risk", f"d{i}", severity=4, hours=0) for i in range(3)]
    extra = _c("x", "service_risk", "dx", severity=2, hours=0)
    extra.decidable = True
    out = R.rank(full + [extra], now=NOW, r_ref_major=20_000, limit=3, min_decidable=1)
    assert "x" in [c.key for c in out] and len(out) == 3
