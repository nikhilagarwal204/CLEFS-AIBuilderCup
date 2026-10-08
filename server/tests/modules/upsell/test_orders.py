"""Quote then confirm: the 10-minute session gate, the linked F&B request,
snapshots, idempotency, lifecycle and the upsell funnel."""

from __future__ import annotations

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.upsell import api
from clefs.modules.upsell import orders as orders_mod
from clefs.modules.upsell.settings import load


def _club(menu_setup, qty=1):
    return [{"item_id": menu_setup["club"]["id"], "qty": qty, "notes": "no onions"}]


def test_create_order_requires_a_matching_quote(cur, menu_setup):
    slug = menu_setup["slug"]
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    assert e.value.code == "quote_required"
    api.quote(cur, slug, room="101", lines=_club(menu_setup, 2))
    # a different basket (qty 1) is not the quoted one
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup, 1), source="web")
    assert e.value.code == "quote_required"
    # another room cannot ride on this room's quote
    with pytest.raises(ClefsError):
        api.create_order(cur, slug, room="102", lines=_club(menu_setup, 2), source="web")


def test_quote_then_order_files_one_fnb_request(cur, menu_setup, service_stub):
    slug = menu_setup["slug"]
    q = api.quote(cur, slug, room="101", lines=_club(menu_setup, 2))
    o = api.create_order(cur, slug, room="101", lines=_club(menu_setup, 2), source="web")
    assert o["order_id"] and o["status"] == "placed" and o["total"] == q["total"] == "100.00"
    assert o["currency"] == "AED" and o["quote_id"] == q["quote_id"] and o["deduped"] is False
    assert o["lines"][0]["name"] == "Club Sandwich" and o["lines"][0]["notes"] == "no onions"
    assert len(service_stub.calls) == 1
    req = service_stub.calls[0]
    assert o["request_id"] == 9001 and o["tracking_token"] == "tok-1"
    assert req["hotel_slug"] == slug and req["room"] == "101" and req["type"] == "Food" and req["source"] == "web"
    assert "2x Club Sandwich" in req["description"] and "AED 100.00" in req["description"]
    assert "no onions" in req["description"] and req["subtype"] == "In-Room Dining"
    # a repeat of the same call returns the same order, no second request
    again = api.create_order(cur, slug, room="101", lines=_club(menu_setup, 2), source="web")
    assert again["order_id"] == o["order_id"] and again["deduped"] is True
    assert len(service_stub.calls) == 1


def test_service_failure_rolls_the_order_back(cur, menu_setup, service_stub):
    slug = menu_setup["slug"]
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    service_stub.fail = ClefsError(503, "down", "service_down")
    cur.execute("SAVEPOINT t")
    with pytest.raises(ClefsError):
        api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    cur.execute("ROLLBACK TO SAVEPOINT t")
    cur.execute("SELECT COUNT(*) AS n FROM ups_orders WHERE hotel_slug = %s", (slug,))
    assert cur.fetchone()["n"] == 0


def test_quote_expires_after_ttl(cur, menu_setup, lunch):
    slug = menu_setup["slug"]
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    lunch.advance(minutes=11)
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    assert e.value.code == "quote_required"


def test_session_quote_is_bound_to_its_session(cur, menu_setup, lunch):
    slug = menu_setup["slug"]
    q = api.quote_for_session(cur, slug, room="101", lines=_club(menu_setup), session_ref="sess-a")
    with pytest.raises(ClefsError) as e:
        api.confirm_order(cur, slug, quote_id=q["quote_id"], room="101", source="web", session_ref="sess-b")
    assert e.value.code == "quote_session_mismatch"
    with pytest.raises(ClefsError) as e:
        api.confirm_order(cur, slug, quote_id=q["quote_id"], room="102", source="web", session_ref="sess-a")
    assert e.value.code == "quote_room_mismatch"
    lunch.advance(minutes=9)
    o = api.confirm_order(cur, slug, quote_id=q["quote_id"], room="101", source="web", session_ref="sess-a")
    assert o["status"] == "placed"
    with pytest.raises(ClefsError) as e:
        api.confirm_order(cur, slug, quote_id="not-a-uuid", room="101", source="web", session_ref="sess-a")
    assert e.value.code == "quote_not_found"


def test_expired_session_quote(cur, menu_setup, lunch):
    q = api.quote_for_session(cur, menu_setup["slug"], room="101", lines=_club(menu_setup), session_ref="s")
    lunch.advance(minutes=10, seconds=1)
    with pytest.raises(ClefsError) as e:
        api.confirm_order(cur, menu_setup["slug"], quote_id=q["quote_id"], room="101", source="web", session_ref="s")
    assert e.value.code == "quote_expired"


def test_price_snapshot_survives_a_menu_change(cur, menu_setup):
    from clefs.modules.upsell import menu

    slug = menu_setup["slug"]
    o_q = api.quote(cur, slug, room="101", lines=_club(menu_setup))
    menu.update_item(cur, slug, "AED", menu_setup["club"]["id"], menu.ItemPatch(price="99", name_en="Club Deluxe"))
    o = api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    assert o["total"] == o_q["total"] == "50.00" and o["lines"][0]["name"] == "Club Sandwich"


def test_item_deactivated_between_quote_and_confirm(cur, menu_setup):
    from clefs.modules.upsell import menu

    slug = menu_setup["slug"]
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    menu.update_item(cur, slug, "AED", menu_setup["club"]["id"], menu.ItemPatch(active=False))
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    assert e.value.code == "item_unavailable"


# ─── Funnel and lifecycle ───


def _funnel(cur, slug):
    cur.execute("SELECT state, COUNT(*) AS n, SUM(value_minor) AS v FROM ups_upsell_events WHERE hotel_slug = %s"
                " GROUP BY state", (slug,))
    return {r["state"]: (r["n"], r["v"]) for r in cur.fetchall()}


def test_suggested_item_ordered_counts_as_accepted_then_fulfilled_and_paid(cur, hotel_uae, menu_setup):
    slug = menu_setup["slug"]
    s = load(cur, slug)
    q1 = api.quote_for_session(cur, slug, room="101", lines=_club(menu_setup), session_ref="sess-1")
    assert q1["suggestions"][0]["item_id"] == menu_setup["fries"]["id"]
    # the same suggestion shown again in the session is one "shown" row
    basket = _club(menu_setup) + [{"item_id": menu_setup["fries"]["id"]}]
    api.quote_for_session(cur, slug, room="101", lines=_club(menu_setup), session_ref="sess-1")
    q2 = api.quote_for_session(cur, slug, room="101", lines=basket, session_ref="sess-1")
    o = api.confirm_order(cur, slug, quote_id=q2["quote_id"], room="101", source="web", session_ref="sess-1")
    assert {line["name"]: line["upsell"] for line in o["lines"]} == {"Club Sandwich": False, "Truffle Fries": True}
    assert _funnel(cur, slug) == {"shown": (1, None), "accepted": (1, 2000)}
    orders_mod.set_status(cur, hotel_uae, o["order_id"], "preparing")
    orders_mod.set_status(cur, hotel_uae, o["order_id"], "delivered")
    orders_mod.mark_paid(cur, hotel_uae, o["order_id"], payment="card")
    f = _funnel(cur, slug)
    assert f["fulfilled"] == (1, 2000) and f["paid"] == (1, 2000)
    orders_mod.set_status(cur, hotel_uae, o["order_id"], "refunded", reason="cold")
    assert _funnel(cur, slug)["refunded"] == (1, 2000)
    assert s.max_suggestions == 2


def test_illegal_transitions(cur, hotel_uae, menu_setup):
    slug = menu_setup["slug"]
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    o = api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    with pytest.raises(ClefsError) as e:
        orders_mod.set_status(cur, hotel_uae, o["order_id"], "refunded")
    assert e.value.code == "illegal_transition"
    _, after = orders_mod.set_status(cur, hotel_uae, o["order_id"], "cancelled", reason="guest changed mind")
    assert after["status"] == "cancelled" and after["cancel_reason"] == "guest changed mind"
    with pytest.raises(ClefsError) as e:
        orders_mod.mark_paid(cur, hotel_uae, o["order_id"])
    assert e.value.code == "illegal_transition"


def test_ordering_switches(cur, menu_setup, set_settings):
    slug = menu_setup["slug"]
    set_settings(ordering_enabled=False)
    with pytest.raises(ClefsError) as e:
        api.quote(cur, slug, room="101", lines=_club(menu_setup))
    assert e.value.code == "ordering_disabled"
    set_settings(ordering_enabled=True, alcohol_orders=True)
    q = api.quote(cur, slug, room="101", lines=[{"item_id": menu_setup["wine"]["id"]}])
    assert q["total"] == "80.00"
    set_settings(allow_unverified_rooms=False)
    with pytest.raises(ClefsError) as e:
        api.confirm_order(cur, slug, quote_id=q["quote_id"], room="101", source="web", verified_room=False)
    assert e.value.code == "room_not_verified"


def test_order_rate_limit_per_room(cur, menu_setup, set_settings):
    slug = menu_setup["slug"]
    set_settings(orders_per_room_hour=1)
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    api.quote(cur, slug, room="101", lines=_club(menu_setup, 2))
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup, 2), source="web")
    assert e.value.status == 429


def test_invalid_lines(cur, menu_setup):
    slug = menu_setup["slug"]
    for lines, code in (([], "empty_order"), ([{"item_id": "x"}], "invalid_line"),
                        ([{"item_id": menu_setup["club"]["id"], "qty": 0}], "invalid_qty"),
                        ([{"item_id": menu_setup["club"]["id"], "qty": 30}] * 2, "invalid_qty")):
        with pytest.raises(ClefsError) as e:
            api.quote(cur, slug, room="101", lines=lines)
        assert e.value.code == code
    # menu_item_id is accepted (the concierge tool's argument name), duplicates merge
    q = api.quote(cur, slug, room="101", lines=[{"menu_item_id": menu_setup["juice"]["id"]},
                                                {"item_id": menu_setup["juice"]["id"], "qty": 2}])
    assert len(q["lines"]) == 1 and q["lines"][0]["qty"] == 3 and q["total"] == "75.00"


def test_guest_order_status_hides_other_rooms(cur, menu_setup):
    slug = menu_setup["slug"]
    api.quote(cur, slug, room="101", lines=_club(menu_setup))
    o = api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    mine = api.order_status_for_guest(cur, slug, o["order_id"], room="101")
    assert mine["status"] == "placed" and "request_id" not in mine
    assert api.order_status_for_guest(cur, slug, o["order_id"], room="102") is None


def test_frozen_create_order_cannot_confirm_a_session_bound_quote(cur, menu_setup):
    slug = menu_setup["slug"]
    api.quote_for_session(cur, slug, room="101", lines=_club(menu_setup), session_ref="sess-x")
    with pytest.raises(ClefsError) as e:
        api.create_order(cur, slug, room="101", lines=_club(menu_setup), source="web")
    assert e.value.code == "quote_required"
