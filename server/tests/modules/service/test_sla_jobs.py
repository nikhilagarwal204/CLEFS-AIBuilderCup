"""Service jobs, metrics and signals (03 spec 1.7, 1.9): the jobs run
through the scheduler, metrics bucket in the hotel's timezone (21:30 UTC is
01:30 Dubai the next day) and leave pre-v2 rows out of ack metrics, and the
detectors raise their signals once."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from clefs.core import signals as core_signals
from clefs.modules.service import api, intake, jobs, state  # jobs registers jobs, metrics, signal kinds

DXB = ZoneInfo("Asia/Dubai")
T0 = datetime(2026, 10, 5, 10, 0, tzinfo=DXB)


def _staff(cur, slug, name="Amina", department="Housekeeping"):
    cur.execute(
        "INSERT INTO staff_members (hotel_slug, name, role, department, is_active, is_online) VALUES (%s, %s, 'A', %s, TRUE, TRUE) RETURNING id",
        (slug, name, department),
    )
    return cur.fetchone()["id"]


def _file(cur, slug, room="101", type_="towels", **kw):
    return api.create_request(cur, slug, room=room, type=type_, description=kw.pop("description", "please"),
                              source=kw.pop("source", "phone_desk"), **kw)


def _signals(cur, slug, kind):
    cur.execute("SELECT dedupe_key, title, status FROM signals WHERE hotel_slug = %s AND kind = %s ORDER BY id", (slug, kind))
    return [dict(r) for r in cur.fetchall()]


# ─── Scheduler wiring ───


def test_every_1_7_job_is_registered_with_its_schedule():
    from clefs import jobs as core_jobs

    got = {j.name: j for j in core_jobs.registered_jobs() if j.name.startswith("service.")}
    assert set(got) == {"service.sla_sweep", "service.scheduled_release", "service.session_autologout",
                        "service.guest_confirm_expire", "service.repeat_fault_detect", "service.daily_rollup",
                        "service.roster_labour_scan", "service.tracker_push"}
    assert all(j.per_hotel for j in got.values())


def test_the_sweep_runs_from_the_scheduler(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    row = _file(cur, s)
    clock.advance(minutes=4)
    report = run_jobs(only="service.sla_sweep", scheduled=True)
    assert report is not None
    cur.execute("SELECT sla_ack_breached FROM staff_requests WHERE id = %s", (row["id"],))
    assert cur.fetchone()["sla_ack_breached"] is True
    cur.execute("SELECT last_sweep_at FROM svc_sweep_state WHERE hotel_slug = %s", (s,))
    assert cur.fetchone()["last_sweep_at"] is not None


def test_scheduled_release_opens_and_routes_at_its_time(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    sid = _staff(cur, s, "Desk", "Front Desk")  # wake-up calls go to Front Office
    when = int((T0 + timedelta(hours=2)).timestamp() * 1000)
    out = intake.create_request(cur, s, intake.IntakeIn(description="wake me", source="phone_desk", room="102",
                                                        request_type_key="wake_up", scheduled_for=when))
    assert out.row["state"] == "scheduled"
    run_jobs(only="service.scheduled_release", scheduled=True)
    assert state.load(cur, s, out.row["id"])["state"] == "scheduled"
    clock.advance(hours=2, minutes=1)
    run_jobs(only="service.scheduled_release", scheduled=True)
    row = state.load(cur, s, out.row["id"])
    assert row["state"] == "assigned" and row["assigned_staff_id"] == sid
    assert row["ladder_started_at"] >= when


def test_session_autologout_ends_overdue_and_idle_sessions(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    a, b, c, d = _staff(cur, s, "A"), _staff(cur, s, "B"), _staff(cur, s, "C"), _staff(cur, s, "D")
    cur.execute("INSERT INTO devices (hotel_slug, name, kind) VALUES (%s, 'floor 1', 'floor') RETURNING id", (s,))
    dev = cur.fetchone()["id"]
    now = T0
    rows = [(a, now - timedelta(hours=9), now - timedelta(minutes=20), now - timedelta(minutes=1)),  # shift over
            (b, now - timedelta(hours=1), now + timedelta(hours=7), now - timedelta(minutes=30)),  # idle 30 min
            (c, now - timedelta(hours=1), now + timedelta(hours=7), now - timedelta(minutes=2)),  # busy
            (d, now - timedelta(hours=1), now + timedelta(hours=7), now - timedelta(minutes=40))]  # acted 3 min ago
    cur.execute("""INSERT INTO svc_request_events (hotel_slug, request_id, kind, actor_type, actor_staff_id, device_id, at)
                   VALUES (%s, 1, 'note', 'device', %s, %s, %s)""", (s, d, dev, now - timedelta(minutes=3)))
    for sid, started, planned, active in rows:
        cur.execute("""INSERT INTO device_sessions (device_id, hotel_slug, staff_member_id, started_at, planned_end_at, last_activity_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""", (dev, s, sid, started, planned, active))
    run_jobs(only="service.session_autologout", scheduled=True)
    cur.execute("SELECT staff_member_id, end_reason FROM device_sessions WHERE device_id = %s ORDER BY staff_member_id", (dev,))
    got = {r["staff_member_id"]: r["end_reason"] for r in cur.fetchall()}
    assert got == {a: "auto_shift_end", b: "auto_idle", c: None, d: None}


def test_unanswered_confirmation_becomes_unknown_after_24h(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    row = _file(cur, s)
    state.transition(cur, s, row["id"], "complete", state.SYSTEM)
    clock.advance(hours=23)
    run_jobs(only="service.guest_confirm_expire", scheduled=True)
    assert state.load(cur, s, row["id"])["guest_confirmation"] is None
    clock.advance(hours=2)
    run_jobs(only="service.guest_confirm_expire", scheduled=True)
    assert state.load(cur, s, row["id"])["guest_confirmation"] == "unknown"


# ─── Metrics and the rollup ───

LATE_UTC = datetime(2026, 10, 5, 21, 30, tzinfo=timezone.utc)  # 01:30 on 6 Oct in Dubai


def _metric(cur, hotel, key, start, end, grain="total", group_by=()):
    from clefs.core.metrics import MetricQuery, compile_metric

    sql, params = compile_metric(hotel, MetricQuery(metric=key, start=start, end=end, grain=grain, group_by=list(group_by)))
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def _acked_request(cur, slug, after_min):
    row = _file(cur, slug)
    from clefs.core import clock as clk

    clk.advance(timedelta(minutes=after_min))
    state.transition(cur, slug, row["id"], "ack", state.Actor("staff", staff_id=row["assigned_staff_id"]))
    return row


def test_metrics_bucket_in_hotel_time(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(LATE_UTC)
    _staff(cur, s)
    _acked_request(cur, s, 2)
    rows = _metric(cur, hotel_uae, "service.requests", date(2026, 10, 5), date(2026, 10, 6), grain="day")
    assert [(str(r["bucket"]), int(r["value"])) for r in rows] == [("2026-10-06", 1)]


def test_ack_metrics_leave_pre_v2_rows_out(cur, hotel_uae, clock):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    _acked_request(cur, s, 2)
    ts = int(T0.timestamp() * 1000)
    cur.execute(
        """INSERT INTO staff_requests (hotel_slug, room, type, description, category, priority, status, "timestamp",
                                       accepted_at, acknowledged_at)
           VALUES (%s, '102', 'Towel', 'x', 'Housekeeping', 'medium', 'progress', %s, %s, %s)""",
        (s, ts, ts + 10_000, ts + 10_000),
    )
    day = T0.date()
    assert float(_metric(cur, hotel_uae, "service.ack_p50_min", day, day)[0]["value"]) == 2.0
    assert int(_metric(cur, hotel_uae, "service.requests", day, day)[0]["value"]) == 2
    by_source = _metric(cur, hotel_uae, "service.requests_by_source", day, day, group_by=["source"])
    assert {r["g_source"]: int(r["value"]) for r in by_source} == {"legacy": 1, "phone_desk": 1}
    assert float(_metric(cur, hotel_uae, "service.phone_desk_share", day, day)[0]["value"]) == 50.0


def test_daily_rollup_uses_the_hotel_local_day(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(LATE_UTC)
    _staff(cur, s)
    _acked_request(cur, s, 3)
    clock.freeze(datetime(2026, 10, 7, 0, 31, tzinfo=DXB))
    run_jobs(only="service.daily_rollup", scheduled=True)
    cur.execute("SELECT local_date, n, n_acked, ack_p50_s, currency FROM svc_daily_rollups WHERE hotel_slug = %s", (s,))
    rows = [dict(r) for r in cur.fetchall()]
    assert rows == [{"local_date": date(2026, 10, 6), "n": 1, "n_acked": 1, "ack_p50_s": 180, "currency": "AED"}]
    assert jobs.rollup_day(cur, hotel_uae, date(2026, 10, 6)) == 1  # a re-run replaces its day


# ─── Signals ───


def test_every_1_7_signal_kind_is_registered_with_a_cadence():
    from clefs.modules.service import signals as svc_signals

    for key, spec in svc_signals.KINDS.items():
        k = core_signals.get_kind(key)
        assert k is not None and k.cadence == spec["cadence"] and k.section == spec["section"], key
    assert core_signals.get_kind("service.labour_flag").section == "compliance"  # never in the daily top 5


def test_three_breaches_in_a_shift_raise_one_cluster_signal(cur, hotel_uae, clock):
    from clefs.modules.service import sla

    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    for room in ("101", "102", "103"):
        _file(cur, s, room)
    clock.advance(minutes=4)
    sla.sweep(cur, s)
    got = _signals(cur, s, "service.sla_breach_cluster")
    assert len(got) == 1 and got[0]["title"].startswith("3 requests missed")
    clock.advance(minutes=1)
    sla.sweep(cur, s)
    assert len(_signals(cur, s, "service.sla_breach_cluster")) == 1  # same shift, same signal


def test_l1_past_its_ack_time_is_its_own_signal(cur, hotel_uae, clock):
    from clefs.modules.service import sla

    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    row = _file(cur, s, "201", "Other", description="my father collapsed", source="voice")
    assert row["severity"] == "L1"
    clock.advance(minutes=3)
    sla.sweep(cur, s)
    keys = [g["dedupe_key"] for g in _signals(cur, s, "service.sla_breach_cluster")]
    assert f"service.sla_breach_cluster:l1:{row['id']}" in keys


def test_guest_unresolved_for_a_second_reopen(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s)
    row = _file(cur, s)
    cur.execute("UPDATE staff_requests SET guest_confirmation = 'no', guest_confirmed_at = %s, reopen_count = 2 WHERE id = %s",
                (int(T0.timestamp() * 1000), row["id"]))
    run_jobs(only="service.guest_confirm_expire", scheduled=True)
    got = _signals(cur, s, "service.guest_unresolved")
    assert [g["dedupe_key"] for g in got] == [f"service.guest_unresolved:{row['id']}"]
    assert "reopened 2 times" in got[0]["title"] and "please" not in got[0]["title"]


def test_repeat_fault_same_room_twice_in_7_days(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(T0)
    _staff(cur, s, "Eng", "Maintenance")
    _file(cur, s, "105", "Maintenance", description="the air con is not cooling")
    clock.advance(days=3)
    second = _file(cur, s, "105", "Maintenance", description="AC not working again")
    state.transition(cur, s, second["id"], "complete", state.SYSTEM)
    run_jobs(only="service.repeat_fault_detect", scheduled=True)
    got = _signals(cur, s, "service.repeat_fault")
    assert [g["dedupe_key"] for g in got] == ["service.repeat_fault:room:105:ac"]
    clock.advance(days=8)
    run_jobs(only="service.repeat_fault_detect", scheduled=True)
    assert _signals(cur, s, "service.repeat_fault")[0]["status"] == "resolved"  # the pattern is gone


def test_roster_labour_scan_flags_long_days(cur, hotel_uae, clock, run_jobs):
    s = hotel_uae.slug
    clock.freeze(datetime(2026, 10, 5, 4, 0, tzinfo=DXB))
    sid = _staff(cur, s, "Long Day")
    start = datetime(2026, 10, 6, 7, 0, tzinfo=DXB)
    cur.execute(
        """INSERT INTO svc_shift_assignments (hotel_slug, staff_member_id, business_date, start_at, end_at)
           VALUES (%s, %s, %s, %s, %s)""",
        (s, sid, start.date(), start, start + timedelta(hours=11)),
    )
    clock.freeze(datetime(2026, 10, 5, 5, 1, tzinfo=DXB))
    run_jobs(only="service.roster_labour_scan", scheduled=True)
    got = _signals(cur, s, "service.labour_flag")
    assert len(got) == 1 and "Long Day" in got[0]["title"] and "2026-10-06" in got[0]["title"]


# ─── Contract checks the foundation runs once the manifest loads jobs.py ───


def test_every_service_metric_and_dimension_compiles_and_explains(cur, hotel_uae):
    from clefs.core import metrics

    mine = [m for m in metrics.registered_metrics() if m.key.startswith("service.")]
    assert len(mine) == 11
    for m in mine:
        for grain, group in [("total", []), ("week", [])] + [("day", [d.key]) for d in m.dimensions]:
            q = metrics.MetricQuery(metric=m.key, start=date(2026, 9, 1), end=date(2026, 9, 30), grain=grain, group_by=group)
            sql, params = metrics.compile_metric(hotel_uae, q)
            cur.execute("EXPLAIN " + sql, params)
            assert cur.fetchall(), (m.key, group)


def test_service_keys_carry_the_service_prefix():
    from clefs import jobs as core_jobs
    from clefs.core import metrics

    keys = [j.name for j in core_jobs.registered_jobs() if j.module == "service"]
    keys += [m.key for m in metrics.registered_metrics() if m.key.startswith(("service.", "ops."))]
    keys += [k.key for k in core_signals.signal_kinds() if k.key.startswith(("service.", "ops."))]
    assert keys and all(k.startswith("service.") for k in keys)
