"""Extraction (04 spec 4.4, 4.12): consent gating of the model, the
deterministic parser, validation, segment rules and routing by intent."""

from __future__ import annotations

from datetime import date

from tests.modules.inquiries.test_intake_support import (  # noqa: F401 (fixtures)
    hotel,
    inquiries,
    now,
    send_wa,
    set_settings,
)


def _ext(**kw) -> dict:
    base = {
        "language": "en", "intent": "room_booking", "segment": "fit", "is_rfp": False,
        "check_in": None, "check_out": None, "nights": None, "dates_flexible": False, "date_text": None,
        "rooms": None, "adults": None, "children": None, "room_type_hint": None, "budget_amount": None,
        "budget_currency": None, "budget_per": "unknown", "company": None, "event_name": None,
        "special_requests": [], "missing": [], "confidence": 0.9, "evidence": [],
    }
    base.update(kw)
    return base


def _web(cur, hotel, text: str, **kw):
    from clefs.modules.inquiries.intake import Inbound, ingest

    return ingest(cur, hotel, Inbound(source="web_form", text=text, email="guest@example.com",
                                      consent_basis="express_web_form", **kw)).inquiry


def test_unconsented_inbound_never_reaches_the_model(cur, hotel, fake_ai, run_jobs):
    send_wa("We need 3 rooms from 12 to 15 October for 6 adults")
    run_jobs("inquiries.extract")
    assert fake_ai.calls == []
    r = inquiries(cur, hotel.slug)[0]
    assert r["extraction"]["reader"] == "deterministic"
    assert (r["check_in"], r["check_out"], r["nights"], r["rooms"], r["adults"]) == (
        date(2026, 10, 12), date(2026, 10, 15), 3, 3, 6)
    assert float(r["extraction_confidence"]) <= 0.3


def test_phone_desk_is_staff_logged_and_stays_off_the_model(cur, hotel, fake_ai, run_jobs):
    from clefs.modules.inquiries.intake import Inbound, ingest

    ingest(cur, hotel, Inbound(source="phone_desk", text="Caller wants 2 rooms next week", phone="+971509998877",
                               author_type="staff"))
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert fake_ai.calls == [] and r["consent_basis"] == "staff_logged"
    assert r["first_response_by"] == "phone_desk"  # the call itself answered it


def test_setting_on_lets_unconsented_inbound_reach_the_model(cur, hotel, fake_ai, run_jobs, set_settings):
    set_settings(ai_on_unconsented_inbound=True)
    fake_ai.push(_ext(rooms=2, check_in="2026-10-12", check_out="2026-10-14"))
    send_wa("2 rooms please")
    run_jobs("inquiries.extract")
    assert len(fake_ai.calls) == 1
    assert inquiries(cur, hotel.slug)[0]["extraction"]["reader"] == "llm"


def test_web_form_uses_the_model_with_contacts_redacted(cur, hotel, fake_ai, run_jobs):
    fake_ai.push(_ext(segment="corporate", company="ACME", rooms=2, check_in="2026-10-20", check_out="2026-10-23"))
    _web(cur, hotel, "Corporate stay for ACME, call +971 50 123 4567 or ops@acme.test")
    run_jobs("inquiries.extract")
    assert len(fake_ai.calls) == 1
    sent = str(fake_ai.calls[0].input)
    assert "[phone]" in sent and "[email]" in sent and "4567" not in sent and "acme.test" not in sent
    r = inquiries(cur, hotel.slug)[0]
    assert r["segment"] == "corporate" and r["company"] == "ACME" and r["nights"] == 3
    # Corporate: the SLA moves to 60 minutes.
    assert r["first_response_due_at"].astimezone(hotel.tz).strftime("%H:%M") == "11:00"


def test_arabic_extraction_on_the_fake_ai(cur, hotel, fake_ai, run_jobs):
    fake_ai.push(_ext(language="ar", rooms=4, check_in="2026-11-01", check_out="2026-11-04",
                      evidence=[{"field": "rooms", "quote": "٤ غرف"}]))
    _web(cur, hotel, "نحتاج ٤ غرف من ١ إلى ٤ نوفمبر")
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert r["language"] == "ar" and r["rooms"] == 4 and r["extraction"]["evidence"][0]["quote"] == "٤ غرف"


def test_validation_and_segment_rules(cur, hotel, fake_ai, run_jobs):
    # 12 rooms -> group whatever the model said; check_out before check_in dropped.
    fake_ai.push(_ext(segment="fit", rooms=12, check_in="2026-10-10", check_out="2026-10-08"))
    _web(cur, hotel, "12 rooms for a wedding party")
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert r["segment"] == "group" and r["check_out"] is None
    assert any("check_out" in w for w in r["extraction"]["warnings"])


def test_long_stay_rule_and_usd_budget_at_the_peg(cur, hotel, fake_ai, run_jobs):
    fake_ai.push(_ext(segment="fit", check_in="2026-11-01", check_out="2026-12-15", budget_amount="1000",
                      budget_currency="USD", budget_per="month"))
    _web(cur, hotel, "Long stay Nov-Dec, budget USD 1000 a month")
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert r["segment"] == "long_stay" and r["nights"] == 44
    assert r["budget_currency"] == "AED" and r["budget_minor"] == 367250


def test_vendor_becomes_spam(cur, hotel, fake_ai, run_jobs):
    fake_ai.push(_ext(intent="vendor", segment="unknown"))
    _web(cur, hotel, "We sell SEO services to hotels")
    run_jobs("inquiries.extract")
    assert inquiries(cur, hotel.slug)[0]["stage"] == "spam"


def test_existing_booking_is_rerouted_to_a_followup(cur, hotel, fake_ai, run_jobs, monkeypatch):
    from clefs.modules.service import api as service_api

    calls = []
    monkeypatch.setattr(service_api, "create_followup", lambda cur, slug, **kw: calls.append(kw) or {"id": 7})
    fake_ai.push(_ext(intent="existing_booking"))
    _web(cur, hotel, "I have a booking next week, can I change the dates?")
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert r["stage"] == "rerouted"
    assert calls and calls[0]["kind"] == "guest_contact" and calls[0]["source"] == "inquiries"


def test_ai_disabled_degrades_to_the_parser(cur, hotel, fake_ai, run_jobs):
    from clefs import ai

    fake_ai.push(ai.AIDisabled())
    _web(cur, hotel, "2 rooms on 2026-10-05 for 2 nights")
    run_jobs("inquiries.extract")
    r = inquiries(cur, hotel.slug)[0]
    assert r["extraction"]["reader"] == "deterministic" and r["check_in"] == date(2026, 10, 5)
    assert r["check_out"] == date(2026, 10, 7)
