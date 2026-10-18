"""upsell.api: get_menu / quote / create_order and the session path."""

from __future__ import annotations

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.upsell import api


def _codes(exc_info) -> str:
    return exc_info.value.code


def test_get_menu_lists_only_orderable_items(cur, menu_setup):
    items = api.get_menu(cur, menu_setup["slug"])
    names = {i["name"] for i in items}
    # breakfast is out of hours, wine is alcohol (not opted in); the bar item
    # is listed (the menu shows it) but cannot be delivered.
    assert "Club Sandwich" in names and "Truffle Fries" in names
    assert "Eggs Benedict" not in names and "House Red" not in names
    club = next(i for i in items if i["name"] == "Club Sandwich")
    assert club["price"] == "50.00" and club["currency"] == "AED"
    assert club["outlet"] == "ird" and club["section"] == "Mains" and "contains_pork" in club["tags"]


def test_get_menu_arabic_and_outlet_filter(cur, menu_setup):
    items = api.get_menu(cur, menu_setup["slug"], outlet="ird", language="ar")
    assert any(i["name"] == "كلوب ساندويتش" for i in items)
    assert all(i["outlet"] == "ird" for i in items)
    assert api.get_menu(cur, "no-such-hotel") == []


def test_quote_inclusive_splits_fees_out(cur, menu_setup):
    q = api.quote(cur, menu_setup["slug"], room="101",
                  lines=[{"item_id": menu_setup["club"]["id"], "qty": 2}, {"item_id": menu_setup["juice"]["id"]}])
    assert q["subtotal"] == "125.00" and q["total"] == "125.00" and q["currency"] == "AED"
    assert q["price_display"] == "inclusive"
    taxes = {t["code"]: t for t in q["taxes"]}
    assert all(t["included"] for t in taxes.values())
    # Convention A on AED 125: R 101.14 -> SC 10.11, MF 7.79, VAT 5.95; the
    # 0.01 residual goes to R (101.15), so the parts add up to the price.
    net = 12500 - sum(t["amount_minor"] for t in taxes.values())
    assert (net, taxes["service_charge"]["amount_minor"], taxes["municipality_fee"]["amount_minor"],
            taxes["vat"]["amount_minor"]) == (10115, 1011, 779, 595)
    # club pairs with fries, which is not in the basket yet: suggested
    assert [s["item_id"] for s in q["suggestions"]] == [menu_setup["fries"]["id"]]
    q2 = api.quote(cur, menu_setup["slug"], room="101",
                   lines=[{"item_id": menu_setup["club"]["id"]}, {"item_id": menu_setup["fries"]["id"]}])
    assert q2["suggestions"] == []


def test_quote_plus_plus_adds_fees(cur, menu_setup, set_settings):
    set_settings(price_display="plus_plus")
    q = api.quote(cur, menu_setup["slug"], room="101", lines=[{"item_id": menu_setup["club"]["id"], "qty": 2}])
    # AED 100 net, convention A: SC 10, MF 7.70, VAT 5.89 -> 123.59
    assert q["subtotal"] == "100.00" and q["service_charge"] == "10.00"
    assert q["municipality_fee"] == "7.70" and q["vat"] == "5.89" and q["total"] == "123.59"
    assert not any(t["included"] for t in q["taxes"])


def test_quote_refuses_unavailable_alcohol_and_non_delivery(cur, menu_setup):
    slug = menu_setup["slug"]
    for key, code in (("breakfast", "item_unavailable"), ("wine", "alcohol_not_allowed"),
                      ("mojito", "outlet_no_delivery")):
        with pytest.raises(ClefsError) as e:
            api.quote(cur, slug, room="101", lines=[{"item_id": menu_setup[key]["id"]}])
        assert e.value.code == code
    with pytest.raises(ClefsError) as e:
        api.quote(cur, slug, room="101", lines=[{"item_id": 999999}])
    assert e.value.code == "item_not_found"
    with pytest.raises(ClefsError) as e:
        api.quote(cur, slug, room="999", lines=[{"item_id": menu_setup["club"]["id"]}])
    assert e.value.code == "room_not_found"
