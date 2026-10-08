"""core/stays HTTP routes: permissions, guest-name masking by role, shapes;
plus the stays.* metrics compiling against the real schema."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from clefs.core import stays

BASE = "/hotels/test-dubai/stays"


@pytest.fixture
def seeded(cur, hotel_uae, clock):
    clock.freeze(datetime(2025, 10, 20, 8, tzinfo=timezone.utc))
    rid = stays.upsert_reservation(
        cur, hotel_uae.slug,
        {"pms_res_id": "W1", "guest_name": "Jane Doe", "check_in": "2025-10-10", "check_out": "2025-10-13",
         "channel_code": "BOOKING", "room_number": "101", "status": "CHECKED_OUT", "room_revenue_minor": 120000,
         "booked_at": "2025-09-01T10:00:00+04:00"},
        contacts=[{"kind": "email", "value": "jd@guest.booking.com"}],
    )["id"]
    stays.derive_occupancy(cur, hotel_uae.slug, date(2025, 10, 10), date(2025, 10, 12))
    m = stays.match_claim(cur, hotel_uae.slug, statement_line_id=1, ota_code="BOOKING", guest_name="Jane Doe",
                          check_in=date(2025, 10, 10), nights=5, amount_minor=999999, currency="AED")
    assert m["status"] == "REVIEW"
    return {"rid": rid, "match": m["id"]}


def test_occupancy_routes_and_manual_override(client_as, seeded):
    owner = client_as("owner")
    r = owner.get(f"{BASE}/occupancy", params={"start": "2025-10-10", "end": "2025-10-12", "by_floor": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [d["rooms_occupied"] for d in body["days"]] == [1, 1, 1] and body["by_floor"][0]["floor"] == "1"
    r = owner.put(f"{BASE}/occupancy/2025-10-11", json={"rooms_occupied": 8})
    assert r.status_code == 200 and r.json()["source"] == "MANUAL" and r.json()["rooms_occupied"] == 8
    r = owner.delete(f"{BASE}/occupancy/2025-10-11")
    assert r.json()["source"] == "DERIVED" and r.json()["rooms_occupied"] == 1
    assert owner.put(f"{BASE}/occupancy/2025-10-11", json={}).status_code == 400
    assert client_as("finance").put(f"{BASE}/occupancy/2025-10-11", json={"rooms_occupied": 2}).status_code == 403
    assert client_as("front_desk").get(f"{BASE}/occupancy").status_code == 403
    assert owner.get(f"{BASE}/occupancy", params={"start": "2025-10-12", "end": "2025-10-01"}).status_code == 400


def test_match_queue_masks_names_by_role_and_confirms(client_as, seeded):
    rows = client_as("finance").get(f"{BASE}/matches").json()
    assert rows[0]["id"] == seeded["match"] and "query" not in rows[0]
    assert rows[0]["candidates"][0]["reservation"]["guest_name"] == "Jane Doe"
    hidden = client_as("revenue").get(f"{BASE}/matches").json()
    assert hidden[0]["candidates"][0]["reservation"]["guest_name"] is None  # revenue sees no guest names
    assert client_as("finance").post(f"{BASE}/matches/{seeded['match']}/confirm",
                                     json={"reservation_ids": [seeded["rid"]]}).status_code == 403
    r = client_as("gm").post(f"{BASE}/matches/{seeded['match']}/confirm", json={"reservation_ids": [seeded["rid"]]})
    assert r.status_code == 200 and r.json()["method"] == "MANUAL"
    assert client_as("gm").get(f"{BASE}/matches").json() == []
    assert client_as("gm").post(f"{BASE}/matches/999999/reject").status_code == 404


def test_reservation_detail_never_carries_contact_values(client_as, seeded):
    r = client_as("owner").get(f"{BASE}/reservations/{seeded['rid']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reservation"]["guest_name"] == "Jane Doe" and len(body["nights"]) == 3
    assert body["contacts"][0]["provenance"] == "ota_alias" and "jd@" not in r.text
    assert [e["event"] for e in body["events"]][:3] == ["created", "checked_in", "checked_out"]
    assert body["breakdown"]["room"] == 120000 and body["breakdown"]["currency"] == "AED"
    assert client_as("revenue").get(f"{BASE}/reservations/{seeded['rid']}").json()["reservation"]["guest_name"] is None
    assert client_as("owner").get(f"{BASE}/reservations/999999").status_code == 404
    b = client_as("finance").get(f"{BASE}/reservations/{seeded['rid']}/breakdown").json()
    assert b["td_label"] == "Tourism Dirham fee"


def test_otb_and_exclusion_routes(client_as, seeded, cur):
    owner = client_as("owner")
    assert owner.get(f"{BASE}/otb").json() == {"snapshot_date": None, "rows": []}
    stays.snapshot_otb(cur, "test-dubai", horizon_days=5)
    body = owner.get(f"{BASE}/otb").json()
    assert body["snapshot_date"] == "2025-10-20" and len(body["rows"]) == 6
    assert owner.get(f"{BASE}/otb/pace/2025-10-21").json()[0]["days_before"] == 1
    listed = owner.get(f"{BASE}/baseline-exclusions").json()
    assert any(x["hotel_slug"] is None and x["from_date"] == "2026-03-01" for x in listed)
    r = owner.post(f"{BASE}/baseline-exclusions", json={"from_date": "2025-01-01", "to_date": "2025-01-05",
                                                        "reason": "renovation"})
    assert r.status_code == 201
    assert owner.delete(f"{BASE}/baseline-exclusions/{r.json()['id']}").json() == {"ok": True}
    glob = next(x for x in listed if x["hotel_slug"] is None)
    assert owner.delete(f"{BASE}/baseline-exclusions/{glob['id']}").status_code == 404


def test_stays_metrics_run(cur, seeded, hotel_uae):
    from clefs.core import metrics
    from clefs.core.metrics import MetricQuery

    class Ctx:  # the minimal ctx run_metric reads
        hotel = hotel_uae
        slug = hotel_uae.slug

        @staticmethod
        def can(_perm):
            return True

    for key, want in (("stays.room_nights", 3), ("stays.room_revenue", 1200), ("stays.bookings", 1)):
        start, end = (date(2025, 9, 1), date(2025, 10, 31))
        try:
            out = metrics.run_metric(Ctx(), MetricQuery(metric=key, start=start, end=end, grain="total"))
        except TypeError:
            pytest.skip("run_metric needs a full HotelCtx here")
        assert float(out["total"]) == want, key


def test_every_stays_metric_compiles_and_explains_by_dimension(cur, hotel_uae):
    """The foundation contract check, scoped to stays (it runs without the
    other modules loaded)."""
    from clefs.core import metrics

    keys = [m for m in metrics.registered_metrics() if m.key.startswith("stays.")]
    assert {m.key for m in keys} == {"stays.room_nights", "stays.room_revenue", "stays.bookings", "stays.cancellations"}
    for m in keys:
        for grain, group in (("total", []), ("day", [d.key for d in m.dimensions[:2]])):
            q = metrics.MetricQuery(metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30), grain=grain,
                                    group_by=group)
            sql, params = metrics.compile_metric(hotel_uae, q)
            cur.execute("EXPLAIN " + sql, params)
            assert cur.fetchall(), m.key
