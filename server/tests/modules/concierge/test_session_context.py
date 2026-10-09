"""Stay context endpoint (03 spec 2.3): verified vs limited mode, no leaks."""

from __future__ import annotations

import json

import pytest

from clefs.core import rooms
from clefs.db import Json


@pytest.fixture
def guests_in(cur, hotel_uae, monkeypatch):
    """Two in-house guests. guests.api.guest_context_for_room is stubbed
    from the same rows (the scoped run does not migrate the Guests lane's
    gst_ tables it also reads); the contract is its allowlisted dict."""
    from clefs.modules.guests import api as guests_api

    data = {}
    for room, name in (("101", "Mr Omar Haddad"), ("102", "Ms Priya Nair")):
        data[room] = {"guests": [{"name": name, "type": "primary"}], "loyaltyTier": "Gold", "roomNumber": room}
        cur.execute(
            "INSERT INTO guest_rooms (hotel_slug, room_number, data) VALUES (%s, %s, %s)",
            (hotel_uae.slug, room, Json(data[room])),
        )
    monkeypatch.setattr(guests_api, "guest_context_for_room", lambda _cur, slug, room: data.get(room) if slug == hotel_uae.slug else None)
    return hotel_uae


def test_guests_api_failure_degrades_to_no_name(client, hotel_uae, monkeypatch):
    from clefs.modules.guests import api as guests_api

    def _boom(cur, slug, room):
        cur.execute("SELECT * FROM no_such_table_for_this_test")

    monkeypatch.setattr(guests_api, "guest_context_for_room", _boom)
    r = _ctx(client, hotel_uae.slug, "101", k=rooms.qr_key(hotel_uae.slug, "101"))
    assert r.status_code == 200 and r.json()["verified"] is True and r.json()["guest"] is None


def _ctx(client, slug, target, **params):
    return client.get(f"/public/stay/{slug}/{target}/context", params=params)


def test_verified_scan_gets_first_name_and_stay_key(client, guests_in):
    k = rooms.qr_key(guests_in.slug, "101")
    r = _ctx(client, guests_in.slug, "101", k=k)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verified"] is True and body["key_status"] == "valid"
    assert body["guest"] == {"first_name": "Omar"}
    assert body["room"] == {"number": "101", "floor": "1"}
    assert body["stay_session_key"]
    dumped = json.dumps(body, ensure_ascii=False)
    assert "Haddad" not in dumped and "Priya" not in dumped and "102" not in dumped and "Gold" not in dumped


def test_legacy_qr_is_limited_mode(client, guests_in):
    body = _ctx(client, guests_in.slug, "101").json()
    assert body["verified"] is False and body["key_status"] == "absent"
    assert body["guest"] is None and body["stay_session_key"] is None
    assert "Omar" not in json.dumps(body, ensure_ascii=False)


def test_wrong_or_rotated_key_is_limited_mode(client, cur, guests_in):
    old = rooms.qr_key(guests_in.slug, "101")
    rooms.rotate_qr(cur, guests_in.slug, "101")
    body = _ctx(client, guests_in.slug, "101", k=old).json()
    assert body["verified"] is False and body["key_status"] == "invalid" and body["guest"] is None


def test_stay_token_verifies_a_later_visit(client, guests_in):
    first = _ctx(client, guests_in.slug, "101", k=rooms.qr_key(guests_in.slug, "101")).json()
    again = _ctx(client, guests_in.slug, "101", st=first["stay_session_key"]).json()
    assert again["verified"] is True and again["key_status"] == "stay_token"
    other = _ctx(client, guests_in.slug, "102", st=first["stay_session_key"]).json()
    assert other["verified"] is False, "a stay token is bound to its room"


def test_unknown_room_and_hotel_404(client, hotel_uae):
    assert _ctx(client, hotel_uae.slug, "999").status_code == 404
    assert _ctx(client, "no-such-hotel", "101").status_code == 404


def test_location_target(client, cur, hotel_uae):
    cur.execute(
        "INSERT INTO hotel_locations (hotel_slug, slug, name, type) VALUES (%s, 'sky-lounge', 'Sky Lounge', 'restaurant')",
        (hotel_uae.slug,),
    )
    body = _ctx(client, hotel_uae.slug, "sky-lounge").json()
    assert body["surface"] == "web_location" and body["room"] is None
    assert body["location"] == {"slug": "sky-lounge", "name": "Sky Lounge", "type": "restaurant"}
    assert _ctx(client, hotel_uae.slug, "nowhere").status_code == 404


def test_context_carries_uae_numbers_consent_texts_and_hotel_card(client, hotel_uae):
    body = _ctx(client, hotel_uae.slug, "room101", lang="ar").json()
    assert body["room"]["number"] == "101"
    assert body["language"] == "ar"
    assert {"number": "998", "kind": "ambulance"} in body["emergency_numbers"]
    texts = body["consent_texts"]
    assert {"ai_processing", "transfer_abroad", "recording", "ai_analytics"} <= set(texts)
    assert texts["ai_processing"]["required"] is True and texts["recording"]["required_for_voice"] is True
    assert "Test Hotel Dubai" in texts["transfer_abroad"]["text"] and texts["transfer_abroad"]["language"] == "ar"
    assert body["hotel"]["tz"] == "Asia/Dubai" and body["hotel"]["currency"] == "AED"
    assert body["chips"] and all(c["key"] != "room_service" for c in body["chips"]), "no menu, no food chip"
    assert "\u2014" not in json.dumps(body, ensure_ascii=False)


def test_default_chips_are_the_six_of_the_guest_page(client, cur, hotel_uae, monkeypatch):
    """Towels, water, cleaning, Wi-Fi, check-out and the front desk, in that
    order (05 spec); "Order food" only when the hotel switches it on."""
    from clefs.core.hotels import set_module_settings
    from clefs.modules.upsell import api as upsell_api

    monkeypatch.setattr(upsell_api, "get_menu", lambda *a, **kw: [{"item_id": 1, "name": "Club sandwich"}])
    body = _ctx(client, hotel_uae.slug, "101").json()
    assert body["features"]["menu"] is True
    assert [c["label"] for c in body["chips"]] == [
        "Extra towels", "Bottled water", "Clean my room", "Wi-Fi details", "Check-out time", "Talk to the front desk",
    ]
    assert [c["key"] for c in _ctx(client, hotel_uae.slug, "101", lang="ar").json()["chips"]] == [
        "towels", "water", "housekeeping", "wifi", "checkout", "front_desk",
    ]
    set_module_settings(cur, hotel_uae.slug, "concierge", {"menu_chip": True}, actor=None)
    assert "room_service" in [c["key"] for c in _ctx(client, hotel_uae.slug, "101").json()["chips"]]


def test_context_rate_limited(client, hotel_uae, monkeypatch):
    from clefs.modules.concierge import constants

    monkeypatch.setattr(constants, "CONTEXT_READS_PER_IP_TARGET", 2)
    assert _ctx(client, hotel_uae.slug, "101").status_code == 200
    assert _ctx(client, hotel_uae.slug, "101").status_code == 200
    assert _ctx(client, hotel_uae.slug, "101").status_code == 429


def test_context_walk_capped_per_address(client, hotel_uae, monkeypatch):
    from clefs.modules.concierge import constants

    monkeypatch.setattr(constants, "CONTEXT_READS_PER_IP", 3)
    codes = [_ctx(client, hotel_uae.slug, room).status_code for room in ("101", "102", "103", "104")]
    assert codes == [200, 200, 200, 429]


# ─── Enumeration: 404s are counted (security-privacy-6) ───


def test_context_404_still_counts_against_the_address(client, hotel_uae, monkeypatch):
    """A miss used to roll its own rate-limit rows back with the 404, so
    walking unknown room numbers was free."""
    from clefs.modules.concierge import constants

    monkeypatch.setattr(constants, "CONTEXT_READS_PER_IP", 3)
    codes = [_ctx(client, hotel_uae.slug, room).status_code for room in ("901", "902", "903", "101")]
    assert codes == [404, 404, 404, 429]


def test_context_misses_capped_per_address(client, hotel_uae, monkeypatch):
    from clefs.modules.concierge import constants

    monkeypatch.setattr(constants, "TARGET_MISSES_PER_IP", 2)
    codes = [_ctx(client, hotel_uae.slug, room).status_code for room in ("901", "902", "101", "102")]
    assert codes == [404, 404, 429, 429], "after the miss cap even a real room answers 429"


def test_mint_misses_capped_per_address(client, hotel_uae, monkeypatch):
    from clefs.modules.concierge import constants

    monkeypatch.setattr(constants, "TARGET_MISSES_PER_IP", 2)
    body = {"hotel": hotel_uae.slug, "lang": "en", "mode": "voice", "consents": {"ai_processing": True}}
    codes = [client.post("/public/concierge/sessions", json={**body, "target": {"room": room}}).status_code
             for room in ("901", "902", "101")]
    assert codes == [404, 404, 429]
