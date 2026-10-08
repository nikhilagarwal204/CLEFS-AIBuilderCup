"""Pure pieces: periods in hotel time, the grounding checker, guardrails,
value matching, derived ratios and the chart spec choice."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.modules.ask import execute, grounding, guardrails, periods, tools, values

from .conftest import q


def test_presets_resolve_in_hotel_time(hotel_uae, frozen):
    from datetime import datetime, timezone

    # 22:30 UTC on 30 Sep is already 1 Oct in Dubai: "today" is Dubai's.
    frozen.freeze(datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc))
    p = periods.resolve({"preset": "today", "from": None, "to": None}, hotel_uae)
    assert p.start == p.end == date(2026, 10, 1)
    lm = periods.resolve({"preset": "last_month"}, hotel_uae)
    assert (lm.start, lm.end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert periods.resolve(None, hotel_uae).preset == "mtd"
    with pytest.raises(periods.PeriodError):
        periods.resolve({"preset": "next_decade"}, hotel_uae)


def test_grounding_tolerates_formatting_not_invention():
    res = {"unit": "money", "rows": [{"bucket": None, "group": {"channel": "BOOKING"}, "value": 10200.0,
                                      "compare_value": 8000.0}], "total": 10200.0, "compare_total": 8000.0,
           "start": "2026-08-01", "end": "2026-08-31", "row_count": 1}
    bag = grounding.allowed([res], question="Booking commission in August vs last year")
    for ok in ("Booking charged AED 10,200 in August 2026.", "AED 10.2k, up 27.5% (AED 2,200 more).",
               "From 1 to 31 Aug 2026 it was AED 10,200.00, versus AED 8,000.", "1.3x last year"):
        assert grounding.check(ok, bag).ok, ok
    for bad in ("Booking charged AED 11,200.", "About 30% more.", "AED 10.5k"):
        assert not grounding.check(bad, bag).ok, bad
    assert grounding.check("Since 2026-08-01.", bag).ok  # the period's own dates
    assert not grounding.check("On 2026-08-15 it peaked.", bag).ok  # no day series: an invented peak


def test_guardrails():
    refused = ["Guest John's phone", "email of the guest in 502", "who stayed in room 1203 last night",
               "list all guest names today", "call +971 50 123 4567 for me", "passport number of Mr Ali",
               "What will competitors charge next year?"]
    allowed = ["Net ADR by channel in August", "How many guests checked in today?", "Guest satisfaction this month",
               "guest numbers this week", "Occupancy GITEX week vs last year", "Top 5 room types by room nights"]
    assert all(not guardrails.check_question(x).ok for x in refused)
    assert all(guardrails.check_question(x).ok for x in allowed)
    assert guardrails.check_question("x" * 600).code == "too_long"


def test_value_matching():
    known = ["BOOKING", "EXPEDIA", "DIRECT", "AGODA"]
    assert values.match("Booking.com", known) == "BOOKING"
    assert values.match("expedia", known) == "EXPEDIA"
    assert values.match("Exp", known) == "EXPEDIA"
    assert values.match("Airbnb", known) is None
    assert values.compare(["1", "2", "5", "10"], "gte", "5") == ["5", "10"]


def test_occupancy_and_adr_are_computed_server_side(cur, seeded, ctx_for):
    from clefs.modules.ask import catalog

    ctx, _ = ctx_for("owner")
    cat = catalog.by_key(ctx)
    occ = execute.run(ctx, tools.validate_query(ctx, q("ask.occupancy", frm="2026-09-01", to="2026-09-30",
                                                      compare="same_period_last_year"), cat))
    # 510 room nights / (13 rooms x 30 days) = 130.77%: the demo data overbooks, the maths must not care.
    assert occ["total"] == pytest.approx(130.77)
    assert occ["compare_total"] == pytest.approx(round(410 / 390 * 100, 2))
    adr = execute.run(ctx, tools.validate_query(ctx, q("ask.adr", dims=["channel"], preset="last_month"), cat))
    by = {r["group"]["channel"]: r["value"] for r in adr["rows"]}
    assert by == {"BOOKING": 700.0, "EXPEDIA": 600.0, "DIRECT": 825.0}


def test_chart_types(cur, seeded, ctx_for):
    from clefs.modules.ask import catalog, present

    ctx, _ = ctx_for("owner")
    cat = catalog.by_key(ctx)

    def spec(args):
        s = tools.validate_query(ctx, args, cat)
        return present.chart_spec(execute.run(ctx, s), s)

    assert spec(q("pg.commission_aed", preset="last_month"))["type"] == "kpi"
    assert spec(q("pg.commission_aed", dims=["channel"], preset="last_month"))["type"] == "column"
    line = spec(q("stays.room_nights", frm="2026-09-01", to="2026-09-30", grain="week"))
    assert line["type"] == "line" and line["x"] == "bucket"
    many = spec(q("am.leakage_aed", dims=["amenity"], preset="last_7d"))
    assert many["type"] == "hbar"  # "Water bottle" is a long label
