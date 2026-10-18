"""Logging gap, stockout, single-use plastic, minibar unbilled, the staff
view and the LLM linking pass."""

from __future__ import annotations

from datetime import timedelta

from clefs.core import signals
from clefs.modules.amenities import detectors, reports, requests, stock, variance

from .conftest import END_DEFAULT, at


def _kinds(cur, slug):
    return {s["kind"]: s for s in signals.list_signals(cur, slug, modules=["amenities"])}


def test_logging_gap_fires_when_kits_outrun_logged_requests(cur, hotel_uae, items, occ_range, issue, make_request):
    occ_range(END_DEFAULT - timedelta(days=13), 14, 30)
    for i in range(14):
        issue(items["DENTAL_KIT"], 2, at(END_DEFAULT - timedelta(days=i)), reason="GUEST_REQUEST")   # 28 issued
    for i in range(4):
        make_request("101", subtype="toothbrush", at=at(END_DEFAULT - timedelta(days=i), 20))       # 4 logged
    requests.alias_pass(cur, hotel_uae.slug, since=at(END_DEFAULT - timedelta(days=20)))
    variance.rollup(cur, hotel_uae, END_DEFAULT - timedelta(days=13), END_DEFAULT)
    assert detectors.detect_logging_gap(cur, hotel_uae, END_DEFAULT)["fired"] == 1
    s = _kinds(cur, hotel_uae.slug)[detectors.GAP]
    assert s["section"] == "service_risk" and "28" in s["title"]


def test_stockout_and_sup_compliance(cur, hotel_uae, items, frozen, issue):
    loc = stock.create_location(cur, hotel_uae.slug, stock.LocationIn(kind="FLOOR_CLOSET", name="F2", floor="2"))
    stock.upsert_par(cur, hotel_uae.slug, stock.ParIn(location_id=loc["id"], item_id=items["DENTAL_KIT"]["id"],
                                                      par_qty=20, safety_stock=6))
    stock.add_counts(cur, hotel_uae.slug, stock.CountsIn(lines=[stock.CountLine(
        location_id=loc["id"], item_id=items["DENTAL_KIT"]["id"], qty=8, counted_at=at(END_DEFAULT, 8))]))
    issue(items["DENTAL_KIT"], 5, at(END_DEFAULT, 12), from_location_id=loc["id"])
    assert detectors.detect_stockout(cur, hotel_uae, END_DEFAULT)["fired"] == 1
    assert detectors.detect_sup(cur, hotel_uae, END_DEFAULT)["fired"] == 1
    k = _kinds(cur, hotel_uae.slug)
    assert "3 left" in k[detectors.STOCKOUT]["title"]
    sup = k[detectors.SUP]
    assert sup["section"] == "compliance" and sup["rankable"] is False and "Dubai" in sup["summary"]
    # A restock clears the stockout signal on the next run.
    issue(items["DENTAL_KIT"], 20, at(END_DEFAULT, 13), to_location_id=loc["id"])
    assert detectors.detect_stockout(cur, hotel_uae, END_DEFAULT)["resolved"] == 1


def test_minibar_unbilled_needs_a_folio_feed(cur, hotel_uae, items, frozen, issue):
    for i in range(7):
        issue(items["MB_SOFT_DRINK"], 10, at(END_DEFAULT - timedelta(days=i)))                    # 70 refills
    assert reports.minibar(cur, hotel_uae, END_DEFAULT - timedelta(days=6), END_DEFAULT)["has_folio"] is False
    assert detectors.detect_minibar(cur, hotel_uae, END_DEFAULT)["fired"] == 0
    cur.execute("INSERT INTO folio_charges (hotel_slug, posted_date, code, qty, amount_minor, currency, pms_txn_id) "
                "VALUES (%s, %s, 'MINIBAR', 50, 75000, 'AED', 'mb-1')", (hotel_uae.slug, END_DEFAULT))
    mb = reports.minibar(cur, hotel_uae, END_DEFAULT - timedelta(days=6), END_DEFAULT)
    assert mb["unbilled"] == "20" and mb["unbilled_value"] == "300.00"                          # 20 x AED 15
    assert detectors.detect_minibar(cur, hotel_uae, END_DEFAULT)["fired"] == 1
    s = _kinds(cur, hotel_uae.slug)[detectors.MINIBAR]
    assert s["impact_minor"] == 30000 and s["recommended_action"]["type"] == "am.post_minibar"


def test_staff_view_is_gated_and_neutral_with_counter_rotation(cur, hotel_uae, items, occ_range, issue):
    start = END_DEFAULT - timedelta(days=13)
    occ_range(start, 14, 30, floors={"1": 10, "2": 10, "5": 10})
    for i in range(14):
        d = start + timedelta(days=i)
        issue(items["SHAMPOO"], 10, at(d), floor="1", staff="Ana")
        issue(items["SHAMPOO"], 10, at(d), floor="2", staff="Ben")
        issue(items["SHAMPOO"], 25, at(d), floor="5", staff="Chen")                            # 15 above expected
    variance.rollup(cur, hotel_uae, start, END_DEFAULT)
    v = reports.staff_view(cur, hotel_uae, start, END_DEFAULT)
    assert v["available"] and "never to assign blame" in v["note"]
    rows = {r["staff_name"]: r for r in v["rows"]}
    assert rows["Ana"]["issued_per_room"] == "1.00" and rows["Chen"]["issued_per_room"] == "2.50"
    assert v["counter_rotation"] is True and "Rotate" in v["counter_rotation_note"]
    short = reports.staff_view(cur, hotel_uae, END_DEFAULT - timedelta(days=5), END_DEFAULT)
    assert short["available"] is False


def test_llm_pass_links_what_aliases_missed(cur, hotel_uae, items, frozen, make_request, fake_ai):
    rid = make_request("205", type_="Amenities", subtype="something to clean my teeth")
    other = make_request("206", type_="Amenities", subtype="the TV remote is broken")
    assert requests.alias_pass(cur, hotel_uae.slug)["linked"] == 0
    fake_ai.push({"links": [{"request_id": rid, "item_key": "DENTAL_KIT", "qty": 1, "confidence": 0.9},
                            {"request_id": other, "item_key": "SHAMPOO", "qty": 1, "confidence": 0.3}]})
    out = requests.llm_pass(cur, hotel_uae.slug)
    assert out == {"scanned": 2, "linked": 1}
    lines = requests.lines_for(cur, hotel_uae.slug, rid)
    assert [(ln["item_key"], ln["match_method"]) for ln in lines] == [("DENTAL_KIT", "LLM")]
    assert requests.lines_for(cur, hotel_uae.slug, other) == []
    # Never paid for twice.
    assert requests.llm_pass(cur, hotel_uae.slug) == {"scanned": 0, "linked": 0}


def test_spike_and_stockout_are_weekly_kinds_canon_18():
    # The daily brief is money at risk with a deadline only: a backward-looking
    # 7-day loss and a service risk compete for the weekly tier instead.
    assert signals.get_kind(detectors.SPIKE).cadence == "weekly"
    assert signals.get_kind(detectors.STOCKOUT).cadence == "weekly"


def test_stockout_goes_daily_only_when_under_a_day_of_use_is_left(cur, hotel_uae, items, frozen, issue):
    kit = items["DENTAL_KIT"]["id"]
    closet = stock.create_location(cur, hotel_uae.slug, stock.LocationIn(kind="FLOOR_CLOSET", name="F3", floor="3"))
    store = stock.create_location(cur, hotel_uae.slug, stock.LocationIn(kind="STORE", name="Main store"))
    # Closet: 10 a day, 3 left -> runs out today. Store: 10 a day, 30 left
    # against 7 days of lead time -> low, but three days away.
    stock.upsert_par(cur, hotel_uae.slug, stock.ParIn(location_id=closet["id"], item_id=kit, par_qty=20,
                                                      days_of_supply=2, lead_days=1, safety_stock=6))
    stock.upsert_par(cur, hotel_uae.slug, stock.ParIn(location_id=store["id"], item_id=kit, par_qty=140,
                                                      days_of_supply=14, lead_days=7, safety_stock=0))
    stock.add_counts(cur, hotel_uae.slug, stock.CountsIn(lines=[
        stock.CountLine(location_id=closet["id"], item_id=kit, qty=3, counted_at=at(END_DEFAULT, 8)),
        stock.CountLine(location_id=store["id"], item_id=kit, qty=30, counted_at=at(END_DEFAULT, 8))]))
    assert detectors.detect_stockout(cur, hotel_uae, END_DEFAULT)["fired"] == 2
    rows = [s for s in signals.list_signals(cur, hotel_uae.slug, modules=["amenities"]) if s["kind"] == detectors.STOCKOUT]
    by_loc = {s["subject_id"]: s["cadence"] for s in rows}
    assert by_loc == {str(closet["id"]): "daily", str(store["id"]): "weekly"}
    daily = signals.list_signals(cur, hotel_uae.slug, modules=["amenities"], cadence="daily")
    assert [s["subject_id"] for s in daily] == [str(closet["id"])]


def test_spike_signal_is_weekly_and_names_the_leaking_floor(cur, hotel_uae, frozen, occ_range, issue):
    from decimal import Decimal

    from clefs.modules.amenities import catalog

    item = catalog.create_item(cur, hotel_uae.slug, "AED", catalog.ItemIn(
        key="TEST_SOAP", name_en="Test soap", category="BATH", basis="PER_OCCUPIED_ROOM_NIGHT",
        std_qty=Decimal("1"), unit_cost=Decimal("1.00")))
    start = END_DEFAULT - timedelta(days=62)
    occ_range(start, 63, 50, floors={"1": 25, "2": 25})
    for i in range(63):
        for f in ("1", "2"):
            issue(item, 25, at(start + timedelta(days=i)), floor=f)
    issue(item, 200, at(END_DEFAULT), floor="1")
    variance.rollup(cur, hotel_uae, start, END_DEFAULT)
    assert detectors.detect_spikes(cur, hotel_uae, END_DEFAULT)["fired"] == 1
    s = _kinds(cur, hotel_uae.slug)[detectors.SPIKE]
    assert s["cadence"] == "weekly" and s["recommended_action"]["params"]["floor"] == "1"
