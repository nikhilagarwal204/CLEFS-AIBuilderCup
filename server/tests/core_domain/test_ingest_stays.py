"""core/ingest commit handlers for core/stays: PMS reservations (with sealed
contacts and the raw redaction after commit), folio, occupancy and
booking-engine attribution (04 spec D1, D2, D4)."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs import files
from clefs.core import stays
from clefs.core.ingest import batches, commit, handlers

PMS = (
    "Confirmation No,Guest Name,Arrival,Departure,Status,Source,Room,Room Type,Room Revenue,Email,Mobile,Segment\n"
    "C100,Jane Doe,10/10/2025,13/10/2025,Checked Out,Booking.com,101,DLX,1200.00,jd@guest.booking.com,,Leisure\n"
    "C101,Omar Ali,11/10/2025,12/10/2025,Confirmed,Website,102,DLX,400.00,omar@example.com,050 123 4567,\n"
    "C102,Bad Dates,12/10/2025,10/10/2025,Confirmed,Website,103,DLX,100.00,,,\n"
)


def put(cur, hotel, text: str, name: str = "pms.csv") -> str:
    return files.put_file(cur, hotel, text.encode("utf-8"), filename=name, content_type="text/csv",
                          purpose="ingest.upload").id


@pytest.fixture
def owner(cur, make_user, hotel_uae):
    from clefs.auth.deps import Principal, resolve_ctx

    user = make_user("owner", hotel_uae.slug)
    return resolve_ctx(Principal(kind="user", user_id=user["id"], label="o", is_clefs_admin=False,
                                 session_kind="web"), hotel_uae.slug, cur=cur)


@pytest.fixture(autouse=True)
def _frozen(clock):
    clock.freeze(datetime(2025, 10, 20, 8, tzinfo=timezone.utc))


def test_pms_reservations_commit_to_stays_and_contacts_leave_staging(cur, hotel_uae, owner):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, PMS), kind="PMS_RESERVATIONS")
    assert b["status"] == "REVIEW" and b["source_code"] == "pms_reservations", b["errors"]
    rows = batches.list_rows(cur, b["id"])
    assert rows[1]["canonical"]["guest_phone"] == "050 123 4567"  # staged plainly until the commit
    done = commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=owner)
    assert done["commit_result"]["created"] == 2 and done["commit_result"]["failed"] == 1
    jane = stays.find_reservation(cur, hotel_uae.slug, "C100")
    assert jane["channel_code"] == "BOOKING" and jane["status"] == "CHECKED_OUT" and jane["segment"] == "LEISURE"
    assert jane["room_revenue_minor"] == 120000 and jane["nights"] == 3
    omar = stays.find_reservation(cur, hotel_uae.slug, "C101")
    assert omar["channel_code"] == "DIRECT_WEB"
    kinds_seen = sorted((c["kind"], c["provenance"]) for c in stays.contacts_for(cur, hotel_uae.slug, omar["id"]))
    assert kinds_seen == [("email", "pms_direct"), ("phone", "pms_direct")]
    [jc] = stays.contacts_for(cur, hotel_uae.slug, jane["id"])
    assert jc["provenance"] == "ota_alias"
    # 04 spec D1: after the commit the sealed copy is the only one.
    cur.execute("SELECT raw::text AS raw, canonical::text AS canon, status, messages FROM import_rows "
                "WHERE batch_id = %s ORDER BY row_no", (b["id"],))
    staged = cur.fetchall()
    for r in staged:
        for secret in ("jd@guest.booking.com", "omar@example.com", "050 123 4567"):
            assert secret not in r["raw"] and secret not in r["canon"]
    assert staged[2]["status"] == "ERROR" and "not written" in staged[2]["messages"][-1]
    assert "Email" in done["stats"]["redacted_columns"] and "Mobile" in done["stats"]["redacted_columns"]


def test_reimporting_the_same_reservations_updates_not_duplicates(cur, hotel_uae, owner):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, PMS), kind="PMS_RESERVATIONS")
    commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=owner, skip_errors=True)
    changed = PMS.replace("Confirmed,Website,102", "Cancelled,Website,102")
    b2 = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, changed),
                              kind="PMS_RESERVATIONS")
    out = commit.commit_batch(cur, hotel_uae.slug, b2["id"], actor=owner, skip_errors=True)
    assert out["commit_result"]["created"] == 0 and out["commit_result"]["updated"] == 2
    assert stays.find_reservation(cur, hotel_uae.slug, "C101")["status"] == "CANCELLED"
    cur.execute("SELECT COUNT(*) AS n FROM reservations WHERE hotel_slug = %s", (hotel_uae.slug,))
    assert cur.fetchone()["n"] == 2


def test_folio_occupancy_and_attribution_handlers(cur, hotel_uae, owner):
    b = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, PMS), kind="PMS_RESERVATIONS")
    commit.commit_batch(cur, hotel_uae.slug, b["id"], actor=owner, skip_errors=True)
    folio = ("Transaction ID,Posting Date,Transaction Code,Description,Amount,Confirmation No\n"
             "T1,10/10/2025,1000,Room Charge,400.00,C100\nT2,10/10/2025,9100,Tourism Dirham Fee,15.00,C100\n"
             "T3,11/10/2025,2000,Minibar,30.00,C999\n")
    f = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, folio, "folio.csv"),
                             kind="PMS_FOLIO")
    assert f["source_code"] == "pms_folio" and f["status"] == "REVIEW", f["errors"]
    out = commit.commit_batch(cur, hotel_uae.slug, f["id"], actor=owner)["commit_result"]
    assert out == {"written": 3, "linked": 2, "failed": 0}
    cur.execute("SELECT pms_txn_id, code FROM folio_charges WHERE hotel_slug = %s ORDER BY pms_txn_id",
                (hotel_uae.slug,))
    assert [(r["pms_txn_id"], r["code"]) for r in cur.fetchall()] == [("T1", "ROOM"), ("T2", "TD"),
                                                                      ("T3", "MINIBAR")]
    occ = "Date,Rooms Sold,Rooms Available,Room Revenue\n2025-10-10,9,13,3600.00\n2025-10-11,10,13,4000\n"
    o = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, occ, "occ.csv"), kind="OCCUPANCY")
    assert commit.commit_batch(cur, hotel_uae.slug, o["id"], actor=owner)["commit_result"]["written"] == 2
    [day] = stays.get_occupancy(cur, hotel_uae.slug, date(2025, 10, 10), date(2025, 10, 10))
    assert (day["rooms_occupied"], day["source"], day["room_revenue_minor"]) == (9, "PMS", 360000)
    attr = ("Booking Ref,utm_source,utm_medium,utm_campaign,Promo Code\n"
            "C101,google,cpc,brand,SAVE10\nNOPE,meta,paid,x,\n")
    a = batches.create_batch(cur, hotel_uae.slug, file_id=put(cur, hotel_uae.slug, attr, "attr.csv"),
                             kind="BOOKING_ENGINE_ATTRIBUTION")
    assert a["source_code"] == "booking_engine_attribution"
    res = commit.commit_batch(cur, hotel_uae.slug, a["id"], actor=owner)["commit_result"]
    assert res["matched"] == 1 and res["unmatched"] == ["NOPE"]
    omar = stays.find_reservation(cur, hotel_uae.slug, "C101")
    assert omar["promo_code"] == "SAVE10" and omar["utm"]["utm_source"] == "google"


def test_normalizers():
    assert handlers.channel_code("Booking Engine") == "DIRECT_WEB"
    assert handlers.channel_code("Expedia Collect") == "EXPEDIA"
    assert handlers.collect_model("TO BE PAID BY GUEST") == "HOTEL_COLLECT"
    assert handlers.collect_model("Prepaid") == "OTA_COLLECT"
    assert handlers.folio_code("MF") == "MF" and handlers.folio_code("777", "Municipality fee") == "MF"
    assert handlers.folio_code("777", "Laundry") == "OTHER"
