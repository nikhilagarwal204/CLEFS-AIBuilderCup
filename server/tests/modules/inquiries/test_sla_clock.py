"""SLA business-hours clock goldens (04 spec 4.5, 4.12)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from clefs.modules.inquiries import sla
from clefs.modules.inquiries.settings import InquirySettings

DXB = ZoneInfo("Asia/Dubai")
S = InquirySettings()


def test_fit_at_2159_is_due_0814_next_day(hotel_uae):
    due = sla.business_due(hotel_uae, datetime(2026, 9, 29, 21, 59, tzinfo=DXB), 15, S)
    assert due == datetime(2026, 9, 30, 8, 14, tzinfo=DXB)


def test_fit_at_2201_is_due_0815_next_day(hotel_uae):
    due = sla.business_due(hotel_uae, datetime(2026, 9, 29, 22, 1, tzinfo=DXB), 15, S)
    assert due == datetime(2026, 9, 30, 8, 15, tzinfo=DXB)


def test_before_opening_starts_at_opening(hotel_uae):
    due = sla.business_due(hotel_uae, datetime(2026, 9, 29, 6, 30, tzinfo=DXB), 60, S)
    assert due == datetime(2026, 9, 29, 9, 0, tzinfo=DXB)


def test_inside_hours_is_plain_addition(hotel_uae):
    due = sla.business_due(hotel_uae, datetime(2026, 9, 29, 10, 0, tzinfo=DXB), 240, S)
    assert due == datetime(2026, 9, 29, 14, 0, tzinfo=DXB)


def test_rfp_day_spans_business_days(hotel_uae):
    # 1440 business minutes = 14 h/day -> 1 day 10 h after 10:00 = next day 20:00... plus
    # 10:00-22:00 (720) + 08:00-20:00 (720) = 1440 -> due next day 20:00.
    due = sla.business_due(hotel_uae, datetime(2026, 9, 29, 10, 0, tzinfo=DXB), 1440, S)
    assert due == datetime(2026, 9, 30, 20, 0, tzinfo=DXB)


def test_business_minutes_between_skips_the_night(hotel_uae):
    start = datetime(2026, 9, 29, 21, 59, tzinfo=DXB)
    end = datetime(2026, 9, 30, 8, 14, tzinfo=DXB)
    assert sla.business_minutes_between(hotel_uae, start, end, S) == 15


def test_sla_minutes_by_segment():
    assert sla.sla_minutes(S, "fit", False) == 15
    assert sla.sla_minutes(S, "corporate", False) == 60
    assert sla.sla_minutes(S, "group", False) == 240
    assert sla.sla_minutes(S, "group", True) == 1440
    assert sla.sla_minutes(S, "unknown", False) == 15
