"""HTTP contract: console routes under /hotels/{slug}/menu and the guest page
routes under /public/menu/{slug} (README.md documents both)."""

from __future__ import annotations

from clefs.core import rooms


def _base(slug):
    return f"/hotels/{slug}/menu"


def test_console_crud_and_permissions(client_as, hotel_uae, lunch):
    slug = hotel_uae.slug
    owner, staff = client_as("owner"), client_as("staff")
    r = owner.post(f"{_base(slug)}/outlets", json={"key": "IRD", "name_en": "In-Room Dining",
                                                   "hours": [{"from": "06:00", "to": "23:30"}]})
    assert r.status_code == 201, r.text
    outlet = r.json()
    assert outlet["key"] == "ird" and outlet["hours"] == [{"from": "06:00", "to": "23:30", "days_mask": 127}]
    assert owner.post(f"{_base(slug)}/outlets", json={"key": "ird", "name_en": "x"}).status_code == 409
    r = owner.post(f"{_base(slug)}/items", json={"outlet_id": outlet["id"], "name_en": "Club", "price": "50.5",
                                                 "dietary": ["Halal"], "available_from": "11:00"})
    assert r.status_code == 400 and r.json()["code"] == "invalid_hours"  # from without to
    r = owner.post(f"{_base(slug)}/items", json={"outlet_id": outlet["id"], "name_en": "Club", "price": "50.5",
                                                 "dietary": ["Halal"]})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["price"] == "50.50" and item["price_minor"] == 5050 and item["currency"] == "AED"
    assert item["dietary"] == ["halal"]
    r = owner.patch(f"{_base(slug)}/items/{item['id']}", json={"price": "55", "name_ar": "كلوب"})
    assert r.json()["price"] == "55.00" and r.json()["name_ar"] == "كلوب"
    assert owner.post(f"{_base(slug)}/items", json={"outlet_id": outlet["id"], "name_en": "X",
                                                    "price": "-1"}).status_code == 400
    # staff can read orders but not edit the menu
    assert staff.get(f"{_base(slug)}/items").status_code == 200
    assert staff.post(f"{_base(slug)}/outlets", json={"key": "spa", "name_en": "Spa"}).status_code == 403
    assert staff.get(f"{_base(slug)}/funnel").status_code == 403
    assert owner.get(f"{_base(slug)}/funnel").json()["accept_rate"] is None
    assert owner.delete(f"{_base(slug)}/items/{item['id']}").status_code == 204
    assert owner.get(f"{_base(slug)}/items").json() == []


def test_console_import_preview_and_staff_order(client_as, hotel_uae, lunch, service_stub, cur):
    slug = hotel_uae.slug
    owner, staff = client_as("owner"), client_as("front_desk")
    csv = "outlet,name,price,section\nird,Club Sandwich,50,Mains\nird,Juice,25,Drinks\n"
    r = owner.post(f"{_base(slug)}/import", json={"csv": csv, "dry_run": True})
    assert r.json()["created"] == 2 and r.json()["applied"] is False
    r = owner.post(f"{_base(slug)}/import", json={"csv": csv})
    assert r.json()["applied"] is True and r.json()["outlets_created"] == ["ird"]
    prev = owner.get(f"{_base(slug)}/preview?lang=en").json()
    assert {i["name"] for i in prev["items"]} == {"Club Sandwich", "Juice"}
    assert prev["price_display"] == "inclusive" and prev["fees"]["convention"] in ("A", "B", "C")
    club = next(i for i in prev["items"] if i["name"] == "Club Sandwich")
    q = staff.post(f"{_base(slug)}/quote", json={"room": "101", "lines": [{"item_id": club["item_id"], "qty": 2}]})
    assert q.status_code == 200, q.text
    # another staff member cannot confirm this staff member's quote
    other = client_as("front_desk")
    r = other.post(f"{_base(slug)}/orders", json={"quote_id": q.json()["quote_id"], "room": "101"})
    assert r.status_code == 409 and r.json()["code"] == "quote_session_mismatch"
    r = staff.post(f"{_base(slug)}/orders", json={"quote_id": q.json()["quote_id"], "room": "101",
                                                  "payment": "cash"})
    assert r.status_code == 201, r.text
    order = r.json()
    assert order["source"] == "console" and order["payment"] == "cash" and order["total"] == "100.00"
    assert service_stub.calls[0]["source"] == "console"
    lst = staff.get(f"{_base(slug)}/orders?status=placed,accepted").json()
    assert [o["id"] for o in lst] == [order["id"]]
    r = staff.post(f"{_base(slug)}/orders/{order['id']}/status", json={"status": "delivered"})
    assert r.json()["status"] == "delivered"
    r = staff.post(f"{_base(slug)}/orders/{order['id']}/status", json={"status": "placed"})
    assert r.status_code == 409 and r.json()["code"] == "illegal_transition"
    r = staff.post(f"{_base(slug)}/orders/{order['id']}/paid", json={})
    assert r.json()["paid_at"] is not None
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'menu.%%' ORDER BY id", (slug,))
    actions = [a["action"] for a in cur.fetchall()]
    assert "menu.import.apply" in actions and "menu.order.create" in actions and "menu.order.delivered" in actions


def test_public_menu_quote_order_and_track(client, cur, menu_setup, service_stub):
    slug = menu_setup["slug"]
    k = rooms.qr_key(slug, "101", 1)
    r = client.get(f"/public/menu/{slug}?room=101&k={k}&lang=ar")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_status"] == "valid" and body["currency"] == "AED"
    names = {i["name"]: i for i in body["items"]}
    assert "كلوب ساندويتش" in names and names["Eggs Benedict"]["available_now"] is False
    assert "House Red" not in names  # alcohol hidden until the hotel opts in
    assert str(menu_setup["club"]["id"]) in {str(x) for x in body["pairs"]}
    lines = [{"item_id": menu_setup["club"]["id"], "qty": 1}]
    q = client.post(f"/public/menu/{slug}/quote", json={"room": "101", "k": k, "lines": lines})
    assert q.status_code == 200 and q.json()["total"] == "50.00"
    assert q.json()["suggestions"][0]["item_id"] == menu_setup["fries"]["id"]
    r = client.post(f"/public/menu/{slug}/orders", json={"room": "102", "k": k, "quote_id": q.json()["quote_id"]})
    assert r.status_code == 409 and r.json()["code"] == "quote_room_mismatch"
    r = client.post(f"/public/menu/{slug}/orders", json={"room": "101", "k": k, "quote_id": q.json()["quote_id"]})
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["verified_room"] is True and o["status"] == "placed" and "request_id" not in o
    t = client.get(f"/public/menu/{slug}/orders/{o['order_token']}")
    assert t.status_code == 200 and t.json()["status"] == "placed"
    assert client.get(f"/public/menu/{slug}/orders/ups:{slug}:{o['order_id']}.forged").status_code == 404


def test_public_unverified_room_and_beacon(client, cur, menu_setup, service_stub):
    slug = menu_setup["slug"]
    lines = [{"item_id": menu_setup["juice"]["id"]}]
    q = client.post(f"/public/menu/{slug}/quote", json={"room": "101", "k": "wrong", "lines": lines}).json()
    assert q["key_status"] == "invalid"
    o = client.post(f"/public/menu/{slug}/orders", json={"room": "101", "quote_id": q["quote_id"]}).json()
    assert o["verified_room"] is False
    assert service_stub.calls[0]["description"].startswith("[Room not verified")
    r = client.post(f"/public/menu/{slug}/events", json={"room": "101", "item_id": menu_setup["fries"]["id"],
                                                          "source_item_id": menu_setup["club"]["id"]})
    assert r.status_code == 204
    client.post(f"/public/menu/{slug}/events", json={"room": "101", "item_id": menu_setup["fries"]["id"]})
    cur.execute("SELECT COUNT(*) AS n FROM ups_upsell_events WHERE hotel_slug = %s AND state = 'shown'", (slug,))
    assert cur.fetchone()["n"] == 1
    assert client.post(f"/public/menu/{slug}/events", json={"room": "101", "item_id": 999999}).status_code == 404
    assert client.get("/public/menu/no-such-hotel").status_code == 404
    assert client.get(f"/public/menu/{slug}?room=999").status_code == 404
