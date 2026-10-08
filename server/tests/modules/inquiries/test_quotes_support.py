"""Quote, payment and verification test support (no tests here): a Dubai
3-star on convention A (TD AED 10), fake Demand guardrails, and helpers that
open inquiries and quotes directly. Test files import what they use."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import clefs.modules.inquiries.module  # noqa: F401  (registers action types, jobs, hooks)

DXB = ZoneInfo("Asia/Dubai")
# Tuesday 2026-09-29 10:00 Dubai. CHECK_IN falls inside the unverified 2026
# exemption announcement, which is charged (no owner toggle) but raises the
# fee notice banner, so the 4.6 goldens hold and fee_notices is non-empty.
NOW = datetime(2026, 9, 29, 10, 0, tzinfo=DXB)
CHECK_IN = date(2026, 10, 20)


@pytest.fixture
def now(clock):
    clock.freeze(NOW)
    return clock


@pytest.fixture
def hotel(cur, hotel_uae, now, outbox):
    """hotel_uae as a Dubai 3-star (TD AED 10) with STD and FAM_2BR types and
    a WhatsApp number."""
    from clefs.core import tax
    from clefs.core.hotels import get_hotel

    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    cur.execute(
        "INSERT INTO room_types (hotel_slug, code, name, bedrooms) VALUES (%s, 'STD', 'Standard', 1),"
        " (%s, 'FAM_2BR', 'Family two-bedroom', 2) ON CONFLICT DO NOTHING",
        (hotel_uae.slug, hotel_uae.slug),
    )
    cur.execute("UPDATE hotels SET whatsapp_phone_number_id = 'PNID-HOTEL', inbound_email_alias = 'qdxb' WHERE slug = %s",
                (hotel_uae.slug,))
    return get_hotel(cur, hotel_uae.slug, fresh=True)


class FakeDemand:
    """demand.api.price_guardrails / demand_index stand-in: one BAR per room
    type (AED, Decimal strings), optional per-date overrides."""

    def __init__(self) -> None:
        self.bar = {"STD": "400.00", "FAM_2BR": "600.00", None: "400.00"}
        self.recommended: dict = {}
        self.floor_pct = "0.80"
        self.ceiling_pct = "1.50"
        self.none_dates: set[date] = set()
        self.demand_index: dict[date, int] = {}
        self.source = "rate_guardrails"
        self.captured_at = NOW - timedelta(days=1)

    def guardrails(self, cur, hotel_slug, stay_date, room_type_code=None):
        from decimal import Decimal

        if stay_date in self.none_dates:
            return None
        bar = Decimal(self.bar.get(room_type_code, self.bar[None]))
        rec = self.recommended.get(room_type_code)
        return {
            "floor": str((bar * Decimal(self.floor_pct)).quantize(Decimal("0.01"))),
            "ceiling": str((bar * Decimal(self.ceiling_pct)).quantize(Decimal("0.01"))),
            "recommended": rec, "bar": str(bar), "currency": "AED", "confidence": 0.8, "reasons": [],
            "bar_captured_at": self.captured_at.isoformat(), "source": self.source,
            "demand_index": self.demand_index.get(stay_date),
        }


@pytest.fixture
def demand(monkeypatch):
    from clefs.modules.demand import api as demand_api

    fake = FakeDemand()
    monkeypatch.setattr(demand_api, "price_guardrails", fake.guardrails)
    return fake


def make_inquiry(cur, hotel, *, segment="fit", check_in=CHECK_IN, nights=2, rooms=1, stage="new",
                 phone="+971501234567", email="guest@example.com", confidence="0.90", language="en",
                 room_type_code="STD", source="web_form", name="Sara Khan", missing=None, **extra) -> dict:
    from clefs.modules.inquiries import store

    fields = {
        "source": source, "consent_basis": "express_web_form", "currency": hotel.currency, "segment": segment,
        "check_in": check_in, "check_out": check_in + timedelta(days=nights), "nights": nights, "rooms": rooms,
        "stage": stage, "extraction_confidence": confidence, "language": language, "room_type_code": room_type_code,
        "extraction": {"missing": missing or []}, "contact_name": name,
        **store.contact_columns(cur, hotel.slug, phone=phone, email=email), **extra,
    }
    return store.insert_inquiry(cur, hotel.slug, fields)
