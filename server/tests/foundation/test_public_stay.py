"""
GET /public/stay/{slug}/{room}: one room's minimal context for the guest QR
page, replacing the download of the whole guest roster.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from clefs.core import rooms
from clefs.db import Json
from clefs.settings import get_settings

GUEST = {
    "roomNumber": "101",
    "guests": [{"name": "Layla Haddad", "type": "primary", "age": 41, "passport": "X123"}],
    "stayType": "business",
    "checkIn": "2026-09-27",
    "checkOut": "2026-09-30",
    "preferences": {"tags": ["late checkout"], "language": "Arabic", "internal_note": "VIP complaint last stay"},
    "loyaltyTier": "member",
    "occasions": [],
    "folio_balance": 1234,
}


def _put_guest(cur, slug: str, room: str, data: dict) -> None:
    cur.execute(
        "INSERT INTO guest_rooms (hotel_slug, room_number, data) VALUES (%s, %s, %s)",
        (slug, room, Json({**data, "roomNumber": room})),
    )


def test_qr_key_formula_matches_spec(hotel_uae):
    secret = get_settings().SECRET_KEY.encode()
    digest = hmac.new(secret, b"test-dubai:101:1", hashlib.sha256).digest()
    assert rooms.qr_key("test-dubai", "101", 1) == base64.b32encode(digest).decode()[:10]
    assert rooms.verify_qr_key("test-dubai", "101", 1, rooms.qr_key("test-dubai", "101", 1).lower())
    assert rooms.stay_path("test-dubai", "101", 2).startswith("/stay/test-dubai/101?k=")


def test_without_key_a_locked_hotel_returns_no_pii(client, cur, hotel_uae):
    _put_guest(cur, hotel_uae.slug, "101", GUEST)
    body = client.get(f"/public/stay/{hotel_uae.slug}/101").json()
    assert body["personalized"] is False and body["room"] is None and body["key_status"] == "absent"
    assert (body["room_number"], body["floor"]) == ("101", "1")
    assert set(body["hotel"]) == {"slug", "name", "city", "star_rating", "category", "assistant_name", "locale", "guest_languages"}


def test_valid_key_returns_only_allowlisted_fields_for_that_room(client, cur, hotel_uae):
    _put_guest(cur, hotel_uae.slug, "101", GUEST)
    _put_guest(cur, hotel_uae.slug, "102", {**GUEST, "guests": [{"name": "Someone Else", "type": "primary"}]})
    k = rooms.qr_key(hotel_uae.slug, "101", 1)
    body = client.get(f"/public/stay/{hotel_uae.slug}/101", params={"k": k}).json()
    assert body["personalized"] is True and body["key_status"] == "valid"
    g = body["room"]  # GuestRoom-shaped, as client/app/lib/guestData.ts getRoomData expects
    assert g["guests"] == [{"name": "Layla Haddad", "type": "primary"}]  # no age, no passport
    assert g["preferences"] == {"tags": ["late checkout"], "language": "Arabic"}
    assert "folio_balance" not in g and "Someone Else" not in str(body)


def test_wrong_or_rotated_key_returns_no_pii(client, cur, client_as, hotel_uae):
    _put_guest(cur, hotel_uae.slug, "101", GUEST)
    old = rooms.qr_key(hotel_uae.slug, "101", 1)
    bad = client.get(f"/public/stay/{hotel_uae.slug}/101", params={"k": "AAAAAAAAAA"}).json()
    assert bad["key_status"] == "invalid" and bad["room"] is None
    owner = client_as("owner")
    rotated = owner.post(f"/hotels/{hotel_uae.slug}/rooms/101/rotate-qr").json()
    assert rotated["qr_key_version"] == 2
    assert client.get(f"/public/stay/{hotel_uae.slug}/101", params={"k": old}).json()["room"] is None
    new_k = rotated["stay_path"].split("k=", 1)[1]
    assert client.get(f"/public/stay/{hotel_uae.slug}/101", params={"k": new_k}).json()["personalized"] is True


def test_unknown_or_inactive_room_in_a_registered_hotel_is_404(client, cur, hotel_uae):
    assert client.get(f"/public/stay/{hotel_uae.slug}/999").status_code == 404
    cur.execute("UPDATE hotel_rooms SET is_active = FALSE WHERE hotel_slug = %s AND room_number = '101'", (hotel_uae.slug,))
    assert client.get(f"/public/stay/{hotel_uae.slug}/101").status_code == 404
    assert client.get("/public/stay/no-such-hotel/101").status_code == 404
    assert client.get(f"/public/stay/{hotel_uae.slug}/..%2F..").status_code == 404


def test_legacy_hotel_keeps_personalization_for_old_printed_qr(client, cur, legacy_slug):
    client.post(f"/hotels/{legacy_slug}/guests/seed")
    body = client.get(f"/public/stay/{legacy_slug}/502").json()
    assert body["personalized"] is True and body["key_status"] == "absent"
    assert body["room"]["guests"][0] == {"name": "Aarav Sharma", "type": "primary"}
    assert body["room"]["roomNumber"] == "502" and body["room"]["loyaltyTier"] == "globalist"
    assert body["floor"] == "5"  # unregistered hotel: inferred from the number
    cur.execute(
        "UPDATE hotel_module_settings SET settings = '{\"legacy_qr_personalization\": false}' WHERE hotel_slug = %s AND module = 'concierge'",
        (legacy_slug,),
    )
    assert client.get(f"/public/stay/{legacy_slug}/502").json()["room"] is None


def test_public_stay_is_rate_limited_per_ip_and_room(client, hotel_uae):
    codes = [client.get(f"/public/stay/{hotel_uae.slug}/102").status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429
    assert client.get(f"/public/stay/{hotel_uae.slug}/103").status_code == 200


def test_room_register_routes(client_as, hotel_uae):
    owner = client_as("owner")
    r = owner.put(f"/hotels/{hotel_uae.slug}/rooms", json={"rooms": [{"room_number": "1201"}, {"room_number": "PH-1", "floor": "PH"}]})
    assert r.json() == {"ok": True, "count": 2}
    listed = {x["room_number"]: x for x in owner.get(f"/hotels/{hotel_uae.slug}/rooms").json()}
    assert listed["1201"]["floor"] == "12" and listed["1201"]["floor_inferred"] is True
    assert listed["PH-1"]["floor"] == "PH" and listed["PH-1"]["floor_inferred"] is False
    qr = owner.get(f"/hotels/{hotel_uae.slug}/rooms/qr").json()
    assert all(x["stay_path"].startswith(f"/stay/{hotel_uae.slug}/") for x in qr)
    assert owner.put(f"/hotels/{hotel_uae.slug}/rooms", json={"rooms": [{"room_number": "1", "service_state": "broken"}]}).status_code == 400
    assert client_as("staff").put(f"/hotels/{hotel_uae.slug}/rooms", json={"rooms": []}).status_code == 403


def test_a_non_ascii_key_is_invalid_not_a_crash(client, cur, hotel_uae):
    # hmac.compare_digest raises TypeError on non-ASCII str; ?k= is unauthenticated input.
    r = client.get(f"/public/stay/{hotel_uae.slug}/101", params={"k": "é"})
    assert r.status_code == 200 and r.json()["key_status"] == "invalid"
    assert rooms.verify_qr_key(hotel_uae.slug, "101", 1, "\ud800") is False
    from clefs.auth import tokens

    assert tokens.verify_ref("brief:approve:1.éé") is None
    assert tokens.verify_ref(tokens.sign_ref("brief:approve:1")) == "brief:approve:1"


def test_a_partial_room_update_keeps_an_explicit_floor(cur, client_as, hotel_uae):
    slug = hotel_uae.slug
    rooms.upsert_rooms(cur, slug, [{"room_number": "PH-1", "floor": "Penthouse"}, {"room_number": "1204", "floor": "12A"}])
    rooms.upsert_rooms(cur, slug, [{"room_number": "PH-1", "is_active": False}, {"room_number": "1204", "service_state": "ooo"}])
    ph, r1204 = rooms.get_room(cur, slug, "PH-1"), rooms.get_room(cur, slug, "1204")
    assert (ph["floor"], ph["floor_inferred"], ph["is_active"]) == ("Penthouse", False, False)
    assert (r1204["floor"], r1204["service_state"]) == ("12A", "ooo")
    owner = client_as("owner")
    assert owner.put(f"/hotels/{slug}/rooms", json={"rooms": [{"room_number": "PH-1", "is_active": True}]}).status_code == 200
    assert rooms.get_room(cur, slug, "PH-1")["floor"] == "Penthouse"
    rooms.upsert_rooms(cur, slug, [{"room_number": "1204", "floor": ""}])  # asking for the inferred floor again
    assert (rooms.get_room(cur, slug, "1204")["floor"], rooms.get_room(cur, slug, "1204")["floor_inferred"]) == ("12", True)
    rooms.upsert_rooms(cur, slug, [{"room_number": "1305"}])  # a new room without a floor: inferred
    assert rooms.get_room(cur, slug, "1305")["floor"] == "13"


def test_long_numeric_room_numbers_do_not_break_the_register(cur, client_as, hotel_uae):
    slug = hotel_uae.slug
    long = "9" * 24  # the longest ROOM_RE allows; a BIGINT sort key overflows
    rooms.upsert_rooms(cur, slug, [{"room_number": long}])
    assert rooms.get_room(cur, slug, long)["floor"] == "9" * 22
    listed = client_as("owner").get(f"/hotels/{slug}/rooms")
    assert listed.status_code == 200 and listed.json()[-1]["room_number"] == long
