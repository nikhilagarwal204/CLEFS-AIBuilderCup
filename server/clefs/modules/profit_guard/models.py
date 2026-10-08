"""
Profit Guard canonical models (02 spec 3.2).

Three families live here:
- the canonical OTA claims a commit handler writes (statement line, payout
  line, virtual card), validated from core/ingest's canonical row dicts;
- `CanonicalContractTerms`, the Structured Outputs schema a contract PDF is
  extracted into (strict mode: every field present, "" or 0 for unknown, and
  every term cites the clause it came from);
- `Finding` and `CalcStep`, the shape every detector returns, so a finding
  always carries its formula trace and evidence.

Money is int minor units + currency in Python and Decimal strings in HTTP
(Canon 2). Percentages are percent Decimals (15 = 15%) except the statement
line's `commission_rate`, which stays the ratio core/ingest reads (0.15).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

# ─── Vocabulary ───

OTA_CODES = ("BOOKING", "AGODA", "EXPEDIA")
# Spellings a person or a PMS uses for the three OTAs. "BDC" is left out on
# purpose: it is a channel-manager code that also names non-Booking feeds.
OTA_ALIASES = {
    "BOOKING": "BOOKING", "BOOKING.COM": "BOOKING", "BOOKINGCOM": "BOOKING", "BOOKING_COM": "BOOKING",
    "AGODA": "AGODA", "AGODA.COM": "AGODA",
    "EXPEDIA": "EXPEDIA", "EXPEDIA.COM": "EXPEDIA", "EXPEDIA_GROUP": "EXPEDIA", "HOTELS.COM": "EXPEDIA",
}
BASE_RULES = ("ROOM_INCL_VAT", "RSR_TAX_INCL", "NET_RATE", "ROOM_EXCL_TAX")
CHARGE_TYPES = ("MUNICIPALITYFEE", "TOURISMFEE", "CITYTAX", "VAT", "SERVICECHARGE", "OTHER")
COLLECT_MODELS = ("OTA_COLLECT", "HOTEL_COLLECT")
PROGRAMS = ("GENIUS", "MOBILE", "COUNTRY", "EARLY_BOOKER", "PREFERRED", "PREFERRED_PLUS", "VISIBILITY_BOOSTER",
            "AGODA_VIP", "AGX", "OTHER")
PROGRAM_KINDS = ("DISCOUNT", "COMMISSION_UPLIFT", "PER_BOOKING_COST")
LINE_STATUSES = ("OK", "NO_SHOW", "CANCELLED", "MODIFIED", "ADJUSTMENT")
PAYOUT_LINE_KINDS = ("BOOKING", "REFUND", "CHARGEBACK", "ADJUSTMENT")
VCC_STATUSES = ("AVAILABLE", "FUNDED", "NOT_LOADED", "PARTIALLY_CHARGED", "FULLY_CHARGED", "CANCELLED", "EXPIRED")
FINDING_CLASSES = ("RECOVERABLE", "PREVENTABLE", "STRUCTURAL", "COMPLIANCE")
FINDING_STATUSES = ("DETECTED", "ACCEPTED", "DISPUTED", "RECOVERED", "REJECTED", "UNRESOLVED", "DISMISSED")
DISMISS_REASONS = ("FALSE_POSITIVE_DATA", "CONTRACT_EXCEPTION", "ALREADY_HANDLED", "TOO_SMALL",
                   "NOT_WORTH_DISPUTING", "OTHER")
DISPUTE_RULE_KINDS = ("HOURS_AFTER_CHECKOUT", "BUSINESS_DAYS_AFTER_INVOICE", "DAY_OF_NEXT_MONTH")


def normalize_ota(code: Any) -> str | None:
    """BOOKING | AGODA | EXPEDIA for a known spelling, else the upper-cased
    code as given (a hotel may run a regional OTA), None for blank."""
    if code is None:
        return None
    raw = str(code).strip().upper().replace(" ", "")
    if not raw:
        return None
    return OTA_ALIASES.get(raw, raw)


def is_known_ota(code: Any) -> bool:
    return normalize_ota(code) in OTA_CODES


# ─── Canonical claims (commit handlers) ───


class Charge(BaseModel):
    """One charge beside the room price on a statement or payout line
    (Booking's price_breakdown: municipality fee, Tourism Dirham, ...)."""

    type: str
    amount_minor: int
    is_already_collected_from_guest: bool | None = None
    is_included_total_partner_payout: bool | None = None


class CanonicalStatementLine(BaseModel):
    ota_res_id: str
    guest_name: str | None = None
    check_in: date
    check_out: date | None = None
    room_nights: int | None = None
    rooms: int | None = None
    line_status: Literal["OK", "NO_SHOW", "CANCELLED", "MODIFIED", "ADJUSTMENT"] = "OK"
    booked_at: date | None = None
    cancelled_at: date | None = None
    collect_model: str | None = None
    gross_minor: int
    commissionable_minor: int | None = None
    commission_rate: Decimal | None = None
    commission_minor: int
    currency: str
    booker_country: str | None = None
    rate_plan: str | None = None
    room_type: str | None = None
    programs: list[str] = Field(default_factory=list)
    charges: list[Charge] = Field(default_factory=list)
    remarks: str | None = None
    doc_no: str | None = None


class CanonicalPayoutLine(BaseModel):
    payout_ref: str | None = None
    payout_date: date | None = None
    ota_res_id: str
    guest_name: str | None = None
    check_in: date | None = None
    check_out: date | None = None
    total_amount_paid_minor: int | None = None
    commissionable_minor: int | None = None
    commission_minor: int | None = None
    charges_minor: int | None = None
    partner_payout_minor: int
    line_kind: Literal["BOOKING", "REFUND", "CHARGEBACK", "ADJUSTMENT"] = "BOOKING"
    payout_type: Literal["GROSS", "NET"] | None = None
    currency: str
    bank_transfer_reference: str | None = None
    event_date: date | None = None


class CanonicalVcc(BaseModel):
    vcc_id: str
    ota_res_id: str
    activation_date: date | None = None
    expiration_date: date
    initial_minor: int | None = None
    current_balance_minor: int
    currency: str
    status: str | None = None


class CanonicalBankCredit(BaseModel):
    value_date: date
    amount_minor: int
    currency: str
    reference: str | None = None
    counterparty: str | None = None
    description: str | None = None


class CanonicalCreditNoteLine(BaseModel):
    doc_no: str | None = None
    related_doc_no: str | None = None
    ota_res_id: str
    guest_name: str | None = None
    check_in: date | None = None
    amount_minor: int
    currency: str
    reason: str | None = None


# ─── Contract extraction (clefs.ai.structured, strict) ───


class ClauseCitation(BaseModel):
    """Where a term was read: the clause reference as printed ("GDT v2601
    2.3.2(ii)"), a verbatim excerpt, the page and the term it supports."""

    ref: str
    text: str
    page: int
    field: str


class DisputeRule(BaseModel):
    """kind HOURS_AFTER_CHECKOUT uses hours, BUSINESS_DAYS_AFTER_INVOICE
    uses days, DAY_OF_NEXT_MONTH uses day; the unused numbers are 0."""

    kind: str
    hours: int
    days: int
    day: int
    applies_to: str


class VccRules(BaseModel):
    """0 means the contract says nothing about it."""

    charge_within_days_after_checkout: int
    expires_days_after_checkout: int
    upc_charge_within_h: int


class CanonicalContractTerms(BaseModel):
    """What a contract PDF says, as the model read it. Strings are "" and
    numbers 0 when the document is silent; nothing is ever guessed from
    general knowledge (the system prompt says so), so an empty field means
    a person must fill it at confirmation."""

    ota_code: str
    entity_name: str
    entity_country: str
    effective_from: str
    effective_to: str
    base_commission_pct: str
    commission_base_rule: str
    excluded_charge_types: list[str]
    collect_models: list[str]
    dispute_rules: list[DisputeRule]
    vcc_rules: VccRules
    clauses: list[ClauseCitation]
    confidence: float


# ─── Findings (every detector returns these) ───


class CalcStep(BaseModel):
    """One line of a finding's formula trace, shown as a stepper."""

    label: str
    formula: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    result_minor: int | None = None
    result: str | None = None


class Finding(BaseModel):
    """A detector's draft before storage. `fingerprint` makes re-runs
    idempotent; `calc` and `evidence` make the amount explainable."""

    detector: str
    fingerprint: str
    klass: Literal["RECOVERABLE", "PREVENTABLE", "STRUCTURAL", "COMPLIANCE"] = Field(alias="class")
    amount_minor: int
    currency: str
    confidence: Decimal
    ota_code: str | None = None
    reservation_id: int | None = None
    statement_line_id: int | None = None
    subject_ref: str | None = None
    calc: list[CalcStep] = Field(default_factory=list)
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    deadline_at: Any = None
    internal_only: bool = False
    source: str = "detector"

    model_config = {"populate_by_name": True}


# ─── HTTP inputs ───


class ContractIn(BaseModel):
    """PUT /contracts/{id} (confirm or edit) and POST /contracts (manual)."""

    ota_code: str = Field(min_length=2, max_length=40)
    entity_name: str | None = Field(None, max_length=200)
    entity_country: str | None = Field(None, min_length=2, max_length=2)
    effective_from: date
    effective_to: date | None = None
    base_commission_pct: Decimal = Field(ge=0, le=100)
    commission_base_rule: Literal["ROOM_INCL_VAT", "RSR_TAX_INCL", "NET_RATE", "ROOM_EXCL_TAX"]
    excluded_charge_types: list[str] = Field(default_factory=list, max_length=10)
    collect_models: list[Literal["OTA_COLLECT", "HOTEL_COLLECT"]] = Field(default_factory=list)
    dispute_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    vcc_rules: dict[str, Any] = Field(default_factory=dict)
    clauses: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    note: str | None = Field(None, max_length=2000)
    confirm: bool = True


class ProgramIn(BaseModel):
    ota_code: str = Field(min_length=2, max_length=40)
    program: Literal["GENIUS", "MOBILE", "COUNTRY", "EARLY_BOOKER", "PREFERRED", "PREFERRED_PLUS",
                     "VISIBILITY_BOOSTER", "AGODA_VIP", "AGX", "OTHER"]
    kind: Literal["DISCOUNT", "COMMISSION_UPLIFT", "PER_BOOKING_COST"]
    pct: Decimal | None = Field(None, ge=0, le=100)
    fixed: Decimal | None = Field(None, ge=0)
    currency: str | None = Field(None, min_length=3, max_length=3)
    booker_countries: list[str] = Field(default_factory=list, max_length=250)
    stay_from: date | None = None
    stay_to: date | None = None
    rate_plans: list[str] = Field(default_factory=list, max_length=100)
    stackable: bool = True
    source: Literal["MANUAL", "EXTRANET_EXPORT"] = "MANUAL"
    label: str | None = Field(None, max_length=200)
    is_active: bool = True


class ChannelConfigIn(BaseModel):
    ota_code: str = Field(min_length=2, max_length=40)
    configured_from: date
    mf_configured_as_charge: bool | None = None
    td_configured_as_charge: bool | None = None
    card_fee_pct: Decimal | None = Field(None, ge=0, le=100)
    payments_fee_pct: Decimal | None = Field(None, ge=0, le=100)
    booking_engine_fee_pct: Decimal | None = Field(None, ge=0, le=100)
    note: str | None = Field(None, max_length=1000)


class PoliciesIn(BaseModel):
    max_total_discount_pct: Decimal | None = Field(None, ge=0, le=100)
    parity_tolerance_pct: Decimal | None = Field(None, ge=0, le=100)
    min_finding: Decimal | None = Field(None, ge=0)
    confidence_floor: Decimal | None = Field(None, ge=0, le=1)
    treat_sc_as_revenue: bool | None = None
    reference_ota: str | None = Field(None, max_length=40)
