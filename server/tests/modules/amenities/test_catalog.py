"""Catalog, cost history, seed, aliases and the frozen amenities.api doors."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from clefs.core.errors import ClefsError
from clefs.modules.amenities import api, catalog


def test_seed_is_uae_bilingual_flagged_for_review_and_once_only(cur, hotel_uae, items):
    assert len(items) >= 15
    for r in items.values():
        assert r["needs_review"] and r["source"] == "SEED" and r["source_url"].startswith("https://")
        assert r["name_ar"], r["key"]
        assert r["currency"].strip() == "AED"
    assert items["DENTAL_KIT"]["is_single_use_plastic"]
    assert items["DENTAL_KIT"]["basis"] == "ON_REQUEST"
    # Deleting a seeded item does not bring it back on the next seed call.
    catalog.delete_item(cur, hotel_uae.slug, items["COMB"]["id"])
    again = catalog.seed_defaults(cur, hotel_uae.slug, "AED")
    assert again["already"] and catalog.item_by_key(cur, hotel_uae.slug, "COMB") is None


def test_excise_is_included_in_cost(cur, hotel_uae, items):
    """02 spec 5.3: excise included in cost."""
    energy = items["MB_ENERGY"]
    assert energy["excise_category"] == "ENERGY"
    costs = catalog.list_costs(cur, hotel_uae.slug, energy["id"])
    assert costs[0]["unit_cost_minor"] == energy["unit_cost_minor"]
    assert 0 < costs[0]["excise_minor"] < costs[0]["unit_cost_minor"]
    catalog.add_cost(cur, hotel_uae.slug, energy["id"], effective_from=date(2026, 9, 1), unit_cost_minor=800,
                     excise_minor=400, source="SUPPLIER_INVOICE", invoice_ref="INV-1")
    book = catalog.CostBook(cur, hotel_uae.slug)
    assert book.on(energy["id"], date(2026, 8, 31)) == 700
    assert book.on(energy["id"], date(2026, 9, 2)) == 800
    assert catalog.get_item(cur, hotel_uae.slug, energy["id"])["unit_cost_minor"] == 800
    with pytest.raises(ClefsError):
        catalog.add_cost(cur, hotel_uae.slug, energy["id"], effective_from=date(2026, 9, 2), unit_cost_minor=100,
                         excise_minor=200)


def test_create_and_patch_item(cur, hotel_uae, frozen):
    row = catalog.create_item(cur, hotel_uae.slug, "AED", catalog.ItemIn(
        name_en="Bamboo toothbrush", category="DENTAL", unit_cost=Decimal("1.25"), aliases=["bamboo brush", " x "]))
    assert row["key"] == "BAMBOO_TOOTHBRUSH" and row["unit_cost_minor"] == 125
    before, after = catalog.update_item(cur, hotel_uae.slug, row["id"], catalog.ItemPatch(price=Decimal("5"),
                                                                                           needs_review=False))
    assert after["price_minor"] == 500
    out = catalog.item_out(after)
    assert out["price"] == "5.00" and out["unit_cost"] == "1.25" and out["std_qty"] == "0"
    with pytest.raises(ClefsError) as e:
        catalog.create_item(cur, hotel_uae.slug, "AED", catalog.ItemIn(key="BAMBOO_TOOTHBRUSH", name_en="x"))
    assert e.value.code == "item_key_taken"


def test_alias_matching_toothbrush_to_dental_kit(items):
    """02 spec 5.3: alias linking ("Toothbrush" -> DENTAL_KIT)."""
    hits = catalog.match_items("Toothbrush", list(items.values()))
    assert [h["item"]["key"] for h in hits] == ["DENTAL_KIT"]
    hits = catalog.match_items("Please send 2 toothbrushes and a razor", list(items.values()))
    assert {h["item"]["key"]: h["qty"] for h in hits} == {"DENTAL_KIT": 2, "SHAVING_KIT": 1}
    assert catalog.match_items("فرشاة أسنان", list(items.values()))[0]["item"]["key"] == "DENTAL_KIT"
    assert catalog.match_items("razorback", list(items.values())) == []


def test_api_frozen_shape(cur, hotel_uae, items):
    rows = api.list_items(cur, hotel_uae.slug)
    assert rows and {"id", "key", "name", "request_type", "max_qty", "chargeable", "price", "currency"} <= set(rows[0])
    ar = api.list_items(cur, hotel_uae.slug, language="ar")
    assert ar[0]["name"] == items[ar[0]["key"]]["name_ar"]
    one = api.resolve_item(cur, hotel_uae.slug, items["MB_SOFT_DRINK"]["id"])
    assert one["chargeable"] and one["price"] == "15.00" and one["request_type_key"] == "drinks"
    assert api.resolve_item(cur, hotel_uae.slug, 999999) is None
    assert api.resolve_item(cur, "no-such-hotel", items["MB_SOFT_DRINK"]["id"]) is None


def test_api_link_request_is_tool_matched_and_never_raises(cur, hotel_uae, items, make_request):
    from clefs.modules.amenities import requests

    rid = make_request("101", subtype="Toothbrush")
    requests.alias_pass(cur, hotel_uae.slug)
    assert [ln["match_method"] for ln in requests.lines_for(cur, hotel_uae.slug, rid)] == ["ALIAS"]
    api.link_request(cur, hotel_uae.slug, request_id=rid, amenity_item_id=items["SHAVING_KIT"]["id"], qty=2)
    lines = requests.lines_for(cur, hotel_uae.slug, rid)
    assert [(ln["item_key"], ln["qty"], ln["match_method"]) for ln in lines] == [("SHAVING_KIT", 2, "TOOL")]
    # Ignored, never raised: unknown item, zero qty, a request of another hotel.
    api.link_request(cur, hotel_uae.slug, request_id=rid, amenity_item_id=987654, qty=1)
    api.link_request(cur, hotel_uae.slug, request_id=rid, amenity_item_id=items["COMB"]["id"], qty=0)
    api.link_request(cur, "other-hotel", request_id=rid, amenity_item_id=items["COMB"]["id"], qty=1)
    assert len(requests.lines_for(cur, hotel_uae.slug, rid)) == 1
