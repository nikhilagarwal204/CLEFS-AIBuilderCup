"""Service-risk producer (brief.sla_breach) from service_requests_v1."""

from __future__ import annotations

from datetime import timedelta

from clefs.core import signals as S
from clefs.modules.brief.producers import service_risk as SR


def _req(cur, slug, *, room="502", category="Housekeeping", priority="medium", status="new", age_min=0,
         accepted_min_ago=None, now):
    ts = int((now - timedelta(minutes=age_min)).timestamp() * 1000)
    acc = int((now - timedelta(minutes=accepted_min_ago)).timestamp() * 1000) if accepted_min_ago is not None else None
    cur.execute(
        """INSERT INTO staff_requests (hotel_slug, room, type, category, priority, status, timestamp, accepted_at)
           VALUES (%s, %s, 'Towels', %s, %s, %s, %s, %s) RETURNING id""",
        (slug, room, category, priority, status, ts, acc),
    )
    return cur.fetchone()["id"]


def _open(cur, slug):
    return {s["dedupe_key"]: s for s in S.list_signals(cur, slug, modules=["brief"], limit=100)
            if s["kind"] == SR.KIND}


def test_sla_minutes_by_department():
    assert SR.sla_minutes("Housekeeping") == 30
    assert SR.sla_minutes("Maintenance") == 60 and SR.sla_minutes("Engineering") == 60
    assert SR.sla_minutes("Food & Beverage") == 45 and SR.sla_minutes("Kitchen") == 45
    assert SR.sla_minutes("Concierge") == 60


def test_new_request_breaches_after_ten_minutes(cur, hotel_uae, frozen):
    now = frozen.now()
    fresh = _req(cur, hotel_uae.slug, age_min=9, now=now)
    late = _req(cur, hotel_uae.slug, room="101", age_min=25, priority="high", now=now)
    out = SR.run(cur, hotel_uae.slug, now=now)
    assert out["breaches"] == 1
    sigs = _open(cur, hotel_uae.slug)
    s = sigs[f"brief:sla:{late}:new"]
    assert f"brief:sla:{fresh}:new" not in sigs
    assert s["title"] == "Room 101: Housekeeping request unacknowledged for 25 min"
    assert s["section"] == "service_risk" and s["cadence"] == "daily" and s["severity"] == 3
    assert s["impact_minor"] is None


def test_in_progress_breaches_after_department_sla_and_low_priority_is_weekly(cur, hotel_uae, frozen):
    now = frozen.now()
    hk = _req(cur, hotel_uae.slug, status="progress", age_min=50, accepted_min_ago=31, now=now)
    mt = _req(cur, hotel_uae.slug, category="Maintenance", status="progress", age_min=50, accepted_min_ago=40, now=now)
    SR.run(cur, hotel_uae.slug, now=now)
    sigs = _open(cur, hotel_uae.slug)
    assert f"brief:sla:{hk}:progress" in sigs and f"brief:sla:{mt}:progress" not in sigs
    assert sigs[f"brief:sla:{hk}:progress"]["cadence"] == "weekly"


def test_resolved_requests_close_their_signal(cur, hotel_uae, frozen):
    now = frozen.now()
    rid = _req(cur, hotel_uae.slug, age_min=30, now=now)
    SR.run(cur, hotel_uae.slug, now=now)
    assert f"brief:sla:{rid}:new" in _open(cur, hotel_uae.slug)
    cur.execute("UPDATE staff_requests SET status = 'completed', resolved_at = %s WHERE id = %s",
                (int(now.timestamp() * 1000), rid))
    out = SR.run(cur, hotel_uae.slug, now=now)
    assert out["resolved"] == 1 and _open(cur, hotel_uae.slug) == {}


def test_breach_ranks_in_the_daily_brief(cur, hotel_uae, frozen):
    from clefs.modules.brief import brief

    _req(cur, hotel_uae.slug, room="202", age_min=40, priority="high", now=frozen.now())
    SR.run(cur, hotel_uae.slug, now=frozen.now())
    out = brief.assemble(cur, hotel_uae, "daily")
    assert out["items"][0]["detector"] == SR.KIND and out["items"][0]["importance"] == 0.7
