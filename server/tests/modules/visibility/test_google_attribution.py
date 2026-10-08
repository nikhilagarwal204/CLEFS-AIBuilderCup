"""Google attribution (04 spec 5.8): UTM-tagged direct bookings, GBP clicks
and commission avoided through Profit Guard's expected_commission."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 10, 2, 7, 0, tzinfo=ZoneInfo("Asia/Dubai"))


@pytest.fixture
def hotel(cur, hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae


def _res(cur, slug, ref, utm, *, booked="2026-09-10 12:00+04", check_in="2026-09-20", status="CONFIRMED",
         revenue=50000):
    cur.execute(
        "INSERT INTO reservations (hotel_slug, pms_res_id, check_in, check_out, nights, currency, utm, booked_at, "
        "status, room_revenue_minor) VALUES (%s, %s, %s, %s::date + 2, 2, 'AED', %s, %s, %s, %s) RETURNING id",
        (slug, ref, check_in, check_in, json.dumps(utm) if utm else None, booked, status, revenue))
    return cur.fetchone()["id"]


@pytest.fixture
def bookings(cur, hotel):
    s = hotel.slug
    ids = [
        _res(cur, s, "A", {"utm_source": "google", "utm_campaign": "gbp_listing"}),
        _res(cur, s, "B", {"utm_source": "Google", "utm_campaign": "fbl_hotel_center"}),
        _res(cur, s, "C", {"utm_source": "google", "utm_campaign": "gbp_listing"}, status="CANCELLED"),
        _res(cur, s, "D", {"utm_source": "google", "utm_campaign": "gbp_listing"}, booked="2026-08-31 23:30+04"),
        _res(cur, s, "E", {"utm_source": "facebook", "utm_campaign": "gbp_listing"}),
        _res(cur, s, "F", None),
    ]
    cur.execute("INSERT INTO vis_gbp_kpis_monthly (hotel_slug, month, website_clicks, computed_at) "
                "VALUES (%s, '2026-09-01', 412, now())", (s,))
    return ids


def test_counts_tagged_direct_bookings_and_commission(cur, hotel, bookings, monkeypatch):
    from clefs.core import signals, stays
    from clefs.modules.profit_guard import api as pg_api
    from clefs.modules.visibility import api, attribution, store

    calls = []
    monkeypatch.setattr(stays, "reservation_breakdown", lambda cur, rid: {"rid": rid})
    monkeypatch.setattr(pg_api, "reference_ota", lambda cur, slug, *, as_of: ("BOOKING", Decimal("15")))

    def commission(cur, slug, *, ota_code, breakdown, on):
        calls.append((ota_code, breakdown["rid"], on))
        return {"commission_minor": 7500, "currency": "AED"}

    monkeypatch.setattr(pg_api, "expected_commission", commission)
    out = attribution.compute(cur, hotel, date(2026, 9, 1))
    assert out["direct_bookings"] == 1 and out["commission_avoided_minor"] == 7500   # only A: gbp_listing
    assert calls == [("BOOKING", bookings[0], date(2026, 9, 20))]
    assert out["line"] == "Google sent 412 website clicks → 1 direct booking → AED 75.00 commission avoided"

    store.update_profile(cur, hotel.slug, {"free_booking_link_pattern": "fbl_%"}, user_id=None)
    out = attribution.compute(cur, hotel, date(2026, 9, 1))
    assert out["direct_bookings"] == 2 and out["direct_room_revenue_minor"] == 100000

    row = api.attribution(cur, hotel.slug, month=date(2026, 9, 17))
    assert row["gbp_website_clicks"] == 412 and row["reference_ota"] == "BOOKING"
    assert "not counted" in row["label"]
    kinds = [s["kind"] for s in signals.list_signals(cur, hotel.slug, modules=["visibility"])]
    assert "vis.google_attribution" in kinds


def test_without_a_reference_ota_commission_is_null(cur, hotel, bookings):
    from clefs.modules.visibility import api, attribution

    assert api.attribution(cur, hotel.slug, month=date(2026, 9, 1)) is None
    out = attribution.compute(cur, hotel, date(2026, 9, 1))
    assert out["direct_bookings"] == 1 and out["commission_avoided_minor"] is None and out["reference_ota"] is None
    assert out["line"] == "Google sent 412 website clicks → 1 direct booking"
