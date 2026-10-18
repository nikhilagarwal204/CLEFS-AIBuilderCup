"""reservation_breakdown (04 spec D3) and folio charges (02 spec 2.1)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import stays, tax
from clefs.core.errors import ClefsError

D = date.fromisoformat


@pytest.fixture
def slug(cur, hotel_uae, clock):
    clock.freeze(datetime(2025, 10, 20, 8, tzinfo=timezone.utc))
    tax.set_tax_profile(cur, hotel_uae.slug, effective_from=date(2020, 1, 1), property_category="hotel_3")
    cur.execute("INSERT INTO room_types (hotel_slug, code, name, bedrooms) VALUES (%s, 'FAM_2BR', 'Family', 2)",
                (hotel_uae.slug,))
    return hotel_uae.slug


def test_breakdown_equals_compose_stay_over_the_nights(cur, slug):
    rid = stays.upsert_reservation(cur, slug, {"pms_res_id": "Z1", "check_in": "2025-10-01", "check_out": "2025-10-04",
                                               "room_type_code": "DLX", "room_revenue_minor": 120000})["id"]
    b = stays.reservation_breakdown(cur, rid)
    lines = [tax.StayLine(D(f"2025-10-0{d}"), "DLX", 1, 40000) for d in (1, 2, 3)]
    assert b == tax.compose_stay(cur, slug, lines=lines)
    assert (b.room, b.td, b.td_nights_charged, b.td_units, b.currency) == (120000, 3000, 3, 1, "AED")
    assert b.room + b.sc + b.mf + b.vat + b.td == b.total


def test_per_bedroom_units_and_early_departure(cur, slug):
    data = {"pms_res_id": "Z2", "check_in": "2025-10-01", "check_out": "2025-10-05", "room_type_code": "FAM_2BR",
            "room_revenue_minor": 160000, "status": "CHECKED_OUT", "actual_check_out": "2025-10-03"}
    rid = stays.upsert_reservation(cur, slug, data)["id"]
    b = stays.reservation_breakdown(cur, rid)
    # Nights after the early departure are out; the seeded TD rule is per
    # bedroom, so a two-bedroom unit is 2 units (10 AED each) per night.
    assert (b.room, b.td_nights_charged, b.td_units, b.td) == (80000, 2, 2, 4000)
    cur.execute("SELECT td_minor, stayed FROM reservation_nights WHERE reservation_id = %s ORDER BY stay_date", (rid,))
    assert [(r["td_minor"], r["stayed"]) for r in cur.fetchall()] == [(2000, True), (2000, True), (0, False),
                                                                        (0, False)]


def test_chain_counts_toward_the_30_night_cap(cur, slug):
    first = stays.upsert_reservation(cur, slug, {"pms_res_id": "C1", "check_in": "2025-08-01", "check_out": "2025-08-29",
                                                 "room_number": "101", "guest_name": "Long Stay", "status": "CHECKED_OUT",
                                                 "room_revenue_minor": 28 * 30000})["id"]
    second = stays.upsert_reservation(cur, slug, {"pms_res_id": "C2", "check_in": "2025-08-29", "check_out": "2025-09-03",
                                                  "room_number": "101", "guest_name": "LONG, Stay",
                                                  "status": "CHECKED_OUT", "room_revenue_minor": 5 * 30000})["id"]
    assert stays.get_reservation(cur, second)["stay_chain_id"] == first
    b = stays.reservation_breakdown(cur, second)
    assert b.td_nights_charged == 2  # nights 29 and 30 of the chain; 31-33 are past the cap
    cur.execute("SELECT td_minor FROM reservation_nights WHERE reservation_id = %s ORDER BY stay_date", (second,))
    assert [r["td_minor"] for r in cur.fetchall()] == [1000, 1000, 0, 0, 0]


def test_unknown_reservation_is_a_404(cur, slug):
    with pytest.raises(ClefsError) as e:
        stays.reservation_breakdown(cur, 10**9)
    assert (e.value.status, e.value.code) == (404, "reservation_not_found")


def test_folio_is_idempotent_and_links_late_reservations(cur, slug):
    rows = [
        {"pms_txn_id": "T1", "posted_date": "2025-10-01", "code": "ROOM", "amount_minor": 40000, "pms_res_id": "F9"},
        {"pms_txn_id": "T2", "posted_date": "2025-10-01", "code": "minibar", "amount_minor": 2500, "pms_res_id": "F9"},
        {"pms_txn_id": "T3", "posted_date": "2025-10-02", "code": "MINIBAR", "amount_minor": -2500, "pms_res_id": "F9"},
        {"pms_txn_id": "T4", "posted_date": "2025-10-02", "code": "SPA", "amount_minor": 9000, "pms_res_id": "F9"},
    ]
    assert stays.upsert_folio_charges(cur, slug, rows) == {"written": 4, "linked": 0}
    rid = stays.upsert_reservation(cur, slug, {"pms_res_id": "F9", "check_in": "2025-10-01", "check_out": "2025-10-03"})["id"]
    assert stays.link_orphans(cur, slug) == 4
    assert stays.upsert_folio_charges(cur, slug, rows[:1]) == {"written": 1, "linked": 1}
    assert stays.folio_totals(cur, slug, rid) == {"MINIBAR": 0, "OTHER": 9000, "ROOM": 40000}
    lines = stays.folio_for(cur, slug, rid)
    assert len(lines) == 4 and [x["is_reversal"] for x in lines if x["pms_txn_id"] == "T3"] == [True]
    with pytest.raises(ClefsError):
        stays.upsert_folio_charges(cur, slug, [{"posted_date": "2025-10-01", "code": "ROOM", "amount_minor": 1}])


def test_a_foreign_currency_stay_converts_the_td_rate(cur, slug):
    """Review finding money-correctness-stays-fees-wrong-currency: a USD
    booking used the AED profile as is, so AED 10 of TD became USD 10 a night
    (3.67x) and every fee was quantized as AED."""
    rid = stays.upsert_reservation(cur, slug, {"pms_res_id": "U1", "check_in": "2025-10-01", "check_out": "2025-10-04",
                                               "room_type_code": "DLX", "room_revenue_minor": 30000,
                                               "currency": "USD"})["id"]
    b = stays.reservation_breakdown(cur, rid)
    # AED 10 at the 3.6725 peg is USD 2.7229 a night: 3 nights round once to USD 8.17.
    assert (b.currency, b.room, b.td, b.td_nights_charged) == ("USD", 30000, 817, 3)
    # Percentage fees do not depend on the currency: same minor units as an AED stay of the same numbers.
    aed = tax.compose_stay(cur, slug, lines=[tax.StayLine(D(f"2025-10-0{d}"), "DLX", 1, 10000) for d in (1, 2, 3)])
    assert (b.sc, b.mf, b.vat, aed.td) == (aed.sc, aed.mf, aed.vat, 3000)
    assert b.room + b.sc + b.mf + b.tf + b.dest + b.vat + b.td == b.total
    cur.execute("SELECT td_minor, currency FROM reservation_nights WHERE reservation_id = %s ORDER BY stay_date", (rid,))
    rows = cur.fetchall()
    assert [r["td_minor"] for r in rows] == [272, 272, 272] and {r["currency"] for r in rows} == {"USD"}


def test_a_currency_without_a_rate_leaves_the_td_out_and_says_so(cur, slug):
    lines = [tax.StayLine(D("2025-10-01"), "DLX", 1, 10000)]
    b = tax.compose_stay(cur, slug, lines=lines, currency="XAF")  # no fx_rates row, no peg
    assert (b.currency, b.td) == ("XAF", 0)
    assert any("No AED->XAF rate" in n for n in b.notices)
    assert b.sc == tax.compose_stay(cur, slug, lines=lines).sc
