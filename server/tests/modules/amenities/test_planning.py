"""Shrinkage-adjusted rates, par recompute and cohort benchmarks."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from clefs.modules.amenities import benchmarks, catalog, par, rates, stock, variance

from .conftest import END_DEFAULT, TODAY, at


def test_shrinkage_when_orn_is_small():
    """02 spec 5.3: a small hotel's rate stays near the prior."""
    prior = Decimal(6)
    small = rates.shrunk_rate(Decimal(1), 40, prior)           # raw 2.5 per 100
    assert Decimal("5.4") < small < Decimal("5.6")
    big = rates.shrunk_rate(Decimal(250), 10000, prior)        # raw 2.5 per 100, lots of data
    assert Decimal("2.5") < big < Decimal("2.6")
    assert rates.shrunk_rate(Decimal(0), 0, prior) == prior


def test_learn_rates_from_rollup(cur, hotel_uae, items, occ_range, make_request):
    from clefs.modules.amenities import requests

    occ_range(END_DEFAULT - timedelta(days=9), 10, 20)
    for i in range(3):
        make_request("101", subtype="toothbrush", at=at(END_DEFAULT - timedelta(days=i), 21))
    requests.alias_pass(cur, hotel_uae.slug, since=at(END_DEFAULT - timedelta(days=12)))
    variance.rollup(cur, hotel_uae, END_DEFAULT - timedelta(days=9), END_DEFAULT)
    out = rates.learn(cur, hotel_uae, END_DEFAULT)
    assert out["orn"] == 200
    row = next(r for r in rates.list_rates(cur, hotel_uae.slug) if r["key"] == "DENTAL_KIT")
    # (3 + 6 x 2) / (200 + 200) x 100 = 3.75
    assert row["rate_per_100"] == "3.75" and row["raw_per_100"] == "1.5"


def test_par_recompute_uses_share_and_keeps_pinned(cur, hotel_uae, items, occ_range, issue):
    occ_range(TODAY - timedelta(days=28), 28, 40, floors={"1": 30, "2": 10})
    for i in range(28):
        issue(items["SHAMPOO"], 40 + (i % 3) * 2, at(TODAY - timedelta(days=28 - i)))
    variance.rollup(cur, hotel_uae, TODAY - timedelta(days=28), TODAY - timedelta(days=1))
    f1 = stock.create_location(cur, hotel_uae.slug, stock.LocationIn(kind="FLOOR_CLOSET", name="F1", floor="1"))
    store = stock.create_location(cur, hotel_uae.slug, stock.LocationIn(kind="STORE", name="Store"))
    stock.upsert_par(cur, hotel_uae.slug, stock.ParIn(location_id=f1["id"], item_id=items["SHAMPOO"]["id"]))
    stock.upsert_par(cur, hotel_uae.slug, stock.ParIn(location_id=store["id"], item_id=items["SHAMPOO"]["id"],
                                                      par_qty=Decimal(999)))
    out = par.recompute(cur, hotel_uae, TODAY)
    assert out == {"updated": 1, "pinned": 1}
    rows = {r["location_id"]: r for r in stock.list_par(cur, hotel_uae.slug)}
    f1_row = rows[f1["id"]]
    # forecast 40 rooms x 75% floor share x 1 shampoo = 30/day, 2 days of supply + safety
    assert f1_row["computed"]["daily_usage"] == "30.00"
    assert Decimal(60) < f1_row["par_qty"] < Decimal(66)
    assert rows[store["id"]]["par_qty"] == 999 and rows[store["id"]]["computed"]["suggested_par"]


def test_benchmarks_suppressed_with_two_hotels_banded_below_five(cur, hotel_uae):
    """02 spec 5.3: benchmarks suppressed with 2 hotels."""
    from clefs.core import hotels

    others = [hotels.create_hotel(cur, slug=f"bench-{i}", name=f"Bench {i}", star_rating=4) for i in range(4)]
    month = date(2026, 8, 1)

    def plant(h, units):
        item = catalog.create_item(cur, h.slug, "AED", catalog.ItemIn(key="SOAP", name_en="Soap", category="BATH"))
        cur.execute("INSERT INTO amn_daily_totals (hotel_slug, business_date, orn, currency) VALUES (%s, %s, 100, "
                    "'AED')", (h.slug, month))
        cur.execute("INSERT INTO amn_variance_daily (hotel_slug, business_date, item_id, issued, currency) "
                    "VALUES (%s, %s, %s, %s, 'AED')", (h.slug, month, item["id"], units))

    plant(hotel_uae, 110)
    plant(others[0], 120)
    benchmarks.rollup(cur, month)
    view = benchmarks.for_hotel(cur, hotel_uae)
    assert view["available"] is False and view["rows"][0]["suppressed_reason"] == "needs_3_hotels"
    assert view["rows"][0]["p50"] is None and view["rows"][0]["band_low"] is None

    plant(others[1], 130)
    benchmarks.rollup(cur, month)
    row = benchmarks.for_hotel(cur, hotel_uae)["rows"][0]
    assert row["published"] and row["p50"] is None                       # bands only below 5
    assert (row["band_low"], row["band_high"]) == ("115.00", "125.00") and row["yours_per_100_orn"] == "110.0"

    plant(others[2], 140)
    plant(others[3], 150)
    benchmarks.rollup(cur, month)
    row = benchmarks.for_hotel(cur, hotel_uae)["rows"][0]
    assert row["hotels_count"] == 5 and row["p50"] == "130.00"


def test_benchmarks_suppressed_when_one_hotel_dominates():
    s = benchmarks.summarize([Decimal(10), Decimal(11), Decimal(12)], [Decimal(900), Decimal(50), Decimal(50)])
    assert not s["published"] and s["suppressed_reason"] == "one_hotel_dominates"
