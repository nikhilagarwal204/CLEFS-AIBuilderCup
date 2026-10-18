"""HTTP routes under /hotels/{slug}/amenities: permissions, shapes, audit."""

from __future__ import annotations

from datetime import timedelta

from .conftest import END_DEFAULT, at


def _base(slug: str) -> str:
    return f"/hotels/{slug}/amenities"


def test_items_seed_on_first_visit_and_crud(client_as, hotel_uae, frozen, cur):
    owner, staff, fin = client_as("owner"), client_as("staff"), client_as("finance")
    b = _base(hotel_uae.slug)
    r = owner.get(f"{b}/items")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 15 and all(i["needs_review"] for i in items)
    assert staff.get(f"{b}/items").status_code == 200                     # issue grid needs the catalog
    assert fin.post(f"{b}/items", json={"name_en": "X"}).status_code == 403
    r = owner.post(f"{b}/items", json={"name_en": "Bamboo comb", "category": "OTHER", "unit_cost": "0.90",
                                       "aliases": ["bamboo comb"]})
    assert r.status_code == 201, r.text
    item = r.json()
    assert item["key"] == "BAMBOO_COMB" and item["unit_cost"] == "0.90"
    r = owner.patch(f"{b}/items/{item['id']}", json={"std_qty": "0.5", "basis": "PER_OCCUPIED_ROOM_NIGHT"})
    assert r.json()["std_qty"] == "0.5"
    r = owner.post(f"{b}/items/{item['id']}/costs", json={"effective_from": "2026-09-01", "unit_cost": "1.10"})
    assert r.status_code == 201 and r.json()["unit_cost_minor"] == 110
    assert owner.get(f"{b}/items/{item['id']}/costs").json()[0]["unit_cost"] == "1.10"
    r = owner.delete(f"{b}/items/{item['id']}")
    assert r.json()["deleted"] is True
    cur.execute("SELECT action FROM audit_log WHERE hotel_slug = %s AND action LIKE 'amenities.%%'", (hotel_uae.slug,))
    actions = {x["action"] for x in cur.fetchall()}
    assert {"amenities.catalog.seed", "amenities.item.create", "amenities.item.delete"} <= actions


def test_locations_par_counts_and_issuance(client_as, hotel_uae, frozen):
    owner, staff = client_as("owner"), client_as("staff")
    b = _base(hotel_uae.slug)
    items = {i["key"]: i for i in owner.get(f"{b}/items").json()}
    loc = owner.post(f"{b}/locations", json={"kind": "FLOOR_CLOSET", "name": "Floor 1 closet", "floor": "1"}).json()
    assert staff.post(f"{b}/locations", json={"kind": "STORE", "name": "Main"}).status_code == 403
    r = owner.put(f"{b}/par", json={"location_id": loc["id"], "item_id": items["SHAMPOO"]["id"], "par_qty": 40})
    assert r.status_code == 200 and r.json()["pinned"] is True and r.json()["days_of_supply"] == "2"
    r = staff.post(f"{b}/stock-counts", json={"lines": [{"location_id": loc["id"], "item_id": items["SHAMPOO"]["id"],
                                                         "qty": 30}]})
    assert r.status_code == 201, r.text
    frozen.advance(minutes=10)
    # Staff log issuance from the closet (one line) and in bulk.
    r = staff.post(f"{b}/issuance", json={"item_id": items["SHAMPOO"]["id"], "qty": 5,
                                          "from_location_id": loc["id"], "staff_name": "Ana"})
    assert r.status_code == 201, r.text
    line = r.json()["lines"][0]
    assert line["business_date"] == "2026-09-29" and line["shift"] == "MORNING" and line["unit_cost"] == "1.20"
    r = staff.post(f"{b}/issuance", json={"lines": [{"item_id": items["DENTAL_KIT"]["id"], "qty": 2,
                                                     "reason": "GUEST_REQUEST", "to_floor": "2"},
                                                    {"item_id": items["DENTAL_KIT"]["id"], "qty": 1,
                                                     "reason": "RETURN"}]})
    assert [ln["qty"] for ln in r.json()["lines"]] == ["2", "-1"]
    par = owner.get(f"{b}/par").json()
    assert par[0]["on_hand"] == "25"                                       # 30 counted - 5 sent out
    assert staff.post(f"{b}/issuance/{line['id']}/void").status_code == 403
    assert owner.post(f"{b}/issuance/{line['id']}/void").json()["voided_at"]
    assert len(owner.get(f"{b}/issuance").json()) == 2


def test_request_items_reports_and_rollup(client_as, hotel_uae, frozen, occ_range, make_request):
    owner = client_as("owner")
    b = _base(hotel_uae.slug)
    items = {i["key"]: i for i in owner.get(f"{b}/items").json()}
    occ_range(END_DEFAULT - timedelta(days=13), 14, 10, floors={"1": 6, "2": 4})
    rid = make_request("101", subtype="Toothbrush", at=at(END_DEFAULT, 20))
    r = owner.post(f"{b}/rollup", json={})
    assert r.status_code == 200, r.text
    assert r.json()["rollup"]["days"] == 14
    assert owner.get(f"{b}/requests/{rid}/items").json()[0]["match_method"] == "ALIAS"
    r = owner.post(f"{b}/requests/{rid}/items", json={"lines": [{"item_id": items["SHAVING_KIT"]["id"], "qty": 1}]})
    assert [x["match_method"] for x in r.json()] == ["MANUAL"]
    rep = owner.get(f"{b}/report", params={"group": "floor"}).json()
    assert rep["days_covered"] == 14 and rep["days_missing_occupancy"] == 16
    assert owner.get(f"{b}/report", params={"group": "bogus"}).status_code == 400
    staff_view = owner.get(f"{b}/report", params={"group": "staff"}).json()
    assert staff_view["available"] is False and "blame" in staff_view["note"]
    cp = owner.get(f"{b}/cpor").json()
    assert cp["orn"] == 140 and cp["expected_cpor"] is not None
    ov = owner.get(f"{b}/overview").json()
    assert ov["catalog"]["needs_review"] >= 15 and ov["currency"] == "AED"
    assert owner.get(f"{b}/benchmarks").json()["available"] is False
    assert owner.get(f"{b}/minibar").json()["has_folio"] is False
    assert client_as("staff").get(f"{b}/report").status_code == 403


def test_floor_device_logs_issuance_and_counts_give_exact_consumption(device_client, client_as, hotel_uae, frozen):
    owner = client_as("owner")
    b = _base(hotel_uae.slug)
    items = {i["key"]: i for i in owner.get(f"{b}/items").json()}
    loc = owner.post(f"{b}/locations", json={"kind": "FLOOR_CLOSET", "name": "F5", "floor": "5"}).json()
    dev = device_client(floors=["5"], departments=["housekeeping"])
    water = items["WATER_500"]["id"]

    def count(qty):
        r = dev.post(f"{b}/stock-counts", json={"lines": [{"location_id": loc["id"], "item_id": water, "qty": qty}]})
        assert r.status_code == 201, r.text

    count(48)
    frozen.advance(minutes=30)
    r = dev.post(f"{b}/issuance", json={"item_id": water, "qty": 24, "to_location_id": loc["id"]})
    assert r.status_code == 201, r.text
    assert r.json()["lines"][0]["to_floor"] == "5"                          # from the closet
    frozen.advance(hours=6)
    count(30)
    assert dev.get(f"{b}/report").status_code == 403                       # devices log, they do not report
    r = owner.get(f"{b}/stock-counts/consumption", params={"location_id": loc["id"], "item_id": water,
                                                            "from": "2026-09-29", "to": "2026-09-29"})
    assert r.json()["periods"] == [{**r.json()["periods"][0], "opening": "48", "received": "24", "closing": "30",
                                    "consumed": "42"}]
