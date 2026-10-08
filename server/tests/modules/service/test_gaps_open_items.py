"""open_items_for_stay by reservation (the guests J6 checkout gate): the
stay's room, the journey-filed guest_ref across a room move, and the
checked-out bound."""

from __future__ import annotations

from datetime import date, timedelta

from clefs.core import clock as clock_mod
from clefs.modules.service import api


def _reservation(cur, slug, room, *, checked_out_at=None):
    today = date.today()
    cur.execute(
        "INSERT INTO reservations (hotel_slug, pms_res_id, guest_name, check_in, check_out, nights, room_number, currency, "
        "checked_out_at) VALUES (%s, %s, 'Guest', %s, %s, 2, %s, 'AED', %s) RETURNING id",
        (slug, f"R-{room}-{clock_mod.now_ms()}", today - timedelta(days=1), today + timedelta(days=1), room, checked_out_at),
    )
    return cur.fetchone()["id"]


def _file(cur, slug, room, type_="towels", **kw):
    return api.create_request(cur, slug, room=room, type=type_, description=f"{type_} please", source="phone_desk", **kw)


def test_reservation_reads_its_room_when_the_caller_has_none(cur, hotel_uae):
    slug = hotel_uae.slug
    rid = _reservation(cur, slug, "401")
    req = _file(cur, slug, "401")
    since = clock_mod.now() - timedelta(days=2)
    assert api.open_items_for_stay(cur, slug, room="", since=since) == []
    items = api.open_items_for_stay(cur, slug, room="", since=since, reservation_id=rid)
    assert [i["id"] for i in items] == [req["id"]]


def test_journey_guest_ref_follows_a_room_move(cur, hotel_uae):
    slug = hotel_uae.slug
    rid = _reservation(cur, slug, "402")
    old_room = _file(cur, slug, "101", guest_ref=str(rid))  # filed before the move
    _file(cur, slug, "101", "pillows")  # someone else's request in the old room
    now_room = _file(cur, slug, "402", "iron")
    since = clock_mod.now() - timedelta(days=2)
    ids = [i["id"] for i in api.open_items_for_stay(cur, slug, room="402", since=since, reservation_id=rid)]
    assert sorted(ids) == sorted([old_room["id"], now_room["id"]])


def test_checked_out_bounds_the_window_and_unknown_reservation_adds_nothing(cur, hotel_uae, clock):
    slug = hotel_uae.slug
    since = clock_mod.now() - timedelta(days=2)
    left = clock_mod.now() - timedelta(minutes=1)
    rid = _reservation(cur, slug, "403", checked_out_at=left)
    _file(cur, slug, "403")  # filed after check-out: the next guest's
    assert api.open_items_for_stay(cur, slug, room="403", since=since, reservation_id=rid) == []
    assert len(api.open_items_for_stay(cur, slug, room="403", since=since)) == 1  # no reservation, no bound
    assert len(api.open_items_for_stay(cur, slug, room="403", since=since, reservation_id=999_999_999)) == 1
    assert api.open_items_for_stay(cur, slug, room="", since=since, reservation_id=999_999_999) == []
