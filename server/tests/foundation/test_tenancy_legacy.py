"""
Tenancy and the moved legacy routes on a LOCKED (non-legacy) hotel: who may
read and write what, cross-hotel isolation, the hotel-timezone fix in
staff-analytics, service.api delegating to the legacy insert, and SSE events
that are only delivered when the transaction commits.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clefs import db, events
from clefs.core import hotels
from clefs.core import time as htime

GOOD_ORIGIN = {"Origin": "http://localhost:3000"}


def _log(slug: str) -> dict:
    return {"session_id": "s1", "room_number": "101", "role": "user", "message": "hi", "timestamp": 1}


# ─── Access matrix on a locked hotel ───


def test_anonymous_is_locked_out_of_member_routes(client, hotel_uae):
    s = hotel_uae.slug
    for method, path in (
        ("GET", f"/hotels/{s}/requests"),
        ("GET", f"/hotels/{s}/staff"),
        ("GET", f"/hotels/{s}/staff-analytics"),
        ("GET", f"/hotels/{s}/pending"),
        ("GET", f"/hotels/{s}/guests"),
        ("GET", f"/hotels/{s}/conversation-logs"),
        ("POST", f"/hotels/{s}/staff/seed"),
        ("POST", f"/hotels/{s}/guests/seed"),
        ("DELETE", f"/hotels/{s}/locations/x"),
    ):
        r = client.request(method, path)
        assert r.status_code == 401, (method, path, r.status_code)
        assert r.json()["code"] == "unauthorized"


def test_public_guest_routes_stay_open_on_a_locked_hotel(client, hotel_uae):
    s = hotel_uae.slug
    assert client.get(f"/hotels/{s}").status_code == 200
    assert client.get(f"/hotels/{s}/locations").status_code == 200
    assert client.post(f"/hotels/{s}/conversation-logs", json=_log(s)).json() == {"ok": True}
    r = client.post(f"/hotels/{s}/pending", json={"data": {"room": "101", "type": "Towel", "description": "2 towels"}})
    assert r.json() == {"ok": True}


def test_role_matrix_on_legacy_routes(client_as, hotel_uae):
    s = hotel_uae.slug
    staff = client_as("staff")
    assert staff.get(f"/hotels/{s}/requests").status_code == 200  # service.view
    assert staff.get(f"/hotels/{s}/guests").status_code == 403  # guest PII
    assert staff.post(f"/hotels/{s}/staff/seed").status_code == 403  # service.manage
    assert staff.get(f"/hotels/{s}/conversation-logs").status_code == 403
    fd = client_as("front_desk")
    assert fd.get(f"/hotels/{s}/guests").status_code == 200
    assert fd.get(f"/hotels/{s}/conversation-logs").status_code == 200
    assert fd.post(f"/hotels/{s}/locations", json={"slug": "spa", "name": "Spa"}).status_code == 403
    owner = client_as("owner")
    assert owner.post(f"/hotels/{s}/locations", json={"slug": "spa", "name": "Spa"}).status_code == 200
    finance = client_as("finance")
    assert finance.get(f"/hotels/{s}/requests").status_code == 200
    assert finance.patch(f"/hotels/{s}/requests/1", json={"status": "completed"}).status_code == 200


def test_hotels_cannot_see_each_other(cur, client_as, hotel_uae, legacy_slug):
    hotels.create_hotel(cur, slug="rival-hotel", name="Rival")
    cur.execute(
        "INSERT INTO staff_requests (hotel_slug, room, type, timestamp) VALUES ('rival-hotel', '9', 'Towel', 1), (%s, '1', 'Towel', 2)",
        (hotel_uae.slug,),
    )
    mine = client_as("owner")
    rows = mine.get(f"/hotels/{hotel_uae.slug}/requests").json()
    assert {r["hotel_slug"] for r in rows} == {hotel_uae.slug}
    assert mine.get("/hotels/rival-hotel/requests").status_code == 403
    assert mine.get("/hotels/rival-hotel/context").status_code == 403
    # A legacy open hotel is open to everyone, including other hotels' members.
    assert mine.get(f"/hotels/{legacy_slug}/requests").status_code == 200


def test_unknown_hotel_is_404(client):
    assert client.get("/hotels/nope-hotel/requests").status_code == 404
    assert client.post("/hotels/nope-hotel/pending", json={"data": {"room": "1"}}).status_code == 404


def test_legacy_open_access_can_be_switched_off(cur, client, client_as, legacy_slug):
    owner = client_as("owner", hotel=legacy_slug)
    assert client.get(f"/hotels/{legacy_slug}/requests").status_code == 200
    r = owner.patch(f"/hotels/{legacy_slug}/settings", json={"legacy_open_access": False}, headers=GOOD_ORIGIN)
    assert r.status_code == 200 and r.json()["legacy_open_access"] is False
    assert client.get(f"/hotels/{legacy_slug}/requests").status_code == 401
    assert owner.get(f"/hotels/{legacy_slug}/requests").status_code == 200


# ─── Hotel timezone in analytics ───


def test_staff_analytics_uses_hotel_local_day(cur, client_as, clock, hotel_uae):
    # 21:30 UTC on the 28th is 01:30 on the 29th in Dubai.
    now = clock.freeze(datetime(2026, 9, 28, 21, 30, tzinfo=timezone.utc))
    s = hotel_uae.slug
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department) VALUES (%s, 'Asha', 'HK', 'Housekeeping')", (s,))
    before_dubai_midnight = htime.to_ms(datetime(2026, 9, 28, 19, 30, tzinfo=timezone.utc))  # 23:30 Dubai, 28th
    after_dubai_midnight = htime.to_ms(datetime(2026, 9, 28, 20, 30, tzinfo=timezone.utc))  # 00:30 Dubai, 29th
    cur.execute(
        """
        INSERT INTO staff_requests (hotel_slug, room, type, status, timestamp, assigned_to, accepted_at, resolved_at)
        VALUES (%s, '101', 'Towel', 'completed', %s, 'Asha', %s, %s),
               (%s, '102', 'Towel', 'completed', %s, 'Asha', %s, %s)
        """,
        (
            s, before_dubai_midnight - 60_000, before_dubai_midnight - 60_000, before_dubai_midnight,
            s, after_dubai_midnight - 60_000, after_dubai_midnight - 60_000, after_dubai_midnight,
        ),
    )
    cur.execute(
        "INSERT INTO staff_shifts (hotel_slug, staff_name, check_in, check_out, duration_minutes) VALUES "
        "(%s, 'Asha', %s, %s, 60), (%s, 'Asha', %s, %s, 30)",
        (
            s, datetime(2026, 9, 28, 19, 0, tzinfo=timezone.utc), datetime(2026, 9, 28, 20, 0, tzinfo=timezone.utc),
            s, datetime(2026, 9, 28, 20, 15, tzinfo=timezone.utc), now,
        ),
    )
    body = client_as("owner").get(f"/hotels/{s}/staff-analytics").json()
    assert body["hotel_kpis"]["completed_today"] == 1  # only the one after Dubai midnight
    asha = body["staff"][0]
    assert (asha["completed_today"], asha["total_completed"]) == (1, 2)
    assert (asha["today_work_mins"], asha["total_work_mins"]) == (30, 90)


# ─── service.api delegates to the legacy insert ───


def test_service_api_create_request_and_guest_status(cur, hotel_uae):
    from clefs.modules.service import api as service_api

    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department, is_online) VALUES (%s, 'Asha', 'HK', 'Housekeeping', TRUE)", (hotel_uae.slug,))
    with db.db() as c:
        row = service_api.create_request(c, hotel_uae.slug, room="101", type="Towel", description="two towels", source="web")
    assert row["assigned_to"] == "Asha" and row["status"] == "progress" and row["category"] == "Housekeeping"
    with db.db() as c:
        status = service_api.request_status_for_guest(c, hotel_uae.slug, row["tracking_token"])
        assert status["id"] == row["id"] and status["status"] == "progress" and "assigned_to" not in status
        assert service_api.request_status_for_guest(c, "other-hotel", row["tracking_token"]) is None
        assert service_api.request_status_for_guest(c, hotel_uae.slug, "forged.token") is None
        esc = service_api.escalate_to_human(
            c, hotel_uae.slug, room="101", reason="complaint", channel="voice", sensitive=True, excerpt="private words"
        )
    cur.execute("SELECT type, priority, description FROM staff_requests WHERE id = %s", (esc["request_id"],))
    r = cur.fetchone()
    assert r["type"] == "Direct Conversation" and r["priority"] == "high" and "private words" not in r["description"]


# ─── Events are delivered on commit only ───


def test_publish_with_cur_waits_for_commit(monkeypatch, hotel_uae):
    seen: list[str] = []
    monkeypatch.setattr(events, "_fanout", lambda payload: seen.append(payload["e"]))
    with db.db() as c:
        events.publish(hotel_uae.slug, "service.test_committed", {"id": 1}, cur=c)
        assert seen == []
    assert seen == ["service.test_committed"]
    with pytest.raises(RuntimeError):
        with db.db() as c:
            events.publish(hotel_uae.slug, "service.test_rolled_back", {}, cur=c)
            raise RuntimeError("boom")
    assert seen == ["service.test_committed"]


def test_legacy_pending_emits_legacy_event_names(monkeypatch, client, hotel_uae):
    seen: list[str] = []
    monkeypatch.setattr(events, "_fanout", lambda payload: seen.append(payload["e"]))
    client.post(f"/hotels/{hotel_uae.slug}/pending", json={"data": {"room": "101", "type": "Towel"}})
    assert seen == ["requests_changed", "new_clefs_request", "new_aria_request"]


def test_audience_filtering_and_payload_cap():
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        q: asyncio.Queue = asyncio.Queue()
        anon = events.Subscriber(hotel_slug="h", queue=q, loop=loop)
        hk = events.Subscriber(hotel_slug="h", queue=q, loop=loop, kind="device", device_id=7, role="device",
                               departments=frozenset({"Housekeeping"}), floors=frozenset({"5"}))
        assert events.matches(anon, None) and not events.matches(anon, {"roles": ["owner"]})
        assert events.matches(hk, {"departments": ["Housekeeping"], "floors": [5]})
        assert not events.matches(hk, {"departments": ["Engineering"]})
        assert events.matches(hk, {"device_ids": [7]}) and not events.matches(hk, {"device_ids": [8]})
        payload, text = events._encode("h", "x.big", {"id": 5, "blob": "a" * 10_000}, None)
        assert payload["d"] == {"truncated": True, "id": 5} and len(text) < events.MAX_PAYLOAD_BYTES
    finally:
        loop.close()


def test_sse_delivers_published_events_to_subscribers(client, monkeypatch, legacy_slug):
    import threading
    import time

    monkeypatch.setattr(events, "STREAM_MAX_S", 1.0)

    def publish_when_subscribed():
        for _ in range(100):
            if events.subscriber_count(legacy_slug):
                events.publish(legacy_slug, "requests_changed", {})
                return
            time.sleep(0.01)

    t = threading.Thread(target=publish_when_subscribed)
    t.start()
    body = client.get(f"/hotels/{legacy_slug}/stream").text
    t.join()
    assert "event: requests_changed\ndata: {}\n\n" in body


def test_business_date_helpers():
    h = hotels.Hotel(id=1, slug="x", name="x", timezone="Asia/Dubai")
    late = datetime(2026, 9, 28, 23, 30, tzinfo=timezone.utc)  # 03:30 Dubai on the 29th
    assert htime.local_date(h, late).day == 29
    assert htime.business_date(h, late).day == 28  # before the 04:00 night audit
    assert htime.business_date(h, late + timedelta(hours=1)).day == 29
