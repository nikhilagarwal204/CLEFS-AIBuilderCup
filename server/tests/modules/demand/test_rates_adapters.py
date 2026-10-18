"""Comp-rate adapters: SerpApi Google Hotels parsing, CSV shapes, Xotelo, MakCorps (02 spec 4.2)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx

from clefs.modules.demand.adapters import csv_rates, makcorps, serpapi_hotels, xotelo

NOW = datetime(2026, 9, 29, 10, 0, tzinfo=ZoneInfo("Asia/Dubai"))
COMPS = [
    {"comp_id": "hyatt-creek", "name": "Hyatt Regency Dubai Creek Heights", "property_token": "tokHYATT",
     "aliases": []},
    {"comp_id": "radisson-deira", "name": "Radisson Blu Hotel, Dubai Deira Creek", "property_token": None,
     "aliases": ["Radisson Blu Deira Creek"]},
    {"comp_id": "sheraton", "name": "Sheraton Dubai Creek", "property_token": "tokSHER", "aliases": []},
]

# Trimmed from a real google_hotels area answer (engine=google_hotels, q="hotels in Deira Dubai").
AREA = {
    "search_parameters": {"engine": "google_hotels", "q": "hotels in Deira Dubai", "currency": "AED"},
    "properties": [
        {"type": "hotel", "name": "Sheraton Dubai Creek (Ad)", "property_token": "tokSHER", "sponsored": True,
         "rate_per_night": {"lowest": "AED 520", "extracted_lowest": 520}},
        {"type": "hotel", "name": "Hyatt Regency Dubai Creek Heights", "property_token": "tokHYATT",
         "gps_coordinates": {"latitude": 25.2343, "longitude": 55.3218}, "extracted_hotel_class": 5,
         "rate_per_night": {"lowest": "AED 612", "extracted_lowest": 612,
                            "before_taxes_fees": "AED 498", "extracted_before_taxes_fees": 498},
         "prices": [{"source": "Booking.com", "rate_per_night": {"extracted_lowest": 640}},
                    {"source": "Hyatt", "official": True, "rate_per_night": {"extracted_lowest": 612}}]},
        {"type": "hotel", "name": "Radisson Blu Hotel, Dubai Deira Creek", "property_token": "tokRAD"},
        {"type": "hotel", "name": "Sheraton Dubai Creek Hotel & Towers", "property_token": "tokSHER",
         "rate_per_night": {"lowest": "AED 505", "extracted_lowest": 505.5}},
        {"type": "hotel", "name": "Test Hotel Dubai", "property_token": "tokSELF",
         "rate_per_night": {"extracted_lowest": 450}},
        {"type": "vacation rental", "name": "Creek view flat", "rate_per_night": {"extracted_lowest": 300}},
    ],
}


def test_serpapi_area_parse(clock):
    clock.freeze(NOW)
    rates, seen = serpapi_hotels.parse_area(AREA, COMPS, stay_date=date(2026, 10, 1), los=1, adults=2,
                                            currency="AED", query="hotels in Deira Dubai", self_token="tokSELF")
    by = {r.comp_id: r for r in rates}
    assert seen == {"hyatt-creek", "radisson-deira", "sheraton"}
    assert by["hyatt-creek"].lowest_minor == 61200 and not by["hyatt-creek"].sold_out
    assert by["hyatt-creek"].by_source == [{"source": "Hyatt", "minor": 61200, "official": True},
                                           {"source": "Booking.com", "minor": 64000}]
    # Ad row and organic row for one token: the lower price wins.
    assert by["sheraton"].lowest_minor == 50550
    # Listed with no price: sold out, not zero.
    assert by["radisson-deira"].sold_out and by["radisson-deira"].lowest_minor is None
    assert all(r.source == "SERPAPI" and r.captured_at == NOW for r in rates)


def test_serpapi_property_page_parse(clock):
    clock.freeze(NOW)
    page = {"type": "hotel", "name": "Hyatt",
            "prices": [{"source": "Agoda", "rate_per_night": {"extracted_lowest": 700}}]}
    r = serpapi_hotels.parse_property_page(page, COMPS[0], stay_date=date(2026, 10, 2), los=1, adults=2, currency="AED")
    assert r.lowest_minor == 70000 and r.by_source[0]["source"] == "Agoda"
    assert serpapi_hotels.parse_property_page({}, COMPS[0], stay_date=date(2026, 10, 2), los=1, adults=2,
                                              currency="AED") is None


def test_csv_cells_and_dates():
    assert csv_rates.parse_cell("AED 1,250.00") == ("price", Decimal("1250.00"))
    assert csv_rates.parse_cell("450,00") == ("price", Decimal("450.00"))
    assert csv_rates.parse_cell("Sold Out") == ("sold_out", None)
    assert csv_rates.parse_cell("-") == ("none", None) and csv_rates.parse_cell("0") == ("none", None)
    assert csv_rates.parse_date("Tue 29 Sep 2026") == date(2026, 9, 29)
    assert csv_rates.parse_date("01/10/2026") == date(2026, 10, 1)
    assert csv_rates.parse_date("garbage") is None
    assert csv_rates.source_for("RateGain Navigator export.csv") == "CSV_RATEGAIN"
    assert csv_rates.source_for(None) == "CSV_LIGHTHOUSE"


def test_csv_wide_and_long_shapes():
    wide = ("Lighthouse rate shop,,,\nDate,Day,Hyatt Regency Dubai Creek Heights,Sheraton Dubai Creek\n"
            "01/10/2026,Thu,612,Sold out\n02/10/2026,Fri,-,540\n")
    rows = csv_rates.parse_csv(wide)
    assert [(r["name"][:6], r["stay_date"].day, r["kind"]) for r in rows] == [
        ("Hyatt ", 1, "price"), ("Sherat", 1, "sold_out"), ("Sherat", 2, "price")]
    long = "hotel;date;rate;channel\nSheraton Dubai Creek;2026-10-03;499;Booking.com\n"
    rows = csv_rates.parse_csv(long)
    assert rows == [{"name": "Sheraton Dubai Creek", "stay_date": date(2026, 10, 3), "kind": "price",
                     "amount": Decimal("499"), "channel": "Booking.com", "los": 1}]


def test_xotelo_parse_and_gate(hotel_uae, monkeypatch):
    body = {"error": None, "result": {"currency": "USD", "rates": [
        {"code": "BookingCom", "name": "Booking.com", "rate": 240, "tax": 20},
        {"code": "Agoda", "name": "Agoda", "rate": 226}]}}
    usd, rows = xotelo.parse_rates(body, nights=2)
    assert usd == 11300 and rows[0]["source"] == "Agoda"
    assert xotelo.parse_rates({"error": "bad key", "result": None}, nights=1) == (None, [])
    monkeypatch.delenv("DEMAND_XOTELO_ENABLED", raising=False)
    assert not xotelo.enabled_for(hotel_uae)
    monkeypatch.setenv("DEMAND_XOTELO_ENABLED", "true")
    assert xotelo.enabled_for(hotel_uae)


def test_xotelo_fetch_converts_usd_to_aed(hotel_uae, clock):
    from clefs.modules.demand import adapters

    clock.freeze(NOW)

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["currency"] == "USD"
        return httpx.Response(200, json={"error": None, "result": {"rates": [{"name": "Agoda", "rate": 100}]}})

    adapters.set_transport(httpx.MockTransport(handler))
    try:
        a = xotelo.Xotelo([{"comp_id": "c1", "xotelo_key": "g295424-d1"}])
        rates = a.fetch(hotel_uae, ["c1"], [date(2026, 10, 1)])
    finally:
        adapters.set_transport(None)
    assert rates[0].lowest_minor == 36725 and rates[0].fx_converted and rates[0].original_minor == 10000
    assert rates[0].original_currency == "USD" and rates[0].currency == "AED"


def test_makcorps_is_a_stub(monkeypatch):
    monkeypatch.delenv("MAKCORPS_API_KEY", raising=False)
    assert not makcorps.configured()
    a = makcorps.MakCorps([{"comp_id": "c1", "makcorps_id": "123"}])
    assert a.fetch(None, ["c1"], [date(2026, 10, 1)]) == [] and a.stopped == "not_built"
