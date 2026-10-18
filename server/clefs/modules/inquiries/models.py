"""
Inquiry Guard constants and schemas (04 spec 4.1-4.5).

Everything that more than one file of the module agrees on lives here: the
source, stage and segment vocabularies (mirroring the 0950 CHECKs), the
consent basis each channel starts with, the assumed stage priors and the
extraction schema the model fills.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# ─── Vocabularies (mirror the CHECK constraints in 0950) ───

SOURCES = ("whatsapp", "email", "web_form", "meta_leadgen", "google_leadform", "phone_desk", "concierge")
STAGES = (
    "new", "replied", "quoted", "payment_pending", "confirmed_verified",
    "lost", "turned_down", "future", "spam", "rerouted",
)
OPEN_STAGES = ("new", "replied", "quoted", "payment_pending")
# Terminal for SLA purposes; lost and future can still reopen (4.2).
CLOSED_STAGES = ("confirmed_verified", "lost", "turned_down", "future", "spam", "rerouted")
REOPENABLE_STAGES = ("lost", "future")
NEVER_REOPEN = ("spam", "turned_down", "rerouted", "confirmed_verified")
SEGMENTS = ("fit", "corporate", "group", "long_stay", "event", "unknown")
LOST_REASONS = (
    "PRICE_VS_COMP", "NO_AVAILABILITY", "SLOW_RESPONSE", "BOOKED_VIA_OTA", "BOOKED_COMPETITOR",
    "GUEST_UNRESPONSIVE", "PAYMENT_LINK_EXPIRED", "POLICY_TERMS", "DATES_CHANGED",
    "PRODUCT_LOCATION_MISMATCH", "TRIP_CANCELLED", "SPAM_DUPLICATE",
)
TURNDOWN_REASONS = ("NO_AVAILABILITY", "DISPLACEMENT", "POLICY", "BELOW_FLOOR")
CONSENT_BASES = ("express_web_form", "pre_contract_notice", "staff_logged")

# ─── Consent basis at creation (4.3) ───
# The column is NOT NULL, so every adapter starts from this map. Ad lead forms
# carry the advertiser's own notice; the web form is the only channel that
# collects express consent (and transfer_abroad) up front.
CONSENT_BASIS_BY_SOURCE: dict[str, str] = {
    "whatsapp": "pre_contract_notice",
    "email": "pre_contract_notice",
    "meta_leadgen": "pre_contract_notice",
    "google_leadform": "pre_contract_notice",
    "web_form": "express_web_form",
    "phone_desk": "staff_logged",
    "concierge": "staff_logged",
}
# Channels that get the fixed acknowledgement (4.3): never staff-logged ones.
ACK_SOURCES = ("whatsapp", "email", "web_form", "meta_leadgen", "google_leadform")

# ─── Reopen window (4.2) ───
REOPEN_DAYS = 30
# An open inquiry takes a new message from the same sender for this long (4.3).
APPEND_DAYS = 30

# ─── Money at risk (4.5, assumptions A5 and A6) ───
PRIOR_PROBABILITY: dict[str, Decimal] = {
    "new": Decimal("0.10"),
    "replied": Decimal("0.15"),  # A5: interpolated
    "quoted": Decimal("0.30"),
    "quoted_engaged": Decimal("0.50"),
    "payment_pending": Decimal("0.80"),
    "future": Decimal("0.05"),
}
# A6: rooms x nights when the inquiry does not say.
SEGMENT_DEFAULT_ROOM_NIGHTS: dict[str, tuple[int, int]] = {
    "fit": (1, 2),
    "unknown": (1, 2),
    "corporate": (1, 3),
    "long_stay": (1, 30),
    "group": (10, 3),
    "event": (5, 2),
}
ESTIMATE_CONFIDENCE_FACTOR = Decimal("0.6")

# ─── First-response SLA minutes (4.5; InquirySettings.sla overrides) ───
DEFAULT_SLA_MINUTES: dict[str, int] = {
    "fit": 15,
    "unknown": 15,
    "corporate": 60,
    "long_stay": 60,
    "group": 240,
    "event": 240,
    "rfp": 1440,
}

# ─── Validation bounds (4.4) ───
MAX_NIGHTS = 365
MAX_ROOMS = 200
LONG_STAY_NIGHTS = 30
GROUP_ROOMS = 9

# ─── Senders that are never inquiries (4.3 email) ───
OTA_SENDER_DOMAINS = ("booking.com", "agoda.com", "expediapartnercentral.com", "expedia.com")


# ─── Extraction schema (4.4) ───


class FieldQuote(BaseModel):
    field: str
    quote: str


class InquiryExtraction(BaseModel):
    """What the model reads out of an inquiry. Every extracted field carries a
    verbatim quote in `evidence`, so the drawer can show where it came from."""

    language: Literal["en", "ar", "other"]
    intent: Literal[
        "room_booking", "group_booking", "rfp", "long_stay", "event_space", "info_only",
        "existing_booking", "complaint", "spam", "vendor", "other",
    ]
    segment: Literal["fit", "corporate", "group", "long_stay", "event", "unknown"]
    is_rfp: bool
    check_in: date | None
    check_out: date | None
    nights: int | None
    dates_flexible: bool
    date_text: str | None
    rooms: int | None
    adults: int | None
    children: int | None
    room_type_hint: str | None
    budget_amount: Decimal | None
    budget_currency: str | None
    budget_per: Literal["night", "stay", "month", "unknown"]
    company: str | None
    event_name: str | None
    special_requests: list[str]
    missing: list[Literal["dates", "guests", "rooms"]]
    confidence: float = Field(ge=0, le=1)
    evidence: list[FieldQuote]
