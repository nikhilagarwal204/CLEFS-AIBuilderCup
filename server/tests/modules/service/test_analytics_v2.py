"""Service analytics v2 (03 spec 1.7): hotel-timezone day buckets (21:30
UTC is 01:30 the next day in Dubai), first response vs completion vs work
time, pre-v2 rows kept out of ack figures, late offline acks using
client_at but never before assigned_at, and grouping with labels."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from clefs.core import time as htime
from clefs.modules.service import analytics, api, config_api, state

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _ops_routes(app):
    config_api.mount_ops(app)


def _ms(dt):
    return htime.to_ms(dt)


def _staff(cur, slug, name="Amina"):
    cur.execute("INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) "
                "VALUES (%s, %s, 'A', 'Housekeeping', TRUE, TRUE) RETURNING id", (slug, name))
    return cur.fetchone()["id"]


def _at(cur, rid, created, *, acked=None, resolved=None, state_="completed"):
    cur.execute(
        """UPDATE staff_requests SET "timestamp" = %s, assigned_at = %s, acknowledged_at = %s, resolved_at = %s,
               state = %s WHERE id = %s""",
        (_ms(created), _ms(created), _ms(acked) if acked else None, _ms(resolved) if resolved else None, state_, rid),
    )


def test_buckets_in_hotel_timezone_and_split_times(client_as, cur, hotel_uae, clock):
    s = hotel_uae.slug
    amina = _staff(cur, s)
    late_night = datetime(2026, 10, 4, 21, 30, tzinfo=UTC)  # 01:30 on 5 Oct in Dubai
    a = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    _at(cur, a["id"], late_night, acked=late_night + timedelta(minutes=4), resolved=late_night + timedelta(minutes=14))
    b = api.create_request(cur, s, room="102", type="water", description="water", source="phone_desk")
    noon = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    _at(cur, b["id"], noon, acked=noon + timedelta(minutes=2), resolved=noon + timedelta(minutes=6))
    # a legacy row: no intake_version, no ack; counted, but not in ack figures
    cur.execute("""INSERT INTO staff_requests (hotel_slug, room, type, description, category, priority, status, "timestamp")
                   VALUES (%s, '103', 'Towel', 'x', 'Housekeeping', 'low', 'completed', %s)""", (s, _ms(noon)))
    out = analytics.compute(cur, hotel_uae, datetime(2026, 10, 5).date(), datetime(2026, 10, 5).date(), group_by="source")
    t = out["totals"]
    assert (t["n"], t["pre_v2"], t["n_acked"]) == (3, 1, 2)
    assert t["ack_p50_min"] == 3.0 and t["resolve_p50_min"] == 10.0 and t["work_p50_min"] == 7.0
    assert [d["day"] for d in out["days"]] == ["2026-10-05"]
    assert {g["key"]: g["n"] for g in out["groups"]} == {"web": 1, "phone_desk": 1, "legacy": 1}
    # the 4 Oct window (Dubai) does not see the 21:30 UTC request
    prev = analytics.compute(cur, hotel_uae, datetime(2026, 10, 4).date(), datetime(2026, 10, 4).date())
    assert prev["totals"]["n"] == 0


def test_late_offline_ack_uses_client_time_not_before_assignment(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    _staff(cur, s)
    created = datetime(2026, 10, 5, 8, 0, tzinfo=UTC)
    r = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    _at(cur, r["id"], created, acked=created + timedelta(minutes=30), state_="acknowledged")
    cur.execute(
        """INSERT INTO svc_request_events (hotel_slug, request_id, kind, action, actor_type, late_sync, client_at, at)
           VALUES (%s, %s, 'transition', 'ack', 'device', TRUE, %s, %s)""",
        (s, r["id"], created + timedelta(minutes=3), created + timedelta(minutes=30)),
    )
    cur.execute("DELETE FROM svc_request_events WHERE request_id = %s AND action = 'ack' AND late_sync IS NOT TRUE", (r["id"],))
    out = analytics.compute(cur, hotel_uae, created.date(), created.date())
    assert out["totals"]["ack_p50_min"] == 3.0
    # a device clock that ran early cannot report an ack before the assignment
    cur.execute("UPDATE svc_request_events SET client_at = %s WHERE request_id = %s AND action = 'ack'",
                (created - timedelta(minutes=10), r["id"]))
    assert analytics.compute(cur, hotel_uae, created.date(), created.date())["totals"]["ack_p50_min"] == 0.0


def test_http_group_by_staff_with_labels(client_as, cur, hotel_uae):
    s = hotel_uae.slug
    amina = _staff(cur, s)
    r = api.create_request(cur, s, room="101", type="towels", description="towels", source="web")
    state.transition(cur, s, r["id"], "complete", state.SYSTEM)
    gm = client_as("gm")
    out = gm.get(f"/hotels/{s}/service/analytics", params={"group_by": "staff"}).json()
    assert out["timezone"] == "Asia/Dubai" and out["totals"]["n"] == 1
    assert out["groups"][0]["label"] == "Amina" and out["groups"][0]["key"] == str(amina)
    assert out["totals"]["currency"] == "AED" and out["totals"]["recovery_cost_minor"] == 0
    bad = gm.get(f"/hotels/{s}/service/analytics", params={"from": "2026-10-05", "to": "2026-10-01"})
    assert bad.status_code == 400
    assert client_as("staff").get(f"/hotels/{s}/service/analytics").status_code in (200, 403)
