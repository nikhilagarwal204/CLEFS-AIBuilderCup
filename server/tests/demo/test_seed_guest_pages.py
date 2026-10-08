"""The demo's guest pages work right after the seed (QA GS-4): the room QR
page of an in-house room is personalized from the roster, every in-house
stay has its gst_stays snapshot, and a sent J1 / J7 step has its /g and
/g/f link, whose page carries the stay dates."""

from __future__ import annotations

from datetime import date

import pytest

from clefs.core import rooms as rooms_mod
from clefs.seed import demo_uae

ANCHOR = date(2026, 9, 27)
SLUG = "demo-t-gpages"
PARTS = ["core", "core_catalog", "core_stays", "ops"]


@pytest.fixture
def seeded(cur):
    return demo_uae.seed(cur, SLUG, anchor=ANCHOR, parts=PARTS, scale=0.1, password="demo-pass-123")


def test_in_house_room_page_is_personalized(cur, seeded, client):
    cur.execute("SELECT id, room_number, guest_name FROM reservations WHERE hotel_slug = %s AND status = 'IN_HOUSE' "
                "AND room_number IS NOT NULL ORDER BY room_number", (SLUG,))
    in_house = [dict(r) for r in cur.fetchall()]
    assert in_house
    cur.execute("SELECT room_number FROM guest_rooms WHERE hotel_slug = %s", (SLUG,))
    assert {r["room_number"] for r in cur.fetchall()} == {r["room_number"] for r in in_house}
    # gst_stays: the snapshot the journey pages and the returning badge read
    cur.execute("SELECT reservation_id, check_in, check_out FROM gst_stays WHERE hotel_slug = %s "
                "AND reservation_id = ANY(%s)", (SLUG, [r["id"] for r in in_house]))
    snaps = cur.fetchall()
    assert len(snaps) == len(in_house) and all(s["check_in"] and s["check_out"] for s in snaps)

    room = in_house[0]
    reg = rooms_mod.get_room(cur, SLUG, room["room_number"])
    key = rooms_mod.qr_key(SLUG, room["room_number"], reg["qr_key_version"] if reg else 1)
    r = client.get(f"/public/stay/{SLUG}/{room['room_number']}", params={"k": key})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["personalized"] is True and body["key_status"] == "valid"
    assert body["room"]["guests"][0]["name"] == room["guest_name"]
    assert set(body["room"]) <= {"roomNumber", "stayType", "checkIn", "checkOut", "loyaltyTier", "occasions",
                                 "guests", "preferences", "returningGuest"}


def test_sent_journey_steps_have_guest_links_with_stay_dates(cur, seeded, client, clock):
    from datetime import datetime

    clock.freeze(datetime.fromisoformat(seeded["as_of"]))  # the demo is opened right after the seed
    counts = seeded["parts"]["ops"]
    links = seeded["credentials"].get("guest_links") or {}
    assert set(links) == {"prefs", "feedback"}, links
    for purpose, url in links.items():
        token = url.rsplit("/", 1)[1]
        r = client.get(f"/public/journeys/{token}")
        assert r.status_code == 200, r.text
        page = r.json()
        assert (page["purpose"], page["state"]) == (purpose, "valid")
        assert page["stay"]["check_in"] and page["stay"]["check_out"]
    assert "/g/f/" in links["feedback"] and "/g/f/" not in links["prefs"]
    cur.execute("SELECT purpose, count(*) AS n FROM gst_public_tokens WHERE hotel_slug = %s GROUP BY 1", (SLUG,))
    minted = {r["purpose"]: r["n"] for r in cur.fetchall()}
    assert minted.get("prefs", 0) >= 1 and minted.get("feedback", 0) >= 1
    # The part's counts carry no token (the determinism test compares them).
    assert not any("/g/" in str(v) for v in counts.values())
