"""Profit Guard contracts: default terms, the Canon K2 commission base with
its goldens, channel configuration, program uplifts, AI extraction with
clause citations and the human confirmation that makes terms count."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clefs import files
from clefs.core import tax
from clefs.core.errors import ClefsError
from clefs.modules.profit_guard import contracts, repo
from clefs.modules.profit_guard.models import CanonicalContractTerms

D = date(2026, 10, 5)


def stay(cur, slug, *, nights=3, rate_minor=35000, start=D):
    from datetime import timedelta

    lines = [tax.StayLine(start + timedelta(days=i), "DLX", 1, rate_minor) for i in range(nights)]
    return tax.compose_stay(cur, slug, lines=lines)


# ─── Expected commission ───


def test_booking_default_matches_the_inquiry_golden(cur, hotel_uae):
    """04 spec 4.9 golden: Booking, 3 nights x AED 350 -> base 1,216.79 and
    commission 182.52 (room + SC + VAT; MF and TD out, Canon K2)."""
    bd = stay(cur, hotel_uae.slug)
    ec = contracts.expected_commission(cur, hotel_uae.slug, ota_code="BOOKING", breakdown=bd, on=D)
    assert ec["base_minor"] == 121679 and ec["commission_minor"] == 18252
    assert ec["pct"] == Decimal("15") and ec["basis"] == "default" and ec["currency"] == "AED"
    assert ec["base_components"] == ["room", "sc", "vat"]
    # The as_dict() form gives the same answer (callers pass either).
    assert contracts.expected_commission(cur, hotel_uae.slug, ota_code="booking.com", breakdown=bd.as_dict(),
                                         on=D)["commission_minor"] == 18252


def test_fit_golden_and_unconfigured_charges_stay_in_the_base(cur, hotel_uae, make_user):
    """04 spec 4.9 FIT golden (2 nights, R 400): 0.15 x 927.08 = 139.06.
    When the owner says MF was NOT configured as a charge, MF is commissioned."""
    bd = stay(cur, hotel_uae.slug, nights=2, rate_minor=40000)
    ec = contracts.expected_commission(cur, hotel_uae.slug, ota_code="BOOKING", breakdown=bd, on=D)
    assert (ec["base_minor"], ec["commission_minor"]) == (92708, 13906)
    repo.put_channel_config(cur, hotel_uae.slug, {"ota_code": "BOOKING", "configured_from": date(2026, 10, 1),
                                                  "mf_configured_as_charge": False})
    ec2 = contracts.expected_commission(cur, hotel_uae.slug, ota_code="BOOKING", breakdown=bd, on=D)
    assert ec2["base_minor"] == 92708 + bd.mf and "mf" in ec2["base_components"]
    # Before the configuration date the default (configured) still holds.
    before = contracts.expected_commission(cur, hotel_uae.slug, ota_code="BOOKING", breakdown=bd, on=date(2026, 9, 1))
    assert before["base_minor"] == 92708


def test_agoda_expedia_and_unknown_channels(cur, hotel_uae):
    bd = stay(cur, hotel_uae.slug, nights=1, rate_minor=40000)
    agoda = contracts.expected_commission(cur, hotel_uae.slug, ota_code="AGODA", breakdown=bd, on=D)
    assert agoda["base_minor"] == bd.room + bd.sc + bd.mf + bd.vat and agoda["pct"] == Decimal("18")
    exp = contracts.expected_commission(cur, hotel_uae.slug, ota_code="EXPEDIA", breakdown=bd, on=D)
    assert exp["base_minor"] == bd.room and exp["commission_minor"] == 7200
    assert contracts.expected_commission(cur, hotel_uae.slug, ota_code="BDC", breakdown=bd, on=D) is None
    assert contracts.expected_commission(cur, hotel_uae.slug, ota_code="BOOKING", breakdown={}, on=D) is None


def test_confirmed_contract_and_uplifts_win_over_defaults(cur, hotel_uae):
    s = hotel_uae.slug
    c = contracts.save_contract(cur, s, {"ota_code": "BOOKING", "effective_from": date(2026, 1, 1),
                                         "base_commission_pct": Decimal("17"),
                                         "commission_base_rule": "ROOM_INCL_VAT",
                                         "excluded_charge_types": ["municipalityfee", "TOURISMFEE"]})
    assert c["status"] == "CONFIRMED" and c["excluded_charge_types"] == ["MUNICIPALITYFEE", "TOURISMFEE"]
    repo.create_program(cur, s, "AED", {"ota_code": "BOOKING", "program": "PREFERRED", "kind": "COMMISSION_UPLIFT",
                                        "pct": Decimal("3"), "stay_from": date(2026, 10, 1)})
    repo.create_program(cur, s, "AED", {"ota_code": "BOOKING", "program": "VISIBILITY_BOOSTER",
                                        "kind": "COMMISSION_UPLIFT", "pct": Decimal("5"), "booker_countries": ["gb"]})
    bd = stay(cur, s, nights=1, rate_minor=40000)
    ec = contracts.expected_commission(cur, s, ota_code="BOOKING", breakdown=bd, on=D)
    assert ec["basis"] == "contract" and ec["contract_id"] == c["id"] and ec["pct"] == Decimal("20")
    uk = contracts.expected_commission(cur, s, ota_code="BOOKING", breakdown=bd, on=D, booker_country="GB")
    assert uk["pct"] == Decimal("25") and len(uk["uplifts"]) == 2
    # A renewal closes the open-ended contract the day before it starts.
    c2 = contracts.save_contract(cur, s, {"ota_code": "BOOKING", "effective_from": date(2027, 1, 1),
                                          "base_commission_pct": Decimal("16"), "commission_base_rule": "ROOM_INCL_VAT"})
    assert contracts.get_contract(cur, s, c["id"])["effective_to"] == date(2026, 12, 31)
    assert contracts.contract_on(cur, s, "BOOKING", date(2027, 2, 1))["id"] == c2["id"]
    with pytest.raises(ClefsError) as exc:
        contracts.save_contract(cur, s, {"ota_code": "BOOKING", "effective_from": date(2026, 6, 1),
                                         "effective_to": date(2026, 7, 1), "base_commission_pct": Decimal("10"),
                                         "commission_base_rule": "ROOM_INCL_VAT"})
    assert exc.value.code == "contract_overlap"


def test_vcc_deadline_is_the_earliest_rule(cur):
    terms = contracts.default_terms("EXPEDIA")
    d, basis = contracts.vcc_charge_deadline(terms, expiration_date=date(2027, 12, 1), check_out=date(2026, 10, 1),
                                             activation_date=None)
    assert (d, basis) == (date(2027, 3, 30), "expires_days_after_checkout")
    upc = contracts.default_terms("AGODA")
    assert contracts.vcc_charge_deadline(upc, expiration_date=date(2027, 1, 1), check_out=date(2026, 10, 3),
                                         activation_date=date(2026, 10, 1))[0] == date(2026, 10, 2)


# ─── Extraction and confirmation ───

CONTRACT_TEXT = (
    "Booking.com B.V. General Delivery Terms v2601. 2.3.2(ii) Commission is calculated on the room price "
    "including VAT and service charge; municipality fee and tourism dirham configured as charges are excluded. "
    "Commission: 15%. Effective 1 January 2026."
)


def extraction(**over):
    base = {
        "ota_code": "BOOKING", "entity_name": "Booking.com B.V.", "entity_country": "NL",
        "effective_from": "2026-01-01", "effective_to": "", "base_commission_pct": "15",
        "commission_base_rule": "ROOM_INCL_VAT", "excluded_charge_types": ["MUNICIPALITYFEE", "TOURISMFEE"],
        "collect_models": ["OTA_COLLECT", "bogus"],
        "dispute_rules": [{"kind": "HOURS_AFTER_CHECKOUT", "hours": 48, "days": 0, "day": 0, "applies_to": "no-show"}],
        "vcc_rules": {"charge_within_days_after_checkout": 365, "expires_days_after_checkout": 0,
                      "upc_charge_within_h": 0},
        "clauses": [
            {"ref": "GDT v2601 2.3.2(ii)", "text": "Commission is calculated on the room price including VAT",
             "page": 1, "field": "commission_base_rule"},
            {"ref": "GDT v2601 1.1", "text": "Commission: 15%", "page": 1, "field": "base_commission_pct"},
            {"ref": "Annex 9", "text": "made up", "page": 7, "field": "vcc_rules"},
        ],
        "confidence": 0.9,
    }
    base.update(over)
    return CanonicalContractTerms(**base)


def test_extract_stores_a_cited_draft_that_counts_only_once_confirmed(cur, hotel_uae, fake_ai, make_user):
    s = hotel_uae.slug
    fid = files.put_file(cur, s, CONTRACT_TEXT.encode(), filename="gdt.txt", content_type="text/plain",
                         purpose="profit.contract").id
    fake_ai.push(extraction())
    draft = contracts.extract_contract(cur, s, pages=contracts.pages_from_file(cur, s, str(fid)), file_id=str(fid))
    assert draft["status"] == "DRAFT" and draft["extracted_by"] == "LLM"
    assert draft["base_commission_pct"] == Decimal("15.000") and draft["collect_models"] == ["OTA_COLLECT"]
    assert [c["ref"] for c in draft["clauses"]] == ["GDT v2601 2.3.2(ii)", "GDT v2601 1.1"]
    assert draft["extraction"]["review_flags"] == ["citation_page_out_of_range:Annex 9"]
    assert draft["dispute_rules"] == [{"kind": "HOURS_AFTER_CHECKOUT", "hours": 48, "applies_to": "no-show"}]
    assert fake_ai.calls[0].task == "pg.contract_extract"
    # A draft is never the hotel's terms.
    assert contracts.contract_on(cur, s, "BOOKING", D)["is_default"] is True
    body = {k: draft[k] for k in contracts.CONTRACT_COLS}
    confirmed = contracts.save_contract(cur, s, {**body, "base_commission_pct": Decimal("16")},
                                        contract_id=draft["id"])
    assert confirmed["status"] == "CONFIRMED" and confirmed["confirmed_at"] is not None
    assert contracts.contract_on(cur, s, "BOOKING", D)["id"] == draft["id"]


def test_extraction_flags_missing_terms_and_ai_off(cur, hotel_uae, fake_ai):
    s = hotel_uae.slug
    fake_ai.push(extraction(base_commission_pct="", commission_base_rule="?", effective_from="", clauses=[],
                            confidence=0.3))
    draft = contracts.extract_contract(cur, s, pages=[CONTRACT_TEXT], ota_code="booking.com")
    flags = draft["extraction"]["review_flags"]
    assert {"base_commission_pct_missing", "commission_base_rule_missing", "effective_from_missing",
            "uncited:base_commission_pct", "low_confidence"} <= set(flags)
    # Missing terms fall back to the OTA's defaults for the draft, flagged.
    assert draft["base_commission_pct"] == Decimal("15.000") and draft["commission_base_rule"] == "ROOM_INCL_VAT"
    from clefs import ai

    fake_ai.push(ai.AIDisabled())
    with pytest.raises(ClefsError) as exc:
        contracts.extract_contract(cur, s, pages=[CONTRACT_TEXT], ota_code="BOOKING")
    assert exc.value.code == "ai_unavailable"
