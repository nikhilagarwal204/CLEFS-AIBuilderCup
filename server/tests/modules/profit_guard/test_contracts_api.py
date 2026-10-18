"""profit_guard.api bodies: the Inquiry Guard leak record (goldens from 04
spec 4.9), channel economics and distribution cost, the reference OTA,
rate evidence and the owner-policy settings. Each keeps its documented
empty answer on an empty hotel."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from clefs.core import stays
from clefs.core.ingest import batches, commit
from clefs.modules.profit_guard import api, imports, repo

CI = date(2026, 9, 1)


@pytest.fixture(autouse=True)
def handlers():
    imports.register()


def res(cur, slug, pms_id, *, channel="BOOKING", ota_id=None, status="CHECKED_OUT", nights=3, rate=35000,
        check_in=CI, name="Ahmed Al Mansouri"):
    ns = [{"stay_date": check_in + timedelta(days=i), "rate_minor": rate} for i in range(nights)]
    return stays.upsert_reservation(cur, slug, {
        "pms_res_id": pms_id, "channel_code": channel, "ota_res_id": ota_id, "guest_name": name,
        "check_in": check_in, "check_out": check_in + timedelta(days=nights), "status": status, "currency": "AED",
        "room_type_code": "DLX",
    }, nights=ns)["id"]


def test_empty_hotel_answers(cur, hotel_uae):
    s, d = hotel_uae.slug, date(2026, 10, 1)
    assert api.channel_economics(cur, s, start=d, end=d) == []
    assert api.channel_economics(cur, "no-such-hotel", start=d, end=d) == []
    assert api.reference_ota(cur, s, as_of=d) is None
    assert api.distribution_cost_by_night(cur, s, start=d, end=d) == []
    assert api.cancellation_cost_by_date(cur, s, start=d, end=d) == []
    assert api.distribution_cost_for_reservation(cur, s, reservation_id=999999) == 0
    assert api.record_rate_evidence(cur, s, evidence={}) == 0
    assert api.record_rate_evidence(cur, s, evidence={"channel": "BOOKING"}) == 0
    assert api.parity_tolerance_pct(cur, s) == Decimal("1.0") and api.treat_sc_as_revenue(cur, s) is True
    assert api.record_ota_leak(cur, s, source="inquiries", inquiry_ref="i1", reservation_ref=None,
                               channel_code="DIRECT_WEB", evidence={}) is None
    assert api.record_ota_leak(cur, "no-such-hotel", source="x", inquiry_ref="i", reservation_ref=None,
                               channel_code="BOOKING", evidence={}, commission_minor=1) is None


def test_record_ota_leak_fills_the_commission(cur, hotel_uae):
    s = hotel_uae.slug
    rid = res(cur, s, "P1", ota_id="4000012345", status="CONFIRMED", check_in=date(2026, 10, 5))
    out = api.record_ota_leak(cur, s, source="inquiries", inquiry_ref="inq-1", reservation_ref=str(rid),
                              channel_code="BOOKING", evidence={"match": "hmac", "confidence": "0.95",
                                                                "hmac": b"\x01\x02", "on": date(2026, 9, 1)})
    # 04 spec 4.9 golden: Booking, 3 nights x AED 350 -> AED 182.52.
    assert out["commission_minor"] == 18252 and out["basis"] == "expected" and out["currency"] == "AED"
    first = repo.get_finding(cur, s, int(out["finding_ref"].split(":")[1]))
    assert first["evidence"][-1]["value"] == {"match": "hmac", "confidence": "0.95", "hmac": "0102", "on": "2026-09-01"}
    assert first["confidence"] == Decimal("0.950")
    again = api.record_ota_leak(cur, s, source="inquiries", inquiry_ref="inq-1", reservation_ref=str(rid),
                                channel_code="BOOKING", evidence={"match": "hmac"})
    assert again["finding_ref"] == out["finding_ref"]
    f = repo.get_finding(cur, s, int(out["finding_ref"].split(":")[1]))
    assert f["internal_only"] is True and f["detector"] == "INQ" and f["class"] == "PREVENTABLE"
    assert f["reservation_id"] == rid and f["calc"][-1]["result_minor"] == 18252
    # The re-report refreshed evidence and confidence (a re-run never duplicates).
    assert f["evidence"][-1]["value"] == {"match": "hmac"} and f["confidence"] == Decimal("0.800")
    cur.execute("SELECT COUNT(*) AS n FROM pfg_finding_events WHERE finding_id = %s", (f["id"],))
    assert cur.fetchone()["n"] == 1
    provided = api.record_ota_leak(cur, s, source="inquiries", inquiry_ref="inq-2", reservation_ref="4000012345",
                                   channel_code="BOOKING", evidence={}, commission_minor=5000, currency="aed")
    assert (provided["basis"], provided["commission_minor"], provided["currency"]) == ("provided", 5000, "AED")


def test_record_ota_leak_prefers_the_invoiced_line(cur, hotel_uae):
    s = hotel_uae.slug
    rid = res(cur, s, "P1", ota_id="4000012345", status="CONFIRMED", check_in=date(2026, 10, 1), nights=2)
    from clefs import files

    csv = ("Book number,Guest name(s),Check-in,Check-out,Status,Price,Commission amount,Currency\n"
           "4000012345,Ahmed Al Mansouri,01/10/2026,03/10/2026,ok,900.00,121.50,AED\n")
    fid = files.put_file(cur, s, csv.encode(), filename="s.csv", content_type="text/csv", purpose="ingest.upload").id
    b = batches.create_batch(cur, s, file_id=fid, source_code="booking_res_statement")
    commit.commit_batch(cur, s, b["id"])
    out = api.record_ota_leak(cur, s, source="inquiries", inquiry_ref="inq-9", reservation_ref="P1",
                              channel_code="BOOKING", evidence={})
    assert (out["basis"], out["commission_minor"]) == ("statement_line", 12150)
    assert api.distribution_cost_for_reservation(cur, s, reservation_id=rid) == 12150


def test_channel_economics_and_distribution_cost(cur, hotel_uae):
    s = hotel_uae.slug
    res(cur, s, "B1", channel="BOOKING", ota_id="4000000001")
    res(cur, s, "D1", channel="DIRECT_WEB", name="Sara Khan")
    res(cur, s, "X1", channel="BOOKING", status="CANCELLED", name="Gone Guest")
    repo.put_channel_config(cur, s, {"ota_code": "DIRECT_WEB", "configured_from": date(2026, 1, 1),
                                     "card_fee_pct": Decimal("2.5"), "booking_engine_fee_pct": Decimal("3")})
    rows = {r["channel_code"]: r for r in api.channel_economics(cur, s, start=CI, end=CI + timedelta(days=30))}
    assert set(rows) == {"BOOKING", "DIRECT_WEB"}
    bk, dw = rows["BOOKING"], rows["DIRECT_WEB"]
    assert bk["room_nights"] == 3 and bk["is_ota"] is True and bk["commission_basis"] == "expected"
    assert bk["commission_fils"] == bk["commission_minor"] == 18252 and bk["room_rev_fils"] == 105000 + 10500
    assert bk["net_minor"] == 115500 - 18252 and bk["net_adr"] == str(Decimal(115500 - 18252) / 3 / 100)[:6]
    assert dw["commission_minor"] == 0 and dw["payment_cost_minor"] == round(dw["gross_minor"] * 0.055)
    # Service charge out of revenue when the owner says so.
    repo.put_policies(cur, s, {"treat_sc_as_revenue": False})
    assert api.channel_economics(cur, s, start=CI, end=CI + timedelta(days=30))[0]["room_rev_minor"] == 105000
    nights = api.distribution_cost_by_night(cur, s, start=CI, end=CI)
    assert {(n["channel_code"], n["commission_minor"] > 0) for n in nights} == {("BOOKING", True), ("DIRECT_WEB", False)}
    assert sum(n["commission_minor"] for n in api.distribution_cost_by_night(cur, s, start=CI, end=CI + timedelta(2))
               if n["channel_code"] == "BOOKING") == 18252
    assert api.reference_ota(cur, s, as_of=CI + timedelta(days=10)) == ("BOOKING", Decimal("15"))


def test_rate_evidence_and_policies(cur, hotel_uae):
    s = hotel_uae.slug
    eid = api.record_rate_evidence(cur, s, evidence={"channel": "google_hotels", "stay_date": "2026-10-10",
                                                     "price": "412.50", "room_label": "Deluxe King Room",
                                                     "source": "SERPAPI", "source_module": "visibility"})
    assert eid > 0
    row = repo.list_rate_evidence(cur, s)[0]
    assert row["emit_signal"] is False and row["price_minor"] == 41250 and row["channel"] == "GOOGLE_HOTELS"
    assert row["comparable_key"] == "2026-10-10|1|2|deluxe-king|DESKTOP|LOGGED_OUT"
    repo.put_policies(cur, s, {"parity_tolerance_pct": Decimal("2.5"), "reference_ota": "agoda"})
    assert api.parity_tolerance_pct(cur, s) == Decimal("2.5")
    assert api.reference_ota(cur, s, as_of=CI) == ("AGODA", Decimal("18"))
