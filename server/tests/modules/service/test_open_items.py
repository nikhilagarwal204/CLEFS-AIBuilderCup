"""service.api.open_items_for_stay: the J6 checkout-recovery gate's view of
the room's unresolved requests (04 spec 3.x J6, frozen shape)."""

from __future__ import annotations

from datetime import timedelta, timezone

from clefs.core import clock as clock_mod
from clefs.modules.service import api


def _file(cur, slug, room, type_, **kw):
    return api.create_request(cur, slug, room=room, type=type_, description=f"{type_} please", source="phone_desk", **kw)


def _set(cur, request_id, **cols):
    sets = ", ".join(f'"{k}" = %s' for k in cols)
    cur.execute(f"UPDATE staff_requests SET {sets} WHERE id = %s", (*cols.values(), request_id))


def test_open_items_shape_and_filters(cur, hotel_uae):
    slug = hotel_uae.slug
    now = clock_mod.now()
    since = now - timedelta(days=2)
    open_l1 = _file(cur, slug, "301", "other", priority_hint="L1")
    open_l3 = _file(cur, slug, "301", "towels")
    done = _file(cur, slug, "301", "pillows")
    _set(cur, done["id"], state="completed", status="completed")
    scheduled = _file(cur, slug, "301", "laundry")
    _set(cur, scheduled["id"], state="scheduled")
    old = _file(cur, slug, "301", "iron")
    _set(cur, old["id"], timestamp=int((since - timedelta(hours=1)).timestamp() * 1000))
    _file(cur, slug, "302", "towels")  # another room

    items = api.open_items_for_stay(cur, slug, room="301", since=since)
    assert [i["id"] for i in items] == [open_l1["id"], open_l3["id"]]
    first = items[0]
    assert set(first) == {"id", "request_type", "severity", "status", "created_at"}
    assert first["severity"] == "L1" and first["status"] in ("new", "assigned")
    assert items[1]["request_type"] == "towels" and items[1]["severity"] in ("L1", "L2", "L3")
    assert first["created_at"].tzinfo is not None
    assert abs(first["created_at"] - now) < timedelta(minutes=5)


def test_open_items_until_bound_and_empty(cur, hotel_uae):
    slug = hotel_uae.slug
    since = clock_mod.now() - timedelta(days=1)
    _file(cur, slug, "303", "towels")
    # until is exclusive and before the request: nothing in the window
    assert api.open_items_for_stay(cur, slug, room="303", since=since, until=since + timedelta(minutes=1)) == []
    # a naive since is read as UTC, not the server's zone
    naive = since.astimezone(timezone.utc).replace(tzinfo=None)
    assert len(api.open_items_for_stay(cur, slug, room="303", since=naive)) == 1
    # an empty hotel or room reads as nothing open
    assert api.open_items_for_stay(cur, slug, room="999", since=since) == []
