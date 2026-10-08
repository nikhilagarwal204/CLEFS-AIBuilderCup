"""guests.api.room_for_sender: the room a WhatsApp sender is staying in now
(concierge WhatsApp chat). Only in-house stays count, overtaken stays and
ambiguous numbers return None."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from clefs.modules.guests import api, identity, linking, stays_port

from .test_identity import PHONE, add_contact, capture, make_res

TODAY = date(2026, 10, 5)
NOON = datetime(2026, 10, 5, 8, 0, tzinfo=timezone.utc)  # 12:00 Dubai
OTHER = "+971509999999"


def _in_house(cur, slug, room="101", pms="R1", check_in=TODAY - timedelta(days=1), phone=PHONE, **kw):
    rid = make_res(cur, slug, check_in=check_in, check_out=TODAY + timedelta(days=2), status="IN_HOUSE",
                   room=room, pms=pms, **kw)
    return rid, add_contact(cur, slug, rid, phone)


def test_in_house_reservation_gives_its_room(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    _rid, h = _in_house(cur, s)
    assert api.room_for_sender(cur, s, sender_hmac=h) == "101"
    # the concierge calls it with is_known_guest_sender's keyword
    assert api.room_for_sender(cur, s, phone_hmac=h) == "101"


def test_unknown_or_missing_sender_is_none(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    _in_house(cur, s)
    assert api.room_for_sender(cur, s, sender_hmac=b"x" * 32) is None
    assert api.room_for_sender(cur, s) is None
    assert api.room_for_sender(cur, s, sender_hmac=b"") is None


def test_arrivals_and_departed_guests_get_no_room(cur, hotel_uae, clock):
    """A CONFIRMED arrival may not be in the room yet (the last guest may
    still be); a checked-out guest has left it."""
    clock.freeze(NOON)
    s = hotel_uae.slug
    arr = make_res(cur, s, check_in=TODAY, check_out=TODAY + timedelta(days=2), status="CONFIRMED", room="102", pms="A1")
    h = add_contact(cur, s, arr, PHONE)
    assert api.is_known_guest_sender(cur, s, phone_hmac=h) is True  # known, but no verified room
    assert api.room_for_sender(cur, s, sender_hmac=h) is None
    gone = make_res(cur, s, check_in=TODAY - timedelta(days=3), check_out=TODAY, status="CHECKED_OUT", room="103",
                    pms="D1")
    h2 = add_contact(cur, s, gone, OTHER)
    assert api.room_for_sender(cur, s, sender_hmac=h2) is None


def test_a_stay_overtaken_in_its_room_does_not_count(cur, hotel_uae, clock):
    """A check-out the PMS never sent leaves two IN_HOUSE stays in 101: only
    the later one is the guest in the room now."""
    clock.freeze(NOON)
    s = hotel_uae.slug
    _old, h_old = _in_house(cur, s, pms="OLD", check_in=TODAY - timedelta(days=4), phone=OTHER)
    _new, h_new = _in_house(cur, s, pms="NEW", check_in=TODAY - timedelta(days=1))
    assert api.room_for_sender(cur, s, sender_hmac=h_old) is None
    assert api.room_for_sender(cur, s, sender_hmac=h_new) == "101"


def test_one_number_on_two_rooms_is_ambiguous(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    _in_house(cur, s, room="201", pms="F1")
    _rid, h = _in_house(cur, s, room="202", pms="F2")
    assert api.room_for_sender(cur, s, sender_hmac=h) is None


def test_purged_contacts_are_ignored(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    rid, h = _in_house(cur, s)
    cur.execute("UPDATE reservation_contacts SET purged_at = now() WHERE reservation_id = %s", (rid,))
    assert api.room_for_sender(cur, s, sender_hmac=h) is None


def test_roster_capture_at_the_desk_gives_the_room(cur, hotel_uae, clock):
    clock.freeze(NOON)
    s = hotel_uae.slug
    linking.capture_room_identity(cur, s, "105", phone="+971503333333", consents={"transfer_abroad": True})
    h = identity.hmac_for(cur, s, "phone", "+971503333333")
    assert api.room_for_sender(cur, s, sender_hmac=h) == "105"


def test_own_captured_point_on_an_in_house_stay(cur, hotel_uae, clock):
    """A number the guest gave the concierge for this stay (not on the PMS
    reservation) is found through the guests snapshot."""
    clock.freeze(NOON)
    s = hotel_uae.slug
    rid = make_res(cur, s, check_in=TODAY - timedelta(days=1), check_out=TODAY + timedelta(days=2),
                   status="IN_HOUSE", room="301", pms="C1")
    linking.link_reservation(cur, s, rid)
    capture(cur, s, value=OTHER, source="console", reservation_id=rid)
    h = identity.hmac_for(cur, s, "phone", OTHER)
    assert api.room_for_sender(cur, s, sender_hmac=h) == "301"


def test_a_backend_without_core_reads_still_uses_the_snapshot(cur, hotel_uae, clock):
    """stays_port backends need not implement sender_rooms (test doubles,
    a database without core/stays): the snapshot still answers."""
    clock.freeze(NOON)
    s = hotel_uae.slug

    class Bare(stays_port.SqlStaysBackend):
        sender_rooms = None  # type: ignore[assignment]

    stays_port.set_backend(Bare())
    try:
        linking.capture_room_identity(cur, s, "106", phone=OTHER, consents={"transfer_abroad": True})
        h = identity.hmac_for(cur, s, "phone", OTHER)
        assert api.room_for_sender(cur, s, sender_hmac=h) == "106"
    finally:
        stays_port.reset_backend()
