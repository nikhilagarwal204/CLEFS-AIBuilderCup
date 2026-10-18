"""Structured KB: approval, revisions, facts, import, unknowns (03 spec 2.5)."""

from __future__ import annotations

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.concierge import api, kb


def _entry(cur, slug, **kw):
    data = {"answer_en": "Wi-Fi is free. Network Clefs-Guest, password sunrise.", "section_key": "wifi"}
    data.update(kw)
    return kb.create_entry(cur, slug, data)


# ─── Entries and approval ───


def test_new_entry_is_pending_and_not_in_facts(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug)
    assert e["status"] == "pending"
    assert api.kb_facts(cur, hotel_uae.slug) == []


def test_approve_puts_entry_in_facts_with_ids(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug, question_en="What is the Wi-Fi?")
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    facts = api.kb_facts(cur, hotel_uae.slug)
    assert len(facts) == 1
    assert facts[0]["entry_id"] == e["id"] and facts[0]["approved_at"] is not None
    assert "sunrise" in facts[0]["text"]


def test_arabic_answer_used_for_arabic(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug, answer_ar="الواي فاي مجاني.")
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    assert "مجاني" in api.kb_facts(cur, hotel_uae.slug, language="ar")[0]["text"]
    assert "sunrise" in api.kb_facts(cur, hotel_uae.slug, language="en")[0]["text"]


def test_edit_of_approved_goes_back_to_pending_and_revisions_kept(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug)
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    edited = kb.update_entry(cur, hotel_uae.slug, e["id"], {"answer_en": "Wi-Fi is free, password moonrise."}, actor=None)
    assert edited["status"] == "pending" and edited["version"] == 2
    changes = [r["change"] for r in kb.list_revisions(cur, hotel_uae.slug, e["id"])]
    assert changes == ["created", "approved", "edited"]


def test_one_live_answer_per_fact_key(cur, hotel_uae):
    a = _entry(cur, hotel_uae.slug, answer_en="Check-in is from 2 pm.", fact_key="checkin_time", fact_value="2pm")
    kb.approve_entry(cur, hotel_uae.slug, a["id"], actor=None)
    b = _entry(cur, hotel_uae.slug, answer_en="Check-in is from 3 pm.", fact_key="checkin_time", fact_value="15:00")
    kb.approve_entry(cur, hotel_uae.slug, b["id"], actor=None)
    keyed = api.kb_facts(cur, hotel_uae.slug, keyed_only=True)
    assert [(f["fact_key"], f["value"]) for f in keyed] == [("checkin_time", "15:00")]
    assert kb.get_entry(cur, hotel_uae.slug, a["id"])["status"] == "archived"


def test_local_place_needs_source_url(cur, hotel_uae):
    with pytest.raises(ClefsError) as exc:
        _entry(cur, hotel_uae.slug, kind="local_place", answer_en="Try Al Ustad kebabs.", section_key="local")
    assert exc.value.code == "source_url_required"


def test_bad_fact_value_rejected(cur, hotel_uae):
    with pytest.raises(ClefsError) as exc:
        _entry(cur, hotel_uae.slug, fact_key="parking", fact_value="maybe")
    assert exc.value.code == "invalid_fact_value"


def test_reject_and_archive(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug)
    assert kb.set_entry_status(cur, hotel_uae.slug, e["id"], "rejected", actor=None, reason="wrong")["status"] == "rejected"
    f = _entry(cur, hotel_uae.slug)
    kb.approve_entry(cur, hotel_uae.slug, f["id"], actor=None)
    kb.set_entry_status(cur, hotel_uae.slug, f["id"], "archived", actor=None)
    assert api.kb_facts(cur, hotel_uae.slug) == []


def test_validity_window(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug, valid_to="2000-01-01")
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    assert api.kb_facts(cur, hotel_uae.slug) == []


def test_tenant_isolation(cur, hotel_uae, legacy_slug):
    e = _entry(cur, hotel_uae.slug)
    assert kb.get_entry(cur, legacy_slug, e["id"]) is None
    assert api.kb_entry_status(cur, legacy_slug, e["id"]) is None


def test_search_approved(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug, question_en="Is there parking?", answer_en="Valet parking costs AED 50 a day.", section_key="parking")
    kb.approve_entry(cur, hotel_uae.slug, e["id"], actor=None)
    hits = kb.search_approved(cur, hotel_uae.slug, "parking")
    assert hits and hits[0]["entry_id"] == e["id"] and hits[0]["kb_ref"] == f"KB:{e['id']}"
    assert kb.search_approved(cur, hotel_uae.slug, "helipad") == []


def test_patching_fact_value_alone_is_normalised(cur, hotel_uae):
    e = _entry(cur, hotel_uae.slug, answer_en="Check-out is at noon.", fact_key="checkout_time", fact_value="12:00")
    out = kb.update_entry(cur, hotel_uae.slug, e["id"], {"fact_value": "11 am"}, actor=None)
    assert out["fact_value"] == "11:00"
    with pytest.raises(ClefsError):
        kb.update_entry(cur, hotel_uae.slug, e["id"], {"fact_value": "whenever"}, actor=None)
