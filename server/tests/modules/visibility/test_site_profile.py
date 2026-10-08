"""Visibility profile store and budget (04 spec 5.1, 5.10)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))


@pytest.fixture
def slug(hotel_uae, clock):
    clock.freeze(NOW)
    return hotel_uae.slug


def test_profile_defaults_then_partial_update(cur, slug):
    from clefs.modules.visibility import store

    p = store.get_profile(cur, slug)
    assert p["exists"] is False and p["grid_size"] == 7 and p["serpapi_enabled"] is True
    p = store.update_profile(cur, slug, {"area": "Deira", "landmarks": ["Gold Souk"]}, user_id=None)
    assert p["area"] == "Deira" and p["landmarks"] == ["Gold Souk"] and p["exists"]
    p = store.update_profile(cur, slug, {"place_id": "ChIJx"}, user_id=None)
    assert p["area"] == "Deira" and p["place_id"] == "ChIJx"


def test_budget_compares_micros_with_minor_times_10000(cur, slug):
    from clefs.modules.visibility import store

    store.update_profile(cur, slug, {"monthly_budget_minor": 100}, user_id=None)  # USD 1.00
    st = store.budget_state(cur, slug)
    assert st["budget_micros"] == 1_000_000 and st["spent_micros"] == 0 and not st["exhausted"]
    store.record_cost(cur, slug, provider="gemini", purpose="visibility.ai", cost_micros=999_999)
    assert not store.budget_state(cur, slug)["exhausted"]
    store.record_cost(cur, slug, provider="gemini", purpose="visibility.ai", cost_micros=1)
    assert store.budget_state(cur, slug)["exhausted"]


def test_fact_errors_route_by_cited_domain():
    from clefs.modules.visibility.action_types import route_fact_error

    host = "www.hotel.example"
    assert route_fact_error(["www.booking.com", "hotel.example"], website_host=host) == "visibility.ota_listing_fix"
    assert route_fact_error(["hotel.example"], website_host=host) == "visibility.site_fact_fix"
    assert route_fact_error(["www.google.com"], website_host=host) == "visibility.lodging_sync"
    assert route_fact_error([], website_host=host) == "visibility.add_kb_faq"


def test_add_kb_faq_needs_an_answer_and_queues_unapproved(cur, slug, monkeypatch):
    from clefs.core import actions
    from clefs.core.hotels import get_hotel
    from clefs.modules.concierge import api as concierge_api
    from clefs.modules.visibility import action_types  # noqa: F401

    t = actions.get_action_type("visibility.add_kb_faq")
    hotel = get_hotel(cur, slug)

    def ctx(params, result=None):
        act = {"id": None, "params": params, "execution_result": result}
        return actions.ActionCtx(cur=cur, hotel=hotel, action=act, action_type=t, actor=None)

    assert t.executor(ctx({"question_en": "Parking?"})).ok is False
    monkeypatch.setattr(concierge_api, "propose_kb_entry", lambda cur, s, **kw: 77)
    res = t.executor(ctx({"question_en": "Parking?", "answer_en": "paid", "fact_key": "parking"}))
    assert res.ok and res.data == {"entry_id": 77}
    monkeypatch.setattr(concierge_api, "kb_entry_status", lambda cur, s, eid: {"entry_id": eid, "status": "pending"})
    assert t.verifier(ctx({}, {"entry_id": 77})).status == "pending"
    monkeypatch.setattr(concierge_api, "kb_entry_status", lambda cur, s, eid: {"entry_id": eid, "status": "approved"})
    assert t.verifier(ctx({}, {"entry_id": 77})).status == "verified"
