"""
Per-hotel menu and ordering settings (hotel_module_settings['upsell'],
validated by this model; GET/PUT /hotels/{slug}/module-settings/upsell).

Every field has a safe default so a hotel that never opened the settings page
gets UAE behaviour: menu prices shown inclusive of every fee (UAE consumer
pricing rules expect the final price), fee rates read from core/tax fee rules
when present, else the Dubai stack below (10% SC, 7% MF, 5% VAT, compound
convention A, Canon "UAE defaults").
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from clefs.core.hotels import get_module_settings


class UpsellSettings(BaseModel):
    # inclusive: the menu price is what the guest pays; the quote splits the
    # fees out of it. plus_plus: the menu price is net and SC/MF/VAT are added
    # on top (03 spec 2.4 place_order).
    price_display: Literal["inclusive", "plus_plus"] = "inclusive"
    # auto: core/tax fee rules for the hotel's emirate (and its stacking
    # convention), falling back to the rates below when none apply.
    # manual: always the rates below (a hotel outside the seeded emirates, or
    # an outlet run by a tenant with its own fee policy).
    fee_source: Literal["auto", "manual"] = "auto"
    service_charge_pct: Decimal = Field(Decimal("10"), ge=0, le=30)
    municipality_fee_pct: Decimal = Field(Decimal("7"), ge=0, le=30)
    vat_pct: Decimal = Field(Decimal("5"), ge=0, le=30)
    convention: Literal["A", "B", "C"] = "A"
    # A confirm must name a quote the same session saw this recently.
    quote_ttl_min: int = Field(10, ge=1, le=60)
    # Guest-facing ordering (web menu page and concierge tools).
    ordering_enabled: bool = True
    # Orders from a legacy QR (no room key) are accepted but flagged for staff
    # to confirm at the door (03 spec 2.3). False refuses them instead.
    allow_unverified_rooms: bool = True
    # Alcohol is never sold through the guest channels unless the hotel holds
    # the licence and opts in.
    alcohol_orders: bool = False
    # Per room per hour, across quotes and orders: a runaway model or a
    # scripted client cannot flood the kitchen.
    orders_per_room_hour: int = Field(6, ge=1, le=100)
    quotes_per_room_hour: int = Field(60, ge=1, le=500)
    # How many pairing suggestions a quote returns.
    max_suggestions: int = Field(2, ge=0, le=5)
    # Service request type for the linked F&B ticket.
    request_type: str = Field("Food", max_length=60)


def load(cur: Any, hotel_slug: str) -> UpsellSettings:
    return get_module_settings(cur, hotel_slug, "upsell", UpsellSettings)
