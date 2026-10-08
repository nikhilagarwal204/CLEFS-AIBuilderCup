"""Sources: SerpApi Google Maps (fixtures), GBP, Tripadvisor, CSV presets."""

from __future__ import annotations

from decimal import Decimal

from clefs.core.hotels import get_hotel
from clefs.modules.reviews import store, sync
from clefs.modules.reviews.sources import csv_upload
from tests.modules.reviews.conftest import GBP_LOCATION

PLACE = {"place_results": {"title": "Test Dubai Hotel", "place_id": "ChIJtest", "data_id": "0x3e5f:0xabc",
                           "address": "Sheikh Zayed Rd", "rating": 4.3, "reviews": 812}}


def _g(rid, rating, text, iso="2026-09-27T08:00:00Z", response=None):
    r = {"review_id": rid, "rating": rating, "snippet": text, "iso_date": iso, "user": {"name": f"User {rid}"},
         "link": f"https://maps.google.com/?r={rid}"}
    if response:
        r["response"] = {"snippet": response, "iso_date": "2026-09-28T08:00:00Z"}
    return r


def test_serpapi_sync_finds_place_then_stops_at_first_known_review(cur, slug, now, serpapi_fake, set_settings):
    set_settings(serpapi_enabled=True)
    src = store.create_source(cur, slug, "serpapi_google")
    serpapi_fake.push(
        PLACE,
        {"reviews": [_g("a", 5, "Great"), _g("b", 2, "AC broken in room 312", response="Sorry")],
         "serpapi_pagination": {"next_page_token": "p2"}},
        {"reviews": [_g("c", 4, "Nice")]},
    )
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["created"] == 3
    reqs = serpapi_fake.requests
    assert reqs[0]["engine"] == "google_maps" and "Test" in reqs[0]["q"]
    assert reqs[1]["engine"] == "google_maps_reviews" and reqs[1]["data_id"] == "0x3e5f:0xabc"
    assert reqs[1]["sort_by"] == "newestFirst" and "num" not in reqs[1]
    assert reqs[2]["next_page_token"] == "p2" and reqs[2]["num"] == "20"
    b = store.get_review(cur, slug, _id(cur, slug, "b"))
    assert b["has_owner_response"] and b["room_hint"] == "312" and b["rating_norm_5"] == Decimal("2.0")
    src = store.get_source(cur, slug, src["id"])
    assert src["config"]["data_id"] == "0x3e5f:0xabc" and src["last_synced_at"] and src["status"] == "active"
    # The same day, the first page comes from core.serpapi's 20 h cache: free.
    assert sync.sync_hotel(cur, get_hotel(cur, slug))["created"] == 0 and len(serpapi_fake.requests) == 3
    # Next day: a new review on top, then a known one: one page, no place lookup.
    now.advance(days=1)
    serpapi_fake.push({"reviews": [_g("d", 3, "Ok", iso="2026-09-29T05:00:00Z"), _g("a", 5, "Great")],
                       "serpapi_pagination": {"next_page_token": "p2b"}})
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["created"] == 1 and len(serpapi_fake.requests) == 4
    cur.execute("SELECT DISTINCT purpose FROM serpapi_calls WHERE hotel_slug = %s", (slug,))
    assert [r["purpose"] for r in cur.fetchall()] == ["reviews.sync"]


def _id(cur, slug, ext):
    cur.execute("SELECT id FROM rev_reviews WHERE hotel_slug = %s AND external_id = %s", (slug, ext))
    return cur.fetchone()["id"]


def test_serpapi_is_off_until_disclosure_toggle_and_skipped_once_gbp_is_connected(cur, slug, serpapi_fake,
                                                                                  set_settings, gbp_source,
                                                                                  http_fake):
    store.create_source(cur, slug, "serpapi_google")
    http_fake.on("GET", f"{GBP_LOCATION}/reviews", {"reviews": []})
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert {r.get("skipped") for r in res["sources"]} >= {"serpapi_disabled"}
    set_settings(serpapi_enabled=True)
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert {r.get("skipped") for r in res["sources"]} >= {"gbp_preferred"}
    assert serpapi_fake.calls == 0


def test_serpapi_budget_exhausted_is_a_quiet_skip(cur, slug, serpapi_fake, set_settings, env):
    env(SERPAPI_MONTHLY_BUDGET_USD="0")
    set_settings(serpapi_enabled=True)
    src = store.create_source(cur, slug, "serpapi_google")
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["sources"][0]["skipped"] == "budget_exhausted"
    assert store.get_source(cur, slug, src["id"])["status"] == "active"


def test_gbp_sync_maps_stars_and_original_text(cur, slug, now, gbp_source, http_fake):
    http_fake.on("GET", f"{GBP_LOCATION}/reviews", {"reviews": [
        {"name": f"{GBP_LOCATION}/reviews/r1", "reviewId": "r1", "starRating": "FOUR",
         "reviewer": {"displayName": "Lina"}, "createTime": "2026-09-28T10:00:00Z",
         "comment": "(Translated by Google) Very clean\n\n(Original)\nنظيفة جدا"},
        {"name": f"{GBP_LOCATION}/reviews/r2", "reviewId": "r2", "starRating": "ONE",
         "reviewer": {"isAnonymous": True, "displayName": "A Google user"}, "createTime": "2026-09-27T10:00:00Z",
         "comment": "Noisy", "reviewReply": {"comment": "Sorry", "updateTime": "2026-09-28T09:00:00Z"}},
    ]})
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["created"] == 2
    r1 = store.get_review(cur, slug, _id(cur, slug, "r1"))
    assert r1["raw_text"] == "نظيفة جدا" and r1["language"] == "ar" and r1["rating"] == Decimal("4.0")
    assert r1["platform_ref"] == f"{GBP_LOCATION}/reviews/r1"
    r2 = store.get_review(cur, slug, _id(cur, slug, "r2"))
    assert r2["author_display"] is None and r2["has_owner_response"]
    assert http_fake.requests[0].headers["authorization"] == "Bearer tok"


def test_gbp_not_connected_marks_the_source(cur, slug):
    src = store.create_source(cur, slug, "gbp", config={"location_name": GBP_LOCATION})
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["sources"][0]["error"] == "not_connected"
    assert store.get_source(cur, slug, src["id"])["status"] == "disconnected"


def test_tripadvisor_accumulates_the_latest_five(cur, slug, env, http_fake):
    env(TRIPADVISOR_API_KEY="ta-key")
    store.create_source(cur, slug, "tripadvisor", config={"location_id": "302"})
    http_fake.on("GET", "/location/302/reviews", {"data": [
        {"id": 9001, "lang": "en", "rating": 2, "title": "Tired room", "text": "Stained carpet in room 1204",
         "published_date": "2026-09-20T10:00:00Z", "travel_date": "2026-09", "user": {"username": "trav"}},
    ]})
    res = sync.sync_hotel(cur, get_hotel(cur, slug))
    assert res["created"] == 1
    r = store.get_review(cur, slug, _id(cur, slug, "9001"))
    assert r["platform"] == "tripadvisor" and r["room_hint"] == "1204" and str(r["stay_date_guess"]) == "2026-09-01"
    assert "key=ta-key" in str(http_fake.requests[0].url)


BOOKING_CSV = """\ufeffReview date,Guest name,Reservation number,Review title,Positive review,Negative review,Review score,Property reply
2026-09-20,Omar,123456,Good location,Great breakfast,The AC in room 504 was noisy,"7,5",
21/09/2026,Maria,123457,,Friendly staff,,10,Thank you Maria!
2026-09-22,Sam,123458,,,,,
"""


def test_booking_csv_preset_converts_ten_to_five(cur, slug, now):
    hotel = get_hotel(cur, slug)
    dry = csv_upload.import_csv(cur, hotel, BOOKING_CSV, preset="booking", dry_run=True)
    assert dry["created"] == 2 and dry["skipped"] == 1 and store.list_sources(cur, slug) == []
    res = csv_upload.import_csv(cur, hotel, BOOKING_CSV, preset="booking")
    assert (res["created"], res["skipped"], res["errors"]) == (2, 1, [])
    cur.execute("SELECT * FROM rev_reviews WHERE hotel_slug = %s ORDER BY posted_at", (slug,))
    omar, maria = cur.fetchall()
    assert omar["rating"] == Decimal("7.5") and omar["rating_scale"] == 10 and omar["rating_norm_5"] == Decimal("3.8")
    assert "Liked: Great breakfast" in omar["raw_text"] and "Disliked:" in omar["raw_text"]
    assert omar["room_hint"] == "504" and omar["platform"] == "booking"
    assert maria["rating_norm_5"] == Decimal("5.0") and maria["has_owner_response"]
    assert maria["posted_at"].astimezone(hotel.tz).date().isoformat() == "2026-09-21"
    again = csv_upload.import_csv(cur, hotel, BOOKING_CSV, preset="booking")
    assert again["created"] == 0 and again["updated"] == 2


def test_agoda_semicolon_csv_and_scale_errors(cur, slug):
    text = "Review ID;Reviewer Name;Review Date;Overall Score;Review Title;Pros;Cons\n" \
           "A1;Li;2026-09-10;8.8;Nice;Pool;Parking\nA2;Ko;2026-09-11;11;Bad;;Dirty\n"
    res = csv_upload.import_csv(cur, get_hotel(cur, slug), text, preset="agoda")
    assert res["created"] == 1 and res["errors"][0]["row"] == 3
    cur.execute("SELECT external_id, rating_norm_5, platform FROM rev_reviews WHERE hotel_slug = %s", (slug,))
    row = cur.fetchone()
    assert row["external_id"] == "A1" and row["rating_norm_5"] == Decimal("4.4") and row["platform"] == "agoda"
