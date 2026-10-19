"""Server-built prompt (03 spec 2.1, 2.8): blocks, prompt_version, safety text."""

from __future__ import annotations

import pytest

from clefs.modules.concierge import api, kb, prompt
from clefs.modules.concierge.constants import GUEST_LANGUAGES, SURFACES

GUEST = {"guests": [{"name": "Mr Omar Haddad", "type": "primary"}], "preferences": {"tags": ["extra pillows"]}, "loyaltyTier": "Gold"}


def _spec(cur, slug, **kw):
    kw.setdefault("surface", "web_room")
    kw.setdefault("room", "101")
    return api.build_surface_session(slug, cur=cur, **kw)


def _approve(cur, slug, **data):
    e = kb.create_entry(cur, slug, data)
    return kb.approve_entry(cur, slug, e["id"], actor=None)


# ─── Snapshots per surface and language ───


@pytest.mark.parametrize("surface", SURFACES)
@pytest.mark.parametrize("language", GUEST_LANGUAGES)
def test_every_surface_and_language_is_clean(cur, hotel_uae, surface, language):
    _approve(cur, hotel_uae.slug, answer_en="Breakfast \u2014 served 7 to 11 in Saffron.", section_key="breakfast")
    spec = _spec(cur, hotel_uae.slug, surface=surface, language=language, room=None if surface == "web_location" else "101")
    for text in (spec.instructions, spec.greeting_text):
        assert "\u2014" not in text, "no em dash anywhere"
        assert "\u20b9" not in text and "INR" not in text, "no rupee"
    low = spec.instructions.lower()
    assert "asia/kolkata" not in low and " ist" not in low
    assert "assamese" not in low and "masor tenga" not in low, "the hardcoded menu is gone"
    assert "highest priority" not in low, "loyalty wording is neutralised"
    for number in ("999", "998", "997"):
        assert number in spec.instructions, f"UAE emergency number {number} missing"
    assert "Asia/Dubai" in spec.instructions
    assert len(spec.prompt_version) == 16
    assert spec.surface == surface


def test_blocks_are_in_order(cur, hotel_uae):
    p = prompt.PromptInput(hotel=hotel_uae, surface="web_room", assistant_name="Layla", room="101")
    names = [n for n, _ in prompt.build_blocks(p)]
    assert names == ["persona", "disclosure", "language", "knowledge", "menu", "guest", "policy", "tools"]
    p.surface = "whatsapp"
    assert "menu" not in [n for n, _ in prompt.build_blocks(p)], "menu only on ordering surfaces"


def test_phone_disclosure_is_mandatory(cur, hotel_uae):
    spec = _spec(cur, hotel_uae.slug, surface="phone")
    assert "DISCLOSURE (mandatory)" in spec.instructions and "AI assistant" in spec.instructions
    assert spec.channel == "sip" and spec.voice


def test_kb_entries_tagged_and_only_approved(cur, hotel_uae):
    approved = _approve(cur, hotel_uae.slug, question_en="Pool hours?", answer_en="The pool is open 7:00 to 21:00.", section_key="pool")
    kb.create_entry(cur, hotel_uae.slug, {"answer_en": "The helipad is on the roof.", "section_key": "general"})
    spec = _spec(cur, hotel_uae.slug)
    assert f"[KB:{approved['id']}]" in spec.instructions and "7:00 to 21:00" in spec.instructions
    assert "helipad" not in spec.instructions
    assert spec.kb_entry_ids == [approved["id"]]


def test_arabic_session_uses_arabic_kb_text(cur, hotel_uae):
    _approve(cur, hotel_uae.slug, answer_en="Wi-Fi is free.", answer_ar="الواي فاي مجاني.", section_key="wifi")
    assert "مجاني" in _spec(cur, hotel_uae.slug, language="ar").instructions
    assert "Wi-Fi is free." in _spec(cur, hotel_uae.slug, language="en").instructions


def test_tool_rules_split_asks_and_route_not_fixed_to_recovery(cur, hotel_uae):
    """One job per distinct ask (multi-request splitting), a confirmation that
    names the person and the minutes, and "not done / not fixed" to
    flag_service_recovery while billing and legal stay with a person."""
    text = _spec(cur, hotel_uae.slug).instructions
    assert "once per distinct ask" in text and "is three calls" in text
    assert "staff member's first name and the minutes" in text
    assert "still not fixed: call flag_service_recovery" in text
    assert "escalate_to_human (reason complaint, refund_or_billing, legal or asked_for_human)" in text


def test_empty_kb_says_escalate_unknown(cur, hotel_uae):
    spec = _spec(cur, hotel_uae.slug)
    assert "No hotel information has been approved yet" in spec.instructions
    assert "unknown_answer" in spec.instructions


def test_large_kb_inlines_titles_and_points_to_search(cur, hotel_uae, monkeypatch):
    monkeypatch.setattr(prompt, "KB_INLINE_CHAR_BUDGET", 50)
    _approve(cur, hotel_uae.slug, question_en="Spa hours?", answer_en="The spa opens at ten and closes at eight.", section_key="spa")
    spec = _spec(cur, hotel_uae.slug)
    assert "Spa hours?" in spec.instructions and "closes at eight" not in spec.instructions
    assert "search_hotel_info" in spec.instructions


# ─── Guest context ───


def test_unverified_session_has_no_personal_context(cur, hotel_uae):
    spec = _spec(cur, hotel_uae.slug, verified=False, guest=GUEST)
    assert "Omar" not in spec.instructions and "pillows" not in spec.instructions
    assert "could not be verified" in spec.instructions
    assert "Omar" not in spec.greeting_text


def test_verified_session_uses_first_name_only(cur, hotel_uae):
    spec = _spec(cur, hotel_uae.slug, verified=True, guest=GUEST)
    assert "Omar" in spec.instructions and "Haddad" not in spec.instructions
    assert "extra pillows" in spec.instructions and "Gold" not in spec.instructions
    assert "Omar" in spec.greeting_text


def test_prompt_version_is_stable_per_guest_and_changes_with_kb(cur, hotel_uae):
    a = _spec(cur, hotel_uae.slug, room="101").prompt_version
    b = _spec(cur, hotel_uae.slug, room="205").prompt_version
    assert a == b, "room and time are placeholders in the version"
    _approve(cur, hotel_uae.slug, answer_en="Parking is free.", section_key="parking")
    assert _spec(cur, hotel_uae.slug, room="101").prompt_version != a


def test_location_surface_uses_venue_notes(cur, hotel_uae):
    cur.execute(
        "INSERT INTO hotel_locations (hotel_slug, slug, name, type, custom_instructions) VALUES (%s, 'spa', 'Serenity Spa', 'spa', 'Old note')",
        (hotel_uae.slug,),
    )
    _approve(cur, hotel_uae.slug, kind="venue_note", location_slug="spa", answer_en="Massages need a booking.", section_key="spa")
    spec = _spec(cur, hotel_uae.slug, surface="web_location", room=None, location="spa")
    assert "Serenity Spa" in spec.instructions and "Massages need a booking." in spec.instructions
    assert "Old note" not in spec.instructions, "unapproved legacy note stays out in structured mode"


# ─── Frozen api.build_session and eval dry runs ───


@pytest.mark.parametrize(
    ("channel", "surface"), [("web", "web_room"), ("sip", "phone"), ("text", "text_fallback"), ("whatsapp", "whatsapp")]
)
def test_frozen_build_session_maps_channels(cur, hotel_uae, channel, surface):
    spec = api.build_session(hotel_uae.slug, channel=channel, room="101")
    assert spec.surface == surface and spec.channel == channel and spec.prompt_version
    assert not spec.verified and not spec.dry_run


def test_web_with_location_only_is_location_surface(cur, hotel_uae):
    assert api.build_session(hotel_uae.slug, channel="web", location="lobby").surface == "web_location"


def test_eval_surface_is_always_dry_run(cur, hotel_uae):
    spec = _spec(cur, hotel_uae.slug, surface="eval")
    assert spec.dry_run is True
    assert _spec(cur, hotel_uae.slug, surface="web_room", dry_run=True).dry_run is True


def test_legacy_text_mode_keeps_old_kb_verbatim(cur, legacy_slug):
    cur.execute("UPDATE hotels SET knowledge_base = %s WHERE slug = %s", ("Checkout is at 11.\n\nThe gym is on floor 2.", legacy_slug))
    spec = api.build_surface_session(legacy_slug, surface="web_room", room="101", cur=cur)
    assert spec.kb_mode == "legacy_text"
    assert "Checkout is at 11." in spec.instructions and "The gym is on floor 2." in spec.instructions
