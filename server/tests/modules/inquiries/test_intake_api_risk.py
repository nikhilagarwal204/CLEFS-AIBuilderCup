"""api.create_inquiry, api.brief_summary and money at risk (04 spec 4.5,
7.2): segment defaults (A6), the stays ADR fallback, stage probabilities."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_wa,
)


def _stay(cur, hotel, *, stay_date: date, rate_minor: int, res_id: str, rooms: int = 1):
    cur.execute(
        """
        INSERT INTO reservations (hotel_slug, source_system, pms_res_id, check_in, check_out, nights, currency, status)
        VALUES (%s, 'DEMO', %s, %s, %s, 1, 'AED', 'CHECKED_OUT') RETURNING id
        """,
        (hotel.slug, res_id, stay_date, stay_date + timedelta(days=1)),
    )
    rid = cur.fetchone()["id"]
    cur.execute(
        "INSERT INTO reservation_nights (hotel_slug, reservation_id, stay_date, rooms, rate_minor, currency) "
        "VALUES (%s, %s, %s, %s, %s, 'AED')",
        (hotel.slug, rid, stay_date, rooms, rate_minor),
    )


def test_create_inquiry_from_the_concierge(cur, hotel):
    from clefs.modules.inquiries import api

    out = api.create_inquiry(
        cur, hotel.slug, source="concierge", text="Can you quote 12 rooms for GITEX?",
        contact=api.ContactIn(name="Guest", phone="+971502223344"), rooms=12, actor=None,
    )
    assert out["stage"] == "new" and out["outcome"] == "created" and out["ref"]
    row = inquiries(cur, hotel.slug)[0]
    assert row["source"] == "concierge" and row["consent_basis"] == "staff_logged" and row["rooms"] == 12


def test_create_inquiry_when_module_off_raises_not_configured(cur, hotel):
    import pytest

    from clefs.core.errors import NotConfigured
    from clefs.core.hotels import set_module_settings
    from clefs.modules.inquiries import api

    set_module_settings(cur, hotel.slug, "inquiries", {}, actor=None, enabled=False)
    with pytest.raises(NotConfigured):
        api.create_inquiry(cur, hotel.slug, source="concierge", text="x", contact=api.ContactIn(), actor=None)
    assert api.brief_summary(cur, hotel.slug, day=date(2026, 9, 29)) == {}


def test_money_at_risk_uses_stays_adr_and_segment_defaults(cur, hotel):
    from clefs.modules.inquiries import risk

    # Two Tuesdays in the trailing 28 days at AED 500 and AED 700 -> ADR 600.
    _stay(cur, hotel, stay_date=date(2026, 9, 22), rate_minor=50000, res_id="R1")
    _stay(cur, hotel, stay_date=date(2026, 9, 15), rate_minor=70000, res_id="R2")
    _stay(cur, hotel, stay_date=date(2026, 9, 16), rate_minor=10000, res_id="R3")  # a Wednesday: ignored
    row = {"segment": "fit", "stage": "new", "guest_engaged": False, "check_in": date(2026, 10, 6),
           "check_out": None, "rooms": None, "nights": None}
    got = risk.compute(cur, hotel, row)
    # fit default 1 x 2 room-nights (A6) x 600 x p(new)=0.10 -> AED 120.
    assert got["room_nights_est"] == 2 and got["room_nights_estimated"] is True
    assert got["expected_adr_minor"] == 60000 and got["money_at_risk_minor"] == 12000
    quoted = risk.compute(cur, hotel, {**row, "stage": "quoted", "guest_engaged": True, "rooms": 2, "nights": 3})
    assert quoted["stage_probability"] == Decimal("0.50") and quoted["money_at_risk_minor"] == 180000
    assert risk.compute(cur, hotel, {**row, "stage": "spam"})["money_at_risk_minor"] == 0


def test_trailing_adr_weights_multi_room_rows(cur, hotel):
    from clefs.modules.inquiries import risk

    # rate_minor is per room per night: a 3-room row at AED 700 earns 2100,
    # so (500 + 3 x 700) / 4 rooms = AED 650. The old SUM(rate) / SUM(rooms)
    # gave (500 + 700) / 4 = AED 300.
    _stay(cur, hotel, stay_date=date(2026, 9, 22), rate_minor=50000, res_id="R1")
    _stay(cur, hotel, stay_date=date(2026, 9, 15), rate_minor=70000, res_id="R2", rooms=3)
    row = {"segment": "fit", "stage": "new", "guest_engaged": False, "check_in": date(2026, 10, 6),
           "check_out": None, "rooms": None, "nights": None}
    assert risk.compute(cur, hotel, row)["expected_adr_minor"] == 65000
    assert risk._trailing_adr(cur, hotel, None) == 65000


def test_recalibrated_probability_wins_over_the_prior(cur, hotel):
    from clefs.modules.inquiries import risk

    cur.execute("INSERT INTO inq_stage_probabilities (hotel_slug, stage, p, n, computed_at) VALUES (%s, 'new', 0.2, 40, now())",
                (hotel.slug,))
    assert risk.stage_probability(cur, hotel.slug, "new", False) == Decimal("0.2")


def test_brief_summary_line(cur, hotel, now, run_jobs):
    from clefs.modules.inquiries import api, store

    _stay(cur, hotel, stay_date=date(2026, 9, 22), rate_minor=50000, res_id="R1")
    send_wa("Need 10 rooms", mid="a", sender="971500000001")
    send_wa("Hello", mid="b", sender="971500000002")
    for r in inquiries(cur, hotel.slug):
        store.update_inquiry(cur, r["id"], {"money_at_risk_minor": 125000})
    now.advance(hours=3)
    run_jobs("inquiries.sla_check")
    out = api.brief_summary(cur, hotel.slug, day=date(2026, 9, 29))
    assert out["unanswered"] == 2 and out["estimated"] is True
    assert out["line"] == "2 unanswered > 2 h, AED 2.5k at risk (est.)"
